"""HTTP routes for the Blog-Post Generation API.

Mounted under ``/internal`` by ``backend.app.main`` (next to
``internal_webhooks``), so routes are declared as ``/blog-post-jobs``.

* ``POST /blog-post-jobs`` (``?wait=N`` optional long-poll) — guarded by
  ``_verify_service_key``; the two-window rate limiter is invoked IN-HANDLER,
  AFTER the idempotency lookup, so retries/polls never spend the budget
  (plan §7).
* ``GET /blog-post-jobs/{job_id}`` — guarded by ``_verify_service_key`` only;
  never rate-limited.

Validation returns an Arabic 400 (``LunaHTTPException``) for empty
required fields and out-of-range enums (Rule #5), rather than FastAPI's
default 422.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from supabase import Client as SupabaseClient

from backend.app.api.deepsearch_api import service
from backend.app.api.deepsearch_api.auth import _verify_service_key
from backend.app.api.deepsearch_api.models import (
    BlogJobStatusResponse,
    BlogJobSubmitResponse,
    BlogPostJobRequest,
)
from backend.app.api.deepsearch_api.ratelimit import enforce_editorial_rate_limit
from backend.app.deps import get_supabase, validate_uuid
from backend.app.errors import ErrorCode, LunaHTTPException

logger = logging.getLogger(__name__)

router = APIRouter()

# Allowed enum values (validated here for an Arabic 400 rather than a 422).
_DISPLAY_MODES = {"question", "title"}
_PUBLISH_POLICIES = {"auto", "always", "never"}
_CONFIDENCE_LABELS = {"high", "medium", "low"}

# Long-poll cap for ?wait= (seconds).
_MAX_WAIT_S = 60.0


def _validate_request(req: BlogPostJobRequest) -> None:
    """Arabic 400 validation of the submit body (Rule #5)."""
    if not (req.idempotency_key or "").strip():
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail="مفتاح التعريف (idempotency_key) مطلوب",
        )
    if not (req.question or "").strip():
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail="السؤال مطلوب",
        )
    if req.display_mode not in _DISPLAY_MODES:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail="قيمة display_mode غير صالحة",
        )
    if req.publish_policy not in _PUBLISH_POLICIES:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail="قيمة publish_policy غير صالحة",
        )
    if req.min_confidence not in _CONFIDENCE_LABELS:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail="قيمة min_confidence غير صالحة",
        )


def _status_url(request: Request, job_id: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/internal/blog-post-jobs/{job_id}"


def _submit_response(request: Request, job: dict, *, status_code: int) -> JSONResponse:
    body = BlogJobSubmitResponse(
        job_id=job["job_id"],
        status=job.get("status", "queued"),
        status_url=_status_url(request, job["job_id"]),
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())


def _status_response(job: dict, *, status_code: int = 200) -> JSONResponse:
    body = BlogJobStatusResponse(
        job_id=job["job_id"],
        status=job.get("status", "queued"),
        result=job.get("result"),
        error=job.get("error"),
    )
    # exclude_none keeps result/error absent (rather than null) when not set.
    return JSONResponse(status_code=status_code, content=body.model_dump(exclude_none=True))


@router.post("/blog-post-jobs")
async def submit_blog_post_job(
    request: Request,
    req: BlogPostJobRequest,
    wait: Optional[float] = Query(
        default=None,
        ge=0,
        description="Optional long-poll: await up to N seconds and inline the result if ready.",
    ),
    _auth: None = Depends(_verify_service_key),
    supabase: SupabaseClient = Depends(get_supabase),
) -> JSONResponse:
    """Submit a blog-post generation job.

    Ordering (plan §7): auth → validate → **idempotency lookup** (existing key
    → 200 replay, before the limiter so retries are free) → **rate limit** →
    create + spawn worker → 202 (or inline status with ``?wait``).
    """
    _validate_request(req)

    # Idempotency replay — free, never rate-limited.
    existing = await service.get_job_by_idempotency_key(supabase, req.idempotency_key)
    if existing is not None:
        # If the caller long-polls a replay of an in-flight job, honor ?wait too.
        if wait and existing.get("status") not in ("completed", "failed"):
            existing = await service.wait_for_job(
                supabase, existing["job_id"], min(float(wait), _MAX_WAIT_S)
            )
        if existing.get("status") in ("completed", "failed"):
            return _status_response(existing, status_code=200)
        return _submit_response(request, existing, status_code=200)

    # Genuinely-new submission — spend the budget (raises 429 on breach).
    rl_state = await enforce_editorial_rate_limit(request)

    job, is_new = await service.create_or_get_job(supabase, req)
    if not is_new:
        # Lost the unique-key race — treat as a replay.
        return _submit_response(request, job, status_code=200)

    # Optional long-poll: await the just-spawned worker up to N seconds.
    if wait and wait > 0:
        job = await service.wait_for_job(
            supabase, job["job_id"], min(float(wait), _MAX_WAIT_S)
        )
        if job.get("status") in ("completed", "failed"):
            return _status_response(job, status_code=200)

    resp = _submit_response(request, job, status_code=202)
    resp.headers["X-RateLimit-Remaining-Hour"] = str(rl_state.hour_remaining)
    resp.headers["X-RateLimit-Remaining-Day"] = str(rl_state.day_remaining)
    return resp


@router.get(
    "/blog-post-jobs/{job_id}",
    response_model=BlogJobStatusResponse,
    dependencies=[Depends(_verify_service_key)],
)
async def get_blog_post_job(
    job_id: str,
    supabase: SupabaseClient = Depends(get_supabase),
) -> JSONResponse:
    """Poll a job. 404 (Arabic) when it doesn't exist. Never rate-limited."""
    validate_uuid(job_id, "معرف المهمة")
    job = await service.get_job(supabase, job_id)
    if job is None:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.BLOG_JOB_NOT_FOUND,
            detail="المهمة غير موجودة",
        )
    return _status_response(job, status_code=200)


__all__ = ["router"]
