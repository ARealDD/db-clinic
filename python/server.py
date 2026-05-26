# -*- coding: utf-8 -*-
"""FastAPI gateway for DB Diagnosis Assistant — Python 3.9 compatible."""
from __future__ import annotations

import sys, os, json, queue, threading, logging
from typing import Optional, Dict, Any

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "generated"))
sys.path.insert(0, _PROJECT_ROOT)

import grpc
import agent_pb2
import agent_pb2_grpc

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, status, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

import auth as auth_mod
import db
from models import UserRegister, UserLogin, TokenResponse, UserResponse, LLMConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("gateway")

GRPC_ADDR = os.environ.get("GRPC_ADDR", "localhost:50051")

app = FastAPI(title="Agent Gateway")


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception):
    log.error("unhandled exception on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.on_event("startup")
async def _startup_init_db():
    await db.init_db()

app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")

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
    user = await db.get_user_by_id(int(user_id))
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
    token = auth_mod.create_access_token({"sub": str(user_id)})
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
    token = auth_mod.create_access_token({"sub": str(user["id"])})
    log.info("logged in user %s (id=%s)", user["username"], user["id"])
    return TokenResponse(access_token=token)


@app.get("/api/auth/me", response_model=UserResponse)
async def me(current_user: Dict[str, Any] = Depends(get_current_user)):
    return UserResponse(id=current_user["id"], username=current_user["username"])


# ---------- Per-user LLM config ----------

async def _get_user_llm_config(user_id: int) -> LLMConfig:
    """Load the LLM config for a given user from the database."""
    configs = await db.get_all_user_configs(user_id)
    return LLMConfig(
        provider=configs.get("llm_provider", ""),
        api_key=configs.get("llm_api_key", ""),
        base_url=configs.get("llm_base_url", ""),
        model=configs.get("llm_model", ""),
        system_prompt=configs.get("llm_system_prompt", "You are a database diagnosis assistant."),
    )


async def _save_user_llm_config(user_id: int, config: LLMConfig) -> None:
    """Save the LLM config for a given user to the database."""
    await db.set_user_config(user_id, "llm_provider", config.provider)
    await db.set_user_config(user_id, "llm_api_key", config.api_key)
    await db.set_user_config(user_id, "llm_base_url", config.base_url)
    await db.set_user_config(user_id, "llm_model", config.model)
    await db.set_user_config(user_id, "llm_system_prompt", config.system_prompt)


# ---------- REST endpoints ----------

@app.get("/")
async def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


@app.get("/login")
async def login_page():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "login.html"))


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
        "has_api_key": bool(config.api_key),
    }


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

    with _grpc_channel() as ch:
        stub = agent_pb2_grpc.AgentServiceStub(ch)
        resp = stub.CreateSession(agent_pb2.CreateSessionRequest(
            model=model,
            system_prompts=[system_prompt],
            api_config=api_config,
        ))
    log.info("created session %s (model=%s, has_api_config=%s, user=%s)",
             resp.session_id, model, api_config is not None, current_user["username"])
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
                proto_attachments = [
                    agent_pb2.ContextAttachment(
                        source=str(a.get("source", "")),
                        content=str(a.get("content", "")),
                    )
                    for a in context_attachments
                ]
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