# -*- coding: utf-8 -*-
"""FastAPI gateway for DB Diagnosis Assistant — Python 3.9 compatible."""
from __future__ import annotations

import sys, os, json, queue, threading, logging, logging.handlers
from pathlib import Path
from typing import Optional, Dict, Any

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "generated"))
sys.path.insert(0, _PROJECT_ROOT)

import grpc
import agent_pb2
import agent_pb2_grpc

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, status, Request, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from python.db.manager import DatabaseManager
from python.db.skill_service import SkillService
from python.db.user_service import UserService

import metadb
from models import UserRegister, UserLogin, TokenResponse, UserResponse, LLMConfig

import auth as auth_mod

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover - fallback for older interpreters
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

_DEFAULT_LOG_CFG = {"dir": "logs", "gateway_prefix": "gateway", "retention_days": 14}


def _load_log_cfg() -> Dict[str, Any]:
    cfg_path = Path(_PROJECT_ROOT) / "config.toml"
    if tomllib is None:
        return dict(_DEFAULT_LOG_CFG)
    try:
        with cfg_path.open("rb") as f:
            data = tomllib.load(f)
        return {**_DEFAULT_LOG_CFG, **data.get("logging", {})}
    except FileNotFoundError:
        return dict(_DEFAULT_LOG_CFG)
    except Exception as exc:  # pragma: no cover - defensive
        sys.stderr.write(f"config.toml parse error: {exc}; using defaults\n")
        return dict(_DEFAULT_LOG_CFG)


_log_cfg = _load_log_cfg()
_log_dir = Path(_log_cfg["dir"])
if not _log_dir.is_absolute():
    _log_dir = Path(_PROJECT_ROOT) / _log_dir
_log_dir.mkdir(parents=True, exist_ok=True)

_fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
_root = logging.getLogger()
_root.setLevel(logging.INFO)
# uvicorn may have already attached a handler when running under --reload;
# clear so we don't double-emit when we install our own pair below.
for _h in list(_root.handlers):
    _root.removeHandler(_h)

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_fmt)
_root.addHandler(_console_handler)

_file_handler = logging.handlers.TimedRotatingFileHandler(
    filename=str(_log_dir / f"{_log_cfg['gateway_prefix']}.log"),
    when="midnight",
    backupCount=int(_log_cfg["retention_days"]),
    encoding="utf-8",
)
_file_handler.setFormatter(_fmt)
_root.addHandler(_file_handler)

log = logging.getLogger("gateway")
log.info(
    "logging initialised (stdout + daily rolling file) dir=%s prefix=%s retention_days=%s",
    _log_dir,
    _log_cfg["gateway_prefix"],
    _log_cfg["retention_days"],
)

GRPC_ADDR = os.environ.get("GRPC_ADDR", "localhost:50051")

app = FastAPI(title="Agent Gateway")

# ---------- Static file serving (SPA-friendly) ----------

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@app.get("/static/{path:path}")
async def serve_static_or_spa(path: str):
    file_path = os.path.join(STATIC_DIR, path)
    if os.path.isfile(file_path):
        return FileResponse(file_path)
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# ---------- Global exception handler ----------

