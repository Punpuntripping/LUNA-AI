"""Blog / public share-by-link business logic (مدونة).

A *blog post* is an immutable **snapshot** of a shareable workspace item —
``agent_search`` (تحليل قانوني) or ``agent_writing`` (رأي قانوني …) — published to a public, unlisted, read-only
page at an unguessable token URL (``/blog/{token}``). The snapshot model (A):
at publish time we freeze ``content_md`` + the fully-resolved ``Reference[]``
into the ``blog_posts`` row, so the public page never touches live workspace
data, survives later edits/deletes of the source artifact, and exposes nothing
beyond the snapshot.

Table ``public.blog_posts`` is already live in prod (do NOT create/alter it):

    post_id        uuid PK   (default gen_random_uuid())
    token          text      UNIQUE NOT NULL (default encode(gen_random_bytes(16),'hex'))
    owner_user_id  uuid      NOT NULL  (FK users.user_id)
    source_item_id uuid                (provenance; no FK)
    subtype        text
    question_text  text      NOT NULL
    title          text
    content_md     text      NOT NULL
    references_json jsonb    NOT NULL default '[]'
    is_published   boolean   NOT NULL default true
    view_count     integer   NOT NULL default 0
    created_at / updated_at timestamptz
    deleted_at     timestamptz

RLS: anon + authenticated may SELECT only ``is_published AND deleted_at IS NULL``.
There is NO INSERT policy — inserts succeed only via the backend's service-role
client (``get_supabase``), which is what every function here is handed.

All functions are SYNCHRONOUS and are invoked from the route handlers via
``run_db`` / ``asyncio.to_thread`` (same convention as the rest of the
codebase). All error messages are Arabic.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from supabase import Client as SupabaseClient

from backend.app.errors import LunaHTTPException, ErrorCode

logger = logging.getLogger(__name__)

# Shareable artifact kinds: the deep_search synthesis (``agent_search`` —
# "تحليل قانوني"/legal_synthesis) AND the writer outputs (``agent_writing`` —
# رأي قانوني / مذكرة / عقد …). Both carry content_md + resolvable references.
# Not shareable: notes, attachments, references, convo_context, raw chat replies.
_PUBLISHABLE_KINDS = frozenset({"agent_search", "agent_writing"})

__all__ = [
    "insert_post",
    "assert_publishable",
    "get_public_post",
    "derive_default_question",
    "unpublish_post",
]


# ---------------------------------------------------------------------------
# PUBLIC READ PATH (anon-safe)
# ---------------------------------------------------------------------------


def get_public_post(supabase: SupabaseClient, token: str) -> Optional[dict]:
    """Fetch a published post by its unguessable token.

    Returns the public projection dict (``question_text``, ``title``,
    ``content_md``, ``references``, ``subtype``, ``created_at``) or ``None``
    when no published/non-deleted row matches the token. Best-effort
    increments ``view_count`` — a failed increment NEVER fails the read.

    Note: although RLS already filters to ``is_published AND deleted_at IS
    NULL``, the service-role client bypasses RLS, so we filter explicitly here
    too — the public contract must not leak unpublished/revoked snapshots.
    """
    try:
        result = (
            supabase.table("blog_posts")
            .select(
                "post_id, question_text, title, content_md, references_json, "
                "subtype, view_count, created_at"
            )
            .eq("token", token)
            .eq("is_published", True)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error fetching public blog post: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب المنشور",
        )

    if result is None or result.data is None:
        return None

    row = result.data

    # Best-effort view counter. Never fail the request on increment trouble.
    try:
        current = int(row.get("view_count") or 0)
        (
            supabase.table("blog_posts")
            .update({"view_count": current + 1})
            .eq("post_id", row["post_id"])
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("blog view_count increment failed for %s: %s", token, e)

    return {
        "question_text": row.get("question_text") or "",
        "title": row.get("title"),
        "content_md": row.get("content_md") or "",
        "references": row.get("references_json") or [],
        "subtype": row.get("subtype"),
        "created_at": row.get("created_at"),
    }


# ---------------------------------------------------------------------------
# SHARE-DRAFT (default question derivation)
# ---------------------------------------------------------------------------


def derive_default_question(supabase: SupabaseClient, item: dict) -> str:
    """Best-guess the السؤال to pre-fill the publish dialog.

    Derivation: from the artifact's ``message_id`` resolve that assistant
    message, find the **preceding user message** in the same conversation
    (the message that triggered the artifact), and return its content.

    Fallbacks, in order:
        1. the triggering user message content,
        2. the artifact title,
        3. "" (empty — the dialog is editable anyway).

    ``query_restatement`` is deliberately NOT used (plan decision: the page
    shows the verbatim user question, edited at publish time).
    """
    message_id = item.get("message_id")
    conversation_id = item.get("conversation_id")
    title = item.get("title") or ""

    if not message_id or not conversation_id:
        return title

    try:
        # Anchor: the assistant message this artifact was produced for.
        anchor = (
            supabase.table("messages")
            .select("created_at")
            .eq("message_id", message_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("derive_default_question: anchor lookup failed: %s", e)
        return title

    if anchor is None or anchor.data is None:
        return title

    anchor_created_at = anchor.data.get("created_at")
    if not anchor_created_at:
        return title

    try:
        # The user message immediately before the anchor in this conversation.
        prev = (
            supabase.table("messages")
            .select("content")
            .eq("conversation_id", conversation_id)
            .eq("role", "user")
            .lt("created_at", anchor_created_at)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("derive_default_question: preceding-user lookup failed: %s", e)
        return title

    rows = prev.data or []
    if rows:
        content = (rows[0].get("content") or "").strip()
        if content:
            return content

    return title


# ---------------------------------------------------------------------------
# PUBLISH (create snapshot)
# ---------------------------------------------------------------------------


def insert_post(
    supabase: SupabaseClient,
    *,
    owner_user_id: str,
    source_item_id: str,
    subtype: Optional[str],
    question_text: str,
    title: Optional[str],
    content_md: str,
    references_json: list[dict[str, Any]],
) -> str:
    """Insert one ``blog_posts`` row and return the DB-minted ``token``.

    ``token`` is omitted from the payload so the column default
    (``encode(gen_random_bytes(16),'hex')``) mints it; we read it back from
    the insert's returning representation.
    """
    payload: dict[str, Any] = {
        "owner_user_id": owner_user_id,
        "source_item_id": source_item_id,
        "subtype": subtype,
        "question_text": question_text,
        "title": title,
        "content_md": content_md,
        "references_json": references_json,
    }

    try:
        result = supabase.table("blog_posts").insert(payload).execute()
    except Exception as e:  # noqa: BLE001
        logger.exception("Error inserting blog post: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء نشر المنشور",
        )

    if not result.data:
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء نشر المنشور",
        )

    token = result.data[0].get("token")
    if not token:
        # Should be impossible (NOT NULL + default), but never return an empty
        # public URL.
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء نشر المنشور",
        )

    return token


def assert_publishable(item: dict) -> None:
    """Raise 400 (Arabic) if the workspace item kind is not shareable.

    Shareable = ``agent_search`` (تحليل قانوني) or ``agent_writing`` (writer
    outputs). Notes, attachments, references, convo_context cannot be published.
    """
    if item.get("kind") not in _PUBLISHABLE_KINDS:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail="لا يمكن نشر هذا النوع من العناصر",
        )


# ---------------------------------------------------------------------------
# REVOKE (owner-scoped soft delete)
# ---------------------------------------------------------------------------


def unpublish_post(
    supabase: SupabaseClient,
    user_id: str,
    post_id: str,
) -> None:
    """Owner-scoped soft-revoke: ``is_published=false`` + ``deleted_at=now()``.

    Scoped to ``owner_user_id = user_id`` so a caller can only revoke their
    own posts; a post that isn't theirs (or doesn't exist / already revoked)
    surfaces as 404 with the same envelope, leaking no existence information.
    ``now()`` is set in the DB via the PostgREST expression so the timestamp
    is server-authoritative.
    """
    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        result = (
            supabase.table("blog_posts")
            .update({
                "is_published": False,
                "deleted_at": now_iso,
                "updated_at": now_iso,
            })
            .eq("post_id", post_id)
            .eq("owner_user_id", user_id)
            .is_("deleted_at", "null")
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error unpublishing blog post: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء إلغاء نشر المنشور",
        )

    if not result.data:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.ARTIFACT_NOT_FOUND,
            detail="المنشور غير موجود",
        )
