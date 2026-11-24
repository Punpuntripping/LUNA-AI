"""
«ملفاتي» API routes -- /api/v1/files (.claude/plans/my_files.md §5, §11).

Endpoints (Phase 1 + 1b):
    GET    /files/status           is the feature on for the caller? (always 200)
    GET    /files                  the user's library (light rows)
    GET    /files/{item_id}        one file: text, full summary, sections
    GET    /files/angle-maps       «الأوجه»: the caller's angle maps (?conversation_id=)
    GET    /files/angle-maps/{id}  «الأوجه»: one map in full
                                   (both also behind ANGLE_MAP_UI_*; angle_map_ui.md)
    DELETE /files/{item_id}        «حذف من ملفاتي» — a stamp, never a delete (204)
    POST   /files/match            the same file, already uploaded? (Phase 1b)

Behind ``MY_FILES_ENABLED`` / ``MY_FILES_USERS``: when the feature is off for
the caller every route answers 404. Ownership is by ``users.user_id`` — another
user's id answers the same 404 as a missing one. Rate limiting is the global
middleware's, exactly as for the workspace routes (no route-level limiter).
Responses are per-user documents: ``Cache-Control: private, no-store``.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Response
from supabase import Client as SupabaseClient

from backend.app.deps import get_current_user, get_supabase, validate_uuid
from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.models.requests import FileMatchRequest
from backend.app.models.responses import (
    AngleMapListResponse,
    AngleMapOut,
    FileMatch,
    FileMatchResponse,
    FilesStatusResponse,
    LibraryFileDetail,
    LibraryFileListResponse,
)
from backend.app.services import angle_maps_service, files_service
from backend.app.services.case_service import get_user_id
from shared.auth.jwt import AuthUser
from shared.db.run import run_db

logger = logging.getLogger(__name__)

router = APIRouter()

_NO_STORE = "private, no-store"

MSG_FEATURE_OFF = "هذه الميزة غير متاحة"
MSG_FILE_NOT_FOUND = "الملف غير موجود"


async def files_user(
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
) -> str:
    """The caller's ``users.user_id`` — or 404 when «ملفاتي» is off for them.

    ``get_user_id`` also carries the account-deactivation gate (403 during the
    deletion grace window), like every other data route.
    """
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    if not files_service.my_files_enabled(user_id):
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.FEATURE_OFF,
            detail=MSG_FEATURE_OFF,
        )
    return user_id


def _not_found() -> LunaHTTPException:
    return LunaHTTPException(
        status_code=404,
        code=ErrorCode.DOC_NOT_FOUND,
        detail=MSG_FILE_NOT_FOUND,
        headers={"Cache-Control": _NO_STORE},
    )


@router.get("/files/status", response_model=FilesStatusResponse)
async def files_status(
    response: Response,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Is «الملفات» on for the caller? Always 200 — this is the one route that
    never answers 404 for a signed-in user, so the web can decide whether to
    show the sidebar row, the page and the pane tabs BEFORE calling anything
    else (a build can carry the UI while ``MY_FILES_USERS`` limits the feature
    to some users). Reads no file data. Declared before ``/files/{item_id}``
    so the literal path wins."""
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    response.headers["Cache-Control"] = _NO_STORE
    enabled = files_service.my_files_enabled(user_id)
    return FilesStatusResponse(
        enabled=enabled,
        angle_maps=enabled and angle_maps_service.angle_maps_ui_enabled(user_id),
    )


@router.get("/files", response_model=LibraryFileListResponse)
async def list_files(
    response: Response,
    user_id: str = Depends(files_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """The user's library: newest first, at most 500, identical files folded."""
    data = await run_db(files_service.list_library_files, supabase, user_id)
    response.headers["Cache-Control"] = _NO_STORE
    return LibraryFileListResponse.model_validate(data)


def _angle_maps_on(user_id: str) -> None:
    """«الأوجه» off for the caller → the same FEATURE_OFF 404 as «الملفات» off."""
    if not angle_maps_service.angle_maps_ui_enabled(user_id):
        raise LunaHTTPException(status_code=404, code=ErrorCode.FEATURE_OFF, detail=MSG_FEATURE_OFF)


@router.get("/files/angle-maps", response_model=AngleMapListResponse)
async def list_angle_maps(
    response: Response,
    conversation_id: str | None = None,
    user_id: str = Depends(files_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """«الأوجه» (.claude/plans/angle_map_ui.md): the caller's fresh angle maps,
    newest first — light rows; ``conversation_id`` keeps one conversation's (the
    chat pane). Declared before ``/files/{item_id}`` so the literal path wins."""
    _angle_maps_on(user_id)
    if conversation_id:
        validate_uuid(conversation_id, "معرف المحادثة")
    maps = await run_db(angle_maps_service.list_angle_maps, supabase, user_id, conversation_id or None)
    response.headers["Cache-Control"] = _NO_STORE
    return AngleMapListResponse.model_validate({"maps": maps})


@router.get("/files/angle-maps/{map_id}", response_model=AngleMapOut)
async def get_angle_map(
    map_id: str,
    response: Response,
    user_id: str = Depends(files_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """One of the caller's angle maps in full. Not theirs, missing or no longer
    fresh → 404."""
    _angle_maps_on(user_id)
    validate_uuid(map_id, "معرف الخريطة")
    data = await run_db(angle_maps_service.get_angle_map, supabase, user_id, map_id)
    if data is None:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.DOC_NOT_FOUND,
            detail="الخيوط غير موجودة أو لم تعد محدّثة",
            headers={"Cache-Control": _NO_STORE},
        )
    response.headers["Cache-Control"] = _NO_STORE
    return AngleMapOut.model_validate(data)


@router.post("/files/match", response_model=FileMatchResponse)
async def match_file(
    body: FileMatchRequest,
    response: Response,
    user_id: str = Depends(files_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Does the caller already have this file (§3.8)? Asked before the upload
    session is opened, so a match costs no row and no storage. Phase 1b
    answers only with a copy inside ``conversation_id``."""
    conversation_id = (body.conversation_id or "").strip() or None
    if conversation_id is not None:
        validate_uuid(conversation_id, "معرف المحادثة")
    match = await run_db(
        files_service.match_library_file,
        supabase,
        user_id,
        filename=body.filename,
        size_bytes=body.size_bytes,
        mime_type=body.mime_type,
        conversation_id=conversation_id,
    )
    response.headers["Cache-Control"] = _NO_STORE
    return FileMatchResponse(match=FileMatch.model_validate(match) if match else None)


@router.get("/files/{item_id}", response_model=LibraryFileDetail)
async def get_file(
    item_id: str,
    response: Response,
    user_id: str = Depends(files_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """One file as the viewer needs it — also a file the library does not list
    (hidden, a folded copy): the conversation pane opens files here too."""
    validate_uuid(item_id, "معرف الملف")
    data = await run_db(files_service.get_library_file, supabase, user_id, item_id)
    if data is None:
        raise _not_found()
    response.headers["Cache-Control"] = _NO_STORE
    return LibraryFileDetail.model_validate(data)


@router.delete("/files/{item_id}", status_code=204, response_class=Response)
async def hide_file(
    item_id: str,
    user_id: str = Depends(files_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Remove the file (and its identical copies) from «ملفاتي». Conversations
    that used it keep it. Idempotent: hiding a hidden file is a 204."""
    validate_uuid(item_id, "معرف الملف")
    found = await run_db(files_service.hide_library_file, supabase, user_id, item_id)
    if not found:
        raise _not_found()
    return Response(status_code=204)
