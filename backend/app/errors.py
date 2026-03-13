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

    # Memories
    MEMORY_NOT_FOUND = "MEMORY_NOT_FOUND"
    MEMORY_INVALID_TYPE = "MEMORY_INVALID_TYPE"

    # Messages
    MSG_SEND_FAILED = "MSG_SEND_FAILED"
    MSG_LIST_FAILED = "MSG_LIST_FAILED"

    # Artifacts
    ARTIFACT_NOT_FOUND = "ARTIFACT_NOT_FOUND"
    ARTIFACT_NOT_EDITABLE = "ARTIFACT_NOT_EDITABLE"

    # Templates
    TEMPLATE_NOT_FOUND = "TEMPLATE_NOT_FOUND"
    TEMPLATE_INVALID_AGENT = "TEMPLATE_INVALID_AGENT"

    # Preferences
    PREFERENCES_FAILED = "PREFERENCES_FAILED"

    # User
    USER_NOT_FOUND = "USER_NOT_FOUND"

    # Validation
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NO_UPDATE_DATA = "NO_UPDATE_DATA"
    INVALID_UUID = "INVALID_UUID"

    # Rate limiting
    RATE_LIMITED = "RATE_LIMITED"

    # Generic
    INTERNAL_ERROR = "INTERNAL_ERROR"


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
