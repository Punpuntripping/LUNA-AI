"""
Message endpoints.
GET  /conversations/{conversation_id}/messages — list messages
POST /conversations/{conversation_id}/messages — send message (SSE stream)
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from supabase import Client as SupabaseClient

from backend.app.deps import get_current_user, get_supabase
from backend.app.models.requests import SendMessageRequest
from backend.app.models.responses import MessageListResponse
from shared.auth.jwt import AuthUser
from backend.app.services import message_service
from backend.app.services.case_service import get_user_id
from shared.db.run import run_db

router = APIRouter()


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=MessageListResponse,
)
async def list_messages(
    conversation_id: str,
    limit: int = Query(50, ge=1, le=100),
    before: Optional[str] = Query(None),
    user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """List messages for a conversation (newest first, cursor-based pagination)."""
    # Snapshot BEFORE the read: a run writes its content and only then frees
    # its slot, so "not active here" + "still empty in the read" = dead run.
    run_active = message_service.conversation_run_active(conversation_id)
    result = await run_db(
        message_service.list_messages,
        supabase,
        user.auth_id,
        conversation_id,
        limit=limit,
        before=before,
    )
    return {**result, "run_active": run_active}


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    body: SendMessageRequest,
    request: Request,
    user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Send a message and receive SSE stream response."""
    # Pre-flight ownership check — BEFORE StreamingResponse (security fix)
    user_id = await run_db(get_user_id, supabase, user.auth_id)
    conv = await run_db(
        message_service.verify_conversation_ownership, supabase, conversation_id, user_id
    )

    return StreamingResponse(
        message_service.send_message_stream(
            supabase,
            user_id=user_id,
            conversation_id=conversation_id,
            conv=conv,
            content=body.content,
            request=request,
            attachment_ids=body.attachment_ids,
        ),
        media_type="text/event-stream",
        headers={
            # no-transform stops an intermediary (Cloudflare, once the zone is
            # proxied) from compressing or otherwise rewriting the stream —
            # the usual cause of SSE arriving buffered behind a CDN.
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
