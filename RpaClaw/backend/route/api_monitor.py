"""FastAPI routes for API Monitor feature.

Prefix: /api/v1/api-monitor
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from backend.config import settings
from backend.user.dependencies import User, get_current_user
from backend.storage import get_repository
from backend.rpa.api_monitor import api_monitor_manager
from backend.rpa.api_monitor.models import (
    AnalyzeSessionRequest,
    ApiMonitorSession,
    StartSessionRequest,
    NavigateRequest,
    PublishMcpRequest,
    UpdateToolRequest,
    UpdateToolSelectionRequest,
)
from backend.rpa.api_monitor.analysis_modes import get_analysis_mode_config
from backend.rpa.api_monitor_mcp_registry import ApiMonitorMcpRegistry
from backend.rpa.api_monitor_auth import build_api_monitor_auth_profile, validate_api_monitor_auth_config
from backend.rpa.api_monitor_token_flow import build_api_monitor_token_flow_profile, resolve_token_flows_for_publish, validate_manual_token_flow
from backend.rpa.screencast import SessionScreencastController
from backend.credential.vault import get_vault

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

    api_monitor_manager.register_screencast(session_id, screencast)
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
        api_monitor_manager.unregister_screencast(session_id)
        await screencast.stop()


async def _resolve_user_model_config(user_id: str) -> Optional[dict]:
    """Resolve the user's model config, same logic as RPA recorder."""
    docs = await get_repository("models").find_many(
        {"$or": [{"user_id": user_id}, {"is_system": True}], "is_active": True, "api_key": {"$nin": ["", None]}},
        sort=[("is_system", 1), ("updated_at", -1)],
        limit=1,
    )
    doc = docs[0] if docs else None
    if doc:
        return {
            "model_name": doc.get("model_name"),
            "base_url": doc.get("base_url"),
            "api_key": doc.get("api_key"),
            "context_window": doc.get("context_window"),
            "provider": doc.get("provider", ""),
        }
    return None


# ── Analysis (SSE) ──────────────────────────────────────────────────


@router.post("/session/{session_id}/analyze")
async def analyze_session(
    session_id: str,
    request: AnalyzeSessionRequest | None = Body(default=None),
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)

    payload = request or AnalyzeSessionRequest()
    try:
        mode_config = get_analysis_mode_config(payload.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    instruction = payload.instruction.strip()
    if mode_config.requires_instruction and not instruction:
        raise HTTPException(
            status_code=400,
            detail=f"Instruction is required for {mode_config.key} analysis",
        )

    model_config = await _resolve_user_model_config(str(current_user.id))

    async def event_generator():
        if mode_config.handler == "free":
            async for event in api_monitor_manager.analyze_page(
                session_id,
                model_config=model_config,
            ):
                yield event
            return

        async for event in api_monitor_manager.analyze_directed_page(
            session_id,
            instruction=instruction,
            mode=mode_config.key,
            business_safety=mode_config.business_safety,
            model_config=model_config,
        ):
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
    model_config = await _resolve_user_model_config(str(current_user.id))
    tools = await api_monitor_manager.stop_recording(session_id, model_config=model_config)
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


@router.get("/session/{session_id}/generation-candidates")
async def list_generation_candidates(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)
    candidates = api_monitor_manager.list_generation_candidates(session_id)
    return {
        "status": "success",
        "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
    }


@router.post("/session/{session_id}/generation-candidates/{candidate_id}/retry")
async def retry_generation_candidate(
    session_id: str,
    candidate_id: str,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)
    model_config = await _resolve_user_model_config(str(current_user.id))
    try:
        candidate = api_monitor_manager.retry_generation_candidate(
            session_id,
            candidate_id,
            model_config=model_config,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "success", "candidate": candidate.model_dump(mode="json")}


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


@router.patch("/session/{session_id}/tools/{tool_id}/selection")
async def update_tool_selection(
    session_id: str,
    tool_id: str,
    request: UpdateToolSelectionRequest,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)

    for tool in session.tool_definitions:
        if tool.id == tool_id:
            tool.selected = request.selected
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


@router.get("/session/{session_id}/auth-profile")
async def get_auth_profile(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)
    return {"status": "success", "profile": build_api_monitor_auth_profile(session)}


@router.get("/session/{session_id}/token-flow-profile")
async def get_token_flow_profile(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)
    calls = session.captured_calls
    logger.info(
        "[TokenFlow] session=%s captured_calls=%d",
        session_id,
        len(calls),
    )
    profile = build_api_monitor_token_flow_profile(calls)
    logger.info(
        "[TokenFlow] session=%s flow_count=%d",
        session_id,
        profile["flow_count"],
    )
    return {"status": "success", "profile": profile}


@router.post("/session/{session_id}/publish-mcp")
async def publish_mcp(
    session_id: str,
    request: PublishMcpRequest,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)

    registry = ApiMonitorMcpRegistry()
    existing = await registry.find_by_name(
        user_id=str(current_user.id),
        mcp_name=request.mcp_name,
    )
    if existing and not request.confirm_overwrite:
        return JSONResponse(
            status_code=409,
            content={
                "detail": "MCP with the same name already exists",
                "needs_confirmation": True,
                "server_id": str(existing["_id"]),
            },
        )

    auth_payload = (
        request.api_monitor_auth.model_dump()
        if request.api_monitor_auth is not None
        else {"credential_type": "placeholder", "credential_id": ""}
    )
    try:
        api_monitor_auth = await validate_api_monitor_auth_config(
            str(current_user.id),
            auth_payload,
            vault=get_vault(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Resolve token flow selections from session profile
    token_flow_selections = []
    if request.api_monitor_auth and request.api_monitor_auth.token_flows:
        token_flow_selections = [
            sel.model_dump() for sel in request.api_monitor_auth.token_flows if sel.enabled
        ]
    token_flows = resolve_token_flows_for_publish(
        session.captured_calls, token_flow_selections
    )

    # Resolve manual token flows
    manual_flows = []
    if request.api_monitor_auth and request.api_monitor_auth.manual_token_flows:
        try:
            manual_flows = [
                validate_manual_token_flow(flow.model_dump())
                for flow in request.api_monitor_auth.manual_token_flows
            ]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    combined_flows = [*token_flows, *manual_flows]
    if combined_flows:
        api_monitor_auth["token_flows"] = combined_flows

    result = await registry.publish_session(
        session=session,
        user_id=str(current_user.id),
        mcp_name=request.mcp_name,
        description=request.description,
        overwrite=bool(existing),
        existing_server_id=str(existing["_id"]) if existing else None,
        api_monitor_auth=api_monitor_auth,
    )
    return {"status": "success", "data": result}
