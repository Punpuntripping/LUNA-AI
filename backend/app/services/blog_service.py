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
    "extract_blog_token",
    "resolve_post_by_token",
    "import_post_for_user",
    "create_blog_item",
    "to_my_blog_item",
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


def strip_frozen_source_views(references: Any) -> list[dict[str, Any]]:
    """Drop ``source_view`` from a FROZEN blog snapshot before serving it.

    ``blog_posts.references_json`` is a snapshot taken at publish time and is
    served by the ANONYMOUS ``GET /public/blog/{token}`` and ``/public/blogs``.
    Publishes from 2026-07-27 onward no longer capture source views at all
    (``fetch_item_references`` defaults to ``with_source_views=False``), but 95
    of the 100 posts published BEFORE that change still carry full case bodies,
    chunk content and uncapped circular text inside their stored JSON — ~3.4 MB
    of corpus text readable by anyone with a share link, with no account and no
    meter. That is a standing bypass around the whole access-tiers design.

    Stripping happens on READ rather than by rewriting the rows: the snapshot is
    the historical record of what was published, a backfill is irreversible, and
    a read-time filter also protects any row written by an older deployment
    during a rolling release.

    What survives is exactly the never-gated class (§1.3): the citation list and
    its mesh — ``n``, ``title``, ``snippet``, ``ref_id``, ``domain``, links,
    ``cross_refs`` — plus the official source URL. The public page keeps its
    credibility layer; only the source BODY is withheld, and reading it now costs
    an unlock like everywhere else.
    """
    out: list[dict[str, Any]] = []
    for ref in references or []:
        if not isinstance(ref, dict):
            continue
        if ref.get("source_view") is None:
            out.append(ref)
            continue
        # Strip the BODY, keep the FACT that a body exists.
        #
        # ``has_source=True`` is deliberate and load-bearing: a legacy row that
        # carried a ``source_view`` is precisely a row whose source CAN be
        # rebuilt, so the blog panel must still render «عرض المصدر» for it — the
        # reader signs in and reveals it through the metered endpoint. Setting
        # this False (as an earlier pass did) silently deleted the reveal
        # affordance from every pre-2026-07-27 post.
        #
        # ``source_view`` stays present-but-null so an un-migrated client
        # degrades to "no reveal" instead of crashing on a missing property.
        out.append({**ref, "source_view": None, "has_source": True})
    return out


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
        "references": strip_frozen_source_views(row.get("references_json")),
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
# IMPORT (blog → مدوناتي library / blog → conversation note)
# .claude/plans/blog_import.md
# ---------------------------------------------------------------------------

# A blog token is 32 lowercase hex chars (encode(gen_random_bytes(16),'hex')).
# Accept either a full share URL (…/blog/<token>) or a bare token.
_TOKEN_IN_URL_RE = re.compile(r"/blog/([0-9a-f]{32})(?![0-9a-f])")
_BARE_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")

# Fields the import paths need: snapshot payload + identity/provenance.
# ``source_item_id`` = the workspace item the post was originally shared from —
# the conversation-import path copies that item's reference rows for a
# full-fidelity المراجع panel.
_RESOLVE_FIELDS = (
    "post_id, owner_user_id, source_post_id, source_item_id, subtype, "
    "question_text, title, content_md, references_json, display_mode, "
    "is_public, created_at"
)


def extract_blog_token(raw: str) -> Optional[str]:
    """Pull a blog token out of a pasted share URL or a bare token string.

    Host-agnostic (matches prod, localhost, any mirror) — only the
    ``/blog/<32-hex>`` path shape matters. Returns ``None`` when nothing
    token-shaped is present.
    """
    text = (raw or "").strip().lower()
    if not text:
        return None
    m = _TOKEN_IN_URL_RE.search(text)
    if m:
        return m.group(1)
    if _BARE_TOKEN_RE.match(text):
        return text
    return None


def resolve_post_by_token(supabase: SupabaseClient, token: str) -> Optional[dict]:
    """Fetch the FULL row of a published, non-deleted post by token.

    The import-path sibling of ``get_public_post``: same access rule (a valid
    token of a published post is sufficient — identical to viewing), but it
    returns identity/provenance fields (``post_id``, ``owner_user_id``,
    ``source_post_id``) and does NOT bump ``view_count`` (importing is not a
    page view).
    """
    try:
        result = (
            supabase.table("blog_posts")
            .select(_RESOLVE_FIELDS)
            .eq("token", token)
            .eq("is_published", True)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error resolving blog post by token: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب المدونة",
        )
    if result is None or result.data is None:
        return None
    return result.data


