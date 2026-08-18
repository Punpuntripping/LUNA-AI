"""
Account lifecycle business logic — deletion scheduling, restore, identity checks.

Deleting an account is a two-phase flow (migration 090):

  1. **Grace** — ``users.deletion_requested_at`` is stamped. The account is
     immediately deactivated for every data route (the ``get_user_id`` gate in
     ``case_service`` raises 403 ACCOUNT_DELETION_PENDING), while ``/auth/*``
     stays reachable so the user can restore or log out.
  2. **Purge** — a daily sweep hard-deletes everything once the grace window
     has elapsed (``account_purge_service``).

``GRACE_PERIOD_DAYS`` is the single source of truth for the window length —
the purge service imports it from here.

All database queries go through the sync Supabase client (routes call these via
``run_db``). All error messages are Arabic.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Union

from supabase import Client as SupabaseClient

from backend.app.errors import ErrorCode, LunaHTTPException, MSG_SERVICE_UNAVAILABLE
from backend.app.services.audit_service import write_audit_log

logger = logging.getLogger(__name__)

GRACE_PERIOD_DAYS = 30


# ============================================
# HELPERS
# ============================================

def compute_purge_at(
    deletion_requested_at: Union[str, datetime, None],
) -> Optional[datetime]:
    """Return the hard-purge date for a deletion request, or None if not pending.

    Server-computed so the frontend never does grace-period date math.
    """
    if not deletion_requested_at:
        return None

    if isinstance(deletion_requested_at, str):
        try:
            deletion_requested_at = datetime.fromisoformat(deletion_requested_at)
        except ValueError:
            logger.warning(
                "Unparseable deletion_requested_at: %r", deletion_requested_at
            )
            return None

    return deletion_requested_at + timedelta(days=GRACE_PERIOD_DAYS)


def _fetch_account_row(supabase: SupabaseClient, auth_id: str) -> dict:
    """Look up user_id + deletion_requested_at from the Supabase auth_id.

    Deliberately NOT ``case_service.get_user_id``: that helper 403s pending
    accounts, which would lock a user out of the very endpoints (delete/restore)
    that manage the pending state.

    Raises:
        HTTPException 401: profile row missing.
        HTTPException 500: query failed.
    """
    try:
        result = (
            supabase.table("users")
            .select("user_id, deletion_requested_at")
            .eq("auth_id", auth_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logger.exception("Error looking up account for auth_id=%s: %s", auth_id, e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ داخلي")

    if result is None or result.data is None:
        raise LunaHTTPException(status_code=401, code=ErrorCode.USER_NOT_FOUND, detail="الملف الشخصي غير موجود")

    return result.data


def get_account_user_id(supabase: SupabaseClient, auth_id: str) -> str:
    """Resolve the internal user_id WITHOUT the deletion gate.

    For auth-surface use only (audit rows on accounts that may be pending).
    Data routes must keep using ``case_service.get_user_id``, which 403s.
    """
    return _fetch_account_row(supabase, auth_id)["user_id"]


# ============================================
# DELETION SCHEDULING
# ============================================

def schedule_account_deletion(supabase: SupabaseClient, auth_id: str) -> dict:
    """Stamp ``deletion_requested_at`` and start the grace window.

    Idempotent: a repeat request while already pending is a no-op that returns
    the ORIGINAL timestamps — the ``IS NULL`` filter guarantees a second call
    can never reset (extend) the purge clock.

    Returns:
        ``{"deletion_requested_at": datetime, "purge_at": datetime}``
    """
    account = _fetch_account_row(supabase, auth_id)
    user_id = account["user_id"]
    existing = account.get("deletion_requested_at")

    if existing:
        return {
            "deletion_requested_at": existing,
            "purge_at": compute_purge_at(existing),
        }

    requested_at = datetime.now(timezone.utc)

    try:
        (
            supabase.table("users")
            .update({"deletion_requested_at": requested_at.isoformat()})
            .eq("auth_id", auth_id)
            .is_("deletion_requested_at", "null")
            .execute()
        )
    except Exception as e:
        logger.exception("Error scheduling deletion for user_id=%s: %s", user_id, e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ داخلي")

    write_audit_log(
        supabase,
        user_id=user_id,
        action="delete",
        resource_type="account",
        resource_id=user_id,
        metadata={"event": "deletion_requested"},
    )
    logger.info("Account deletion scheduled: user_id=%s", user_id)

    return {
        "deletion_requested_at": requested_at,
        "purge_at": compute_purge_at(requested_at),
    }


def cancel_account_deletion(supabase: SupabaseClient, auth_id: str) -> None:
    """Clear ``deletion_requested_at`` — the account is active again.

    Idempotent: cancelling a non-pending account is a silent no-op.
    """
    account = _fetch_account_row(supabase, auth_id)
    user_id = account["user_id"]

    if not account.get("deletion_requested_at"):
        return

    try:
        (
            supabase.table("users")
            .update({"deletion_requested_at": None})
            .eq("auth_id", auth_id)
            .execute()
        )
    except Exception as e:
        logger.exception("Error cancelling deletion for user_id=%s: %s", user_id, e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ داخلي")

    write_audit_log(
        supabase,
        user_id=user_id,
        action="update",
        resource_type="account",
        resource_id=user_id,
        metadata={"event": "deletion_cancelled"},
    )
    logger.info("Account deletion cancelled: user_id=%s", user_id)


# ============================================
# IDENTITY
# ============================================

def has_password(supabase: SupabaseClient, auth_id: str) -> bool:
    """True when the account holds a usable password credential.

    Reads ``auth.users.encrypted_password`` through the ``user_has_password``
    RPC (migration 141) rather than scanning GoTrue's identity list, because
    those two disagree in the case this codebase now creates on purpose:
    setting a password on an OAuth-only account writes the credential and makes
    password sign-in work, but does NOT add an ``email`` identity ("ghost
    password", supabase/discussions#37737). Keying off identities would leave a
    Google user who just set a password still reported as password-less —
    /change-password would keep 400ing and إعدادات الحساب would keep hiding the
    form, which is the very bug set-password exists to fix.

    Authoritative live check — a client claim about the provider is never
    trusted, and the JWT's app_metadata can be up to an hour stale.

    Fails CLOSED: any error raises 503 rather than reporting "no password",
    which would skip password confirmation on delete-account and would let
    /set-password overwrite an existing password without re-authentication.

    Raises:
        HTTPException 503: the RPC failed or returned an unusable answer.
    """
    try:
        response = supabase.rpc("user_has_password", {"p_auth_id": auth_id}).execute()
        value = response.data
    except Exception as e:
        logger.error("user_has_password RPC failed for auth_id=%s: %s", auth_id, e)
        raise LunaHTTPException(
            status_code=503,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            detail=MSG_SERVICE_UNAVAILABLE,
        )

    # A scalar-returning RPC answers with the bare boolean; tolerate the
    # single-row-list shape too rather than trusting one client version.
    if isinstance(value, list):
        value = value[0] if value else None

    if not isinstance(value, bool):
        logger.error(
            "user_has_password returned a non-boolean for auth_id=%s: %r",
            auth_id,
            value,
        )
        raise LunaHTTPException(
            status_code=503,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            detail=MSG_SERVICE_UNAVAILABLE,
        )

    return value
