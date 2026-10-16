"""FastAPI Application Entrypoint for LunaBot Autochat Dashboard."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from .config import settings
from .core.spa_static import mount_precompressed_spa
from .db.engine import get_engine
from ..bridge.schema_bootstrap import bootstrap_database
from .routers.auth import router as auth_router
from .routers.bootstrap import router as bootstrap_router
from .routers.groups import router as groups_router
from .routers.memories import router as memories_router
from .routers.telemetry import router as telemetry_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Startup: idempotent database bootstrap and PRAGMA configuration
    bootstrap_database(settings.DB_PATH)
    # Ensure engine initialized with active path
    get_engine(settings.DB_PATH)
    yield
    # Shutdown hook


def create_app() -> FastAPI:
    app = FastAPI(
        title="LunaBot Autochat Dashboard Sidecar",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    # 1. GZip middleware for dynamic JSON payloads (> 1KB)
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    # 2. CORS configuration (allows development with Vite dev server)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["ETag"],
    )

    # 3. Include API routers
    app.include_router(auth_router)
    app.include_router(bootstrap_router)
    app.include_router(groups_router)
    app.include_router(memories_router)
    app.include_router(telemetry_router)

    # 4. Global exception handler
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc)},
        )

    # 5. Mount pre-compressed SPA static asset handler at root catch-all
    mount_precompressed_spa(app, settings.STATIC_DIR)

    return app


app = create_app()
