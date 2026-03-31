"""
FastAPI 应用入口 — 精简版。

挂载路由：auth / models / sessions / file / rpa / chat / statistics
启动时：连接 MongoDB → 初始化系统模型 → 创建默认 admin
"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from contextlib import asynccontextmanager

from backend.storage import init_storage, close_storage, get_repository
from backend.route.auth import router as auth_router
from backend.route.sessions import router as sessions_router, cleanup_orphaned_sessions, graceful_shutdown_agents
from backend.route.file import router as file_router
from backend.route.models import router as models_router
from backend.route.task_settings import router as task_settings_router
from backend.route.memory import router as memory_router
from backend.route.chat import router as chat_router
from backend.route.statistics import router as statistics_router
from backend.route.rpa import router as rpa_router
from backend.models import init_system_models
from backend.user.bootstrap import ensure_admin_user


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_storage()
    try:
        await init_system_models()
    except Exception as e:
        logger.error(f"Failed to init system models: {e}")
    try:
        await ensure_admin_user()
    except Exception as e:
        logger.error(f"Failed to bootstrap admin user: {e}")
    try:
        await cleanup_orphaned_sessions()
    except Exception as e:
        logger.error(f"Failed to cleanup orphaned sessions: {e}")
    yield
    try:
        await graceful_shutdown_agents()
    except Exception as e:
        logger.error(f"Failed to gracefully shutdown agents: {e}")
    try:
        from backend.rpa.cdp_connector import cdp_connector
        await cdp_connector.close()
    except Exception as e:
        logger.error(f"Failed to close CDP connector: {e}")
    await close_storage()


def create_app() -> FastAPI:
    app = FastAPI(title="ScienceClaw Agent Backend", lifespan=lifespan)

    cors_origins = [
        o.strip()
        for o in os.environ.get(
            "CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000",
        ).split(",")
        if o.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/ready")
    async def ready():
        try:
            repo = get_repository("sessions")
            await repo.find_one({})
            return {"status": "ready", "storage": "ok"}
        except Exception as exc:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "storage": str(exc)},
            )

    @app.get("/api/v1/client-config")
    async def client_config():
        """Return configuration needed by the frontend."""
        from backend.config import settings
        return {
            "sandbox_public_url": settings.sandbox_public_url or "",
        }

    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(sessions_router, prefix="/api/v1")
    app.include_router(file_router, prefix="/api/v1")
    app.include_router(models_router, prefix="/api/v1")
    app.include_router(task_settings_router, prefix="/api/v1")
    app.include_router(memory_router, prefix="/api/v1")
    app.include_router(chat_router, prefix="/api/v1")
    app.include_router(statistics_router, prefix="/api/v1")
    app.include_router(rpa_router, prefix="/api/v1/rpa")

    logger.info("FastAPI initialized with /api/v1 endpoints")
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