def to_my_blog_item(row: dict) -> dict:
    """Project a blog_posts row into the MyBlogItem card dict (مدوناتي shape)."""
    return {
        "post_id": row.get("post_id"),
        "token": row.get("token"),
        "title": row.get("title"),
        "snippet": make_snippet(row.get("content_md") or ""),
        "subtype": row.get("subtype"),
        "display_mode": row.get("display_mode") or "question",
        "is_public": bool(row.get("is_public")),
        "is_imported": row.get("source_post_id") is not None,
        "created_at": row.get("created_at"),
    }


def import_post_for_user(
    supabase: SupabaseClient,
    *,
    user_id: str,
    token: str,
) -> tuple[dict, bool]:
    """Snapshot-copy the published post behind ``token`` into ``user_id``'s مدوناتي.

    Returns ``(row, already_saved)``. Dedup model (root propagation):
    ``source_post_id`` always stores the ROOT original post_id — copying a copy
    carries the copy's ``source_post_id`` forward — so one user can hold at most
    one live post per root (authored or imported). The copy gets its own
    DB-minted token (independently re-shareable), ``is_published=true``,
    ``is_public=false`` (never auto-gallery-listed).
    """
    post = resolve_post_by_token(supabase, token)
    if post is None:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.ARTIFACT_NOT_FOUND,
            detail="المدونة غير موجودة أو تم إلغاء نشرها",
        )

    root_id = post.get("source_post_id") or post["post_id"]

    # Caller pasted a token of their own post → nothing to copy.
    if post["owner_user_id"] == user_id:
        return {**post, "token": token}, True

    def _find_existing() -> Optional[dict]:
        """The caller's live post for this root — authored or imported."""
        result = (
            supabase.table("blog_posts")
            .select(_RESOLVE_FIELDS + ", token")
            .eq("owner_user_id", user_id)
            .is_("deleted_at", "null")
            .or_(f"post_id.eq.{root_id},source_post_id.eq.{root_id}")
            .limit(1)
            .execute()
        )
        rows = result.data or []
        return rows[0] if rows else None

    try:
        existing = _find_existing()
    except Exception as e:  # noqa: BLE001
        logger.exception("Error checking existing blog import: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء استيراد المدونة",
        )
    if existing is not None:
        return existing, True

    payload: dict[str, Any] = {
        "owner_user_id": user_id,
        "source_post_id": root_id,
        # source_item_id deliberately NOT copied — it references the original
        # author's workspace item, meaningless (and misleading) on the copy.
        "subtype": post.get("subtype"),
        "question_text": post.get("question_text") or "",
        "title": post.get("title"),
        "content_md": post.get("content_md") or "",
        # Strip on COPY as well as on read: importing a pre-2026-07-27 post would
        # otherwise mint a brand-new snapshot carrying the old full source
        # bodies, re-creating the unmetered mirror this closes.
        "references_json": strip_frozen_source_views(post.get("references_json")),
        "display_mode": post.get("display_mode") or "question",
        "is_published": True,
    }
    try:
        result = supabase.table("blog_posts").insert(payload).execute()
        row = (result.data or [None])[0]
    except Exception as e:  # noqa: BLE001
        # Concurrent double-import: the partial unique index
        # (owner_user_id, source_post_id) rejects the second insert — re-read.
        if "23505" in str(getattr(e, "code", "")) or "uq_blog_posts_owner_source" in str(e):
            try:
                existing = _find_existing()
            except Exception:  # noqa: BLE001
                existing = None
            if existing is not None:
                return existing, True
        logger.exception("Error importing blog post: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء استيراد المدونة",
        )

    if not row:
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء استيراد المدونة",
        )
    return row, False


# Default subtype when the snapshot carries none — renders as تحليل قانوني,
# the same treatment as a native deep_search output.
_DEFAULT_IMPORT_SUBTYPE = "legal_synthesis"
_ITEM_TITLE_MAX = 150

# Must match the default set by conversation_service.create_conversation —
# the retitle below only ever replaces this placeholder, never a real title.
_DEFAULT_CONVO_TITLE = "محادثة جديدة"


