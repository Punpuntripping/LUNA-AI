"""HTTP routes for the Blog-Post Generation API.

Mounted under ``/internal`` by ``backend.app.main`` (next to
``internal_webhooks``), so routes are declared as ``/blog-post-jobs``.

* ``POST /blog-post-jobs`` (``?wait=N`` optional long-poll) — guarded by
  ``_verify_service_key``; the two-window rate limiter is invoked IN-HANDLER,
  AFTER the idempotency lookup, so retries/polls never spend the budget
  (plan §7).
* ``GET /blog-post-jobs/{job_id}`` — guarded by ``_verify_service_key`` only;
  never rate-limited.
* ``POST /public-blogs/{root_id}/retract`` — delist a published blog
  (``blog_subjects.md`` D11). Service-key authed and **deliberately NOT
  owner-scoped**: the in-app publish/unpublish routes filter by ``user_id``, so
  a moderator hitting editorial-bot's row would get a 404, not a 403, and no
  user-facing flag can fix that. The service key IS the authority here.

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
    PublicBlogRetractResponse,
)
from backend.app.api.deepsearch_api.ratelimit import enforce_editorial_rate_limit
from backend.app.deps import get_supabase, validate_uuid
from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.services import public_blog_service
from shared.db.run import run_db
from shared.seo.judgment_naming import slugify_ar

logger = logging.getLogger(__name__)

router = APIRouter()

# Allowed enum values (validated here for an Arabic 400 rather than a 422).
_DISPLAY_MODES = {"question", "title"}
_PUBLISH_POLICIES = {"auto", "always", "never"}
_CONFIDENCE_LABELS = {"high", "medium", "low"}

# ``public_blogs.type`` (blog_subjects.md D3) — carried by the BLOG, never by a
# subject. Sourced from the service so the vocabulary has ONE definition.
_BLOG_TYPES = public_blog_service.BLOG_TYPES

# ``PlannerDecision.mode`` (agents/deep_search_v4/planner/models.py:54).
# ⚠ ``None`` is VALID and means "the planner decides" — it is checked for
# membership only when a value was actually supplied, and is never coerced to a
# default (blog_subjects.md §5; marketing_agents.md §3).
_PLANNER_MODES = {"case_led", "reg_compliance_led", "full"}

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

    # ── the public_blogs half (blog_subjects.md §5) ─────────────────────────
    # ``type`` is REQUIRED: public_blogs.type is NOT NULL and drives the badge
    # the wing filters on. Absent and out-of-vocabulary get distinct messages so
    # a caller can tell "you forgot it" from "you misspelled it".
    if not (req.type or "").strip():
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail="نوع المدونة مطلوب",
        )
    if req.type not in _BLOG_TYPES:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail="نوع المدونة غير معروف",
        )

    # ⚠ mode: ``None`` is a legitimate request ("the planner decides"), so only
    # a SUPPLIED value is checked. Coercing None to a default here would quietly
    # turn every planner-decides job into a pinned one. ``support`` needs no
    # check at all — Pydantic already constrains it to true | false | null, and
    # all three are meaningful.
    if req.mode is not None and req.mode not in _PLANNER_MODES:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail="قيمة mode غير صالحة",
        )


async def _validate_against_db(
    supabase: SupabaseClient, req: BlogPostJobRequest
) -> None:
    """The validations that need the database (blog_subjects.md §5).

    ⚠ **Runs only for a genuinely-new submission**, AFTER the idempotency
    lookup. A replay of a completed job would otherwise 409 on the very slug it
    itself published, turning idempotency into a hard failure on every retry.

    ⚠ Runs BEFORE the rate limiter too, so a malformed body never spends the
    hourly budget.
    """
    # Unknown subject slug → 400, NEVER a silent drop. A blog that publishes
    # with no subject is invisible in the browse tree and nobody notices until
    # the traffic does not arrive.
    if req.subjects:
        await run_db(
            public_blog_service.assert_subjects_known, supabase, list(req.subjects)
        )

    # Mint-time slug refusal — reserved literal, subject-vocabulary collision,
    # ASCII-kebab shape (that shape belongs to subjects by construction), then
    # uniqueness (409). Checked here so a typo costs a round trip instead of a
    # full deep_search run.
    supplied = (req.slug or "").strip()
    if supplied:
        await run_db(public_blog_service.assert_slug_available, supabase, supplied)
        return

    await _assert_mintable_slug(supabase, req)


async def _assert_mintable_slug(
    supabase: SupabaseClient, req: BlogPostJobRequest
) -> None:
    """Fail fast when the slug we WOULD mint from ``title`` is not publishable.

    ⚠ **This is the difference between a 400 now and a 400 after a full
    deep_search run.** With no ``slug``, the publisher mints one from the
    resolved title — and ``slugify_ar`` over a Latin title yields ASCII
    kebab-case, precisely the shape reserved to SUBJECTS by migration 153's
    CHECK. ``insert_public_blog`` then refuses it, correctly, but only at the
    very end: the job has already spent 1–4 minutes and a full retrieval budget,
    and the operator learns about a title they could have fixed in a second.

    The prediction is EXACT, not approximate, whenever ``title`` is supplied:
    the publisher's title precedence is request title → body H1 → WI title, so a
    supplied title wins there too and mints the same slug this checks. With no
    title the headline comes from the aggregator and is genuinely unknowable
    here — nothing is checked and the publish-time refusal stands as the
    backstop. That backstop is NOT removed by this function; this is a
    fail-fast layer in front of it.
    """
    title = (req.title or "").strip()
    if not title:
        return

    minted = slugify_ar(title)
    try:
        await run_db(public_blog_service.assert_slug_available, supabase, minted)
    except LunaHTTPException as exc:
        if exc.status_code != 400:
            # 409 = the minted slug is already live. That message already names
            # the real problem ("this link belongs to another blog"), and it is
            # about the slug, not the title — pass it through untouched.
            raise
        # Every 400 here (empty, reserved, subject collision, ASCII shape) has a
        # message written for a caller who SENT a slug. This caller sent a
        # title, so those messages would send them hunting for a field they
        # never filled in. Name the actual cause instead.
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail=(
                "تعذّر اشتقاق رابط عربي صالح من عنوان المدونة؛ "
                "أرسل عنواناً بالعربية أو حدّد الرابط صراحةً"
            ),
        ) from exc


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

    # Genuinely-new submission. Validate what needs the DB first (a bad body
    # must not spend the budget), THEN spend it.
    await _validate_against_db(supabase, req)

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


@router.post(
    "/public-blogs/{root_id}/retract",
    response_model=PublicBlogRetractResponse,
    dependencies=[Depends(_verify_service_key)],
)
async def retract_public_blog(
    root_id: str,
    supabase: SupabaseClient = Depends(get_supabase),
) -> PublicBlogRetractResponse:
    """Delist a published blog — ``is_public = false`` on the CURRENT version.

    Moderation for the public wing (blog_subjects.md D11). ``can_access_blog``
    was retired as a gate in step 9 precisely because it never granted the power
    actually wanted: moderating someone else's row is blocked by OWNERSHIP, not
    curation, so the in-app routes answer a moderator with a 404. Here the
    service key is the authority and nothing is owner-scoped.

    Retract delists **only** — ``deleted_at`` and ``is_published`` are untouched,
    so the URL keeps resolving for anyone holding the link, exactly like a
    ``blog_posts`` share link. ⚠ Because that leaves a live 200 it does **not**
    deindex; ``robots: noindex`` on the frontend (driven by the very flag this
    flips) is what does.

    Addressed by ``root_id`` — the LOGICAL blog — not by a version id, so it
    keeps working across every SEO rewrite.
    """
    validate_uuid(root_id, "معرف المدونة")
    # Raises a clean Arabic 404 when the root has no current, undeleted version.
    await run_db(public_blog_service.set_public, supabase, root_id, False)
    logger.info("editorial: retracted public blog root_id=%s", root_id)
    return PublicBlogRetractResponse(root_id=root_id, is_public=False)


__all__ = ["router"]
