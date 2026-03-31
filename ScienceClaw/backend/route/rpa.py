import json
import logging
import asyncio
from contextlib import suppress
from typing import Dict, Any
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
import websockets

from backend.rpa.manager import rpa_manager
from backend.rpa.generator import PlaywrightGenerator
from backend.rpa.executor import ScriptExecutor
from backend.rpa.skill_exporter import SkillExporter
from backend.rpa.assistant import RPAAssistant
from backend.user.dependencies import get_current_user, User
from backend.config import settings
from backend.mongodb.db import db
from backend.sandbox_utils import (
    build_sandbox_auth_headers,
    build_sandbox_vnc_target_url,
    get_sandbox_vnc_proxy_url,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["RPA"])
generator = PlaywrightGenerator()
executor = ScriptExecutor(rpa_manager.sandbox_url)
exporter = SkillExporter()
assistant = RPAAssistant(rpa_manager.sandbox_url)


_PROXY_RESPONSE_HEADERS = {
    "content-type",
    "cache-control",
    "etag",
    "last-modified",
    "expires",
}


class StartSessionRequest(BaseModel):
    sandbox_session_id: str


class GenerateRequest(BaseModel):
    params: Dict[str, Any] = {}


class SaveSkillRequest(BaseModel):
    skill_name: str
    description: str
    params: Dict[str, Any] = {}


class ChatRequest(BaseModel):
    message: str


async def _resolve_user_model_config(user_id: str) -> dict | None:
    """Resolve the user's model config for the RPA assistant.

    Priority: user's own models → system models → env defaults (None).
    """
    # Try user's own active model first, then system models
    doc = await db.get_collection("models").find_one(
        {"$or": [{"user_id": user_id}, {"is_system": True}], "is_active": True, "api_key": {"$nin": ["", None]}},
        sort=[("is_system", 1), ("updated_at", -1)],  # user models first
    )
    if doc:
        return {
            "model_name": doc.get("model_name"),
            "base_url": doc.get("base_url"),
            "api_key": doc.get("api_key"),
            "context_window": doc.get("context_window"),
            "provider": doc.get("provider", ""),
        }
    # Fall back to env defaults
    if (getattr(settings, "model_ds_api_key", None) or "").strip():
        return None  # get_llm_model(config=None) uses env defaults
    return None


@router.post("/session/start")
async def start_rpa_session(
    request: StartSessionRequest,
    current_user: User = Depends(get_current_user),
):
    try:
        session = await rpa_manager.create_session(
            user_id=str(current_user.id),
            sandbox_session_id=request.sandbox_session_id,
        )
        return {
            "status": "success",
            "session": session,
            "sandbox": {"vnc_url": get_sandbox_vnc_proxy_url()},
        }
    except Exception as e:
        logger.error(f"Failed to start RPA session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sandbox/vnc/{asset_path:path}")
async def proxy_sandbox_vnc_asset(asset_path: str, request: Request):
    target_url = build_sandbox_vnc_target_url(
        asset_path or "index.html",
        request.query_params.multi_items(),
    )
    headers = build_sandbox_auth_headers()
    accept = request.headers.get("accept")
    if accept:
        headers["Accept"] = accept

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.get(target_url, headers=headers)
        filtered_headers = {
            key: value
            for key, value in resp.headers.items()
            if key.lower() in _PROXY_RESPONSE_HEADERS
        }
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=filtered_headers,
        )


@router.websocket("/sandbox/vnc/websockify")
async def proxy_sandbox_vnc_websocket(websocket: WebSocket):
    await websocket.accept()
    target_url = build_sandbox_vnc_target_url(
        "websockify",
        websocket.query_params.multi_items(),
        websocket=True,
    )

    try:
        async with websockets.connect(
            target_url,
            additional_headers=build_sandbox_auth_headers(),
            open_timeout=30,
        ) as remote_ws:
            async def client_to_remote():
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        break
                    if message.get("bytes") is not None:
                        await remote_ws.send(message["bytes"])
                    elif message.get("text") is not None:
                        await remote_ws.send(message["text"])

            async def remote_to_client():
                async for message in remote_ws:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)

            tasks = [
                asyncio.create_task(client_to_remote()),
                asyncio.create_task(remote_to_client()),
            ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            for task in done:
                task.result()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.error(f"Failed to proxy VNC websocket: {exc}")
        with suppress(Exception):
            await websocket.close(code=1011, reason="VNC proxy error")


@router.get("/session/{session_id}")
async def get_rpa_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")
    return {"status": "success", "session": session}


@router.post("/session/{session_id}/stop")
async def stop_rpa_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")
    await rpa_manager.stop_session(session_id)
    return {"status": "success", "session": session}


@router.post("/session/{session_id}/generate")
async def generate_script(
    session_id: str,
    request: GenerateRequest = GenerateRequest(),
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    steps = [step.model_dump() for step in session.steps]
    script = generator.generate_script(steps, request.params)
    return {"status": "success", "script": script}


@router.post("/session/{session_id}/test")
async def test_script(
    session_id: str,
    request: GenerateRequest = GenerateRequest(),
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    steps = [step.model_dump() for step in session.steps]
    script = generator.generate_script(steps, request.params)

    logs = []
    result = await executor.execute(
        session.sandbox_session_id,
        script,
        on_log=lambda msg: logs.append(msg),
    )

    return {"status": "success", "result": result, "logs": logs, "script": script}


@router.post("/session/{session_id}/save")
async def save_skill(
    session_id: str,
    request: SaveSkillRequest,
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    steps = [step.model_dump() for step in session.steps]
    script = generator.generate_script(steps, request.params)

    skill_name = await exporter.export_skill(
        user_id=str(current_user.id),
        skill_name=request.skill_name,
        description=request.description,
        script=script,
        params=request.params,
    )

    session.status = "saved"
    return {"status": "success", "skill_name": skill_name}


@router.post("/session/{session_id}/chat")
async def chat_with_assistant(
    session_id: str,
    request: ChatRequest,
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")

    # Resolve user's model config
    model_config = await _resolve_user_model_config(str(current_user.id))

    steps = [step.model_dump() for step in session.steps]

    async def event_generator():
        try:
            async for event in assistant.chat(
                session_id=session_id,
                sandbox_session_id=session.sandbox_session_id,
                message=request.message,
                steps=steps,
                model_config=model_config,
            ):
                evt_type = event.get("event", "message")
                evt_data = event.get("data", {})

                # If execution succeeded and returned a step, add it to session
                if evt_type == "result" and evt_data.get("success") and evt_data.get("step"):
                    step_data = evt_data["step"]
                    await rpa_manager.add_step(session_id, step_data)

                yield {
                    "event": evt_type,
                    "data": json.dumps(evt_data, ensure_ascii=False),
                }
        except Exception as e:
            logger.error(f"Chat error: {e}")
            yield {
                "event": "error",
                "data": json.dumps({"message": str(e)}, ensure_ascii=False),
            }
            yield {"event": "done", "data": "{}"}

    return EventSourceResponse(event_generator())


@router.websocket("/session/{session_id}/steps")
async def steps_stream(websocket: WebSocket, session_id: str):
    """Stream real-time step updates to frontend."""
    await websocket.accept()

    session = await rpa_manager.get_session(session_id)
    if not session:
        await websocket.close(code=1008, reason="Session not found")
        return

    rpa_manager.register_ws(session_id, websocket)

    try:
        for step in session.steps:
            await websocket.send_json({"type": "step", "data": step.model_dump()})

        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        rpa_manager.unregister_ws(session_id, websocket)
