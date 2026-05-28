# -*- coding: utf-8 -*-
"""FastAPI gateway for DB Diagnosis Assistant — Python 3.9 compatible."""
from __future__ import annotations

import sys, os, json, queue, threading, logging, logging.handlers
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "generated"))
sys.path.insert(0, _PROJECT_ROOT)

import grpc
import agent_pb2
import agent_pb2_grpc

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, status, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

import db
import db_skills
from models import UserRegister, UserLogin, TokenResponse, UserResponse, LLMConfig
from tool_usage_guidance import TOOL_USAGE_GUIDANCE

import auth as auth_mod

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover - fallback for older interpreters
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

_DEFAULT_LOG_CFG = {
    "dir": "logs",
    "gateway_prefix": "gateway",
    "retention_days": 14,
    "prompt_log_dir": "logs/prompts",
    "prompt_log_prefix_gateway": "prompt-gateway",
}


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

_prompt_log_dir = Path(_log_cfg["prompt_log_dir"])
if not _prompt_log_dir.is_absolute():
    _prompt_log_dir = Path(_PROJECT_ROOT) / _prompt_log_dir
_prompt_log_dir.mkdir(parents=True, exist_ok=True)

prompt_log = logging.getLogger("gateway.prompt")
prompt_log.setLevel(logging.INFO)
prompt_log.propagate = False
for _h in list(prompt_log.handlers):
    prompt_log.removeHandler(_h)
_prompt_file_handler = logging.handlers.TimedRotatingFileHandler(
    filename=str(_prompt_log_dir / f"{_log_cfg['prompt_log_prefix_gateway']}.log"),
    when="midnight",
    backupCount=int(_log_cfg["retention_days"]),
    encoding="utf-8",
)
_prompt_file_handler.setFormatter(logging.Formatter("%(message)s"))
prompt_log.addHandler(_prompt_file_handler)
log.info(
    "prompt logging initialised dir=%s prefix=%s",
    _prompt_log_dir,
    _log_cfg["prompt_log_prefix_gateway"],
)

GRPC_ADDR = os.environ.get("GRPC_ADDR", "localhost:50051")

app = FastAPI(title="Agent Gateway")