def _retitle_new_conversation(
    supabase: SupabaseClient,
    conv: dict,
    item_title: str,
) -> None:
    """Rename a still-pristine «محادثة جديدة» after the blog it now carries.

    Chat-with-blog creates the conversation before the user commits a message,
    so an abandoned import lingers in the sidebar disguised as a fresh empty
    chat while silently carrying the blog item. Only fires while the
    conversation is untouched (default title AND zero messages), and the
    UPDATE re-checks the title server-side so a concurrent first-message
    auto-title is never clobbered — if the user does send a message, the
    stream-end auto-title wins, which is the desired outcome either way.

    Best-effort like ``_materialize_references`` — a rename hiccup must not
    fail the import.
    """
    try:
        if (conv.get("title_ar") or "").strip() != _DEFAULT_CONVO_TITLE:
            return
        if conv.get("message_count"):
            return
        title = f"مدونة: {item_title}".strip()
        if len(title) > 60:
            title = title[:60].strip() + "..."
        (
            supabase.table("conversations")
            .update({"title_ar": title})
            .eq("conversation_id", conv["conversation_id"])
            .eq("title_ar", _DEFAULT_CONVO_TITLE)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception(
            "blog import: conversation retitle failed for %s: %s",
            conv.get("conversation_id"), e,
        )


def _materialize_references(
    supabase: SupabaseClient,
    wi_id: str,
    post: dict,
) -> int:
    """Give the imported WI a working المراجع panel by writing its
    ``workspace_item_references`` rows. Returns rows written.

    Primary path: copy the ORIGINAL workspace item's rows (``used=true`` only —
    the share snapshot itself was frozen ``used_only=True``). The original is
    ``post.source_item_id``; for an imported copy (``source_post_id`` set,
    ``source_item_id`` deliberately not copied) it's resolved off the ROOT
    post. Row copies carry ``item_id`` (source-table PK) verbatim, so even
    compliance refs — whose ``ref_id`` hash is irreversible — reconstruct
    fully on read.

    Fallback (original rows gone / editorial posts without a WI): rebuild rows
    from the frozen ``references_json``. ``ref_id`` recovers regulations
    (``reg:<chunk uuid>``) and cases (``case:<case_ref>``) completely;
    compliance rows get ``item_id=NULL`` and render as stub cards.

    Best-effort like ``persist_item_references``: failures are logged and
    swallowed — a refs hiccup must not fail the import (the item still
    renders; the panel degrades).
    """
    try:
        source_item_id = post.get("source_item_id")
        if not source_item_id and post.get("source_post_id"):
            root = (
                supabase.table("blog_posts")
                .select("source_item_id")
                .eq("post_id", post["source_post_id"])
                .maybe_single()
                .execute()
            )
            if root is not None and root.data is not None:
                source_item_id = root.data.get("source_item_id")

        payloads: list[dict[str, Any]] = []

        if source_item_id:
            rows = (
                supabase.table("workspace_item_references")
                .select(
                    "item_id, ref_id, domain, n, relevance, "
                    "sub_queries, content_word_count"
                )
                .eq("wi_id", str(source_item_id))
                .eq("used", True)
                .execute()
            ).data or []
            payloads = [
                {**row, "wi_id": wi_id, "used": True}
                for row in rows
                if row.get("ref_id")
            ]

        if not payloads:
            for ref in post.get("references_json") or []:
                ref_id = (ref.get("ref_id") or "").strip()
                domain = ref.get("domain")
                n = ref.get("n")
                if not ref_id or n is None or domain not in ("regulations", "cases", "compliance"):
                    continue
                item_uuid = None
                if domain == "regulations" and ref_id.startswith("reg:"):
                    item_uuid = ref_id[4:]
                payloads.append({
                    "wi_id": wi_id,
                    "item_id": item_uuid,
                    "ref_id": ref_id,
                    "domain": domain,
                    "n": int(n),
                    "relevance": ref.get("relevance") or "medium",
                    "used": True,  # the snapshot froze used_only refs
                    "sub_queries": [],
                    "content_word_count": 0,
                })

        if not payloads:
            return 0

        supabase.table("workspace_item_references").insert(payloads).execute()
        return len(payloads)
    except Exception as e:  # noqa: BLE001
        logger.exception(
            "blog import: reference materialization failed for wi_id=%s: %s",
            wi_id, e,
        )
        return 0


def create_blog_item(
    supabase: SupabaseClient,
    *,
    user_id: str,
    conversation_id: str,
    token: str,
) -> tuple[dict, bool]:
    """Copy the published post behind ``token`` into the conversation as a
    ``kind=agent_search`` workspace item (تحليل قانوني) with a REAL المراجع
    panel. Returns ``(item_row, already_attached)``.

    ``agent_search`` (not ``note``) so the import gets the exact same
    treatment as a native search output: read-only viewer, clickable [n]
    citations resolving through ``GET /workspace/{id}/references``, unfold
    manifest for the agents, action-bar share/feedback. References are
    materialized via ``_materialize_references`` right after the insert.

    Ownership of the conversation is verified here. Dedup: one live import per
    root post per conversation, keyed on ``metadata->>'source_post_id'``
    (root-propagated, same key as مدوناتي import; kind-agnostic so older
    imports also match). Summary is set at insert (the blog snippet) so the
    router context is populated without an analyzer pass.
    """
    # Lazy imports — matches the workspace_service ↔ message_service convention.
    from backend.app.services.message_service import verify_conversation_ownership
    from backend.app.services.workspace_service import create_workspace_item

    conv = verify_conversation_ownership(supabase, conversation_id, user_id)

    post = resolve_post_by_token(supabase, token)
    if post is None:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.ARTIFACT_NOT_FOUND,
            detail="المدونة غير موجودة أو تم إلغاء نشرها",
        )

    root_id = post.get("source_post_id") or post["post_id"]

    try:
        existing = (
            supabase.table("workspace_items")
            .select("*")
            .eq("conversation_id", conversation_id)
            .eq("metadata->>source_post_id", root_id)
            .is_("deleted_at", "null")
            .limit(1)
            .execute()
        )
        rows = existing.data or []
    except Exception as e:  # noqa: BLE001
        logger.exception("Error checking existing blog import: %s", e)
        rows = []
    if rows:
        # Pre-fix debris: a re-import into a still-abandoned conversation
        # gets the honest title too.
        _retitle_new_conversation(
            supabase, conv, rows[0].get("title") or "مدونة مستوردة",
        )
        return rows[0], True

    question_text = (post.get("question_text") or "").strip()
    title = (post.get("title") or "").strip() or question_text[:_ITEM_TITLE_MAX].strip() or "مدونة مستوردة"
    content_md = post.get("content_md") or ""

    item = create_workspace_item(
        supabase,
        user_id,
        kind="agent_search",
        created_by="user",
        title=title,
        conversation_id=conversation_id,
        content_md=content_md,
        summary=make_snippet(content_md, 400),
        metadata={
            "subtype": post.get("subtype") or _DEFAULT_IMPORT_SUBTYPE,
            "source_post_id": root_id,
            "source_token": token,
        },
    )

    _materialize_references(supabase, item["item_id"], post)
    _retitle_new_conversation(supabase, conv, title)
    return item, False


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
    post_ids: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """List the caller's own blogs (مدوناتي) — owner-scoped.

    Newest first, both display_modes, not deleted. Returns card dicts
    (post_id, token, title, snippet, subtype, display_mode, is_public,
    created_at) — the management list, so it carries post_id + is_public for
    the per-row publish toggle.

    ``post_ids`` is the BM25 search path (bm25 plan §5.2): an ORDERED id list
    from ``search_service.corpus_search_ids``, already owner-scoped by the RPC.
    When present, only those posts are returned and THEIR ORDER IS PRESERVED —
    the ``created_at`` ordering below would otherwise silently discard the
    ranking, which is the entire point of having searched. An empty list means
    "nothing matched"; ``None`` means "no search", and the two must not be
    conflated (an empty list returning the whole shelf is the classic version of
    this bug).
    """
    limit = max(1, min(int(limit or 100), 200))
    offset = max(0, int(offset or 0))

    if post_ids is not None and not post_ids:
        return []

    try:
        qb = (
            supabase.table("blog_posts")
            .select(
                "post_id, token, title, content_md, subtype, "
                "display_mode, is_public, source_post_id, created_at"
            )
            .eq("owner_user_id", user_id)
            .is_("deleted_at", "null")
        )
        if post_ids is not None:
            # Ranked subset: no DB ordering (it is re-imposed below) and no
            # range — the id list is already bounded by the search limit.
            result = qb.in_("post_id", post_ids).execute()
        else:
            result = (
                qb.order("created_at", desc=True)
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

    rows = [r for r in (result.data or []) if r.get("post_id")]
    if post_ids is not None:
        rank = {str(pid): i for i, pid in enumerate(post_ids)}
        rows.sort(key=lambda r: rank.get(str(r.get("post_id")), len(post_ids)))
    return [to_my_blog_item(row) for row in rows]


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