@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception):
    log.exception("unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ---------- No-cache middleware ----------

@app.middleware("http")
async def _no_cache_html(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.endswith((".html", ".js")) or path in ("/", "/login"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# ---------- Database ----------

db = DatabaseManager()
user_service = UserService(db)
skill_service = SkillService(db)


@app.on_event("startup")
async def _startup_init_db():
    # uvicorn installs its own handlers on these loggers with propagate=False,
    # so without rerouting they would never reach our file handler. Strip the
    # uvicorn handlers and let the records bubble up to root so both sinks
    # (console + file) receive them exactly once.
    for _name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        _lg = logging.getLogger(_name)
        _lg.handlers.clear()
        _lg.propagate = True
    await metadb.init_db()
    await db.initialize()
    count = await db.seed_official_skills()
    log.info("db initialized; seeded %d official skills", count)


# ---------- Auth helpers ----------

_bearer_scheme = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> Dict[str, Any]:
    """FastAPI dependency that extracts and validates the JWT bearer token."""
    payload = auth_mod.decode_access_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject",
        )
    user = await metadb.get_user_by_id(int(user_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user


def _grpc_channel():
    return grpc.insecure_channel(GRPC_ADDR)


# ---------- Auth endpoints ----------

@app.post("/api/auth/register", response_model=TokenResponse)
async def register(body: UserRegister):
    existing = await metadb.get_user_by_username(body.username)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already exists",
        )
    hashed = auth_mod.hash_password(body.password)
    user_id = await metadb.create_user(body.username, hashed)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user",
        )
    token = auth_mod.create_access_token({"sub": str(user_id)})
    log.info("registered user %s (id=%s)", body.username, user_id)
    return TokenResponse(access_token=token)


@app.post("/api/auth/login", response_model=TokenResponse)
async def login(body: UserLogin):
    """Login with username+password (JWT) OR username-only (internal)."""
    user = await metadb.get_user_by_username(body.username)
    if user is None:
        # Auto-create user with a random password hash
        hashed = auth_mod.hash_password(os.urandom(16).hex())
        user_id = await metadb.create_user(body.username, hashed)
        if user_id is None:
            raise HTTPException(status_code=500, detail="Failed to create user")
        token = auth_mod.create_access_token({"sub": str(user_id)})
        log.info("auto-created + logged in user %s (id=%s)", body.username, user_id)
        return TokenResponse(access_token=token)

    if body.password:
        # Standard JWT login with password verification
        if not auth_mod.verify_password(body.password, user["hashed_pw"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
            )
    # If no password provided, skip verification (username-only mode)
    token = auth_mod.create_access_token({"sub": str(user["id"])})
    log.info("logged in user %s (id=%s)", user["username"], user["id"])
    return TokenResponse(access_token=token)


@app.get("/api/auth/me", response_model=UserResponse)
async def me(current_user: Dict[str, Any] = Depends(get_current_user)):
    return UserResponse(id=current_user["id"], username=current_user["username"])


# ---------- Per-user LLM config (metadb) ----------

async def _get_user_llm_config(user_id: int) -> LLMConfig:
    """Load the LLM config for a given user from the metadatabase."""
    configs = await metadb.get_all_user_configs(user_id)
    return LLMConfig(
        provider=configs.get("llm_provider", ""),
        api_key=configs.get("llm_api_key", ""),
        base_url=configs.get("llm_base_url", ""),
        model=configs.get("llm_model", ""),
        system_prompt=configs.get("llm_system_prompt", "You are a database diagnosis assistant."),
    )


async def _save_user_llm_config(user_id: int, config: LLMConfig) -> None:
    """Save the LLM config for a given user to the metadatabase."""
    await metadb.set_user_config(user_id, "llm_provider", config.provider)
    await metadb.set_user_config(user_id, "llm_api_key", config.api_key)
    await metadb.set_user_config(user_id, "llm_base_url", config.base_url)
    await metadb.set_user_config(user_id, "llm_model", config.model)
    await metadb.set_user_config(user_id, "llm_system_prompt", config.system_prompt)


# ---------- Static pages ----------

@app.get("/")
async def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


# ---------- Config endpoints (metadb, JWT-authenticated) ----------

@app.post("/api/config")
async def set_config(
    config: LLMConfig,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    await _save_user_llm_config(current_user["id"], config)
    log.info(
        "config updated for user %s: provider=%s model=%s base_url=%s",
        current_user["username"], config.provider, config.model, config.base_url,
    )
    return {"ok": True}


@app.get("/api/config")
async def get_config(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    config = await _get_user_llm_config(current_user["id"])
    return {
        "provider": config.provider,
        "model": config.model,
        "base_url": config.base_url,
        "system_prompt": config.system_prompt,
        "api_key": config.api_key,
        "has_api_key": bool(config.api_key),
    }


# ---------- Session management ----------

@app.post("/api/sessions")
async def create_session(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    config = await _get_user_llm_config(current_user["id"])
    model = config.model or "mock"
    system_prompt = config.system_prompt or "You are a database diagnosis assistant."

    api_config = None
    if config.api_key:
        api_config = agent_pb2.ApiConfig(
            provider=config.provider,
            api_key=config.api_key,
            base_url=config.base_url,
        )

    try:
        with _grpc_channel() as ch:
            stub = agent_pb2_grpc.AgentServiceStub(ch)
            resp = stub.CreateSession(agent_pb2.CreateSessionRequest(
                model=model,
                system_prompts=[system_prompt],
                api_config=api_config,
            ))
    except grpc.RpcError as e:
        log.error("CreateSession gRPC error: code=%s details=%s", e.code(), e.details())
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Agent kernel failed to create session: {e.code().name}: {e.details()}",
        ) from e
    log.info("created session %s (model=%s, has_api_config=%s, user=%s)",
             resp.session_id, model, api_config is not None, current_user["username"])
    return {"session_id": resp.session_id, "created_at_ms": resp.created_at_ms}


_DELIVERABLE_DIRECTIVES = {
    "conclusion": "Respond with a single short conclusion paragraph. Do NOT include a bulleted evidence list.",
    "evidence": "Respond with a bulleted evidence list ONLY. Do NOT include a standalone conclusion paragraph.",
    "both": "Respond with (1) a one-paragraph conclusion, then (2) a bulleted evidence list backing it up.",
}


@app.post("/api/sessions/fork")
async def fork_session(
    payload: Dict[str, Any],
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Create a child session scoped to a focused investigation."""
    parent_session_id = payload.get("parent_session_id") or ""
    selected_units = payload.get("selected_units") or []
    task = (payload.get("task") or "").strip()
    deliverable = payload.get("deliverable") or "both"

    if not task:
        raise HTTPException(status_code=400, detail="task is required")
    if deliverable not in _DELIVERABLE_DIRECTIVES:
        raise HTTPException(status_code=400, detail=f"deliverable must be one of {list(_DELIVERABLE_DIRECTIVES)}")

    config = await _get_user_llm_config(current_user["id"])
    model = config.model or "mock"
    base_prompt = config.system_prompt or "You are a database diagnosis assistant."

    if selected_units:
        context_block = "\n\n".join(
            f"## Selected {u.get('kind', 'item')}\n{u.get('content', '')}"
            for u in selected_units
        )
    else:
        context_block = "_(no context units were attached — work from your task only)_"

    fork_prompt = (
        "# Fork investigation\n\n"
        "You are a focused sub-agent spun off from a larger DB diagnosis. "
        "Your scope is STRICTLY the task below. Do NOT pursue tangents.\n\n"
        f"## Task\n{task}\n\n"
        f"## Selected context from parent conversation\n{context_block}\n\n"
        f"## Expected deliverable\n{_DELIVERABLE_DIRECTIVES[deliverable]}\n\n"
        "If the selected context is insufficient to answer the task, say so explicitly "
        "and list what additional evidence you would need — do NOT make up findings."
    )

    api_config = None
    if config.api_key:
        api_config = agent_pb2.ApiConfig(
            provider=config.provider,
            api_key=config.api_key,
            base_url=config.base_url,
        )

    with _grpc_channel() as ch:
        stub = agent_pb2_grpc.AgentServiceStub(ch)
        resp = stub.CreateSession(agent_pb2.CreateSessionRequest(
            model=model,
            system_prompts=[base_prompt, fork_prompt],
            api_config=api_config,
        ))
    log.info(
        "forked session %s from parent %s (task=%r, deliverable=%s, units=%d, user=%s)",
        resp.session_id, parent_session_id, task, deliverable,
        len(selected_units), current_user["username"],
    )
    return {"session_id": resp.session_id, "created_at_ms": resp.created_at_ms}


@app.delete("/api/sessions/{session_id}")
async def close_session(session_id: str):
    with _grpc_channel() as ch:
        stub = agent_pb2_grpc.AgentServiceStub(ch)
        resp = stub.CloseSession(agent_pb2.CloseSessionRequest(session_id=session_id))
    usage = {}
    if resp.total_usage:
        usage = {
            "input_tokens": resp.total_usage.input_tokens,
            "output_tokens": resp.total_usage.output_tokens,
        }
    log.info("closed session %s", session_id)
    return {"ok": True, "total_usage": usage}


@app.get("/api/health")
async def health():
    try:
        with _grpc_channel() as ch:
            stub = agent_pb2_grpc.AgentServiceStub(ch)
            resp = stub.HealthCheck(agent_pb2.HealthCheckRequest())
        return {
            "status": "serving" if resp.status == 1 else "not_serving",
            "active_sessions": resp.active_sessions,
            "uptime_seconds": resp.uptime_seconds,
            "version": resp.version,
        }
    except grpc.RpcError as e:
        return {"status": "unavailable", "error": str(e)}


# ---------- User & Skill REST endpoints ----------


@app.post("/api/login")
async def login(body: dict):
    username = body.get("username", "").strip()
    if not username:
        return {"error": "username required"}, 400
    user = await user_service.login(username)
    return user


@app.get("/api/skills/square")
async def get_skill_square(category: str = "", search: str = ""):
    result = await skill_service.get_square_skills(
        category=category or None,
        search=search or None,
    )
    return result


@app.get("/api/skills/mine")
async def get_my_skills(user_id: str = ""):
    if not user_id:
        return {"error": "user_id required"}, 400
    skills = await skill_service.get_user_skills(user_id)
    active_ids = await skill_service.get_active_skill_ids(user_id)
    return {"skills": skills, "active_ids": active_ids}


@app.post("/api/skills")
async def create_skill(body: dict):
    user_id = body.get("user_id", "")
    if not user_id:
        return {"error": "user_id required"}, 400
    skill = await skill_service.create_skill(user_id, body)
    return skill


@app.post("/api/skills/upload")
async def upload_skill_file(user_id: str = Form(...), file: UploadFile = File(...)):
    if not user_id:
        return {"error": "user_id required"}, 400
    content = await file.read()
    import tempfile, pathlib
    suffix = pathlib.Path(file.filename or "skill.md").suffix
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmppath = tmp.name
    try:
        data = SkillService.parse_skill_file(tmppath)
        skill = await skill_service.create_skill(user_id, data)
        return skill
    finally:
        os.unlink(tmppath)


@app.put("/api/skills")
async def update_skill(body: dict):
    skill_id = body.get("skill_id", "")
    user_id = body.get("user_id", "")
    if not skill_id:
        return {"error": "skill_id required"}, 400
    if not user_id:
        return {"error": "user_id required"}, 400
    skill = await skill_service.update_skill(skill_id, user_id, body)
    if skill is None:
        return {"error": "not found or not owned by user"}, 404
    return skill


@app.delete("/api/skills")
async def delete_skill(body: dict):
    skill_id = body.get("skill_id", "")
    user_id = body.get("user_id", "")
    if not skill_id:
        return {"error": "skill_id required"}, 400
    if not user_id:
        return {"error": "user_id required"}, 400
    await skill_service.delete_skill(skill_id, user_id)
    return {"ok": True}


@app.post("/api/skills/publish")
async def publish_skill(body: dict):
    skill_id = body.get("skill_id", "")
    user_id = body.get("user_id", "")
    if not skill_id:
        return {"error": "skill_id required"}, 400
    if not user_id:
        return {"error": "user_id required"}, 400
    result = await skill_service.toggle_publish(skill_id, user_id)
    if result is None:
        return {"error": "not found or not owned by user"}, 404
    return {"is_published": result}


@app.post("/api/skills/toggle-active")
async def toggle_active_skill(body: dict):
    skill_id = body.get("skill_id", "")
    user_id = body.get("user_id", "")
    if not skill_id:
        return {"error": "skill_id required"}, 400
    if not user_id:
        return {"error": "user_id required"}, 400
    is_active = await skill_service.toggle_active(user_id, skill_id)
    return {"is_active": is_active}


@app.post("/api/skills/clone")
async def clone_skill(body: dict):
    skill_id = body.get("skill_id", "")
    user_id = body.get("user_id", "")
    if not skill_id:
        return {"error": "skill_id required"}, 400
    if not user_id:
        return {"error": "user_id required"}, 400
    skill = await skill_service.clone_skill(skill_id, user_id)
    if skill is None:
        return {"error": "skill not found"}, 404
    return skill


# ---------- Skill preview ----------

@app.post("/api/preview_skills")
async def preview_skills(payload: dict):
    """Stateless preview: ask the Rust kernel which skills would match a draft
    message so the UI can render a picker before the user actually sends it."""
    session_id = payload.get("session_id", "")
    text = payload.get("text", "")
    top_k = int(payload.get("top_k", 8))
    try:
        with _grpc_channel() as ch:
            stub = agent_pb2_grpc.AgentServiceStub(ch)
            resp = stub.PreviewSkills(
                agent_pb2.PreviewSkillsRequest(
                    session_id=session_id, text=text, top_k=top_k
                )
            )
        return {
            "matches": [
                {
                    "id": m.skill_id,
                    "name": m.skill_name,
                    "category": m.category,
                    "score": round(m.score, 1),
                    "type": m.skill_type,
                    "description": m.description,
                }
                for m in resp.matches
            ]
        }
    except grpc.RpcError as e:
        return {"matches": [], "error": str(e)}


# ---------- WebSocket chat ----------

class _ChatInputIterator:
    """Thread-safe iterator that feeds ChatInput messages to the gRPC stream."""

    def __init__(self):
        self._q: queue.Queue = queue.Queue()
        self._done = False

    def put(self, msg: agent_pb2.ChatInput):
        self._q.put(msg)

    def close(self):
        self._done = True
        self._q.put(None)

    def __iter__(self):
        return self

    def __next__(self):
        item = self._q.get()
        if item is None or self._done:
            raise StopIteration
        return item


def _chat_output_to_json(out: agent_pb2.ChatOutput) -> Optional[Dict[str, Any]]:
    field = out.WhichOneof("payload")
    if field == "text_delta":
        return {"type": "text_delta", "content": out.text_delta.content}
    if field == "thinking_delta":
        return {"type": "thinking_delta", "content": out.thinking_delta.content}
    if field == "tool_execution":
        te = out.tool_execution
        status_map = {0: "unknown", 1: "started", 2: "completed", 3: "failed"}
        return {
            "type": "tool_execution",
            "tool_use_id": te.tool_use_id,
            "tool_name": te.tool_name,
            "status": status_map.get(te.status, "unknown"),
            "output": te.output,
            "is_error": te.is_error,
        }
    if field == "proxy_instruction":
        pi = out.proxy_instruction
        card = pi.card
        parameters = dict(card.parameters) if card else {}
        return {
            "type": "proxy_instruction",
            "instruction_id": pi.instruction_id,
            "tool_use_id": pi.tool_use_id,
            "tool_name": pi.tool_name,
            "command": card.command if card else "",
            "target_environment": card.target_environment if card else "",
            "expected_format": card.expected_format if card else "",
            "hint": card.hint if card else "",
            "timeout_seconds": card.timeout_seconds if card else 0,
            "read_only": card.read_only if card else False,
            "parameters": parameters,
            "purpose": card.purpose if card else "",
        }
    if field == "proxy_instruction_expired":
        pe = out.proxy_instruction_expired
        return {
            "type": "proxy_instruction_expired",
            "instruction_id": pe.instruction_id,
            "tool_use_id": pe.tool_use_id,
            "reason": pe.reason,
        }
    if field == "turn_complete":
        tc = out.turn_complete
        usage = {}
        if tc.turn_usage:
            usage = {
                "input_tokens": tc.turn_usage.input_tokens,
                "output_tokens": tc.turn_usage.output_tokens,
            }
        return {"type": "turn_complete", "usage": usage, "message_count": len(tc.messages)}
    if field == "error":
        return {
            "type": "error",
            "code": out.error.code,
            "message": out.error.message,
            "recoverable": out.error.recoverable,
        }
    if field == "usage_update":
        return {
            "type": "usage_update",
            "input_tokens": out.usage_update.input_tokens,
            "output_tokens": out.usage_update.output_tokens,
        }
    if field == "skill_match":
        return {
            "type": "skill_match",
            "skills": [
                {
                    "id": s.skill_id,
                    "name": s.skill_name,
                    "category": s.category,
                    "score": round(s.score, 1),
                    "type": s.skill_type,
                    "description": s.description,
                }
                for s in out.skill_match.skills
            ],
        }
    return None


@app.websocket("/ws/chat/{session_id}")
async def ws_chat(ws: WebSocket, session_id: str):
    token = ws.query_params.get("token", "")
    user = None
    if token:
        payload = auth_mod.decode_access_token(token)
        if payload is None:
            await ws.close(code=4001, reason="Invalid or missing token")
            log.warning("ws rejected: no/invalid token for session %s", session_id)
            return
        user_id = payload.get("sub")
        if user_id is None:
            await ws.close(code=4001, reason="Token missing subject")
            return
        user = await metadb.get_user_by_id(int(user_id))
        if user is None:
            await ws.close(code=4001, reason="User not found")
            return

    await ws.accept()
    log.info("ws connected for session %s (user=%s)", session_id, user["username"] if user else "anonymous")

    ch = grpc.insecure_channel(GRPC_ADDR)
    stub = agent_pb2_grpc.AgentServiceStub(ch)
    input_iter = _ChatInputIterator()

    response_stream = stub.Chat(iter(input_iter))

    stop_event = threading.Event()

    def read_grpc_responses():
        """Background thread: read gRPC responses and queue them for the WS sender."""
        try:
            for out in response_stream:
                if stop_event.is_set():
                    break
                payload = _chat_output_to_json(out)
                if payload:
                    ws_send_queue.put(payload)
        except grpc.RpcError as e:
            if not stop_event.is_set():
                ws_send_queue.put({"type": "error", "message": f"gRPC error: {e}", "recoverable": False})
        finally:
            ws_send_queue.put(None)

    ws_send_queue: queue.Queue = queue.Queue()
    reader_thread = threading.Thread(target=read_grpc_responses, daemon=True)
    reader_thread.start()

    import asyncio

    async def send_loop():
        """Forward messages from the gRPC reader thread to the WebSocket."""
        loop = asyncio.get_event_loop()
        while True:
            payload = await loop.run_in_executor(None, ws_send_queue.get)
            if payload is None:
                break
            try:
                await ws.send_json(payload)
            except WebSocketDisconnect:
                break

    send_task = asyncio.create_task(send_loop())

    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type", "")
            if msg_type == "user_message":
                content = data.get("content", "")
                context_attachments = data.get("context") or []
                log.info(
                    "session %s: user_message len=%d context=%d",
                    session_id, len(content), len(context_attachments),
                )
                proto_attachments = []
                for a in context_attachments:
                    raw_meta = a.get("metadata") or {}
                    meta = {
                        str(k): str(v)
                        for k, v in raw_meta.items()
                        if v is not None
                    } if isinstance(raw_meta, dict) else {}
                    proto_attachments.append(
                        agent_pb2.ContextAttachment(
                            source=str(a.get("source", "")),
                            content=str(a.get("content", "")),
                            metadata=meta,
                        )
                    )
                chat_input = agent_pb2.ChatInput(session_id=session_id)
                chat_input.user_message.CopyFrom(
                    agent_pb2.UserMessage(content=content, context=proto_attachments)
                )
                input_iter.put(chat_input)
            elif msg_type == "cancel":
                chat_input = agent_pb2.ChatInput(session_id=session_id)
                chat_input.cancel.CopyFrom(agent_pb2.CancelTurn(reason="user cancelled"))
                input_iter.put(chat_input)
            elif msg_type == "proxy_result":
                instruction_id = data.get("instruction_id", "")
                tool_use_id = data.get("tool_use_id", "")
                output = data.get("output", "")
                is_error = bool(data.get("is_error", False))
                log.info(
                    "session %s: proxy_result instruction=%s is_error=%s len=%d",
                    session_id, instruction_id, is_error, len(output),
                )
                chat_input = agent_pb2.ChatInput(session_id=session_id)
                chat_input.proxy_result.CopyFrom(
                    agent_pb2.ProxyToolResult(
                        instruction_id=instruction_id,
                        tool_use_id=tool_use_id,
                        output=output,
                        is_error=is_error,
                    )
                )
                input_iter.put(chat_input)
            else:
                await ws.send_json({"type": "error", "message": f"unknown message type: {msg_type}"})
    except WebSocketDisconnect:
        log.info("ws disconnected for session %s", session_id)
    except Exception as e:
        log.error("ws error for session %s: %s", session_id, e)
    finally:
        stop_event.set()
        input_iter.close()
        send_task.cancel()
        try:
            response_stream.cancel()
        except Exception:
            pass
        # Browser refresh closes the WS without firing DELETE /api/sessions,
        # so the rust session would otherwise linger — and if its turn was
        # mid-proxy-wait, the session-manager thread stays blocked, queueing
        # every subsequent gRPC command behind it. Explicitly close the
        # session on the rust side; the close path cancels any in-flight turn
        # via an atomic flag so the manager unblocks within ~500ms.
        try:
            with _grpc_channel() as cleanup_ch:
                cleanup_stub = agent_pb2_grpc.AgentServiceStub(cleanup_ch)
                cleanup_stub.CloseSession(
                    agent_pb2.CloseSessionRequest(session_id=session_id)
                )
            log.info("closed session %s (ws disconnect)", session_id)
        except Exception as e:
            log.warning("close_session on ws disconnect failed for %s: %s", session_id, e)
        ch.close()


# ---------- SPA catch-all — must be last ----------

@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    """Serve index.html for client-side routing paths (chat, settings, etc.)."""
    # Don't interfere with API / WebSocket paths
    if full_path.startswith("api/") or full_path.startswith("ws/"):
        return JSONResponse(status_code=404, content={"error": "not found"})
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
