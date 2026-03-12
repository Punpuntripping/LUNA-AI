"""
FastAPI application factory.
Entry point: uvicorn backend.app.main:app
"""
from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from shared.config import get_settings
from shared.db.client import get_supabase_client, get_supabase_anon_client
from shared.cache.redis import get_async_redis_client

logger = logging.getLogger(__name__)


# ============================================
# LIFESPAN — startup / shutdown
# ============================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle: init clients on startup, cleanup on shutdown."""
    settings = get_settings()

    # --- STARTUP ---
    # 1. Supabase clients (sync, singleton via lru_cache)
    #    - service_role client: for data operations (bypasses RLS)
    #    - anon client: for GoTrue auth operations (sign_in, sign_up, etc.)
    #    Separate clients prevent sign_in from polluting the service_role session.
    app.state.supabase = get_supabase_client()
    app.state.supabase_auth = get_supabase_anon_client()
    logger.info("Supabase clients ready")

    # 2. Redis async client (singleton via lru_cache)
    try:
        redis = get_async_redis_client()
        await redis.ping()
        app.state.redis = redis
        logger.info("Redis client ready")
    except Exception as e:
        logger.warning(f"Redis unavailable at startup — rate limiting disabled: {e}")
        app.state.redis = None

    logger.info(
        "Backend started — env=%s port=%s",
        settings.ENVIRONMENT,
        settings.PORT,
    )

    yield  # ---- app is running ----

    # --- SHUTDOWN ---
    if app.state.redis is not None:
        await app.state.redis.close()
        logger.info("Redis connection closed")

    logger.info("Backend shutdown complete")


# ============================================
# APP FACTORY
# ============================================

def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = get_settings()

    application = FastAPI(
        title="Luna Legal AI — Backend",
        description="FastAPI backend for Luna Legal AI RAG application",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
    )

    # ------------------------------------------
    # Middleware (order matters — top = outermost)
    # ------------------------------------------

    # 1. Request-ID middleware (custom, added as raw ASGI middleware)
    @application.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    # 2. CORS
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept", "X-Request-ID"],
        expose_headers=["X-Request-ID", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
    )

    # 3. Rate limiting middleware
    from backend.app.middleware.rate_limit import RateLimitMiddleware
    application.add_middleware(RateLimitMiddleware)

    # ------------------------------------------
    # Exception handlers
    # ------------------------------------------

    @application.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"detail": "حدث خطأ داخلي في الخادم"},
        )

    # ------------------------------------------
    # Routes
    # ------------------------------------------

    # Health check (no auth required)
    @application.get("/api/v1/health", tags=["health"])
    async def health_check():
        return {"status": "ok"}

    # Auth router
    from backend.app.api.auth import router as auth_router

    application.include_router(
        auth_router,
        prefix="/api/v1/auth",
        tags=["auth"],
    )

    # Cases router
    from backend.app.api.cases import router as cases_router

    application.include_router(
        cases_router,
        prefix="/api/v1/cases",
        tags=["cases"],
    )

    # Conversations router
    from backend.app.api.conversations import router as conversations_router

    application.include_router(
        conversations_router,
        prefix="/api/v1/conversations",
        tags=["conversations"],
    )

    # Messages router
    from backend.app.api.messages import router as messages_router

    application.include_router(
        messages_router,
        prefix="/api/v1",
        tags=["messages"],
    )

    # Documents router
    from backend.app.api.documents import router as documents_router

    application.include_router(
        documents_router,
        prefix="/api/v1",
        tags=["documents"],
    )

    # Memories router
    from backend.app.api.memories import router as memories_router

    application.include_router(
        memories_router,
        prefix="/api/v1",
        tags=["memories"],
    )

    # Artifacts router
    from backend.app.api.artifacts import router as artifacts_router

    application.include_router(
        artifacts_router,
        prefix="/api/v1",
        tags=["artifacts"],
    )

    # Preferences + Templates router
    from backend.app.api.preferences import router as preferences_router

    application.include_router(
        preferences_router,
        prefix="/api/v1",
        tags=["preferences"],
    )

    return application


# Create the app instance (used by uvicorn)
app = create_app()


# ============================================
# CLI entry point (python -m backend.app.main)
# ============================================

if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    port = int(os.environ.get("PORT", settings.PORT))
    uvicorn.run(
        "backend.app.main:app",
        host="0.0.0.0",
        port=port,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )
