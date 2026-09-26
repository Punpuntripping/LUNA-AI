"""Web push subscriptions — /api/v1/push (pwa_step1.md §1D).

    GET    /api/v1/push/vapid-public-key   → {"public_key": "..."}   (503 if unconfigured)
    POST   /api/v1/push/subscribe          body {endpoint, keys: {p256dh, auth}}  → 204
                                           (422 unless endpoint is a known push-service host;
                                            max push_service.MAX_SUBS_PER_USER devices per user)
    DELETE /api/v1/push/subscribe          body {endpoint}                        → 204

All three are AUTHED. The frontend fetches the public key from this route, so no
NEXT_PUBLIC_VAPID_PUBLIC_KEY build arg is needed (and rotating the key is a
luna-backend env change, no frontend rebuild). Rate limiting is the global
RateLimitMiddleware, same as every other /api/v1 data route.

The service-role client bypasses RLS, so ownership is enforced in
push_service by filtering on the caller's user_id (resolved from
AuthUser.auth_id via get_user_id — also the account-deactivation gate).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, Request, Response
from pydantic import BaseModel, Field, field_validator
from supabase import Client as SupabaseClient

from backend.app.deps import get_current_user, get_supabase
from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.services import push_service
from backend.app.services.case_service import get_user_id
from shared.auth.jwt import AuthUser
from shared.db.run import run_db

logger = logging.getLogger(__name__)

router = APIRouter()

MSG_PUSH_UNCONFIGURED = "الإشعارات غير متاحة حالياً"
MSG_PUSH_SUBSCRIBE_FAILED = "تعذّر تفعيل الإشعارات، حاول مجدداً"
MSG_PUSH_UNSUBSCRIBE_FAILED = "تعذّر إيقاف الإشعارات، حاول مجدداً"


def _validate_endpoint(v: str) -> str:
    """Subscribe: https AND a known browser push-service host.

    The sender POSTs to this URL after every turn, so an arbitrary host is both
    SSRF and a thread-pool tarpit. The allowlist lives in push_service (it is
    re-checked at send time). Failure → pydantic ValidationError → 422.
    """
    v = v.strip()
    if not push_service.is_allowed_push_endpoint(v):
        raise ValueError("endpoint must be an https URL on a known push service")
    return v


def _validate_unsubscribe_endpoint(v: str) -> str:
    # Lenient on purpose: only https, so a user can still delete a row stored
    # before the host allowlist. Delete never contacts the URL.
    v = v.strip()
    if not v.startswith("https://"):
        raise ValueError("endpoint must be an https URL")
    return v


class PushKeys(BaseModel):
    p256dh: str = Field(..., min_length=1, max_length=256)
    auth: str = Field(..., min_length=1, max_length=128)


class PushSubscribeRequest(BaseModel):
    endpoint: str = Field(..., min_length=10, max_length=2048)
    keys: PushKeys

    _check_endpoint = field_validator("endpoint")(_validate_endpoint)


class PushUnsubscribeRequest(BaseModel):
    endpoint: str = Field(..., min_length=10, max_length=2048)

    _check_endpoint = field_validator("endpoint")(_validate_unsubscribe_endpoint)


@router.get("/push/vapid-public-key")
async def get_vapid_public_key(
    current_user: AuthUser = Depends(get_current_user),
) -> dict:
    key = push_service.vapid_public_key()
    if not key:
        raise LunaHTTPException(
            status_code=503,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            detail=MSG_PUSH_UNCONFIGURED,
        )
    return {"public_key": key}


@router.post("/push/subscribe", status_code=204)
async def subscribe(
    request: Request,
    body: PushSubscribeRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
) -> Response:
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    user_agent: Optional[str] = request.headers.get("user-agent")
    try:
        await run_db(
            push_service.upsert_subscription,
            supabase,
            user_id=user_id,
            endpoint=body.endpoint,
            p256dh=body.keys.p256dh,
            auth=body.keys.auth,
            user_agent=user_agent,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("push subscribe failed: %s", e)
        raise LunaHTTPException(
            status_code=500, code=ErrorCode.INTERNAL_ERROR, detail=MSG_PUSH_SUBSCRIBE_FAILED,
        )
    return Response(status_code=204)


@router.delete("/push/subscribe", status_code=204)
async def unsubscribe(
    body: PushUnsubscribeRequest = Body(...),
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
) -> Response:
    """Remove the caller's own subscription for ``endpoint``.

    Idempotent: 204 whether or not a row matched (an endpoint owned by another
    user is never touched and its existence is not disclosed).
    """
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    try:
        await run_db(
            push_service.delete_subscription,
            supabase,
            user_id=user_id,
            endpoint=body.endpoint,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("push unsubscribe failed: %s", e)
        raise LunaHTTPException(
            status_code=500, code=ErrorCode.INTERNAL_ERROR, detail=MSG_PUSH_UNSUBSCRIBE_FAILED,
        )
    return Response(status_code=204)
