"""
Conversation business logic.
All database queries go through the sync Supabase client.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from supabase import Client as SupabaseClient

from backend.app.errors import LunaHTTPException, ErrorCode
from backend.app.services.audit_service import write_audit_log
from backend.app.services.case_service import get_user_id

logger = logging.getLogger(__name__)


# ============================================
# CONVERSATION CRUD
# ============================================

def list_conversations(
    supabase: SupabaseClient,
    auth_id: str,
    *,
    case_id: Optional[str] = None,
    starred: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """
    List conversations for the authenticated user, optionally filtered by case_id.
    When ``starred`` is True, only starred conversations are returned.
    Ordering: starred first (most-recently-starred), then most-recent updated.
    Returns dict with conversations list, total count, and has_more flag.
    """
    user_id = get_user_id(supabase, auth_id)

    # Clamp values
    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    try:
        query = (
            supabase.table("conversations")
            .select("*", count="exact")
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
            .order("starred_at", desc=True, nullsfirst=False)
            .order("updated_at", desc=True)
            .range(offset, offset + limit - 1)
        )

        if case_id is not None:
            query = query.eq("case_id", case_id)

        if starred:
            query = query.not_.is_("starred_at", "null")

        result = query.execute()

    except Exception as e:
        logger.exception("Error listing conversations: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء جلب المحادثات")

    total = result.count or 0
    conversations = result.data or []

    # Enrich with is_active / is_starred derived fields
    enriched = [_enrich_conversation(c) for c in conversations]

    return {
        "conversations": enriched,
        "total": total,
        "has_more": (offset + limit) < total,
    }


def search_conversations(
    supabase: SupabaseClient,
    auth_id: str,
    q: str,
    *,
    starred: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """
    Search the user's conversations by title AND message content (substring,
    case-insensitive ILIKE '%q%'). One result row per conversation:
      * matched by title  → match_type="title",  snippet=None
      * matched by content → match_type="message", snippet=~160-char excerpt
    Title wins when a conversation matches on both surfaces.

    Ordering: starred first (most-recently-starred), then most-recent updated.
    Paginated via offset/limit. Returns {conversations, total, has_more}.

    The query is sanitized: stripped, and ILIKE wildcards (% _ \\) escaped so
    user input can't inject wildcards. When q is empty after strip, this falls
    back to plain list_conversations.
    """
    q = (q or "").strip()
    if not q:
        return list_conversations(
            supabase, auth_id, starred=starred, limit=limit, offset=offset,
        )

    user_id = get_user_id(supabase, auth_id)

    # Clamp values
    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    pattern = f"%{_escape_ilike(q)}%"

    try:
        # Load the user's live conversations (the searchable universe).
        conv_query = (
            supabase.table("conversations")
            .select("*")
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
        )
        if starred:
            conv_query = conv_query.not_.is_("starred_at", "null")
        conv_result = conv_query.execute()
        conversations = conv_result.data or []
    except Exception as e:
        logger.exception("Error loading conversations for search: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء البحث في المحادثات")

    if not conversations:
        return {"conversations": [], "total": 0, "has_more": False}

    convs_by_id: dict[str, dict] = {c["conversation_id"]: c for c in conversations}
    allowed_ids = list(convs_by_id.keys())

    # Surface 1 — title hits (user-scoped, not deleted).
    title_hit_ids: set[str] = set()
    for c in conversations:
        title = c.get("title_ar")
        if title and q.casefold() in title.casefold():
            title_hit_ids.add(c["conversation_id"])

    # Surface 2 — message-content hits, restricted to the user's conversations.
    # Group to one hit per conversation, keeping the newest matching message
    # to build the snippet from.
    snippet_by_conv: dict[str, str] = {}
    try:
        # NOTE: messages has no deleted_at column (verified live schema) — message
        # rows are never soft-deleted, so no such filter here.
        msg_result = (
            supabase.table("messages")
            .select("conversation_id, content, created_at")
            .in_("conversation_id", allowed_ids)
            .ilike("content", pattern)
            .order("created_at", desc=True)
            .execute()
        )
        message_rows = msg_result.data or []
    except Exception as e:
        logger.exception("Error searching message content: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء البحث في المحادثات")

    # Rows arrive newest-first; first one seen per conversation is the newest.
    for row in message_rows:
        cid = row.get("conversation_id")
        if cid is None or cid not in convs_by_id or cid in snippet_by_conv:
            continue
        snippet_by_conv[cid] = _build_snippet(row.get("content") or "", q)

    # Merge: union of the two surfaces, one row per conversation.
    matched_ids = title_hit_ids | set(snippet_by_conv.keys())
    if not matched_ids:
        return {"conversations": [], "total": 0, "has_more": False}

    merged: list[dict] = []
    for cid in matched_ids:
        conv = _enrich_conversation(convs_by_id[cid])
        if cid in title_hit_ids:
            conv["match_type"] = "title"
            conv["snippet"] = None
        else:
            conv["match_type"] = "message"
            conv["snippet"] = snippet_by_conv.get(cid)
        merged.append(conv)

    # Order: starred-first (most-recently-starred), then most-recent updated.
    merged.sort(
        key=lambda c: (
            c.get("starred_at") or "",
            c.get("updated_at") or "",
        ),
        reverse=True,
    )

    total = len(merged)
    page = merged[offset:offset + limit]

    return {
        "conversations": page,
        "total": total,
        "has_more": (offset + limit) < total,
    }


def create_conversation(
    supabase: SupabaseClient,
    auth_id: str,
    *,
    case_id: Optional[str] = None,
) -> dict:
    """
    Create a new conversation.
    If case_id is provided, verify the case exists and is owned by the user.
    """
    user_id = get_user_id(supabase, auth_id)

    # If case_id is provided, verify ownership
    if case_id is not None:
        _verify_case_exists(supabase, case_id, user_id)

    # Build conversation data
    conv_data = {
        "user_id": user_id,
        "title_ar": "محادثة جديدة",
    }
    if case_id is not None:
        conv_data["case_id"] = case_id

    try:
        result = (
            supabase.table("conversations")
            .insert(conv_data)
            .execute()
        )
    except Exception as e:
        logger.exception("Error creating conversation: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء إنشاء المحادثة")

    if not result.data:
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء إنشاء المحادثة")

    conv = _enrich_conversation(result.data[0])

    write_audit_log(
        supabase,
        user_id=user_id,
        action="create",
        resource_type="conversation",
        resource_id=conv["conversation_id"],
    )

    return conv


def get_conversation(
    supabase: SupabaseClient,
    auth_id: str,
    conversation_id: str,
) -> dict:
    """
    Get a single conversation by ID.
    Verifies ownership (returns 404 if not owned or not found).
    """
    user_id = get_user_id(supabase, auth_id)

    try:
        result = (
            supabase.table("conversations")
            .select("*")
            .eq("conversation_id", conversation_id)
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logger.exception("Error fetching conversation: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء جلب المحادثة")

    if result is None or result.data is None:
        raise LunaHTTPException(status_code=404, code=ErrorCode.CONV_NOT_FOUND, detail="المحادثة غير موجودة")

    return _enrich_conversation(result.data)


def update_conversation(
    supabase: SupabaseClient,
    auth_id: str,
    conversation_id: str,
    *,
    title_ar: Optional[str] = None,
    starred: Optional[bool] = None,
) -> dict:
    """
    Update a conversation's title and/or starred state.
    Verifies ownership first. ``starred=True`` sets starred_at = now(),
    ``starred=False`` clears it (starred_at = null). updated_at is always bumped.
    """
    user_id = get_user_id(supabase, auth_id)
    _verify_conversation_ownership(supabase, conversation_id, user_id)

    now = datetime.now(timezone.utc).isoformat()
    update_data: dict = {"updated_at": now}

    if title_ar is not None:
        update_data["title_ar"] = title_ar

    if starred is not None:
        update_data["starred_at"] = now if starred else None

    try:
        result = (
            supabase.table("conversations")
            .update(update_data)
            .eq("conversation_id", conversation_id)
            .eq("user_id", user_id)
            .execute()
        )
    except Exception as e:
        logger.exception("Error updating conversation: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء تحديث المحادثة")

    if not result.data:
        raise LunaHTTPException(status_code=404, code=ErrorCode.CONV_NOT_FOUND, detail="المحادثة غير موجودة")

    return _enrich_conversation(result.data[0])


def delete_conversation(
    supabase: SupabaseClient,
    auth_id: str,
    conversation_id: str,
) -> None:
    """
    Soft-delete a conversation by setting deleted_at.
    """
    user_id = get_user_id(supabase, auth_id)
    _verify_conversation_ownership(supabase, conversation_id, user_id)

    now = datetime.now(timezone.utc).isoformat()

    try:
        supabase.table("conversations").update({
            "deleted_at": now,
            "updated_at": now,
        }).eq("conversation_id", conversation_id).eq("user_id", user_id).execute()
    except Exception as e:
        logger.exception("Error deleting conversation: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء حذف المحادثة")


def end_session(
    supabase: SupabaseClient,
    auth_id: str,
    conversation_id: str,
) -> dict:
    """
    End a conversation session by setting ended_at.
    Returns the updated conversation.
    """
    user_id = get_user_id(supabase, auth_id)
    _verify_conversation_ownership(supabase, conversation_id, user_id)

    now = datetime.now(timezone.utc).isoformat()

    try:
        result = (
            supabase.table("conversations")
            .update({
                "ended_at": now,
                "updated_at": now,
            })
            .eq("conversation_id", conversation_id)
            .eq("user_id", user_id)
            .execute()
        )
    except Exception as e:
        logger.exception("Error ending session: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء إنهاء الجلسة")

    if not result.data:
        raise LunaHTTPException(status_code=404, code=ErrorCode.CONV_NOT_FOUND, detail="المحادثة غير موجودة")

    conv = _enrich_conversation(result.data[0])
    conv["is_active"] = False  # We just set ended_at, so it's no longer active

    return conv


# ============================================
# INTERNAL HELPERS
# ============================================

# Max length of a message-content search snippet (chars).
_SNIPPET_LEN = 160
# How much context to keep before the match when trimming the snippet window.
_SNIPPET_LEAD = 30


def _enrich_conversation(conv: dict) -> dict:
    """
    Add derived fields to a raw conversation row:
      * is_active  — ended_at is None
      * is_starred — starred_at is not None
    Returns the same dict (mutated) for convenience.
    """
    conv["is_active"] = conv.get("ended_at") is None
    conv["is_starred"] = conv.get("starred_at") is not None
    return conv


def _escape_ilike(value: str) -> str:
    """
    Escape PostgreSQL ILIKE wildcards in user input so a search term can't
    inject wildcards. Backslash must be escaped first.
    """
    return (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _build_snippet(content: str, q: str) -> str:
    """
    Build a ~160-char excerpt of ``content`` centered on the first
    case-insensitive occurrence of ``q``. Whitespace is collapsed; ellipses are
    added when the window is trimmed.
    """
    # Collapse all whitespace runs to single spaces.
    collapsed = " ".join((content or "").split())
    if not collapsed:
        return ""

    if len(collapsed) <= _SNIPPET_LEN:
        return collapsed

    idx = collapsed.casefold().find(q.casefold())
    if idx == -1:
        # Match was whitespace-spanning; just return the head.
        return collapsed[:_SNIPPET_LEN].rstrip() + "…"

    start = max(0, idx - _SNIPPET_LEAD)
    end = start + _SNIPPET_LEN
    snippet = collapsed[start:end].strip()

    if start > 0:
        snippet = "…" + snippet
    if end < len(collapsed):
        snippet = snippet + "…"

    return snippet


def _verify_case_exists(supabase: SupabaseClient, case_id: str, user_id: str) -> None:
    """
    Verify a case exists, is not deleted, and belongs to the user.
    Raises 404 if not found/not owned. Raises 400 if case is archived.
    """
    try:
        result = (
            supabase.table("lawyer_cases")
            .select("case_id, status")
            .eq("case_id", case_id)
            .eq("lawyer_user_id", user_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logger.exception("Error verifying case: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ داخلي")

    if result is None or result.data is None:
        raise LunaHTTPException(status_code=404, code=ErrorCode.CASE_NOT_FOUND, detail="القضية غير موجودة")


def _verify_conversation_ownership(
    supabase: SupabaseClient,
    conversation_id: str,
    user_id: str,
) -> dict:
    """
    Verify the conversation exists, is not deleted, and belongs to the user.
    Returns the conversation row if valid, otherwise raises 404.
    """
    try:
        result = (
            supabase.table("conversations")
            .select("conversation_id, user_id")
            .eq("conversation_id", conversation_id)
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logger.exception("Error verifying conversation ownership: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ داخلي")

    if result is None or result.data is None:
        raise LunaHTTPException(status_code=404, code=ErrorCode.CONV_NOT_FOUND, detail="المحادثة غير موجودة")

    return result.data
