"""Plan activation codes API — /api/v1/plans/redeem.

A user redeems a single-use activation code to get a subscription plan (the
marketing_lawyer / marketing_individual launch offers). The atomic claim +
plan grant lives in the redeem_plan_code() Postgres RPC (migration 069); this
route adds the per-user brute-force wall and maps the RPC's raised errors to
Arabic HTTP responses.

Two throttle layers protect the endpoint:
  - 5 requests/min per user+IP — the rate-limit middleware (burst control).
  - 5 FAILED attempts / rolling 24h per user — the Redis counter here
    (redeem:fails:{user_id}). Only wrong-code attempts count; a success clears
    the counter and an "already on an active plan" rejection is not counted
    (it isn't a guess). Fails open if Redis is down (the 33M keyspace makes a
    brief unthrottled window safe).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from redis.asyncio import Redis as AsyncRedis
from supabase import Client as SupabaseClient

from backend.app.deps import get_current_user, get_redis, get_supabase
from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.models.requests import RedeemCodeRequest
from backend.app.services.case_service import get_user_id
from shared.auth.jwt import AuthUser
from shared.db.run import run_db

logger = logging.getLogger(__name__)

router = APIRouter()

# ── brute-force wall ─────────────────────────────────────────────────────────

REDEEM_MAX_FAILS = 5
REDEEM_FAIL_WINDOW_S = 24 * 60 * 60  # rolling 24h, auto-unlock
_FAIL_KEY = "redeem:fails:{}"

# ── Arabic messages ──────────────────────────────────────────────────────────

CODE_INVALID_AR = "الرمز غير صالح أو مُستخدَم من قبل."
PLAN_ACTIVE_AR = "لديك اشتراك فعّال بالفعل، ولا يمكن استبداله برمز ترويجي."
REDEEM_LOCKED_AR = "لقد تجاوزت عدد المحاولات المسموح بها. حاول مرة أخرى لاحقًا."


# ── RPC domain errors (local) ────────────────────────────────────────────────

class _PlanAlreadyActive(Exception):
    """RPC raised 'plan_already_active' — user holds an active paid/dev plan."""


class _CodeInvalid(Exception):
    """RPC raised 'code_invalid_or_used' — unknown / used / expired code."""


def _redeem_rpc(supabase: SupabaseClient, user_id: str, code: str) -> dict:
    """Call the atomic redeem RPC (sync — run via run_db). Translates the
    PL/pgSQL RAISE messages into local exceptions; re-raises anything
    unexpected so the generic 500 handler surfaces it."""
    try:
        result = supabase.rpc(
            "redeem_plan_code", {"p_code": code, "p_user_id": user_id}
        ).execute()
    except Exception as e:  # noqa: BLE001 — inspect the raised PG message
        msg = (getattr(e, "message", None) or str(e) or "").lower()
        if "plan_already_active" in msg:
            raise _PlanAlreadyActive()
        if "code_invalid_or_used" in msg:
            raise _CodeInvalid()
        logger.exception("redeem_plan_code RPC failed: %s", e)
        raise

    rows = getattr(result, "data", None) or []
    if not rows:
        # RETURNS TABLE produced no row on the success path — defensive only.
        raise _CodeInvalid()
    return rows[0]


# ── Redis counter helpers (fail open) ────────────────────────────────────────

async def _is_redeem_locked(redis: Optional[AsyncRedis], user_id: str) -> tuple[bool, int]:
    if redis is None:
        return False, 0
    try:
        key = _FAIL_KEY.format(user_id)
        val = await redis.get(key)
        n = int(val) if val else 0
        if n >= REDEEM_MAX_FAILS:
            ttl = await redis.ttl(key)
            return True, max(int(ttl), 0)
        return False, 0
    except Exception as e:  # noqa: BLE001
        logger.debug("redeem lock check failed (fail open): %s", e)
        return False, 0


async def _incr_redeem_fail(redis: Optional[AsyncRedis], user_id: str) -> None:
    if redis is None:
        return
    try:
        key = _FAIL_KEY.format(user_id)
        n = await redis.incr(key)
        if n == 1:  # first fail in this window — start the 24h clock
            await redis.expire(key, REDEEM_FAIL_WINDOW_S)
    except Exception as e:  # noqa: BLE001
        logger.debug("redeem fail incr failed: %s", e)


async def _clear_redeem_fails(redis: Optional[AsyncRedis], user_id: str) -> None:
    if redis is None:
        return
    try:
        await redis.delete(_FAIL_KEY.format(user_id))
    except Exception as e:  # noqa: BLE001
        logger.debug("redeem fail clear failed: %s", e)


# ── route ────────────────────────────────────────────────────────────────────

@router.post("/plans/redeem")
async def redeem_plan_code(
    payload: RedeemCodeRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    redis: Optional[AsyncRedis] = Depends(get_redis),
):
    """Redeem a single-use activation code → assign its plan to the caller.

    Returns ``{plan_id, name_ar, expires_at}`` on success. Raises:
      - 429 REDEEM_LOCKED — 5 failed attempts in the last 24h.
      - 409 PLAN_ALREADY_ACTIVE — caller already on an active paid/dev plan.
      - 400 CODE_INVALID — unknown, used, or expired code.
    """
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)

    locked, retry_after = await _is_redeem_locked(redis, user_id)
    if locked:
        raise LunaHTTPException(
            status_code=429,
            code=ErrorCode.REDEEM_LOCKED,
            detail=REDEEM_LOCKED_AR,
            headers={"Retry-After": str(retry_after)} if retry_after else None,
        )

    try:
        row = await run_db(_redeem_rpc, supabase, user_id, payload.code)
    except _PlanAlreadyActive:
        # Not a guess at a code — does NOT count toward the brute-force wall.
        raise LunaHTTPException(
            status_code=409,
            code=ErrorCode.PLAN_ALREADY_ACTIVE,
            detail=PLAN_ACTIVE_AR,
        )
    except _CodeInvalid:
        await _incr_redeem_fail(redis, user_id)
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.CODE_INVALID,
            detail=CODE_INVALID_AR,
        )

    await _clear_redeem_fails(redis, user_id)
    logger.info("plan code redeemed: user=%s plan=%s", user_id, row.get("plan_id"))
    return {
        "plan_id": row.get("plan_id"),
        "name_ar": row.get("name_ar"),
        "expires_at": row.get("expires_at"),
    }
