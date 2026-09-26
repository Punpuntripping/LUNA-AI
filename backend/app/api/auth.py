"""
Auth API routes — /api/v1/auth/
13 endpoints: login, otp/request, otp/verify, refresh, logout, me, profession,
preferred-name, delete-account, restore-account, change-password, set-password,
logout-all

(Signup runs client-side via supabase.auth.signUp() — see the note above /refresh.)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field, field_validator
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
    SetPasswordRequest,
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
    has_password,
    schedule_account_deletion,
)
from backend.app.services.audit_service import write_audit_log
from backend.app.services.subscription_service import resolve_paid_activated_at
from shared.auth.jwt import AuthUser
from shared.config import get_settings
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

    # No RPC needed: this route IS the password grant. Reaching this line means
    # sign_in_with_password just succeeded, so the account demonstrably has
    # one. Google users never arrive here — they come through /auth/callback
    # and get the resolved value from /me.
    return await _build_login_response(
        session, user, supabase=supabase, redis=redis, has_password=True
    )


async def _build_login_response(
    session,
    user,
    *,
    supabase: SupabaseClient,
    redis: Optional[AsyncRedis],
    has_password: bool,
) -> LoginResponse:
    """Shared tail of every grant that hands the browser a session.

    Used by /login (password grant) and /otp/verify (email code). Creates the
    best-effort Redis session, reads the users-row state (names, deletion
    grace, profession) and assembles the ``LoginResponse`` — so both grants
    produce byte-identical shapes and the frontend runs one post-login path.

    ``has_password`` is the caller's to decide: the password grant proves one
    exists; an OTP grant proves nothing and must resolve it like /me does.
    """
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
            has_password=has_password,
            profession_group=profession_group,
            profession_label=profession_label,
        ),
    )


# ============================================
# POST /otp/request + POST /otp/verify — «الدخول برمز عبر البريد»
# ============================================
#
# .claude/plans/email_otp_login.md, Phase A (dev-gated). An emailed code typed
# INTO the app — no redirect, so it survives the installed-PWA → Safari bounce
# that breaks Google OAuth there.
#
# Gate: ``EMAIL_OTP_ALLOWED_EMAILS`` (Settings.email_otp_allowlist). Empty/unset
# => feature off. Not on the list => /otp/request answers exactly like a real
# send and GoTrue is never called; /otp/verify answers exactly like a wrong
# code. Membership must never be observable from the response, which is also
# why the per-email limiters run BEFORE the allowlist check — otherwise only
# listed emails could ever earn a 429.
#
# Per-IP limiting is not repeated here: RateLimitMiddleware already meters every
# /api/v1/auth/* path per resolved client IP (AUTH_RATE_LIMIT per minute).
#
# Both GoTrue calls run on a THROWAWAY anon client, never the shared
# ``app.state.supabase_auth``: sign_in_with_otp and verify_otp both call
# ``_remove_session()`` on the client, and verify_otp then PARKS the new session
# in it — on the shared singleton that would clobber whatever another request
# parked there (same trap as _verify_password / /logout above).

OTP_REQUEST_COOLDOWN_S = 60          # 1 request / 60s per email
OTP_REQUEST_HOURLY_MAX = 5           # 5 requests / hour per email
OTP_REQUEST_HOURLY_WINDOW_S = 3600
OTP_VERIFY_FAIL_MAX = 5              # 5 failed verifies / 15 min per email → 429
OTP_VERIFY_FAIL_WINDOW_S = 900

MSG_OTP_REQUEST_RATE_LIMITED = "انتظر قليلاً قبل طلب رمز جديد"
MSG_OTP_INVALID = "الرمز غير صحيح أو منتهي الصلاحية"
MSG_OTP_VERIFY_LOCKED = "محاولات كثيرة غير صحيحة — انتظر قليلاً ثم حاول مجدداً"

_OTP_REQ_COOLDOWN_KEY = "otp:req:cd:{}"
_OTP_REQ_HOURLY_KEY = "otp:req:hr:{}"
_OTP_VERIFY_FAIL_KEY = "otp:verify:fails:{}"


class OtpRequestBody(BaseModel):
    """POST /api/v1/auth/otp/request"""
    email: EmailStr


class OtpVerifyBody(BaseModel):
    """POST /api/v1/auth/otp/verify

    The code length is whatever GoTrue is configured for (6 today) — accepted as
    6–10 digits so a dashboard change does not need a backend deploy.
    """
    email: EmailStr
    code: str = Field(..., pattern=r"^[0-9]{6,10}$")

    @field_validator("code", mode="before")
    @classmethod
    def _strip_code(cls, v):
        # iOS one-time-code autofill / paste can carry spaces.
        return "".join(v.split()) if isinstance(v, str) else v


class OtpRequestResponse(BaseModel):
    sent: bool = True


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _email_key(email: str) -> str:
    """Stable, non-reversible handle for an email — Redis keys and logs only.

    The raw address never goes into a log line or a Redis key name.
    """
    return hashlib.sha256(email.encode("utf-8")).hexdigest()[:24]


def _email_domain(email: str) -> str:
    return email.rpartition("@")[2]


def _otp_allowed(email: str) -> bool:
    return email in get_settings().email_otp_allowlist


async def _otp_request_limited(redis: Optional[AsyncRedis], ekey: str) -> bool:
    """True when this email has asked too recently / too often. Fails OPEN.

    A Redis outage must not lock testers out of login; GoTrue's own email rate
    limit and the per-IP middleware still bound abuse.
    """
    if redis is None:
        logger.warning("auth.otp.request: Redis unavailable — per-email limit skipped (fail-open)")
        return False
    try:
        # 1 / 60s: SET NX is the whole cooldown — present key == too soon.
        first = await redis.set(
            _OTP_REQ_COOLDOWN_KEY.format(ekey), "1", nx=True, ex=OTP_REQUEST_COOLDOWN_S
        )
        if not first:
            return True
        hourly_key = _OTP_REQ_HOURLY_KEY.format(ekey)
        n = await redis.incr(hourly_key)
        if n == 1:
            await redis.expire(hourly_key, OTP_REQUEST_HOURLY_WINDOW_S)
        return n > OTP_REQUEST_HOURLY_MAX
    except Exception as e:  # noqa: BLE001
        logger.warning("auth.otp.request: Redis limiter error — fail-open: %s", e)
        return False


async def _otp_verify_locked(redis: Optional[AsyncRedis], ekey: str) -> bool:
    """True once OTP_VERIFY_FAIL_MAX wrong codes landed inside the window.

    Fails open on a Redis outage: GoTrue's own verify limit and the per-IP
    middleware remain, and a 6-digit code expires in 10 minutes.
    """
    if redis is None:
        logger.warning("auth.otp.verify: Redis unavailable — fail counter skipped (fail-open)")
        return False
    try:
        val = await redis.get(_OTP_VERIFY_FAIL_KEY.format(ekey))
        return val is not None and int(val) >= OTP_VERIFY_FAIL_MAX
    except Exception as e:  # noqa: BLE001
        logger.warning("auth.otp.verify: Redis lock check failed — fail-open: %s", e)
        return False


async def _otp_verify_record_fail(redis: Optional[AsyncRedis], ekey: str) -> None:
    if redis is None:
        return
    try:
        key = _OTP_VERIFY_FAIL_KEY.format(ekey)
        n = await redis.incr(key)
        if n == 1:  # first fail in this window — start the 15-min clock
            await redis.expire(key, OTP_VERIFY_FAIL_WINDOW_S)
    except Exception as e:  # noqa: BLE001
        logger.warning("auth.otp.verify: fail counter incr failed: %s", e)


async def _otp_verify_clear_fails(redis: Optional[AsyncRedis], ekey: str) -> None:
    if redis is None:
        return
    try:
        await redis.delete(_OTP_VERIFY_FAIL_KEY.format(ekey))
    except Exception as e:  # noqa: BLE001
        logger.warning("auth.otp.verify: fail counter clear failed: %s", e)


def _audit_otp_by_email(supabase: SupabaseClient, email: str, event: str) -> None:
    """Best-effort audit row for an OTP event keyed only by email.

    Written only when the email resolves to a users row — audit_logs.user_id is
    a users FK, and an unknown address has nothing to attach to (the log line
    carries the event either way). IP/UA come from the ambient request context
    (resolve_client_ip), never from here.
    """
    result = (
        supabase.table("users")
        .select("user_id")
        .eq("email", email)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    if not rows:
        return
    user_id = rows[0]["user_id"]
    write_audit_log(
        supabase,
        user_id=user_id,
        action="login",
        resource_type="account",
        resource_id=user_id,
        metadata={"event": event},
    )


async def _audit_otp(supabase: SupabaseClient, email: str, event: str) -> None:
    try:
        await run_db(_audit_otp_by_email, supabase, email, event)
    except Exception as e:  # noqa: BLE001
        logger.warning("Audit write failed for %s: %s", event, e)


def _gotrue_unavailable() -> LunaHTTPException:
    return LunaHTTPException(
        status_code=503,
        code=ErrorCode.SERVICE_UNAVAILABLE,
        detail=MSG_SERVICE_UNAVAILABLE,
    )


async def _close_client(client: SupabaseClient) -> None:
    try:
        await asyncio.to_thread(client.auth.close)
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not close OTP auth client: %s", e)


@router.post("/otp/request", response_model=OtpRequestResponse)
async def otp_request(
    body: OtpRequestBody,
    supabase: SupabaseClient = Depends(get_supabase),
    redis: Optional[AsyncRedis] = Depends(get_redis),
):
    """Email a login code to an EXISTING, allowlisted account.

    Always ``{"sent": true}`` for well-formed input — allowlisted or not,
    existing account or not. 429 when this email asked too recently/often, 503
    when GoTrue itself is unavailable (same mapping as /login).
    """
    email = _normalize_email(body.email)
    ekey = _email_key(email)

    if await _otp_request_limited(redis, ekey):
        raise LunaHTTPException(
            status_code=429,
            code=ErrorCode.RATE_LIMITED,
            detail=MSG_OTP_REQUEST_RATE_LIMITED,
            headers={"Retry-After": str(OTP_REQUEST_COOLDOWN_S)},
        )

    if not _otp_allowed(email):
        logger.info(
            "auth.otp.not_allowed email_hash=%s domain=%s", ekey, _email_domain(email)
        )
        return OtpRequestResponse(sent=True)

    client = await asyncio.to_thread(create_isolated_anon_client)
    try:
        await _gotrue_call(
            client.auth.sign_in_with_otp,
            # No signups through this path — an unknown email is a GoTrue 4xx,
            # which is swallowed into the same generic 200 below.
            {"email": email, "options": {"should_create_user": False}},
        )
    except (AuthRetryableError, TimeoutError) as e:
        logger.error("GoTrue unavailable during otp request: %s", e)
        raise _gotrue_unavailable()
    except AuthApiError as e:
        if e.status is not None and e.status >= 500:
            logger.error(
                "GoTrue API error during otp request (status=%s code=%s)",
                e.status,
                e.code,
            )
            raise _gotrue_unavailable()
        # User not found / signups disabled / GoTrue's own email limit — never
        # reveal which: same 200 as a real send.
        logger.info(
            "auth.otp.request.gotrue_rejected email_hash=%s status=%s code=%s",
            ekey,
            e.status,
            e.code,
        )
        return OtpRequestResponse(sent=True)
    except Exception as e:
        logger.exception("Unexpected otp request error: %s", e)
        raise _gotrue_unavailable()
    finally:
        await _close_client(client)

    logger.info("auth.otp.requested email_hash=%s", ekey)
    await _audit_otp(supabase, email, "auth.otp.requested")
    return OtpRequestResponse(sent=True)


@router.post("/otp/verify", response_model=LoginResponse)
async def otp_verify(
    body: OtpVerifyBody,
    supabase: SupabaseClient = Depends(get_supabase),
    redis: Optional[AsyncRedis] = Depends(get_redis),
):
    """Exchange an emailed code for a session — the SAME LoginResponse as /login.

    401 on a wrong/expired code or a non-allowlisted email (indistinguishable);
    429 once OTP_VERIFY_FAIL_MAX wrong codes land inside 15 minutes.
    """
    email = _normalize_email(body.email)
    ekey = _email_key(email)

    if await _otp_verify_locked(redis, ekey):
        raise LunaHTTPException(
            status_code=429,
            code=ErrorCode.RATE_LIMITED,
            detail=MSG_OTP_VERIFY_LOCKED,
            headers={"Retry-After": str(OTP_VERIFY_FAIL_WINDOW_S)},
        )

    async def _reject(reason: str) -> LunaHTTPException:
        await _otp_verify_record_fail(redis, ekey)
        logger.info("auth.otp.failed email_hash=%s reason=%s", ekey, reason)
        if reason != "not_allowed":
            await _audit_otp(supabase, email, "auth.otp.failed")
        return LunaHTTPException(
            status_code=401,
            code=ErrorCode.AUTH_INVALID,
            detail=MSG_OTP_INVALID,
        )

    # Defence in depth: the request route already refuses to send to unlisted
    # addresses, but a code must never be accepted for one either.
    if not _otp_allowed(email):
        raise await _reject("not_allowed")

    client = await asyncio.to_thread(create_isolated_anon_client)
    try:
        response = await _gotrue_call(
            client.auth.verify_otp,
            {"email": email, "token": body.code, "type": "email"},
        )
    except (AuthRetryableError, TimeoutError) as e:
        logger.error("GoTrue unavailable during otp verify: %s", e)
        raise _gotrue_unavailable()
    except AuthApiError as e:
        if e.status in (400, 401, 403, 404, 422):
            raise await _reject(f"gotrue_{e.status}")
        if e.status == 429:
            # GoTrue's own verify limit — surface it in Arabic, don't count it.
            raise LunaHTTPException(
                status_code=429,
                code=ErrorCode.RATE_LIMITED,
                detail=MSG_OTP_VERIFY_LOCKED,
                headers={"Retry-After": str(OTP_VERIFY_FAIL_WINDOW_S)},
            )
        logger.error(
            "GoTrue API error during otp verify (status=%s code=%s)", e.status, e.code
        )
        raise _gotrue_unavailable()
    except Exception as e:
        logger.exception("Unexpected otp verify error: %s", e)
        raise _gotrue_unavailable()
    finally:
        await _close_client(client)

    session = response.session
    user = response.user
    if session is None or user is None:
        raise await _reject("no_session")

    await _otp_verify_clear_fails(redis, ekey)

    # An OTP grant proves nothing about a password — resolve it like /me does
    # (migration 141 RPC), degrading to False: that only ever OFFERS «تعيين
    # كلمة مرور», and /set-password re-checks server-side (409) anyway.
    try:
        password_set = await run_db(has_password, supabase, user.id)
    except Exception as e:
        logger.warning("Could not resolve has_password during otp verify: %s", e)
        password_set = False

    logger.info("auth.otp.verified email_hash=%s", ekey)
    try:
        await run_db(_audit_account_event, supabase, user.id, "login", "auth.otp.verified")
    except Exception as e:  # noqa: BLE001
        logger.warning("Audit write failed for otp_verified: %s", e)

    return await _build_login_response(
        session, user, supabase=supabase, redis=redis, has_password=password_set
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
        # The four columns beside it are what `resolve_paid_activated_at` needs to
        # answer "did this account BUY a plan, and when" — a question `plan_id`
        # alone cannot answer (a dev grant and a purchase look identical to it).
        return (
            supabase.table("users")
            .select("user_id, auth_id, email, full_name_ar, preferred_name, created_at, "
                    "deletion_requested_at, profession_group, profession_label, "
                    "user_subscriptions(plan_id, source, started_at, expires_at, "
                    "usage_reset_at)")
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

    # Whether a password exists lives in GoTrue's schema, not in `users`, so it
    # cannot ride the select above. A failure here must not break /me — the
    # frontend's blocking restore screen depends on this route answering — so it
    # degrades to False, which only ever OFFERS «تعيين كلمة مرور» to someone who
    # may already have one. That is safe: /set-password re-checks server-side
    # and refuses (409) when a password already exists.
    try:
        password_set = await run_db(has_password, supabase, current_user.auth_id)
    except Exception as e:
        logger.warning("Could not resolve has_password during /me: %s", e)
        password_set = False

    profile = result.data
    # Embedded one-to-one may arrive as a dict or a single-element list.
    sub = profile.get("user_subscriptions")
    if isinstance(sub, list):
        sub = sub[0] if sub else None
    plan_id = (sub or {}).get("plan_id")
    paid_activated_at = resolve_paid_activated_at(sub)
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
        paid_activated_at=paid_activated_at,
        created_at=profile.get("created_at"),
        deletion_pending=bool(deletion_requested_at),
        deletion_requested_at=deletion_requested_at,
        purge_at=compute_purge_at(deletion_requested_at),
        has_password=password_set,
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
        has_password, supabase, current_user.auth_id
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
    if not await run_db(has_password, supabase, current_user.auth_id):
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail="لا توجد كلمة مرور لهذا الحساب — استخدم «تعيين كلمة مرور»",
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
# POST /set-password
# ============================================

@router.post("/set-password", response_model=SuccessResponse)
async def set_password(
    body: SetPasswordRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """
    Set a FIRST password on an account that has none (Google-OAuth-only users).

    There is no current-password to re-enter, so the only thing standing between
    a caller and a new credential is the JWT — which is why this route refuses
    the moment the server sees an existing password (409). Without that guard it
    would be a password-overwrite endpoint that skips re-authentication, i.e. a
    session-hijack escalation: steal an access token, own the account forever.
    ``has_password`` fails CLOSED, so a GoTrue/DB wobble 503s rather than
    reporting "no password" and opening exactly that hole.

    Note this creates a "ghost password": GoTrue writes the credential without
    adding an ``email`` identity, so password sign-in starts working while
    ``user.identities`` still reads ["google"]. That is why every reader in this
    codebase goes through ``has_password`` (migration 141) and never through the
    identity list — see the migration header.
    """
    if await run_db(has_password, supabase, current_user.auth_id):
        raise LunaHTTPException(
            status_code=409,
            code=ErrorCode.VALIDATION_ERROR,
            detail="هذا الحساب يملك كلمة مرور بالفعل — استخدم «تغيير كلمة المرور»",
        )

    try:
        await run_db(
            supabase.auth.admin.update_user_by_id,
            current_user.auth_id,
            {"password": body.new_password},
        )
    except AuthApiError as e:
        if e.status in (400, 422):
            # GoTrue rejected the password itself (too weak, breached in HIBP if
            # that check is on) — a user error, not an outage.
            logger.info(
                "GoTrue rejected new password on set-password (status=%s code=%s)",
                e.status,
                e.code,
            )
            raise LunaHTTPException(
                status_code=400,
                code=ErrorCode.VALIDATION_ERROR,
                detail="تعذّر تعيين كلمة المرور. اختر كلمة مرور أقوى",
            )
        logger.error(
            "GoTrue API error during set-password (status=%s code=%s)",
            e.status,
            e.code,
        )
        raise LunaHTTPException(
            status_code=503,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            detail=MSG_SERVICE_UNAVAILABLE,
        )
    except Exception as e:
        logger.exception("Unexpected error during set-password: %s", e)
        raise LunaHTTPException(
            status_code=503,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            detail=MSG_SERVICE_UNAVAILABLE,
        )

    # Deliberately NO sign-out of other sessions, unlike /change-password. There
    # this account had a password that may have leaked, so other sessions are
    # suspect; here the user is ADDING a first credential to sessions they own,
    # and killing their other devices would punish them for improving security.
    try:
        await run_db(
            _audit_account_event,
            supabase,
            current_user.auth_id,
            "update",
            "password_set",
        )
    except Exception as e:
        logger.warning("Audit write failed after set-password: %s", e)

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
