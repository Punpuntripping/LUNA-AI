"""«رسائل ريحان» — /api/v1/announcements (.claude/plans/announcements.md).

    GET  /api/v1/announcements        → {"items": [...], "unread_count": n}
                                        newest first, ≤ 50, only what the caller
                                        may see (everyone / paying subscribers)
    POST /api/v1/announcements/read   body {ids: [uuid, ...]} (1–50) → 204
                                        idempotent; unseen ids are ignored

Both AUTHED. The audience rule lives in the ``get_user_announcements`` RPC
(migration 189); see ``announcement_service``. Shapes are mirrored in
``frontend/lib/contracts/announcements.ts`` for the web and the app.
"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from supabase import Client as SupabaseClient

from backend.app.deps import get_current_user, get_supabase
from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.services import announcement_service
from backend.app.services.case_service import get_user_id
from shared.auth.jwt import AuthUser
from shared.db.run import run_db

logger = logging.getLogger(__name__)

router = APIRouter()

MSG_LIST_FAILED = "تعذّر تحميل الرسائل، حاول مجدداً"
MSG_READ_FAILED = "تعذّر تحديث حالة الرسائل، حاول مجدداً"


class MarkReadRequest(BaseModel):
    ids: list[UUID] = Field(..., min_length=1, max_length=50)


@router.get("/announcements")
async def list_announcements(
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
) -> dict:
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    try:
        return await run_db(announcement_service.list_for_user, supabase, user_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("announcements list failed: %s", e)
        raise LunaHTTPException(
            status_code=500, code=ErrorCode.INTERNAL_ERROR, detail=MSG_LIST_FAILED,
        )


@router.post("/announcements/read", status_code=204)
async def mark_announcements_read(
    body: MarkReadRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
) -> Response:
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    try:
        await run_db(
            announcement_service.mark_read,
            supabase,
            user_id,
            [str(i) for i in body.ids],
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("announcements mark-read failed: %s", e)
        raise LunaHTTPException(
            status_code=500, code=ErrorCode.INTERNAL_ERROR, detail=MSG_READ_FAILED,
        )
    return Response(status_code=204)
