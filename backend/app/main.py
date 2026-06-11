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
import contextlib
import logging
import os
import random
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from shared import pricing
from shared.auth.jwt import prewarm_jwks
from shared.config import get_settings
from shared.db.client import get_supabase_client, get_supabase_anon_client
from shared.cache.redis import get_async_redis_client
from shared.observability import (
    configure_logfire,
    instrument_fastapi_app,
    observability_status,
)
from backend.app.services.attachment_cleanup import cleanup_old_pdf_attachments
from backend.app.services.summary_sweeper import sweep_missing_summaries
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
    # Resize the default executor FIRST — every route now offloads its sync
    # Supabase service call via asyncio.to_thread (run_db), which uses ONLY the
    # default executor. The stdlib default is min(32, cpu_count + 4) → 6 threads
    # on a 2-vCPU Railway box, which would become the concurrency ceiling. 40
    # workers < httpx max_connections=50, so threads never block on the pool;
    # this pool is also shared by the deep_search to_thread fan-out.
    from concurrent.futures import ThreadPoolExecutor

    loop = asyncio.get_running_loop()
    loop.set_default_executor(
        ThreadPoolExecutor(max_workers=40, thread_name_prefix="luna-db")
    )

    settings = get_settings()

    # Dedup invariant — message_service._active_runs is in-PROCESS memory.
    # uvicorn honors WEB_CONCURRENCY when --workers is absent (our Dockerfile
    # passes none). >1 worker = duplicate sends pass the dedup guard on the
    # other worker = double-billed pipelines. Hard-fail rather than run wrong.
    _workers = int(os.getenv("WEB_CONCURRENCY", "1") or "1")
    if _workers > 1:
        logger.critical(
            "WEB_CONCURRENCY=%d: in-flight send dedup is per-process; refusing "
            "to start multi-worker until Redis SET NX dedup ships (see "
            "message_service._active_runs).", _workers)
        raise RuntimeError("multi-worker boot blocked: in-process send dedup")

    # --- STARTUP ---
    # 1. Supabase clients (sync, singleton via lru_cache)
    #    - service_role client: for data operations (bypasses RLS)
    #    - anon client: for GoTrue auth operations (sign_in, sign_up, etc.)
    #    Separate clients prevent sign_in from polluting the service_role session.
    app.state.supabase = get_supabase_client()
    app.state.supabase_auth = get_supabase_anon_client()
    logger.info("Supabase clients ready")

    # 1b. LLM pricing — load model_pricing rows into an in-memory cache so
    #     cost_usd(model_name, ...) is a sub-µs dict lookup on the hot path.
    #     Failures leave the cache empty; cost_usd then returns 0.0 silently
    #     (cost accounting is best-effort and must not block user runs).
    try:
        loaded = pricing.load_pricing(app.state.supabase)
        logger.info("Pricing cache ready (%d models)", loaded)
    except Exception as e:  # noqa: BLE001
        logger.warning("Pricing cache load failed (cost_usd → 0): %s", e)

    # 1c. JWKS pre-warm — non-blocking, best-effort. A slow/down JWKS endpoint
    #     must add zero cold-start latency, so fire-and-forget in a thread. Keep
    #     the task reference to prevent GC; it self-completes in ≤5s.
    app.state.jwks_prewarm_task = asyncio.create_task(asyncio.to_thread(prewarm_jwks))

    # 2. Redis — supervised. app.state.redis is the singleton client when
    #    healthy, None when down. A background task owns the transitions, so
    #    startup is NOT gated on Redis at all (a dead Redis no longer blocks
    #    boot). Per-request fail-open covers the ≤~45s detection window.
    app.state.redis = None
    redis_client = get_async_redis_client()   # singleton; auto-reconnects per command

    async def _redis_supervisor() -> None:
        backoff, was_down_logged, failures = 1.0, False, 0
        while True:
            try:
                await redis_client.ping()
                if app.state.redis is None:
                    app.state.redis = redis_client
                    logger.info("Redis %s — rate limiting enabled",
                                "recovered" if was_down_logged else "client ready")
                    was_down_logged = False
                failures, backoff = 0, 1.0
                await asyncio.sleep(15)            # healthy: poll every 15s
            except asyncio.CancelledError:
                raise
            except Exception as e:                 # noqa: BLE001
                failures += 1
                if app.state.redis is not None and failures >= 3:
                    app.state.redis = None         # stop per-request hammering
                if not was_down_logged:
                    logger.warning("Redis unavailable (failure %d): %s — "
                                   "rate limiting disabled, reconnecting with backoff",
                                   failures, e)
                    was_down_logged = True
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    app.state.redis_supervisor = asyncio.create_task(_redis_supervisor())

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

    # 5. APScheduler — daily summary-NULL sweep. Re-runs the summarizer on
    #    workspace items left with summary IS NULL (dropped pg_net webhook or a
    #    persist that failed after the LLM call). Bounded at SWEEP_CAP/day.
    #    Offset 30 min after the PDF/upload jobs so the three never contend for
    #    the same postgrest connection pool.
    async def _run_summary_sweep() -> None:
        try:
            stats = await sweep_missing_summaries(app.state.supabase)
            logger.info("Summary NULL sweep complete: %s", stats)
        except Exception as e:  # noqa: BLE001
            logger.warning("Summary NULL sweep failed: %s", e)

    scheduler.add_job(
        _run_summary_sweep,
        trigger=CronTrigger(hour=3, minute=30),  # daily at 03:30 UTC
        id="summary_null_sweep",
        replace_existing=True,
    )

    # 6. APScheduler — one-shot startup catch-up for the upload reconciler. The
    #    03:15 cron silently skips a day whenever the process restarts across
    #    it; the reconciler is idempotent and cheap, so run it once shortly
    #    after every boot. The 60s base delay lets the app warm; the 0–30s
    #    jitter avoids a thundering herd if replicas ever exist. Reuses the same
    #    _run_upload_reconciler wrapper (to_thread + swallow).
    scheduler.add_job(
        _run_upload_reconciler,
        trigger=DateTrigger(
            run_date=datetime.now(timezone.utc)
            + timedelta(seconds=60 + random.uniform(0, 30))
        ),
        id="upload_reconciler_startup",
        replace_existing=True,
    )

    scheduler.start()
    app.state.scheduler = scheduler
    logger.info(
        "Scheduler started — PDF cleanup 03:00, upload reconciler 03:15, "
        "summary sweep 03:30 UTC, + one-shot upload-reconciler catch-up on boot"
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

    # Stop the Redis supervisor, then close the SINGLETON client — NOT
    # app.state.redis, which is None mid-outage even though the client object
    # still exists and holds open connections.
    supervisor = getattr(app.state, "redis_supervisor", None)
    if supervisor is not None:
        supervisor.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await supervisor
    try:
        await redis_client.close()
        logger.info("Redis connection closed")
    except Exception as e:  # noqa: BLE001
        logger.warning("Redis close failed during shutdown: %s", e)

    # The JWKS pre-warm task self-completes in ≤5s; nothing to await. Don't
    # crash if it is somehow still pending — it holds no resources worth
    # blocking shutdown on.

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

    from backend.app.errors import (
        LunaHTTPException,
        luna_exception_handler,
        MSG_SERVICE_UNAVAILABLE,
    )
    application.add_exception_handler(LunaHTTPException, luna_exception_handler)

    # DbDeadlineExceeded → 503 SERVICE_UNAVAILABLE. A dependency failure (DB
    # outage / pool exhaustion) is NOT a user error — surface it as a transient
    # 503 with the canonical Arabic outage string. shared/db/run.py raises this
    # but cannot import backend.app.errors, so the mapping lives here.
    from shared.db.run import DbDeadlineExceeded

    @application.exception_handler(DbDeadlineExceeded)
    async def db_deadline_handler(request: Request, exc: DbDeadlineExceeded):
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "SERVICE_UNAVAILABLE",
                    "message": MSG_SERVICE_UNAVAILABLE,
                    "status": 503,
                },
                "detail": MSG_SERVICE_UNAVAILABLE,
            },
        )

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

    # Health check (no auth required). Returns the resolved environment label
    # so a `curl /api/v1/health` from any environment instantly proves which
    # backend you're actually talking to — the localhost/Railway routing
    # ambiguity that triggered Wave-9 tracking-reliability work.
    @application.get("/api/v1/health", tags=["health"])
    async def health_check():
        status = observability_status()
        return {
            "status": "ok",
            "service": status["service_name"],
            "version": status["service_version"],
            "environment": status["environment"],
        }

    # Observability self-check — exposes the live Logfire wiring so a
    # silently-broken deploy (no token, failed SDK instrument) shows up as
    # `configured: false` instead of as a quiet zero-span deploy. No secrets.
    @application.get("/api/v1/_meta/observability", tags=["health"])
    async def observability_check():
        return observability_status()

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

    # Usage limits — read-only snapshot for the Settings → حدود الاستخدام dialog.
    from backend.app.api.usage import router as usage_router

    application.include_router(
        usage_router,
        prefix="/api/v1",
        tags=["usage"],
    )

    # Templates router (قوالبي — per-user markdown templates)
    from backend.app.api.templates import router as templates_router

    application.include_router(
        templates_router,
        prefix="/api/v1",
        tags=["templates"],
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
