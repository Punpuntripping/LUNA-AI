"""
FastAPI application factory.
Entry point: uvicorn backend.app.main:app

Build marker: Wave 8A snapshot 2026-05-01.
"""
from __future__ import annotations

# Load .env into the process environment BEFORE anything reads os.getenv.
# configure_logfire() below checks LOGFIRE_TOKEN via os.getenv directly (not
# via pydantic Settings), so without this a local `uvicorn` run has no token
# and Logfire silently no-ops — zero traceability. Harmless on Railway, where
# there is no .env file (load_dotenv no-ops) and real env vars are injected;
# load_dotenv does not override already-set process env vars.
from dotenv import load_dotenv

load_dotenv()

import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from shared.config import get_settings
from shared.db.client import get_supabase_client, get_supabase_anon_client
from shared.cache.redis import get_async_redis_client
from shared.observability import configure_logfire, instrument_fastapi_app
from backend.app.services.attachment_cleanup import cleanup_old_pdf_attachments
from backend.app.services.upload_reconciler import reconcile_stuck_uploads

logger = logging.getLogger(__name__)

# Configure Pydantic Logfire as early as possible so HTTPX / Pydantic AI
# instrumentations attach before any client is constructed at import time.
# No-ops gracefully when LOGFIRE_TOKEN is unset.
configure_logfire(service_version="0.1.0")


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

    # 3. APScheduler — daily PDF-attachment cleanup sweep. Hard-deletes
    #    workspace PDF attachments older than 24h (storage file + DB row).
    #    Runs in-process; the backend is single-worker so the job fires
    #    exactly once per day.
    scheduler = AsyncIOScheduler()

    async def _run_pdf_cleanup() -> None:
        # cleanup_old_pdf_attachments uses the sync Supabase client and does
        # network I/O — run it off the event loop so the scheduler tick never
        # blocks request handling.
        try:
            await asyncio.to_thread(
                cleanup_old_pdf_attachments, app.state.supabase
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("PDF cleanup job failed: %s", e)

    scheduler.add_job(
        _run_pdf_cleanup,
        trigger=CronTrigger(hour=3, minute=0),  # daily at 03:00 UTC
        id="pdf_attachment_cleanup",
        replace_existing=True,
    )

    # 4. APScheduler — daily upload reconciler. Sweeps case_documents +
    #    workspace_items rows stuck in 'uploading' status for > 24h. For each
    #    stuck row: HEADs storage, promotes to 'ready' if the bytes match
    #    (auto-recovery for crashed-browser case), otherwise soft-deletes.
    #    Offset 15 minutes after the PDF cleanup so the two jobs don't fight
    #    for the same postgrest connection pool.
    async def _run_upload_reconciler() -> None:
        try:
            await asyncio.to_thread(
                reconcile_stuck_uploads, app.state.supabase
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Upload reconciler job failed: %s", e)

    scheduler.add_job(
        _run_upload_reconciler,
        trigger=CronTrigger(hour=3, minute=15),  # daily at 03:15 UTC
        id="upload_reconciler",
        replace_existing=True,
    )

    scheduler.start()
    app.state.scheduler = scheduler
    logger.info(
        "Scheduler started — PDF cleanup 03:00 UTC, upload reconciler 03:15 UTC"
    )

    logger.info(
        "Backend started — env=%s port=%s",
        settings.ENVIRONMENT,
        settings.PORT,
    )

    yield  # ---- app is running ----

    # --- SHUTDOWN ---
    if getattr(app.state, "scheduler", None) is not None:
        app.state.scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")

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

    # 2. Security headers middleware
    @application.middleware("http")
    async def security_headers_middleware(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    # 3. CORS middleware
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept", "X-Request-ID"],
        expose_headers=["X-Request-ID", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
    )

    # 4. Rate limiting middleware
    from backend.app.middleware.rate_limit import RateLimitMiddleware
    application.add_middleware(RateLimitMiddleware)

    # 5. Logfire FastAPI instrumentation — adds request spans + tags
    instrument_fastapi_app(application)

    # ------------------------------------------
    # Exception handlers
    # ------------------------------------------

    from backend.app.errors import LunaHTTPException, luna_exception_handler
    application.add_exception_handler(LunaHTTPException, luna_exception_handler)

    @application.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception: %s", exc)
        detail_msg = "حدث خطأ داخلي في الخادم"
        return JSONResponse(
            status_code=500,
            content={
                "error": {"code": "INTERNAL_ERROR", "message": detail_msg, "status": 500},
                "detail": detail_msg,
            },
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

    # Workspace router (post-026 schema -- /workspace paths)
    from backend.app.api.workspace import router as workspace_router

    application.include_router(
        workspace_router,
        prefix="/api/v1",
        tags=["workspace"],
    )

    # Preferences + Templates router
    from backend.app.api.preferences import router as preferences_router

    application.include_router(
        preferences_router,
        prefix="/api/v1",
        tags=["preferences"],
    )

    # Internal webhooks — invoked by Supabase database triggers, NOT end users.
    # Auth via X-Webhook-Secret header. Lives under /internal/ to keep it
    # visually separate from /api/v1/.
    from backend.app.api.internal_webhooks import router as internal_webhooks_router

    application.include_router(
        internal_webhooks_router,
        prefix="/internal",
        tags=["internal"],
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