app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception):
    log.exception("unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.middleware("http")
async def _no_cache_html(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.endswith((".html", ".js")) or path in ("/", "/login"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.on_event("startup")
async def _startup_init_db():
    for _name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        _lg = logging.getLogger(_name)
        _lg.handlers.clear()
        _lg.propagate = True
    await db.init_db()

    # Bootstrap admin user from environment variables
    admin_username = os.environ.get("ADMIN_USERNAME", "").strip()
    admin_password = os.environ.get("ADMIN_PASSWORD", "").strip()
    if admin_username and admin_password:
        hashed = auth_mod.hash_password(admin_password)
        user_id = await db.create_user(admin_username, hashed)
        if user_id is not None:
            await db.set_user_role(user_id, "admin")
            log.info("bootstrapped admin user: %s (id=%s)", admin_username, user_id)
        else:
            existing = await db.get_user_by_username(admin_username)
            if existing:
                await db.update_user_password(existing["id"], hashed)
                if existing.get("role") != "admin":
                    await db.set_user_role(existing["id"], "admin")
                log.info("updated existing user to admin: %s (id=%s)", admin_username, existing["id"])


# ---------- Auth helpers ----------

_bearer_scheme = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> Dict[str, Any]:
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
    user = await db.get_user_by_id(int(user_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user


async def require_admin(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    log.info("admin action by user %s (id=%s)", current_user["username"], current_user["id"])
    return current_user


def _grpc_channel():
    return grpc.insecure_channel(GRPC_ADDR)


# ---------- Auth endpoints ----------

@app.post("/api/auth/register", response_model=TokenResponse)
async def register(body: UserRegister):
    existing = await db.get_user_by_username(body.username)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already exists",
        )
    hashed = auth_mod.hash_password(body.password)
    user_id = await db.create_user(body.username, hashed)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user",
        )
    created = await db.get_user_by_id(user_id)
    role = created["role"] if created else "user"
    token = auth_mod.create_access_token({"sub": str(user_id), "role": role})
    db_skills.ensure_user_exists(str(user_id), body.username)
    log.info("registered user %s (id=%s)", body.username, user_id)
    return TokenResponse(access_token=token)


@app.post("/api/auth/login", response_model=TokenResponse)
async def login(body: UserLogin):
    user = await db.get_user_by_username(body.username)
    if user is None or not auth_mod.verify_password(body.password, user["hashed_pw"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    token = auth_mod.create_access_token({"sub": str(user["id"]), "role": user.get("role", "user")})
    db_skills.ensure_user_exists(str(user["id"]), user["username"])
    log.info("logged in user %s (id=%s)", user["username"], user["id"])
    return TokenResponse(access_token=token)


@app.get("/api/auth/me", response_model=UserResponse)
async def me(current_user: Dict[str, Any] = Depends(get_current_user)):
    return UserResponse(
        id=current_user["id"],
        username=current_user["username"],
        role=current_user.get("role", "user"),
    )


# ---------- Per-user LLM config ----------

async def _get_user_llm_config(user_id: int) -> LLMConfig:
    configs = await db.get_all_user_configs(user_id)
    role = configs.get("llm_system_prompt_role")
    if role is None:
        legacy = configs.get("llm_system_prompt")
        role = legacy if legacy is not None else "You are a database diagnosis assistant."
    return LLMConfig(
        provider=configs.get("llm_provider", ""),
        api_key=configs.get("llm_api_key", ""),
        base_url=configs.get("llm_base_url", ""),
        model=configs.get("llm_model", ""),
        system_prompt_role=role,
        system_prompt_background=configs.get("llm_system_prompt_background", ""),
        system_prompt_rules=configs.get("llm_system_prompt_rules", ""),
    )


async def _save_user_llm_config(user_id: int, config: LLMConfig) -> None:
    await db.set_user_config(user_id, "llm_provider", config.provider)
    await db.set_user_config(user_id, "llm_api_key", config.api_key)
    await db.set_user_config(user_id, "llm_base_url", config.base_url)
    await db.set_user_config(user_id, "llm_model", config.model)
    await db.set_user_config(user_id, "llm_system_prompt_role", config.system_prompt_role)
    await db.set_user_config(user_id, "llm_system_prompt_background", config.system_prompt_background)
    await db.set_user_config(user_id, "llm_system_prompt_rules", config.system_prompt_rules)


def _assemble_system_segments(config: LLMConfig) -> list:
    segments = [
        config.system_prompt_role,
        config.system_prompt_background,
        config.system_prompt_rules,
    ]
    return [s for s in segments if s and s.strip()]


def _log_prompt(
    session_id: str,
    user: Dict[str, Any],
    model: str,
    provider: str,
    raw_text: str,
    context_attachments: List[Dict[str, Any]],
) -> None:
    try:
        row = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
            "side": "gateway",
            "session_id": session_id,
            "user_id": user.get("id"),
            "username": user.get("username"),
            "model": model,
            "provider": provider,
            "raw_user_text": raw_text,
            "context_attachments": context_attachments,
        }
        prompt_log.info(json.dumps(row, ensure_ascii=False))
    except Exception:
        log.exception("failed to emit gateway prompt JSONL row")


# ---------- Static pages ----------

@app.get("/")
async def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


@app.get("/login")
async def login_page():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "login.html"))


# ---------- Config endpoints ----------

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
        "system_prompt_role": config.system_prompt_role,
        "system_prompt_background": config.system_prompt_background,
        "system_prompt_rules": config.system_prompt_rules,
        "has_api_key": bool(config.api_key),
    }


_SESSION_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


@app.get("/api/system_prompt/preview")
async def preview_system_prompt(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    config = await _get_user_llm_config(current_user["id"])
    segments = _assemble_system_segments(config)
    if not segments:
        segments = ["You are a database diagnosis assistant."]
    all_segments = segments + [TOOL_USAGE_GUIDANCE]
    assembled = "\n\n".join(all_segments)
    return {
        "assembled": assembled,
        "segments": {
            "role": config.system_prompt_role,
            "background": config.system_prompt_background,
            "rules": config.system_prompt_rules,
            "tool_usage_guidance": TOOL_USAGE_GUIDANCE,
        },
    }


# ---------- Session management ----------

@app.post("/api/sessions")
async def create_session(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    config = await _get_user_llm_config(current_user["id"])
    model = config.model or "mock"
    system_prompts = _assemble_system_segments(config) or [
        "You are a database diagnosis assistant."
    ]

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
                system_prompts=system_prompts,
                api_config=api_config,
                data_dir=_SESSION_DATA_DIR,
                user_id=current_user["username"],
            ))
    except grpc.RpcError as e:
        log.error("CreateSession gRPC error: code=%s detail=%s", e.code(), e.details())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create session: {e.details()}",
        )
    log.info("created session %s (model=%s, has_api_config=%s, user=%s)",
             resp.session_id, model, api_config is not None, current_user["username"])
    await db.register_session(current_user["id"], resp.session_id)
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
    base_segments = _assemble_system_segments(config) or [
        "You are a database diagnosis assistant."
    ]

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
            system_prompts=base_segments + [fork_prompt],
            api_config=api_config,
        ))
    log.info(
        "forked session %s from parent %s (task=%r, deliverable=%s, units=%d, user=%s)",
        resp.session_id, parent_session_id, task, deliverable,
        len(selected_units), current_user["username"],
    )

    return {
        "session_id": resp.session_id,
        "fork_parent_id": parent_session_id,
    }


