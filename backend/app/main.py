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
from shared.db.client import get_supabase_client
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
    # 1. Supabase client (sync, singleton via lru_cache)
    app.state.supabase = get_supabase_client()
    logger.info("Supabase client ready")

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
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
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
