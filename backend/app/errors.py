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
