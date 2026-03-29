import logging
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Depends
from pydantic import BaseModel

from backend.rpa.manager import rpa_manager
from backend.rpa.generator import PlaywrightGenerator
from backend.rpa.executor import ScriptExecutor
from backend.rpa.skill_exporter import SkillExporter
from backend.user.dependencies import get_current_user, User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["RPA"])
generator = PlaywrightGenerator()
executor = ScriptExecutor(rpa_manager.sandbox_url)
exporter = SkillExporter()


class StartSessionRequest(BaseModel):
    sandbox_session_id: str


class GenerateRequest(BaseModel):
    params: Dict[str, Any] = {}


class SaveSkillRequest(BaseModel):
    skill_name: str
    description: str
    params: Dict[str, Any] = {}


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
        return {"status": "success", "session": session}
    except Exception as e:
        logger.error(f"Failed to start RPA session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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

    skill_dir = exporter.export_skill(
        request.skill_name,
        request.description,
        script,
        request.params,
    )

    session.status = "saved"
    return {"status": "success", "skill_dir": skill_dir}


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
