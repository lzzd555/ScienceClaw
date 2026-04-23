"""FastAPI routes for API Monitor feature.

Prefix: /api/v1/api-monitor
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sse_starlette.sse import EventSourceResponse

from backend.config import settings
from backend.user.dependencies import User, get_current_user
from backend.storage import get_repository
from backend.rpa.api_monitor import api_monitor_manager
from backend.rpa.api_monitor.models import (
    ApiMonitorSession,
    StartSessionRequest,
    NavigateRequest,
    UpdateToolRequest,
)
from backend.rpa.screencast import SessionScreencastController

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api-monitor", tags=["API Monitor"])


# ── Auth helpers ─────────────────────────────────────────────────────


async def _get_ws_user(websocket: WebSocket) -> Optional[User]:
    """Resolve the current user for a WebSocket request."""
    if settings.storage_backend == "local":
        return User(id="local_admin", username="admin", role="admin")

    if getattr(settings, "auth_provider", "local") == "none":
        return User(id="anonymous", username="Anonymous", role="user")

    session_id = (
        websocket.query_params.get("token")
        or websocket.cookies.get(settings.session_cookie)
    )
    if not session_id:
        return None

    repo = get_repository("user_sessions")
    session_doc = await repo.find_one({"_id": session_id})
    if not session_doc:
        return None

    import time
    if session_doc.get("expires_at", 0) < time.time():
        await repo.delete_one({"_id": session_id})
        return None

    return User(
        id=str(session_doc["user_id"]),
        username=session_doc["username"],
        role=session_doc.get("role", "user"),
    )


def _verify_session_owner(session: Optional[ApiMonitorSession], user: User) -> None:
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(user.id):
        raise HTTPException(status_code=403, detail="Not authorized")


# ── Session management ──────────────────────────────────────────────


@router.post("/session/start")
async def start_session(
    request: StartSessionRequest,
    current_user: User = Depends(get_current_user),
):
    try:
        session = await api_monitor_manager.create_session(
            user_id=str(current_user.id),
            target_url=request.url,
        )
        return {"status": "success", "session": session.model_dump()}
    except Exception as e:
        logger.error("Failed to start API Monitor session: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/session/{session_id}")
async def get_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)
    return {"status": "success", "session": session.model_dump()}


@router.post("/session/{session_id}/stop")
async def stop_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)
    stopped = await api_monitor_manager.stop_session(session_id)
    return {"status": "success", "session": stopped.model_dump() if stopped else None}


@router.post("/session/{session_id}/navigate")
async def navigate(
    session_id: str,
    request: NavigateRequest,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)
    await api_monitor_manager.navigate(session_id, request.url)
    return {"status": "success"}


# ── Browser tabs ─────────────────────────────────────────────────────


@router.get("/session/{session_id}/tabs")
async def list_tabs(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)
    tabs = api_monitor_manager.list_tabs(session_id)
    return {"status": "success", "tabs": tabs}


# ── Screencast WebSocket ────────────────────────────────────────────


@router.websocket("/screencast/{session_id}")
async def screencast_ws(websocket: WebSocket, session_id: str):
    user = await _get_ws_user(websocket)
    await websocket.accept()

    if not user:
        await websocket.close(code=1008, reason="Not authenticated")
        return

    session = api_monitor_manager.get_session(session_id)
    if not session or session.user_id != str(user.id):
        await websocket.close(code=1008, reason="Not authorized")
        return

    logger.info(
        "[ApiMonitor] Screencast WS session=%s user=%s",
        session_id,
        user.username,
    )

    screencast = SessionScreencastController(
        page_provider=lambda: api_monitor_manager.get_page(session_id),
        tabs_provider=lambda: api_monitor_manager.list_tabs(session_id),
    )

    try:
        await screencast.start(websocket)
    except WebSocketDisconnect:
        logger.info("[ApiMonitor] Screencast WS disconnected session=%s", session_id)
    except Exception as e:
        logger.exception("[ApiMonitor] Screencast error session=%s: %s", session_id, e)
        try:
            await websocket.close(code=1011, reason="Screencast failed")
        except Exception:
            pass
    finally:
        await screencast.stop()


# ── Analysis (SSE) ──────────────────────────────────────────────────


@router.post("/session/{session_id}/analyze")
async def analyze_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)

    async def event_generator():
        async for event in api_monitor_manager.analyze_page(session_id):
            yield event

    return EventSourceResponse(event_generator())


# ── Recording ────────────────────────────────────────────────────────


@router.post("/session/{session_id}/record/start")
async def start_recording(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)
    await api_monitor_manager.start_recording(session_id)
    return {"status": "success"}


@router.post("/session/{session_id}/record/stop")
async def stop_recording(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)
    tools = await api_monitor_manager.stop_recording(session_id)
    return {
        "status": "success",
        "tools": [t.model_dump() for t in tools],
    }


# ── Tool management ─────────────────────────────────────────────────


@router.get("/session/{session_id}/tools")
async def list_tools(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)
    return {
        "status": "success",
        "tools": [t.model_dump() for t in session.tool_definitions],
    }


@router.put("/session/{session_id}/tools/{tool_id}")
async def update_tool(
    session_id: str,
    tool_id: str,
    request: UpdateToolRequest,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)

    for tool in session.tool_definitions:
        if tool.id == tool_id:
            tool.yaml_definition = request.yaml_definition
            from datetime import datetime
            tool.updated_at = datetime.now()
            return {"status": "success", "tool": tool.model_dump()}

    raise HTTPException(status_code=404, detail="Tool not found")


@router.delete("/session/{session_id}/tools/{tool_id}")
async def delete_tool(
    session_id: str,
    tool_id: str,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)

    session.tool_definitions = [
        t for t in session.tool_definitions if t.id != tool_id
    ]
    return {"status": "success"}


@router.post("/session/{session_id}/export")
async def export_tools(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)

    tools_yaml = [t.yaml_definition for t in session.tool_definitions]
    export_content = "---\n" + "\n---\n".join(tools_yaml)
    return {
        "status": "success",
        "content": export_content,
        "filename": f"api-monitor-tools-{session_id[:8]}.yaml",
    }
