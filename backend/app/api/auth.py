"""
Auth API routes — /api/v1/auth/
10 endpoints: login, refresh, logout, me, profession, preferred-name,
delete-account, restore-account, change-password, logout-all

(Signup runs client-side via supabase.auth.signUp() — see the note above /refresh.)
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from redis.asyncio import Redis as AsyncRedis
from supabase import Client as SupabaseClient
from supabase_auth.errors import (
    AuthApiError,
    AuthRetryableError,
    AuthSessionMissingError,
)

from backend.app.errors import (
    LunaHTTPException,
    ErrorCode,
    MSG_SERVICE_UNAVAILABLE,
)
from backend.app.deps import get_current_user, get_supabase, get_supabase_auth, get_redis
from backend.app.models.requests import (
    ChangePasswordRequest,
    DeleteAccountRequest,
    LoginRequest,
    RefreshRequest,
    UpdatePreferredNameRequest,
    UpdateProfessionRequest,
)
from backend.app.models.responses import (
    LoginResponse,
    PreferredNameResponse,
    ProfessionResponse,
    TokenResponse,
    UserProfile,
    UserProfileResponse,
    SuccessResponse,
)
from backend.app.services.account_service import (
    cancel_account_deletion,
    compute_purge_at,
    get_account_user_id,
    has_password_identity,
    schedule_account_deletion,
)
from backend.app.services.audit_service import write_audit_log
from shared.auth.jwt import AuthUser
from shared.db.client import create_isolated_anon_client
from shared.db.run import run_db
from shared.identity import resolve_call_name

logger = logging.getLogger(__name__)

router = APIRouter()

# Redis session TTL: 24 hours
_SESSION_TTL = 86400

# Hard deadline for any single sync GoTrue call (matches gotrue's own httpx
# default of 5s, so a wait_for-abandoned thread self-terminates quickly).
_GOTRUE_TIMEOUT = 5.0


async def _gotrue_call(fn, /, *args, **kwargs):
    """Run a sync GoTrue call off the event loop with a hard 5s deadline.

    On Python 3.11+ asyncio.TimeoutError is builtins.TimeoutError, so callers
    catch TimeoutError to detect a hung GoTrue.
    """
    return await asyncio.wait_for(
        asyncio.to_thread(fn, *args, **kwargs), timeout=_GOTRUE_TIMEOUT
    )


def _raw_jwt(request: Request) -> str:
    """Return the caller's raw bearer token.

    admin.sign_out() revokes the refresh tokens attached to the JWT's session,
    so it needs the token itself — not the decoded AuthUser.
    """
    header = request.headers.get("Authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise LunaHTTPException(
            status_code=401,
            code=ErrorCode.AUTH_INVALID,
            detail="بيانات الدخول غير صحيحة",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token.strip()


async def _verify_password(
    email: str,
    password: str,
    wrong_password_detail: str,
) -> None:
    """Re-verify the caller's password against GoTrue before a sensitive action.

    ``email`` MUST come from the verified JWT claim, never from the request body.
    Error mapping mirrors /login: bad credentials → 401 AUTH_INVALID, anything
    else (outage, hang, unexpected shape) → 503.

    Runs on a THROWAWAY anon client, not the shared ``app.state.supabase_auth``
    singleton: sign_in_with_password parks its session in the client's in-memory
    auth store, and gotrue's ``auth.sign_out()`` acts on whatever session is
    parked there — so verifying on the shared client would let one request's
    re-auth collide with another request's session.
    """
    client = await asyncio.to_thread(create_isolated_anon_client)
    try:
        response = await _verify_on(client, email, password, wrong_password_detail)
    finally:
        try:
            await asyncio.to_thread(client.auth.close)
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not close verification auth client: %s", e)

    if response.user is None:
        raise LunaHTTPException(
            status_code=401,
            code=ErrorCode.AUTH_INVALID,
            detail=wrong_password_detail,
        )


async def _verify_on(
    client: SupabaseClient,
    email: str,
    password: str,
    wrong_password_detail: str,
):
    try:
        return await _gotrue_call(
            client.auth.sign_in_with_password,
            {"email": email, "password": password},
        )
    except (AuthRetryableError, TimeoutError) as e:
        logger.error("GoTrue unavailable during password verification: %s", e)
        raise LunaHTTPException(
            status_code=503,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            detail=MSG_SERVICE_UNAVAILABLE,
        )
    except AuthApiError as e:
        if e.status in (400, 401, 403, 422):
            raise LunaHTTPException(
                status_code=401,
                code=ErrorCode.AUTH_INVALID,
                detail=wrong_password_detail,
            )
        logger.error(
            "GoTrue API error during password verification (status=%s code=%s)",
            e.status,
            e.code,
        )
        raise LunaHTTPException(
            status_code=503,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            detail=MSG_SERVICE_UNAVAILABLE,
        )
    except Exception as e:
        logger.exception("Unexpected password verification error: %s", e)
        raise LunaHTTPException(
            status_code=503,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            detail=MSG_SERVICE_UNAVAILABLE,
        )


def _audit_account_event(
    supabase: SupabaseClient, auth_id: str, action: str, event: str
) -> None:
    """Resolve the internal user_id (ungated) and write one account audit row."""
    user_id = get_account_user_id(supabase, auth_id)
    write_audit_log(
        supabase,
        user_id=user_id,
        action=action,
        resource_type="account",
        resource_id=user_id,
        metadata={"event": event},
    )


async def _drop_redis_session(redis: Optional[AsyncRedis], auth_id: str) -> None:
    """Best-effort Redis session teardown — never blocks the response."""
    if redis is None:
        return
    try:
        await redis.delete(f"session:{auth_id}")
    except Exception as e:
        logger.warning("Failed to delete Redis session for %s: %s", auth_id, e)


# ============================================
# POST /login
# ============================================

@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    supabase: SupabaseClient = Depends(get_supabase),
    supabase_auth: SupabaseClient = Depends(get_supabase_auth),
    redis: Optional[AsyncRedis] = Depends(get_redis),
):
    """
    Authenticate a user with email + password.
    Returns access_token, refresh_token, and user profile (incl. deletion state,
    so an account in its grace window lands straight on the blocking screen).
    """
    try:
        response = await _gotrue_call(
            supabase_auth.auth.sign_in_with_password,
            {"email": body.email, "password": body.password},
        )
    except (AuthRetryableError, TimeoutError) as e:
        # Network error inside gotrue, GoTrue 502/503/504, or GoTrue hung >5s.
        logger.error("GoTrue unavailable during login: %s", e)
        raise LunaHTTPException(
            status_code=503,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            detail=MSG_SERVICE_UNAVAILABLE,
        )
    except AuthApiError as e:
        if e.status in (400, 401, 403, 422):
            raise LunaHTTPException(
                status_code=401,
                code=ErrorCode.AUTH_INVALID,
                detail="بيانات الدخول غير صحيحة",
            )
        # Other status (5xx) — GoTrue server error, not the user's credentials.
        logger.error(
            "GoTrue API error during login (status=%s code=%s)", e.status, e.code
        )
        raise LunaHTTPException(
            status_code=503,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            detail=MSG_SERVICE_UNAVAILABLE,
        )
    except Exception as e:
        # AuthUnknownError / AuthSessionMissingError / anything unexpected:
        # don't blame the user's password for a garbage/unexpected response.
        logger.exception("Unexpected login error: %s", e)
        raise LunaHTTPException(
            status_code=503,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            detail=MSG_SERVICE_UNAVAILABLE,
        )

    session = response.session
    user = response.user

    if session is None or user is None:
        raise LunaHTTPException(status_code=401, code=ErrorCode.AUTH_INVALID, detail="بيانات الدخول غير صحيحة")

    # Create Redis session (fail silently if Redis unavailable)
    if redis is not None:
        try:
            session_data = json.dumps(
                {
                    "auth_id": user.id,
                    "email": user.email,
                    "logged_in_at": str(session.expires_at),
                },
                ensure_ascii=False,
            )
            await redis.set(f"session:{user.id}", session_data, ex=_SESSION_TTL)
        except Exception as e:
            logger.warning("Failed to create Redis session: %s", e)

    user_metadata = user.user_metadata or {}

    def _fetch_users_row_state():
        return (
            supabase.table("users")
            .select(
                "deletion_requested_at, profession_group, profession_label, "
                "full_name_ar, preferred_name"
            )
            .eq("auth_id", user.id)
            .maybe_single()
            .execute()
        )

    # A failure here must never break login — degrade to "not pending"; the
    # get_user_id gate still blocks every data route server-side either way.
    # Profession degrades to the "unknown" sentinel (fail-closed: only an
    # explicit NULL read from the DB may trigger the onboarding prompt).
    deletion_requested_at = None
    profession_group = "unknown"
    profession_label = None
    # The users row is the better source for the name than user_metadata: only
    # our own signup form writes full_name_ar into metadata, so for a Google
    # sign-in the metadata key is absent while the row (migration 122) holds
    # the real name. Metadata stays as the fallback for a degraded read.
    full_name_ar = user_metadata.get("full_name_ar")
    preferred_name = None
    try:
        result = await run_db(_fetch_users_row_state)
        if result is not None and result.data is not None:
            deletion_requested_at = result.data.get("deletion_requested_at")
            profession_group = result.data.get("profession_group")
            profession_label = result.data.get("profession_label")
            full_name_ar = result.data.get("full_name_ar") or full_name_ar
            preferred_name = result.data.get("preferred_name")
    except Exception as e:
        logger.warning("Could not read users-row state during login: %s", e)

    return LoginResponse(
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        user=UserProfile(
            user_id=user.id,
            email=user.email or "",
            full_name_ar=full_name_ar,
            preferred_name=preferred_name,
            call_name=resolve_call_name(preferred_name, full_name_ar),
            subscription_tier="free",
            created_at=user.created_at if user.created_at else None,
            deletion_pending=bool(deletion_requested_at),
            deletion_requested_at=deletion_requested_at,
            purge_at=compute_purge_at(deletion_requested_at),
            profession_group=profession_group,
            profession_label=profession_label,
        ),
    )


# Signup is performed in the browser via supabase.auth.signUp() (see
# frontend/stores/auth-store.ts). Doing it client-side keeps the PKCE
# code_verifier in the same browser that opens the email-confirmation link,
# which is required for /auth/callback's exchangeCodeForSession() to succeed.


# ============================================
# POST /refresh
# ============================================

@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    supabase_auth: SupabaseClient = Depends(get_supabase_auth),
):
    """
    Exchange a refresh token for a new access + refresh token pair.
    """
    try:
        response = await _gotrue_call(
            supabase_auth.auth.refresh_session, body.refresh_token
        )
        session = response.session
        if session is None:
            raise LunaHTTPException(
                status_code=401,
                code=ErrorCode.AUTH_EXPIRED,
                detail="الرمز منتهي الصلاحية",
            )

        return TokenResponse(
            access_token=session.access_token,
            refresh_token=session.refresh_token,
        )
    except LunaHTTPException:
        raise
    except (AuthRetryableError, TimeoutError) as e:
        # Headline fix: an outage must NOT masquerade as an expired token, or
        # the frontend force-logs-out every user during a Supabase blip.
        logger.error("GoTrue unavailable during refresh: %s", e)
        raise LunaHTTPException(
            status_code=503,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            detail=MSG_SERVICE_UNAVAILABLE,
        )
    except AuthSessionMissingError:
        raise LunaHTTPException(
            status_code=401,
            code=ErrorCode.AUTH_EXPIRED,
            detail="الرمز منتهي الصلاحية",
        )
    except AuthApiError as e:
        if e.status in (400, 401, 403):
            raise LunaHTTPException(
                status_code=401,
                code=ErrorCode.AUTH_EXPIRED,
                detail="الرمز منتهي الصلاحية",
            )
        logger.error(
            "GoTrue API error during refresh (status=%s code=%s)", e.status, e.code
        )
        raise LunaHTTPException(
            status_code=503,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            detail=MSG_SERVICE_UNAVAILABLE,
        )
    except Exception as e:
        logger.exception("Unexpected token refresh error: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ داخلي",
        )


# ============================================
# POST /logout
# ============================================

@router.post("/logout", response_model=SuccessResponse)
async def logout(
    request: Request,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    redis: Optional[AsyncRedis] = Depends(get_redis),
):
    """
    Sign out the current user, delete Redis session.

    Always returns 200 even when degraded: the client discards its tokens
    regardless, and a 503 would trap users who just want to log out. Shared-
    device risk is bounded by token expiry. Degradation is logged loudly once.

    ⚠ MUST target the caller's own token via the SERVICE-ROLE admin API, never
    ``supabase_auth.auth.sign_out()``. ``app.state.supabase_auth`` is an
    ``lru_cache``d anon client shared by every request, and
    ``sign_in_with_password`` parks each login's session in its in-memory GoTrue
    store. ``auth.sign_out()`` acts on whatever session is parked there and
    revokes it with scope="global" — so one user's logout could revoke a
    DIFFERENT user's refresh tokens on every device, silently (the exception path
    below swallows it and still returns 200). ``_verify_password`` avoids the same
    trap by using a throwaway client; this route avoids it by not touching the
    shared client at all. Scope is "local" — /logout-all owns "global".
    """
    gotrue_ok = True
    redis_ok = True
    gotrue_err: Optional[Exception] = None
    redis_err: Optional[Exception] = None

    # Revoke only this session's refresh token. _raw_jwt is inside the try so a
    # malformed Authorization header degrades to a logged 200 rather than the 401
    # it raises — get_current_user has already validated the token by here, and a
    # logout that refuses to log you out is the one failure mode worth avoiding.
    try:
        await _gotrue_call(supabase.auth.admin.sign_out, _raw_jwt(request), "local")
    except Exception as e:
        gotrue_ok = False
        gotrue_err = e

    # Delete Redis session
    if redis is not None:
        try:
            await redis.delete(f"session:{current_user.auth_id}")
        except Exception as e:
            redis_ok = False
            redis_err = e

    if not (gotrue_ok and redis_ok):
        logger.warning(
            "Degraded logout (gotrue_ok=%s redis_ok=%s): gotrue_err=%s redis_err=%s",
            gotrue_ok,
            redis_ok,
            gotrue_err,
            redis_err,
        )

    return SuccessResponse(success=True)


# ============================================
# GET /me
# ============================================

@router.get("/me", response_model=UserProfileResponse)
async def me(
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """
    Return the authenticated user's profile from the users table.

    Never 403s for an account pending deletion — the frontend's blocking
    restore screen is driven by the deletion_* fields returned here.
    """
    def _fetch_profile():
        # plan_id comes from the user_subscriptions SSoT (embedded via the FK),
        # not the legacy users.plan_id mirror. subscription_tier is a dead column.
        return (
            supabase.table("users")
            .select("user_id, auth_id, email, full_name_ar, preferred_name, created_at, "
                    "deletion_requested_at, profession_group, profession_label, "
                    "user_subscriptions(plan_id)")
            .eq("auth_id", current_user.auth_id)
            .maybe_single()
            .execute()
        )

    try:
        # Run the sync Supabase query off the event loop (httpx is blocking).
        result = await run_db(_fetch_profile)
    except Exception as e:
        logger.exception("Error querying user profile: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ داخلي")

    if result is None or result.data is None:
        raise LunaHTTPException(status_code=404, code=ErrorCode.USER_NOT_FOUND, detail="الملف الشخصي غير موجود")

    profile = result.data
    # Embedded one-to-one may arrive as a dict or a single-element list.
    sub = profile.get("user_subscriptions")
    if isinstance(sub, list):
        sub = sub[0] if sub else None
    plan_id = (sub or {}).get("plan_id")
    deletion_requested_at = profile.get("deletion_requested_at")
    return UserProfileResponse(
        user_id=profile["user_id"],
        email=profile["email"],
        full_name_ar=profile.get("full_name_ar"),
        preferred_name=profile.get("preferred_name"),
        call_name=resolve_call_name(
            profile.get("preferred_name"), profile.get("full_name_ar")
        ),
        subscription_tier=None,  # legacy column retired — plan_id is the truth
        plan_id=plan_id,
        created_at=profile.get("created_at"),
        deletion_pending=bool(deletion_requested_at),
        deletion_requested_at=deletion_requested_at,
        purge_at=compute_purge_at(deletion_requested_at),
        # NULL from the DB (never asked) passes through as JSON null — that is
        # the frontend's signal to show the onboarding profession prompt.
        profession_group=profile.get("profession_group"),
        profession_label=profile.get("profession_label"),
    )


# ============================================
# PATCH /profession
# ============================================

@router.patch("/profession", response_model=ProfessionResponse)
async def update_profession(
    body: UpdateProfessionRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """
    Store the onboarding profession answer (migration 115).

    The label only exists for the specialist/individual groups — the other
    groups are single-tap answers, so any label sent with them is dropped.
    """
    label = (
        body.profession_label
        if body.profession_group in ("specialist", "individual")
        else None
    )

    def _update_profession():
        return (
            supabase.table("users")
            .update(
                {"profession_group": body.profession_group, "profession_label": label}
            )
            .eq("auth_id", current_user.auth_id)
            .execute()
        )

    try:
        result = await run_db(_update_profession)
    except Exception as e:
        logger.exception("Error updating profession: %s", e)
        raise LunaHTTPException(
            status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ داخلي"
        )

    if not result.data:
        raise LunaHTTPException(
            status_code=404, code=ErrorCode.USER_NOT_FOUND, detail="الملف الشخصي غير موجود"
        )

    row = result.data[0]
    return ProfessionResponse(
        profession_group=row["profession_group"],
        profession_label=row.get("profession_label"),
    )


# ============================================
# PATCH /preferred-name
# ============================================

@router.patch("/preferred-name", response_model=PreferredNameResponse)
async def update_preferred_name(
    body: UpdatePreferredNameRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """
    Store «بماذا تحب أن نناديك؟» (users.preferred_name — migration 122).

    Sending null (or an empty string) clears the override, and the response
    carries the derived default so the settings field refills with it in the
    same round trip. The name is normalised by the request model, not here —
    it reaches the router's instructions, so the cap and the control-character
    strip are part of validation.
    """
    preferred_name = body.preferred_name  # already cleaned → str | None

    def _update_preferred_name():
        return (
            supabase.table("users")
            .update({"preferred_name": preferred_name})
            .eq("auth_id", current_user.auth_id)
            .execute()
        )

    try:
        result = await run_db(_update_preferred_name)
    except Exception as e:
        logger.exception("Error updating preferred name: %s", e)
        raise LunaHTTPException(
            status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ داخلي"
        )

    if not result.data:
        raise LunaHTTPException(
            status_code=404, code=ErrorCode.USER_NOT_FOUND, detail="الملف الشخصي غير موجود"
        )

    row = result.data[0]
    return PreferredNameResponse(
        preferred_name=row.get("preferred_name"),
        call_name=resolve_call_name(
            row.get("preferred_name"), row.get("full_name_ar")
        ),
    )


# ============================================
# POST /delete-account
# ============================================

@router.post("/delete-account", response_model=SuccessResponse)
async def delete_account(
    body: DeleteAccountRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    redis: Optional[AsyncRedis] = Depends(get_redis),
):
    """
    Schedule the caller's account for deletion (30-day grace window).

    Password re-entry is required only for accounts that actually have a
    password identity — the server decides from live GoTrue state, not from
    what the client sent. Google-OAuth-only users confirm with the JWT alone.

    No global sign-out here: the user must keep a working session to reach the
    restore button during grace. Every data route is already blocked by the
    get_user_id gate.
    """
    needs_password = await run_db(
        has_password_identity, supabase, current_user.auth_id
    )

    if needs_password:
        if not body.password:
            raise LunaHTTPException(
                status_code=422,
                code=ErrorCode.VALIDATION_ERROR,
                detail="كلمة المرور مطلوبة",
            )
        await _verify_password(
            current_user.email,
            body.password,
            "كلمة المرور غير صحيحة",
        )

    await run_db(schedule_account_deletion, supabase, current_user.auth_id)
    await _drop_redis_session(redis, current_user.auth_id)

    return SuccessResponse(success=True)


# ============================================
# POST /restore-account
# ============================================

@router.post("/restore-account", response_model=SuccessResponse)
async def restore_account(
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """
    Cancel a pending deletion and reactivate the account. Idempotent.
    """
    await run_db(cancel_account_deletion, supabase, current_user.auth_id)
    return SuccessResponse(success=True)


# ============================================
# POST /change-password
# ============================================

@router.post("/change-password", response_model=SuccessResponse)
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """
    Change the caller's password after re-verifying the current one.

    The caller's own session survives; other devices are signed out.
    """
    if not await run_db(has_password_identity, supabase, current_user.auth_id):
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail="هذا الحساب مسجّل عبر Google ولا يملك كلمة مرور",
        )

    await _verify_password(
        current_user.email,
        body.current_password,
        "كلمة المرور الحالية غير صحيحة",
    )

    raw_jwt = _raw_jwt(request)

    try:
        await run_db(
            supabase.auth.admin.update_user_by_id,
            current_user.auth_id,
            {"password": body.new_password},
        )
    except AuthApiError as e:
        if e.status in (400, 422):
            # GoTrue rejected the new password itself (e.g. identical to the
            # current one) — a user error, not an outage.
            logger.info(
                "GoTrue rejected new password (status=%s code=%s)", e.status, e.code
            )
            raise LunaHTTPException(
                status_code=400,
                code=ErrorCode.VALIDATION_ERROR,
                detail="تعذّر تحديث كلمة المرور. اختر كلمة مرور مختلفة",
            )
        logger.error(
            "GoTrue API error during password update (status=%s code=%s)",
            e.status,
            e.code,
        )
        raise LunaHTTPException(
            status_code=503,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            detail=MSG_SERVICE_UNAVAILABLE,
        )
    except Exception as e:
        logger.exception("Unexpected password update error: %s", e)
        raise LunaHTTPException(
            status_code=503,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            detail=MSG_SERVICE_UNAVAILABLE,
        )

    # scope="others": the password is already changed, so a failure here must not
    # fail the request — but stolen sessions on other devices should die with it.
    try:
        await run_db(supabase.auth.admin.sign_out, raw_jwt, "others")
    except Exception as e:
        logger.warning(
            "Could not revoke other sessions after password change (auth_id=%s): %s",
            current_user.auth_id,
            e,
        )

    try:
        await run_db(
            _audit_account_event,
            supabase,
            current_user.auth_id,
            "update",
            "password_changed",
        )
    except Exception as e:
        logger.warning("Audit write failed after password change: %s", e)

    return SuccessResponse(success=True)


# ============================================
# POST /logout-all
# ============================================

@router.post("/logout-all", response_model=SuccessResponse)
async def logout_all(
    request: Request,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    redis: Optional[AsyncRedis] = Depends(get_redis),
):
    """
    Revoke every refresh token for the caller, including this device's.

    Unlike /logout, a failure here is a hard 503: the entire point is killing
    other sessions, so a silent 200 would be false safety.

    Already-issued access tokens still work until they expire (stateless JWT) —
    other devices die on their next refresh, within ~1h.
    """
    raw_jwt = _raw_jwt(request)

    try:
        await run_db(supabase.auth.admin.sign_out, raw_jwt, "global")
    except Exception as e:
        logger.error(
            "Global sign-out failed for auth_id=%s: %s", current_user.auth_id, e
        )
        raise LunaHTTPException(
            status_code=503,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            detail=MSG_SERVICE_UNAVAILABLE,
        )

    await _drop_redis_session(redis, current_user.auth_id)

    try:
        await run_db(
            _audit_account_event,
            supabase,
            current_user.auth_id,
            "update",
            "logout_all_devices",
        )
    except Exception as e:
        logger.warning("Audit write failed after logout-all: %s", e)

    return SuccessResponse(success=True)
