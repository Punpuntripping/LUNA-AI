"""
Structured error codes for Luna Legal AI.
All API errors return a consistent JSON envelope:
  { "error": { "code": "...", "message": "...", "status": 4xx }, "detail": "..." }
"""
from __future__ import annotations

from enum import Enum

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


class ErrorCode(str, Enum):
    """Machine-readable error codes returned in API responses."""

    # Auth
    AUTH_INVALID = "AUTH_INVALID"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    FORBIDDEN = "FORBIDDEN"  # authenticated but not permitted for this resource

    # Account lifecycle
    ACCOUNT_DELETION_PENDING = "ACCOUNT_DELETION_PENDING"  # 403 — in the 30-day grace window

    # Cases
    CASE_NOT_FOUND = "CASE_NOT_FOUND"
    CASE_INVALID_TYPE = "CASE_INVALID_TYPE"
    CASE_INVALID_STATUS = "CASE_INVALID_STATUS"
    CASE_INVALID_PRIORITY = "CASE_INVALID_PRIORITY"

    # Conversations
    CONV_NOT_FOUND = "CONV_NOT_FOUND"
    CONV_ACCESS_DENIED = "CONV_ACCESS_DENIED"

    # Documents
    DOC_NOT_FOUND = "DOC_NOT_FOUND"
    DOC_TOO_LARGE = "DOC_TOO_LARGE"
    DOC_INVALID_TYPE = "DOC_INVALID_TYPE"
    DOC_EMPTY = "DOC_EMPTY"
    DOC_MAGIC_MISMATCH = "DOC_MAGIC_MISMATCH"
    DOC_UPLOAD_FAILED = "DOC_UPLOAD_FAILED"

    # Resumable upload sessions (init → TUS → finalize)
    UPLOAD_NOT_COMPLETE = "UPLOAD_NOT_COMPLETE"
    UPLOAD_SIZE_MISMATCH = "UPLOAD_SIZE_MISMATCH"
    UPLOAD_INVALID_STATE = "UPLOAD_INVALID_STATE"

    # Memories
    MEMORY_NOT_FOUND = "MEMORY_NOT_FOUND"
    MEMORY_INVALID_TYPE = "MEMORY_INVALID_TYPE"

    # Messages
    MSG_SEND_FAILED = "MSG_SEND_FAILED"
    MSG_LIST_FAILED = "MSG_LIST_FAILED"

    # Artifacts
    ARTIFACT_NOT_FOUND = "ARTIFACT_NOT_FOUND"
    ARTIFACT_NOT_EDITABLE = "ARTIFACT_NOT_EDITABLE"

    # Blog-post generation jobs (internal editorial API)
    BLOG_JOB_NOT_FOUND = "BLOG_JOB_NOT_FOUND"

    # Preferences
    PREFERENCES_FAILED = "PREFERENCES_FAILED"

    # Templates
    TEMPLATE_NOT_FOUND = "TEMPLATE_NOT_FOUND"
    TEMPLATE_FAILED = "TEMPLATE_FAILED"

    # User
    USER_NOT_FOUND = "USER_NOT_FOUND"

    # Plan activation codes (redemption)
    CODE_INVALID = "CODE_INVALID"                # unknown / used / expired / capacity-full code
    CODE_ALREADY_REDEEMED = "CODE_ALREADY_REDEEMED"  # caller already redeemed THIS code
    PLAN_ALREADY_ACTIVE = "PLAN_ALREADY_ACTIVE"  # active paid plan can't be overwritten
    REDEEM_LOCKED = "REDEEM_LOCKED"              # too many failed attempts (24h wall)

    # Payments (Moyasar one-time checkout — moyasar_payments.md Phase C)
    PAYMENT_PLAN_NOT_PURCHASABLE = "PAYMENT_PLAN_NOT_PURCHASABLE"  # 400 — unknown plan / plans.price_sar IS NULL
    PAYMENT_DOWNGRADE_BLOCKED = "PAYMENT_DOWNGRADE_BLOCKED"        # 409 — a higher-ranked plan is still active
    PAYMENT_NOT_FOUND = "PAYMENT_NOT_FOUND"                        # 404 — no such payment for THIS caller (also: id unfetchable with our key)
    PAYMENT_REFUND_WINDOW_CLOSED = "PAYMENT_REFUND_WINDOW_CLOSED"  # 409 — past the 24h window, or the row is not in a refundable state
    PAYMENT_PROVIDER_ERROR = "PAYMENT_PROVIDER_ERROR"              # 400/502 — Moyasar refused / is unreachable / the payment disagrees with our row

    # Subscription lifecycle (إلغاء الاشتراك — subscription_cancellation.md)
    SUBSCRIPTION_NOT_CANCELLABLE = "SUBSCRIPTION_NOT_CANCELLABLE"  # 409 — no paid running term to cancel, or nothing to undo
    SUBSCRIPTION_ALREADY_CANCELLED = "SUBSCRIPTION_ALREADY_CANCELLED"  # 409 — renewal already opted out (never a second survey row)

    # Library entitlement (access tiers — Layer B refusals, all HTTP 402)
    LIBRARY_QUOTA_EXCEEDED = "LIBRARY_QUOTA_EXCEEDED"  # period allowance spent
    LIBRARY_FROZEN = "LIBRARY_FROZEN"                  # paid-era unlock, now on free
    LIBRARY_ANONYMOUS = "LIBRARY_ANONYMOUS"            # no account → 402, never 401
    LIBRARY_UNRESOLVABLE = "LIBRARY_UNRESOLVABLE"      # ref_id/item could not be resolved

    # Validation
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NO_UPDATE_DATA = "NO_UPDATE_DATA"
    INVALID_UUID = "INVALID_UUID"

    # Rate limiting
    RATE_LIMITED = "RATE_LIMITED"

    # Service availability (dependency failure ≠ user error)
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"

    # Generic
    INTERNAL_ERROR = "INTERNAL_ERROR"


