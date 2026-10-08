"""Document export — ``POST /api/v1/export`` (PDF / Word download).

Plan: ``.claude/plans/document_export_pdf_word.md`` § Contract.

Request JSON::

    {"format": "pdf" | "docx", "title": "≤ 200 chars", "markdown": "≤ 400_000 chars"}

``markdown`` is exactly what the workspace action bar's «نسخ» copies (body +
«المراجع» block); the server renders it as-is and never re-derives references.

Responses:
    200  the file — application/pdf or the .docx media type, with
         ``Content-Disposition: attachment; filename="document.<ext>";
         filename*=UTF-8''<percent-encoded sanitized title>.<ext>``
    401  no / bad token (``get_current_user``)
    413  body or markdown over the cap
    422  malformed JSON / unknown format / title too long / empty markdown
    429  per-user export budget (20/min, ``RouteRateLimiter`` scope ``export``)
    503  renderer unavailable («تعذّر إنشاء الملف حالياً، حاول مرة أخرى»)

The body is parsed by hand (not a FastAPI body parameter) for two reasons:
the 413/422 split must be ours — a Pydantic ``max_length`` failure would
surface as a generic English 422 — and an oversize body is refused from
``Content-Length`` before it is read.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from backend.app.deps import get_current_user
from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.middleware.route_limits import RouteRateLimiter
from backend.app.services import export_service
from shared.auth.jwt import AuthUser

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

# 400k chars of markdown is at most ~2.4 MB of JSON even if every char arrives
# \u-escaped (6 bytes); plus the title and envelope. Anything bigger cannot be
# a valid request, so it is refused before the body is read.
MAX_BODY_BYTES = export_service.MAX_MARKDOWN_CHARS * 6 + 64_000

EXPORT_RATE_LIMIT = 20
EXPORT_RATE_WINDOW_SECONDS = 60

# Its own scope ⇒ its own budget: exporting must not eat the library reveal
# budget, and vice versa. Keyed on the VERIFIED user (fail-closed limiter).
export_rate_limit = RouteRateLimiter(
    scope="export",
    limit=EXPORT_RATE_LIMIT,
    window_seconds=EXPORT_RATE_WINDOW_SECONDS,
)

_render_slots = asyncio.Semaphore(export_service.RENDER_CONCURRENCY)

# ---------------------------------------------------------------------------
# Arabic messages
# ---------------------------------------------------------------------------

MSG_EXPORT_INVALID = "طلب التصدير غير صالح"
MSG_EXPORT_FORMAT = "صيغة التصدير غير مدعومة"
MSG_EXPORT_TITLE = "العنوان طويل جداً (الحد الأقصى 200 حرف)"
MSG_EXPORT_EMPTY = "لا يوجد محتوى للتصدير"
MSG_EXPORT_TOO_LARGE = "المستند أكبر من الحد المسموح للتصدير"
MSG_EXPORT_UNAVAILABLE = "تعذّر إنشاء الملف حالياً، حاول مرة أخرى"


class ExportRequest(BaseModel):
    """``POST /api/v1/export`` body."""

    model_config = ConfigDict(extra="ignore")

    format: Literal["pdf", "docx"]
    title: str = Field(default="", max_length=export_service.MAX_TITLE_CHARS)
    markdown: str = Field(..., max_length=export_service.MAX_MARKDOWN_CHARS)


def _invalid(detail: str) -> LunaHTTPException:
    return LunaHTTPException(status_code=422, code=ErrorCode.VALIDATION_ERROR, detail=detail)


def _too_large() -> LunaHTTPException:
    return LunaHTTPException(
        status_code=413, code=ErrorCode.VALIDATION_ERROR, detail=MSG_EXPORT_TOO_LARGE
    )


async def _read_request(request: Request) -> ExportRequest:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > MAX_BODY_BYTES:
                raise _too_large()
        except ValueError:
            raise _invalid(MSG_EXPORT_INVALID)

    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise _too_large()

    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise _invalid(MSG_EXPORT_INVALID)
    if not isinstance(payload, dict):
        raise _invalid(MSG_EXPORT_INVALID)

    try:
        req = ExportRequest.model_validate(payload)
    except ValidationError as exc:
        errors = exc.errors()
        fields = {str(e["loc"][0]) for e in errors if e.get("loc")}
        if any(
            e.get("loc") and e["loc"][0] == "markdown" and e.get("type") == "string_too_long"
            for e in errors
        ):
            raise _too_large()
        if "format" in fields:
            raise _invalid(MSG_EXPORT_FORMAT)
        if "title" in fields:
            raise _invalid(MSG_EXPORT_TITLE)
        raise _invalid(MSG_EXPORT_INVALID)

    if not req.markdown.strip():
        raise _invalid(MSG_EXPORT_EMPTY)
    return req


@router.post(
    "/export",
    response_class=Response,
    responses={
        200: {
            "content": {
                export_service.MEDIA_TYPES["pdf"]: {},
                export_service.MEDIA_TYPES["docx"]: {},
            },
            "description": "The rendered file (attachment).",
        }
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": ExportRequest.model_json_schema()}},
        }
    },
)
async def export_document(
    request: Request,
    current_user: AuthUser = Depends(get_current_user),
    _rl=Depends(export_rate_limit),
) -> Response:
    """Render the caller's markdown to a PDF or Word file and return it."""
    req = await _read_request(request)

    async with _render_slots:
        try:
            data = await run_in_threadpool(
                export_service.render, req.format, req.markdown, req.title
            )
        except export_service.ExportUnavailableError:
            raise LunaHTTPException(
                status_code=503,
                code=ErrorCode.SERVICE_UNAVAILABLE,
                detail=MSG_EXPORT_UNAVAILABLE,
                headers={"Retry-After": "30"},
            )

    logger.info(
        "export: user=%s format=%s md_chars=%d bytes=%d",
        current_user.auth_id,
        req.format,
        len(req.markdown),
        len(data),
    )
    return Response(
        content=data,
        media_type=export_service.MEDIA_TYPES[req.format],
        headers={
            "Content-Disposition": export_service.content_disposition(req.title, req.format),
            "Cache-Control": "private, no-store",
        },
    )
