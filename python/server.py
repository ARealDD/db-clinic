import sys, os, json, queue, threading, logging

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "generated"))
sys.path.insert(0, _PROJECT_ROOT)

import grpc
import agent_pb2
import agent_pb2_grpc

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("gateway")

GRPC_ADDR = os.environ.get("GRPC_ADDR", "localhost:50051")

app = FastAPI(title="Agent Gateway")

app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")


def _grpc_channel():
    return grpc.insecure_channel(GRPC_ADDR)


# ---------- In-memory LLM config ----------

class LLMConfig(BaseModel):
    provider: str = ""
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    system_prompt: str = "You are a database diagnosis assistant."

_current_config = LLMConfig()


# ---------- REST endpoints ----------

@app.get("/")
async def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


@app.post("/api/config")
async def set_config(config: LLMConfig):
    global _current_config
    _current_config = config
    log.info("config updated: provider=%s model=%s base_url=%s", config.provider, config.model, config.base_url)
    return {"ok": True}


@app.get("/api/config")
async def get_config():
    return {
        "provider": _current_config.provider,
        "model": _current_config.model,
        "base_url": _current_config.base_url,
        "system_prompt": _current_config.system_prompt,
        "has_api_key": bool(_current_config.api_key),
    }


@app.post("/api/sessions")
async def create_session():
    cfg = _current_config
    model = cfg.model or "mock"
    system_prompt = cfg.system_prompt or "You are a database diagnosis assistant."

    api_config = None
    if cfg.api_key:
        api_config = agent_pb2.ApiConfig(
            provider=cfg.provider,
            api_key=cfg.api_key,
            base_url=cfg.base_url,
        )

    with _grpc_channel() as ch:
        stub = agent_pb2_grpc.AgentServiceStub(ch)
        resp = stub.CreateSession(agent_pb2.CreateSessionRequest(
            model=model,
            system_prompts=[system_prompt],
            api_config=api_config,
        ))
    log.info("created session %s (model=%s, has_api_config=%s)", resp.session_id, model, api_config is not None)
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


def _chat_output_to_json(out: agent_pb2.ChatOutput) -> dict | None:
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
        return {
            "type": "proxy_instruction",
            "instruction_id": pi.instruction_id,
            "tool_name": pi.tool_name,
            "command": pi.card.command if pi.card else "",
            "hint": pi.card.hint if pi.card else "",
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
                }
                for s in out.skill_match.skills
            ],
        }
    return None


@app.websocket("/ws/chat/{session_id}")
async def ws_chat(ws: WebSocket, session_id: str):
    await ws.accept()
    log.info("ws connected for session %s", session_id)

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
                log.info("session %s: user_message len=%d", session_id, len(content))
                chat_input = agent_pb2.ChatInput(session_id=session_id)
                chat_input.user_message.CopyFrom(agent_pb2.UserMessage(content=content))
                input_iter.put(chat_input)
            elif msg_type == "cancel":
                chat_input = agent_pb2.ChatInput(session_id=session_id)
                chat_input.cancel.CopyFrom(agent_pb2.CancelTurn(reason="user cancelled"))
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
        ch.close()
