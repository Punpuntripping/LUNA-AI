"""اسأل ريحان — anonymous ask popup + post-signup claim (SEO Library Phase 4).

Mounted under ``/api/v1`` (the router declares the prefix itself, like
``public_library.py``). Three endpoints:

    POST /api/v1/public/ask            — PUBLIC (no auth). Ask one question about
                                         the page you're on; get a short teaser
                                         (the full answer is stored server-side).
    POST /api/v1/ask/claim             — AUTH. Claim the full answer post-signup
                                         (the "continuity moment").
    GET  /api/v1/public/ask/{id}       — PUBLIC (no auth). Re-fetch your own teaser
                                         after a page refresh (id + session_key).

Design (see ``.claude/plans/seo_public_library.md`` § "Phase 4" and the storage
schema ``shared/db/migrations/099_anon_questions.sql``):
  - The two public GET/POSTs intentionally have NO ``Depends(get_current_user)`` —
    auth is per-endpoint in this codebase (no global auth middleware), so omitting
    the dep is what makes them anonymous-accessible. The IP-keyed rate-limit
    middleware still applies.
  - Server-side truncation is the trust boundary: an anon client only ever
    receives the first ``visible_prefix_chars`` (220) of the answer; the rest is
    revealed ONLY via the authed claim endpoint.
  - Abuse controls (kill switch, per-session cap, global daily budget, Turnstile)
    live in ``ask_service`` and are checked here before any model spend.

Environment variables (documented in ``ask_service``): ANON_ASK_ENABLED (default
OFF, fail-closed), ANON_ASK_DAILY_MAX (default 200), TURNSTILE_SECRET_KEY (unset =
Turnstile skipped). All error messages are Arabic.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from supabase import Client as SupabaseClient

from backend.app.deps import get_current_user, get_supabase, validate_uuid
from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.middleware.rate_limit import resolve_client_ip
from backend.app.services import ask_service
from backend.app.services.case_service import get_user_id
from shared.auth.jwt import AuthUser
from shared.db.run import run_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["public-ask"])


# ============================================
# REQUEST / RESPONSE MODELS
# ============================================


class AnonAskRequest(BaseModel):
    """POST /public/ask body. ``question`` length is enforced 3..500 by the schema;
    ``page_type`` + ``page_id`` are the grounding key; ``session_key`` (if present)
    ties the ask to the visitor's anon session; ``turnstile_token`` is verified
    only when TURNSTILE_SECRET_KEY is configured."""

    question: str = Field(..., min_length=3, max_length=500)
    page_type: str = Field(..., min_length=1, max_length=40)
    page_id: str = Field(..., min_length=1, max_length=400)
    session_key: Optional[str] = Field(None, max_length=64)
    turnstile_token: Optional[str] = Field(None, max_length=4000)


class AnonAskResponse(BaseModel):
    """POST /public/ask success. NEVER carries the full answer — only the teaser
    prefix. ``session_key`` is echoed so the frontend can persist it."""

    question_id: str
    session_key: str
    visible_prefix: str
    is_truncated: bool
    total_chars: int


class AnonTeaserResponse(BaseModel):
    """GET /public/ask/{id} — re-fetch of one's own teaser."""

    question: str
    visible_prefix: str
    is_truncated: bool
    claimed: bool


class ClaimRequest(BaseModel):
    """POST /ask/claim body — the id + session_key of the anon answer to claim."""

    question_id: str = Field(..., min_length=1)
    session_key: str = Field(..., min_length=1, max_length=64)


class ClaimResponse(BaseModel):
    """POST /ask/claim success — the FULL answer, revealed to the authed owner."""

    question: str
    answer_md: str
    page_type: str
    page_id: str


# ============================================
# HELPERS
# ============================================


def _client_ip(request: Request) -> Optional[str]:
    """Best-effort caller IP for Turnstile ``remoteip``.

    Delegates to the rate limiter's ``resolve_client_ip`` — this used to
    duplicate the leftmost-X-Forwarded-For logic, which meant the edge cutover
    (``TRUST_CF_HEADERS`` → ``CF-Connecting-IP``) would have had to be made
    twice. One resolver, one trust boundary.
    """
    return resolve_client_ip(request)


# ============================================
# POST /public/ask — anonymous
# ============================================