@app.get("/api/sessions")
async def list_sessions(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    return await db.list_user_sessions(current_user["id"])


@app.delete("/api/sessions/{session_id}")
async def close_session(
    session_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    owner = await db.get_session_owner(session_id)
    if owner is not None and owner != current_user["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Session not owned by you",
        )
    try:
        with _grpc_channel() as ch:
            stub = agent_pb2_grpc.AgentServiceStub(ch)
            resp = stub.CloseSession(agent_pb2.CloseSessionRequest(session_id=session_id))
    except grpc.RpcError as e:
        log.warning("gRPC CloseSession error for %s: %s (continuing to delete file)", session_id, e.details())
        resp = None
    usage = {}
    if resp and resp.total_usage:
        usage = {
            "input_tokens": resp.total_usage.input_tokens,
            "output_tokens": resp.total_usage.output_tokens,
        }
    log.info("closed session %s (user=%s)", session_id, current_user["username"])
    await db.delete_session(session_id)
    jsonl_path = os.path.join(_SESSION_DATA_DIR, "sessions", current_user["username"], f"{session_id}.jsonl")
    if os.path.exists(jsonl_path):
        try:
            os.remove(jsonl_path)
            log.info("deleted session file %s", jsonl_path)
        except OSError as e:
            log.warning("failed to delete session file %s: %s", jsonl_path, e)
    return {"ok": True, "total_usage": usage}


@app.post("/api/sessions/{session_id}/resume")
async def resume_session(
    session_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    owner = await db.get_session_owner(session_id)
    if owner is not None and owner != current_user["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Session not owned by you",
        )
    config = await _get_user_llm_config(current_user["id"])
    model = config.model or "mock"
    system_prompts = _assemble_system_segments(config) or [
        "You are a database diagnosis assistant."
    ]

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
            resp = stub.ResumeSession(agent_pb2.ResumeSessionRequest(
                session_id=session_id,
                data_dir=_SESSION_DATA_DIR,
                user_id=current_user["username"],
                model=model,
                system_prompts=system_prompts,
                api_config=api_config,
            ))
    except grpc.RpcError as e:
        log.error("ResumeSession gRPC error for %s: code=%s detail=%s",
                  session_id, e.code(), e.details())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to resume session: {e.details()}",
        )
    log.info("resumed session %s (loaded %d messages, user=%s)",
             resp.session_id, resp.loaded_messages, current_user["username"])
    return {
        "session_id": resp.session_id,
        "created_at_ms": resp.created_at_ms,
        "loaded_messages": resp.loaded_messages,
    }


@app.get("/api/sessions/{session_id}/history")
async def session_history(
    session_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    owner = await db.get_session_owner(session_id)
    if owner is not None and owner != current_user["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Session not owned by you",
        )
    jsonl_path = os.path.join(_SESSION_DATA_DIR, "sessions", current_user["username"], f"{session_id}.jsonl")
    if not os.path.exists(jsonl_path):
        return {"messages": []}
    messages = []
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") == "message":
                    msg_obj = record.get("message", {})
                    role = msg_obj.get("role", "")
                    blocks = msg_obj.get("blocks", [])
                    text_parts = []
                    for b in blocks:
                        try:
                            if "text" in b:
                                txt = b["text"]
                                if isinstance(txt, dict):
                                    text_parts.append(txt.get("text", ""))
                                elif isinstance(txt, str):
                                    text_parts.append(txt)
                            elif "thinking" in b:
                                thk = b["thinking"]
                                if isinstance(thk, dict):
                                    text_parts.append(thk.get("thinking", ""))
                                elif isinstance(thk, str):
                                    text_parts.append(thk)
                            elif "tool_use" in b:
                                tu = b["tool_use"]
                                text_parts.append(f"[Tool: {tu.get('name', '')}] {tu.get('input', '')}")
                            elif "tool_result" in b:
                                tr = b["tool_result"]
                                text_parts.append(f"[Result: {tr.get('tool_name', '')}] {tr.get('output', '')[:200]}")
                        except (KeyError, TypeError, AttributeError):
                            continue
                    content = "\n".join(text_parts)
                    if content:
                        messages.append({"role": role, "content": content})
    except Exception as e:
        log.error("failed to read session history for %s: %s", session_id, e)
        return {"messages": []}
    return {"messages": messages}


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


# ---------- Skill preview ----------

@app.post("/api/preview_skills")
async def preview_skills(
    payload: dict,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    session_id = payload.get("session_id", "")
    if session_id:
        owner = await db.get_session_owner(session_id)
        if owner is not None and owner != current_user["id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Session not owned by you",
            )
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


# ---------- Skills management ----------

@app.get("/api/skills/mine")
async def skills_mine(current_user: Dict[str, Any] = Depends(get_current_user)):
    uid = str(current_user["id"])
    is_admin = current_user.get("role") == "admin"
    skills = db_skills.get_user_skills(uid, is_admin=is_admin)
    active_ids = db_skills.get_user_active_ids(uid)
    return {"skills": skills, "active_ids": active_ids}


@app.get("/api/skills/square")
async def skills_square(search: str = "", category: str = ""):
    return db_skills.get_square_skills(search, category)


@app.post("/api/skills")
async def skills_create(
    body: dict,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    skill_id = db_skills.create_skill(
        user_id=str(current_user["id"]),
        name=body.get("name", ""),
        content=body.get("content", ""),
        description=body.get("description", ""),
        skill_type=body.get("skill_type", "case"),
        category=body.get("category", ""),
        keywords=body.get("keywords"),
        triggers=body.get("triggers"),
        symptoms=body.get("symptoms"),
        tags=body.get("tags"),
    )
    return {"id": skill_id}


@app.put("/api/skills")
async def skills_update(
    body: dict,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    skill_id = body.get("skill_id", "")
    if not skill_id:
        raise HTTPException(status_code=400, detail="skill_id required")
    ok = db_skills.update_skill(skill_id, str(current_user["id"]), body)
    if not ok:
        raise HTTPException(status_code=404, detail="Skill not found")
    return {"ok": True}


@app.delete("/api/skills")
async def skills_delete(
    body: dict,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    skill_id = body.get("skill_id", "")
    if not skill_id:
        raise HTTPException(status_code=400, detail="skill_id required")
    db_skills.delete_skill(skill_id)
    return {"ok": True}


@app.post("/api/skills/clone")
async def skills_clone(
    body: dict,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    skill_id = body.get("skill_id", "")
    if not skill_id:
        raise HTTPException(status_code=400, detail="skill_id required")
    new_id = db_skills.clone_skill(skill_id, str(current_user["id"]))
    if new_id is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return {"id": new_id}


@app.post("/api/skills/toggle-active")
async def skills_toggle_active(
    body: dict,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    skill_id = body.get("skill_id", "")
    if not skill_id:
        raise HTTPException(status_code=400, detail="skill_id required")
    is_active = db_skills.toggle_active(skill_id, str(current_user["id"]))
    return {"is_active": is_active}


@app.post("/api/skills/publish")
async def skills_publish(
    body: dict,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    skill_id = body.get("skill_id", "")
    if not skill_id:
        raise HTTPException(status_code=400, detail="skill_id required")
    is_published = db_skills.toggle_publish(skill_id, str(current_user["id"]))
    return {"is_published": is_published}


@app.post("/api/skills/upload")
async def skills_upload(
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    import tempfile
    form = await request.form()
    file = form.get("file")
    if file is None:
        raise HTTPException(status_code=400, detail="No file uploaded")
    content = await file.read()
    text = content.decode("utf-8")
    name = getattr(file, "filename", "uploaded_skill.md") or "uploaded_skill.md"
    skill_id = db_skills.create_skill(
        user_id=str(current_user["id"]),
        name=name.replace(".md", "").replace(".yaml", "").replace(".yml", ""),
        content=text,
        description=f"Uploaded from {name}",
    )
    return {"id": skill_id}


# ---------- Admin endpoints ----------

@app.get("/api/admin/users")
async def admin_list_users(
    current_user: Dict[str, Any] = Depends(require_admin),
):
    users = await db.get_all_users()
    return {"users": users}


@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(
    user_id: int,
    current_user: Dict[str, Any] = Depends(require_admin),
):
    if user_id == current_user["id"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )
    await db.delete_user_cascade(user_id)
    return {"ok": True}


# ---------- WebSocket chat ----------

class _ChatInputIterator:
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


def _chat_output_to_json(out: agent_pb2.ChatOutput):
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
                "cache_creation_input_tokens": tc.turn_usage.cache_creation_input_tokens,
                "cache_read_input_tokens": tc.turn_usage.cache_read_input_tokens,
            }
        return {"type": "turn_complete", "usage": usage, "message_count": len(tc.messages),
                "stop_reason": tc.stop_reason}
    if field == "error":
        return {
            "type": "error",
            "code": out.error.code,
            "message": out.error.message,
            "recoverable": out.error.recoverable,
            "failure_class": out.error.failure_class or "",
            "request_id": out.error.request_id or "",
            "provider_status": out.error.provider_status,
        }
    if field == "usage_update":
        return {
            "type": "usage_update",
            "input_tokens": out.usage_update.input_tokens,
            "output_tokens": out.usage_update.output_tokens,
            "cache_creation_input_tokens": out.usage_update.cache_creation_input_tokens,
            "cache_read_input_tokens": out.usage_update.cache_read_input_tokens,
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
    payload = auth_mod.decode_access_token(token)
    if payload is None:
        await ws.close(code=4001, reason="Invalid or missing token")
        log.warning("ws rejected: no/invalid token for session %s", session_id)
        return
    user_id = payload.get("sub")
    if user_id is None:
        await ws.close(code=4001, reason="Token missing subject")
        return
    user = await db.get_user_by_id(int(user_id))
    if user is None:
        await ws.close(code=4001, reason="User not found")
        return

    await ws.accept()
    log.info("ws connected for session %s (user=%s)", session_id, user["username"])

    ch = grpc.insecure_channel(GRPC_ADDR)
    stub = agent_pb2_grpc.AgentServiceStub(ch)
    input_iter = _ChatInputIterator()

    response_stream = stub.Chat(iter(input_iter))

    stop_event = threading.Event()

    def read_grpc_responses():
        try:
            for out in response_stream:
                if stop_event.is_set():
                    break
                field = out.WhichOneof("payload")
                log.debug("gRPC response field=%s", field)
                payload_json = _chat_output_to_json(out)
                if payload_json:
                    ws_send_queue.put(payload_json)
                else:
                    log.warning("Unhandled gRPC payload field=%s", field)
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
        loop = asyncio.get_event_loop()
        while True:
            payload_json = await loop.run_in_executor(None, ws_send_queue.get)
            if payload_json is None:
                break
            try:
                await ws.send_json(payload_json)
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
                try:
                    _user_cfg = await _get_user_llm_config(int(user_id))
                    _log_prompt(
                        session_id=session_id,
                        user=user,
                        model=_user_cfg.model,
                        provider=_user_cfg.provider,
                        raw_text=content,
                        context_attachments=context_attachments,
                    )
                except Exception:
                    log.exception("prompt logging failed for session %s", session_id)
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