# Canonical Arabic outage string — used by auth, deps, the DbDeadlineExceeded
# handler, and storage 503s. Defined module-level so every 503 path reuses it.
MSG_SERVICE_UNAVAILABLE = "الخدمة غير متاحة مؤقتاً، حاول مجدداً"


class LunaHTTPException(HTTPException):
    """HTTPException subclass that carries a structured ErrorCode."""

    def __init__(self, status_code: int, code: ErrorCode, detail: str, headers: dict | None = None):
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code


async def luna_exception_handler(request: Request, exc: LunaHTTPException):
    """Return structured error JSON for LunaHTTPException instances."""
    headers = getattr(exc, "headers", None) or {}
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code.value,
                "message": exc.detail,
                "status": exc.status_code,
            },
            "detail": exc.detail,  # backward compatibility
        },
        headers=headers,
    )


# ==========================================================================
# LIBRARY ENTITLEMENT REFUSALS (access-tiers plan, DECISIONS D14)
#
# ONE payload shape for every refused reveal — the library page gate, the
# reference-source endpoint (Phase C) and مكتبتي all return exactly this, so the
# frontend has a single branch. HTTP **402**, never 401: /library/full is
# reached from PUBLIC pages, and a 401 would trip the frontend's global
# redirect-to-login and eject a browsing anon visitor.
#
# Copy is framed as a plan feature, never a scolding (D10). The meter string
# itself lives in shared.quota so every surface that mentions فتح المصادر reads
# the same constant.
# ==========================================================================

LIBRARY_REFUSAL_STATUS = 402

MSG_LIBRARY_ANONYMOUS = "سجّل مجاناً لعرض النص كاملاً"
MSG_LIBRARY_FROZEN = "هذا المصدر محفوظ في مكتبتك — رقِّ باقتك لفتحه من جديد."
MSG_LIBRARY_UNRESOLVABLE = "تعذّر تحديد المصدر المطلوب."
MSG_LIBRARY_LOCKED = "حسابك غير مفعّل بعد. تواصل معنا لتفعيل اشتراكك."


def _frozen_message(stored_count: int) -> str:
    """«لديك {n} مصدراً محفوظاً …» when we know the shelf size (D10), else the
    generic per-item line. The count is the whole point of the CTA — it is what
    makes the upgrade prompt concrete (§5B.4)."""
    n = int(stored_count or 0)
    if n > 0:
        return f"لديك {n} مصدراً محفوظاً في مكتبتك — رقِّ باقتك لفتحها من جديد."
    return MSG_LIBRARY_FROZEN


# reason (AccessDecision.reason) → (ErrorCode, Arabic message)
_LIBRARY_REFUSAL_CODES: dict[str, ErrorCode] = {
    "anonymous": ErrorCode.LIBRARY_ANONYMOUS,
    "quota_exhausted": ErrorCode.LIBRARY_QUOTA_EXCEEDED,
    "frozen_library": ErrorCode.LIBRARY_FROZEN,
    "unresolvable": ErrorCode.LIBRARY_UNRESOLVABLE,
    "locked": ErrorCode.LIBRARY_QUOTA_EXCEEDED,
}


def library_refusal_payload(decision) -> dict:
    """Build the D14 refusal body from a ``library_service.AccessDecision``.

    Duck-typed on purpose (``reason``/``used``/``limit``/``resets_at``/
    ``stored_count``) so ``backend.app.errors`` stays import-free of the service
    layer — errors.py is imported by nearly every module, including
    library_service itself.
    """
    # Imported lazily: shared.quota pulls redis+supabase, and errors.py is on
    # the import path of essentially the whole backend.
    from shared.quota import LIBRARY_QUOTA_EXHAUSTED_AR

    reason = str(getattr(decision, "reason", "") or "unresolvable")
    stored_count = int(getattr(decision, "stored_count", 0) or 0)
    code = _LIBRARY_REFUSAL_CODES.get(reason, ErrorCode.LIBRARY_UNRESOLVABLE)

    if reason == "anonymous":
        message = MSG_LIBRARY_ANONYMOUS
    elif reason == "frozen_library":
        message = _frozen_message(stored_count)
    elif reason == "quota_exhausted":
        message = LIBRARY_QUOTA_EXHAUSTED_AR
    elif reason == "locked":
        message = MSG_LIBRARY_LOCKED
    else:
        message = MSG_LIBRARY_UNRESOLVABLE

    resets_at = getattr(decision, "resets_at", None)
    body: dict = {
        "error": {
            "code": code.value,
            "message": message,
            "status": LIBRARY_REFUSAL_STATUS,
        },
        "detail": message,          # backward compatibility with the envelope
        "reason": reason,
        "used": int(getattr(decision, "used", 0) or 0),
        "limit": getattr(decision, "limit", None),
        "resets_at": resets_at.isoformat() if hasattr(resets_at, "isoformat") else resets_at,
    }
    if reason == "frozen_library":
        body["stored_count"] = stored_count
    return body


def library_refusal_response(decision) -> JSONResponse:
    """The 402 JSONResponse for a refused reveal (D14).

    ``Cache-Control: private, no-store`` is set here and not left to the caller:
    a refusal is a per-USER answer, and one of these landing in the shared ISR /
    CDN cache would pin somebody else's exhausted quota onto every visitor.
    """
    return JSONResponse(
        status_code=LIBRARY_REFUSAL_STATUS,
        content=library_refusal_payload(decision),
        headers={"Cache-Control": "private, no-store"},
    )
