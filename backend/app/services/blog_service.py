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
import re
from typing import Any, Optional

from supabase import Client as SupabaseClient

from backend.app.errors import LunaHTTPException, ErrorCode

logger = logging.getLogger(__name__)

# The two share templates a blog_posts row can render as.
_DISPLAY_MODES = frozenset({"question", "title"})

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
    "user_can_access_blog",
    "list_public_blogs",
    "list_my_blogs",
    "set_post_public",
    "make_snippet",
]


# ---------------------------------------------------------------------------
# SNIPPET (directory card preview)
# ---------------------------------------------------------------------------


def make_snippet(content_md: str, max_len: int = 200) -> str:
    """Reduce a Markdown body to a short plain-text snippet for a directory card.

    Best-effort: strips headings/emphasis/code fences/links/citation markers and
    collapses whitespace. Never raises — a bad body just yields a shorter
    snippet. Not security-sensitive (the full content is already public on the
    post page); this is purely cosmetic.
    """
    text = content_md or ""
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)   # fenced code
    text = re.sub(r"`([^`]*)`", r"\1", text)                  # inline code
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)         # images
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)      # links -> label
    text = re.sub(r"\[\d+(?:\s*,\s*\d+)*\]", " ", text)       # [n] citations
    text = re.sub(r"[#>*_~`]+", " ", text)                    # md punctuation
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


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
                "subtype, view_count, created_at, display_mode"
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
        "display_mode": row.get("display_mode") or "question",
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
    display_mode: str = "question",
    is_published: bool = True,
) -> str:
    """Insert one ``blog_posts`` row and return the DB-minted ``token``.

    ``token`` is omitted from the payload so the column default
    (``encode(gen_random_bytes(16),'hex')``) mints it; we read it back from
    the insert's returning representation. ``display_mode`` selects the share
    template ('question' vs 'title'/مدونة).

    ``is_published`` (default ``True``) lets a caller create an unpublished
    draft — the editorial blog-post-jobs API sets it per ``publish_policy`` /
    ``min_confidence``. The default keeps the in-app مشاركة share path
    unchanged. ``is_public`` is deliberately NOT a param: it stays at its
    column default (``false``) so these posts are never gallery-listed.
    """
    payload: dict[str, Any] = {
        "owner_user_id": owner_user_id,
        "source_item_id": source_item_id,
        "subtype": subtype,
        "question_text": question_text,
        "title": title,
        "content_md": content_md,
        "references_json": references_json,
        "display_mode": display_mode if display_mode in _DISPLAY_MODES else "question",
        "is_published": is_published,
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


# ---------------------------------------------------------------------------
# CURATION GATE + LISTINGS (v2 — viewing is open; only curation is gated)
# ---------------------------------------------------------------------------


def user_can_access_blog(supabase: SupabaseClient, auth_id: str) -> bool:
    """True when the caller may CURATE the public gallery (push a post public).

    v2 inverts the meaning: this no longer gates *viewing* (the gallery and
    every post are anonymous). It now gates *curation* — who may set a post's
    ``is_public=true``. Reads ``users.can_access_blog`` for the JWT's auth_id;
    any lookup miss / error is treated as NOT authorized (fail closed).
    """
    try:
        result = (
            supabase.table("users")
            .select("can_access_blog")
            .eq("auth_id", auth_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("user_can_access_blog lookup failed for %s: %s", auth_id, e)
        return False

    if result is None or result.data is None:
        return False
    return bool(result.data.get("can_access_blog"))


def list_public_blogs(
    supabase: SupabaseClient,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List published PUBLIC posts for the anonymous gallery (/blog).

    Newest first. Returns lightweight card dicts (token, title, snippet,
    subtype, view_count, created_at) — never the full body. Anonymous: there
    is NO ``can_access_blog`` gate on this read (v2 inverts the model). The
    gallery keys on ``is_public`` (any user's published, public share appears),
    regardless of display_mode.
    """
    limit = max(1, min(int(limit or 50), 100))
    offset = max(0, int(offset or 0))

    try:
        result = (
            supabase.table("blog_posts")
            .select("token, title, content_md, subtype, view_count, created_at")
            .eq("is_public", True)
            .eq("is_published", True)
            .is_("deleted_at", "null")
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error listing public blogs: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب المدونة",
        )

    rows = result.data or []
    return [
        {
            "token": row.get("token"),
            "title": row.get("title"),
            "snippet": make_snippet(row.get("content_md") or ""),
            "subtype": row.get("subtype"),
            "view_count": int(row.get("view_count") or 0),
            "created_at": row.get("created_at"),
        }
        for row in rows
        if row.get("token")
    ]


def list_my_blogs(
    supabase: SupabaseClient,
    user_id: str,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List the caller's own blogs (مدوناتي) — owner-scoped.

    Newest first, both display_modes, not deleted. Returns card dicts
    (post_id, token, title, snippet, subtype, display_mode, is_public,
    created_at) — the management list, so it carries post_id + is_public for
    the per-row publish toggle.
    """
    limit = max(1, min(int(limit or 100), 200))
    offset = max(0, int(offset or 0))

    try:
        result = (
            supabase.table("blog_posts")
            .select(
                "post_id, token, title, content_md, subtype, "
                "display_mode, is_public, created_at"
            )
            .eq("owner_user_id", user_id)
            .is_("deleted_at", "null")
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error listing my blogs: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب المدونة",
        )

    rows = result.data or []
    return [
        {
            "post_id": row.get("post_id"),
            "token": row.get("token"),
            "title": row.get("title"),
            "snippet": make_snippet(row.get("content_md") or ""),
            "subtype": row.get("subtype"),
            "display_mode": row.get("display_mode") or "question",
            "is_public": bool(row.get("is_public")),
            "created_at": row.get("created_at"),
        }
        for row in rows
        if row.get("post_id")
    ]


def set_post_public(
    supabase: SupabaseClient,
    user_id: str,
    post_id: str,
    is_public: bool,
) -> None:
    """Owner-scoped toggle of a post's gallery visibility (``is_public``).

    Scoped to ``owner_user_id = user_id`` and non-deleted so a caller can only
    flip their own posts; a post that isn't theirs (or is missing / revoked)
    surfaces as 404 with the same envelope, leaking no existence information.
    The *curation* gate (``can_access_blog``) is enforced by the route handler
    for the publish-to-public direction; retracting your own post needs no gate.
    """
    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        result = (
            supabase.table("blog_posts")
            .update({
                "is_public": is_public,
                "updated_at": now_iso,
            })
            .eq("post_id", post_id)
            .eq("owner_user_id", user_id)
            .is_("deleted_at", "null")
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error setting blog post public flag: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء تحديث حالة النشر",
        )

    if not result.data:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.ARTIFACT_NOT_FOUND,
            detail="المنشور غير موجود",
        )