@router.post("/public/ask", response_model=AnonAskResponse)
async def anon_ask(
    body: AnonAskRequest,
    request: Request,
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Answer one anonymous question grounded in the current page's own text.

    Guard order (cheapest first, all before any model spend): kill switch →
    per-session cap → global daily budget → Turnstile → generate. Returns only the
    teaser prefix; the full answer is stored in ``anon_questions`` and revealed
    only on authed claim.
    """
    # 1. Kill switch — DEFAULT OFF (fail-closed).
    if not ask_service.anon_ask_enabled():
        raise LunaHTTPException(
            status_code=503, code=ErrorCode.SERVICE_UNAVAILABLE,
            detail="الخدمة غير متاحة حالياً",
        )

    question = (body.question or "").strip()
    if len(question) < 3:
        raise LunaHTTPException(
            status_code=400, code=ErrorCode.VALIDATION_ERROR,
            detail="السؤال قصير جداً",
        )

    # 2. Session key — use provided or mint a fresh one (returned in the response).
    session_key = (body.session_key or "").strip() or uuid.uuid4().hex

    # 3a. Per-session cap: max 1 unclaimed question / session / 24h.
    if await run_db(ask_service.session_unclaimed_count, supabase, session_key) >= 1:
        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "code": ErrorCode.RATE_LIMITED.value,
                    "message": "سجّل مجاناً لطرح المزيد من الأسئلة",
                    "status": 429,
                },
                "detail": "سجّل مجاناً لطرح المزيد من الأسئلة",
                "limit": "session",
            },
        )

    # 3b. Global daily budget (cost guard).
    if await run_db(ask_service.global_today_count, supabase) >= ask_service.daily_max():
        raise LunaHTTPException(
            status_code=503, code=ErrorCode.SERVICE_UNAVAILABLE,
            detail="الخدمة غير متاحة حالياً",
        )

    # 3c. Turnstile (skipped entirely when TURNSTILE_SECRET_KEY is unset).
    if not await ask_service.verify_turnstile(body.turnstile_token, _client_ip(request)):
        raise LunaHTTPException(
            status_code=403, code=ErrorCode.FORBIDDEN,
            detail="فشل التحقق الأمني، حاول مجدداً",
        )

    # 4. Ground → generate → store. Returns the teaser only.
    result = await ask_service.generate_anon_answer(
        supabase,
        question=question,
        page_type=(body.page_type or "").strip(),
        page_id=(body.page_id or "").strip(),
        session_key=session_key,
    )
    return AnonAskResponse(
        question_id=result["question_id"],
        session_key=session_key,
        visible_prefix=result["visible_prefix"],
        is_truncated=result["is_truncated"],
        total_chars=result["total_chars"],
    )


# ============================================
# GET /public/ask/{question_id} — anonymous re-fetch
# ============================================


@router.get("/public/ask/{question_id}", response_model=AnonTeaserResponse)
async def anon_ask_teaser(
    question_id: str,
    session_key: str = Query(..., min_length=1, max_length=64),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Re-fetch one's own teaser after a refresh (id + session_key must match).

    404 (Arabic) when the row is missing or the session_key doesn't match. Never
    returns the full answer — only the visible prefix + a ``claimed`` flag.
    """
    validate_uuid(question_id, "معرف السؤال")
    teaser = await run_db(ask_service.get_teaser, supabase, question_id, session_key)
    if teaser is None:
        raise LunaHTTPException(
            status_code=404, code=ErrorCode.ARTIFACT_NOT_FOUND,
            detail="الإجابة غير موجودة",
        )
    return AnonTeaserResponse(**teaser)


# ============================================
# POST /ask/claim — authenticated
# ============================================


@router.post("/ask/claim", response_model=ClaimResponse)
async def claim_anon_answer(
    body: ClaimRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Claim the full anon answer for the authenticated caller.

    The row must match id AND session_key. Unclaimed → set owner + return full
    answer; already claimed by this user → idempotent re-return; claimed by another
    → 403 (Arabic); wrong session_key / missing → 404 (Arabic).
    """
    validate_uuid(body.question_id, "معرف السؤال")
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    payload = await run_db(
        ask_service.claim_answer,
        supabase,
        question_id=body.question_id,
        session_key=body.session_key,
        user_id=user_id,
    )
    return ClaimResponse(**payload)
