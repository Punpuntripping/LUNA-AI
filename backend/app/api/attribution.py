"""Signup attribution — channel at signup + X Conversions API (migration 172).

    POST /api/v1/attribution/signup        — JWT. Fresh, confirmed account
                                             records its source once; SignUp to
                                             X when it came from an X ad.
    POST /api/v1/attribution/x-visit       — ANONYMOUS. The landing of an X ad
                                             click was visible ≥3 s (X8). 204.
    POST /internal/x-conversions/retry     — X-Webhook-Secret. Manual run of
                                             the daily retry job.

⚠ /attribution/signup NEVER FAILS VISIBLY: always 200 ``{"ok": true}``, also
for a stale account, a missing row or a DB error. It is analytics — the
visitor must never see it. Rules live in ``x_conversions_service``.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from supabase import Client as SupabaseClient

from backend.app.api.analytics import is_bot_request
from backend.app.api.internal_webhooks import _verify_webhook_secret
from backend.app.deps import get_current_user, get_supabase
from backend.app.errors import LunaHTTPException
from backend.app.middleware.route_limits import RouteRateLimiter
from backend.app.services import x_conversions_service
from shared.auth.jwt import AuthUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/attribution", tags=["attribution"])
internal_router = APIRouter(prefix="/internal/x-conversions", tags=["internal"])


class SignupAttributionBody(BaseModel):
    # Free-form on purpose: the service validates shape and drops junk rather
    # than 422-ing, because this endpoint must never fail visibly.
    twclid: Optional[str] = None
    utm_source: Optional[str] = None
    utm_campaign: Optional[str] = None


@router.post("/signup")
async def record_signup_attribution(
    body: Optional[SignupAttributionBody] = None,
    user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
) -> dict:
    body = body or SignupAttributionBody()
    await x_conversions_service.record_signup(
        supabase,
        user.auth_id,
        twclid=body.twclid,
        utm_source=body.utm_source,
        utm_campaign=body.utm_campaign,
    )
    return {"ok": True}


# One visit per ad click is all a real visitor ever sends; 10/min per IP leaves
# room for a few tabs and keeps forged-twclid noise down. Own bucket.
x_visit_rate_limit = RouteRateLimiter(scope="x_visit", limit=10, window_seconds=60)

_MAX_VISIT_BODY = 1_024


@router.post("/x-visit", status_code=204)
async def record_x_visit(request: Request) -> Response:
    """X8. No auth, no DB row, no retry. ALWAYS 204 — never an oracle, never
    visible. Reads raw bytes so a ``keepalive`` fetch of any content type and
    malformed JSON are both a silent drop rather than a 422."""
    done = Response(status_code=204)
    if is_bot_request(request):
        return done
    try:
        await x_visit_rate_limit(request, None)
    except LunaHTTPException:
        return done
    except Exception as exc:  # noqa: BLE001 — a limiter fault must not 500
        logger.warning("x-visit: rate limiter error (allowing): %s", exc)
    try:
        raw = await request.body()
        if len(raw) > _MAX_VISIT_BODY:
            return done
        body = json.loads(raw or b"{}")
        if isinstance(body, dict):
            x_conversions_service.schedule_visit(body.get("twclid"))
    except Exception:  # noqa: BLE001
        pass
    return done


@internal_router.post("/retry", dependencies=[Depends(_verify_webhook_secret)])
async def retry_x_conversions(
    supabase: SupabaseClient = Depends(get_supabase),
) -> dict:
    return await x_conversions_service.retry_pending(supabase)
