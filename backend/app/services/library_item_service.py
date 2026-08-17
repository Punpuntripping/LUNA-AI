"""Case-B library carrier — bring ONE public library page into a conversation.

Backs ``POST /api/v1/conversations/{id}/library-items``
(``backend/app/api/library_items.py``). See
``.claude/plans/simple_search_family.md`` §8 «Case B — the library carrier» and
the pinned route contract in §12a C3.

The problem it solves: the authed «تحدّث مع ريحان عن هذه الصفحة» CTA used to link
to a bare ``/chat``, so the object identity the reader was holding was lost at the
door. This service is the door.

Shape — a deliberate twin of ``blog_service.create_blog_item``:

    public object → server-side snapshot → ``workspace_items`` row with
    ``summary`` pre-filled → ``attachment_ids`` → ``message_attachments``
    → a summary on every later turn, unfoldable on demand.

Three things are load-bearing and were verified live against
``dwgghvxogtwyaxmbgjod`` before this file was written:

``kind='references'``
    A real member of the ``workspace_item_kind`` enum (migration 026) and
    **exempt from the 15-item cap** — ``enforce_artifact_cap`` counts only
    ``agent_search | agent_writing | note`` (migration 031). Probed live: a
    conversation already holding 15 counted items rejects a 16th ``note`` with
    ``workspace_items_cap_exceeded`` and accepts a ``references`` row in the same
    breath. So carrying library pages never crowds a user's workspace.
    ``workspace_context._partition`` already buckets the kind (``:233-238``), so
    the agents see it with no change on their side.

``summary`` is pre-filled
    The ``summarize_artifact_on_insert`` trigger fires only ``WHEN new.summary IS
    NULL``, so setting it at insert means **no analyzer pass runs** — the router
    context is populated for free. Probed live: a pre-filled summary survives the
    insert verbatim.

Zero OCR quota, zero send-payload change
    The returned ``item_id`` rides the EXISTING ``attachment_ids`` array.
    ``message_service._insert_attachment_links`` filters only on ownership so it
    already accepts any owned ``workspace_items.item_id``, and
    ``_estimate_ocr_pages`` skips resolved non-``attachment`` kinds
    (``message_service.py:478``), so a library object contributes 0 projected
    pages to the quota gate.

Two invariants this module owes the rest of the system:

``title`` == the public page's H1
    The card label is how the reader refers to the object on a later turn, and
    §2.3.1's premise is that the agent sees what the user sees. So every title
    branch reuses the page's OWN derivation (``judgment_subject`` for a حكم, the
    ``{article_label} من {regulation}`` heading for a مادة,
    ``clean_title → title`` for a نظام, ``title → question_text`` for a post) and
    the 150-char cap cuts on a word boundary with «…» rather than mid-word.

``metadata.source_page_key`` == the object, not the spelling
    A page has several legal URL spellings (slug or uuid; for a مادة also the
    ``{reg_id}#{no}`` gate key). The §8 dedup key
    (``source_page_type`` + ``source_page_id``) is kept verbatim, but the probe
    matches the NORMALIZED key so re-carrying one page is one card. The same
    resolution feeds ``metadata.simple_search_object``, so the label on the card
    and the object the family opens are the same row by construction.

Coverage is FOUR page types — ``regulation | article | judgment | blog``.
``fetch_grounding`` has no grounder for ``circular`` / ``form`` / ``calculator`` /
``topic`` (nor, despite its docstring, for ``service``), so those get a clean
Arabic 400 rather than an empty item. See ``SUPPORTED_PAGE_TYPES``.

Sync throughout (service-role client) — call via ``run_db``. Every user-facing
message is Arabic. The service-role client bypasses RLS, so the explicit
ownership filters here ARE the scope enforcement.
"""
from __future__ import annotations

import logging
from typing import Any, NamedTuple, Optional

from supabase import Client as SupabaseClient

from backend.app.errors import ErrorCode, LunaHTTPException
# THE (page_type, page_id) resolver. It already takes exactly the pair the اسأل
# ريحان widget holds — including the composite ``{reg_slug}/{article_slug}``
# shape for مواد — so no new page identity is invented here. Imported rather
# than reimplemented so the carrier and the anon popup can never disagree about
# what a page's text is.
from backend.app.services.ask_service import (
    _looks_like_uuid,
    _resolve_content_id,
    fetch_grounding,
)
from backend.app.services.blog_service import make_snippet
# ``judgment_subject`` — NOT ``judgment_display_title``. The /judgments page H1 is
# ``doc.subject`` (``library_service.py:4974`` → ``judgments/[slug]/page.tsx:229-231``);
# ``judgment_display_title`` appends « — {court} {year}هـ» and is the <title>/card
# string for the SEO grid. Verified live on rayhanai.com: the rendered <h1> is the
# subject alone while the <title> carries the court tail. Measured over all 10,000
# published rulings, the tail made 10,000/10,000 WI titles differ from their own
# page. See ``test_reference_library_links`` — the المراجع panel settled the same
# question the same way.
from shared.seo.judgment_naming import judgment_subject

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# The page types that can be carried. A STRICT SUBSET of the frontend's
# ``LibraryPageType`` — mirrored there as ``LibraryItemPageType``
# (``frontend/types/index.ts``), whose type predicate keeps the UI from ever
# offering the button on a type this tuple does not list.
SUPPORTED_PAGE_TYPES: tuple[str, ...] = ("regulation", "article", "judgment", "blog")

# Arabic label per carried type — used for the WI's content frame so the agent
# (and the workspace panel) can tell a نظام from a حكم at a glance.
_PAGE_TYPE_LABEL_AR: dict[str, str] = {
    "regulation": "نظام",
    "article": "مادة",
    "judgment": "حكم قضائي",
    "blog": "مدونة",
}

# Public route shape per type, used to record where the object came from.
# ``article`` is absent on purpose: its page_id is already the composite
# ``{reg_slug}/{article_slug}``, so it rides the regulation prefix (see
# ``_public_path``).
_PUBLIC_PREFIX: dict[str, str] = {
    "regulation": "/regulations",
    "article": "/regulations",
    "judgment": "/judgments",
    "blog": "/blog",
}

# Same cap blog imports use for a derived item title.
_ITEM_TITLE_MAX = 150
# Sentinel appended when a title is cut. Arabic «…» (U+2026), one char, so the
# cut label still fits the cap.
_ELLIPSIS = "…"
# The page types that HAVE a simple_search entry level. ``blog`` is deliberately
# absent (there is no level for it), so a blog carry's missing identity is not a
# downgrade and must not log like one.
_BRIDGEABLE_PAGE_TYPES: frozenset[str] = frozenset({"regulation", "article", "judgment"})
# Pre-filled agent-facing summary length — matches ``create_blog_item``.
_SUMMARY_CHARS = 400
# The placeholder a brand-new conversation is born with. Only ever replaced,
# never a title the user or the auto-titler already chose.
_DEFAULT_CONVO_TITLE = "محادثة جديدة"


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def normalize_page_type(raw: Optional[str]) -> str:
    """Lower/trim a client-supplied page_type (``fetch_grounding`` does the same)."""
    return (raw or "").strip().lower()


def normalize_page_id(raw: Optional[str]) -> str:
    """Trim a client-supplied page_id. Case is PRESERVED — blog tokens and slugs
    are both case-sensitive identities."""
    return (raw or "").strip()


def is_supported_page_type(page_type: Optional[str]) -> bool:
    """True when ``fetch_grounding`` has a grounder for this type."""
    return normalize_page_type(page_type) in SUPPORTED_PAGE_TYPES


def _truncate_title(text: str, limit: int = _ITEM_TITLE_MAX) -> str:
    """Cut ``text`` to ``limit`` chars on a WORD boundary, marked with «…».

    A bare ``[:limit]`` slice cuts Arabic mid-word — measured over the 10,000
    published rulings, 460 display titles were cut inside a word («… ضريبة
    القيمة المضافة والسلع الانتقائية ف») and read as corrupted rather than
    shortened. Worse, an unmarked cut is indistinguishable from the object's real
    name, so a user (or an agent) quoting the card label back quotes a name that
    does not exist.

    The «…» is inside the cap, so the returned string is never longer than
    ``limit``. A single word longer than the cap has no boundary to find, so it
    falls back to a hard cut — still marked.
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    window = text[: limit - 1].rstrip()
    cut = window.rfind(" ")
    # Keep at least half the budget: a title whose first word eats the cap would
    # otherwise collapse to «…».
    head = window[:cut].rstrip() if cut >= limit // 2 else window
    return f"{head.rstrip(' ،؛:-–—')}{_ELLIPSIS}"


def _title_from_slug(page_type: str, page_id: str) -> str:
    """Last-resort human title from the slug itself.

    Only ever reached when the corpus lookup misses but grounding did not — rare,
    but a WI must never render with an empty title. For an article the composite
    ``{reg_slug}/{article_slug}`` is reduced to its مادة half.
    """
    raw = page_id
    if page_type == "article" and "/" in page_id:
        raw = page_id.rsplit("/", 1)[-1]
    if _looks_like_uuid(raw):
        return _PAGE_TYPE_LABEL_AR.get(page_type, "صفحة من المكتبة")
    text = raw.replace("-", " ").replace("_", " ").strip()
    if not text:
        return _PAGE_TYPE_LABEL_AR.get(page_type, "صفحة من المكتبة")
    return _truncate_title(text)


def _public_path(page_type: str, page_id: str) -> Optional[str]:
    """Site-relative path of the page this item was carried from.

    Recorded in metadata (not in the body) so nothing here depends on
    ``PUBLIC_WEB_URL`` being set. ``None`` for a uuid/gate-key page_id, which is
    not a public URL shape.

    ⚠ ``blog`` is exempt from the uuid guard on purpose: a share token is 32 hex
    chars, and ``uuid.UUID()`` PARSES a dash-less 32-hex string happily — so the
    guard would reject every real blog token and silently drop the path. The
    token IS the public segment (``/blog/<token>``), so there is nothing to
    guard against.
    """
    prefix = _PUBLIC_PREFIX.get(page_type)
    if not prefix or not page_id:
        return None
    if page_type == "blog":
        return f"{prefix}/{page_id}"
    if page_type == "article":
        # Only the composite '{reg_slug}/{article_slug}' shape is a real URL.
        if "/" not in page_id or _looks_like_uuid(page_id.split("/", 1)[0]):
            return None
        return f"{prefix}/{page_id}"
    if _looks_like_uuid(page_id) or "#" in page_id:
        return None
    return f"{prefix}/{page_id}"


def build_content(title: str, page_type: str, body: str) -> str:
    """Frame the grounding text as the WI body.

    A bare 6k-char slab of نظام text with no header is unattributable once it is
    sitting in a workspace beside three other items — the frame is what lets an
    agent (and the reader) say WHICH object this is. Deliberately two lines:
    everything below is the page's own text, verbatim from ``fetch_grounding``.
    """
    label = _PAGE_TYPE_LABEL_AR.get(page_type, "صفحة")
    head = f"# {title}\n\n*{label} — من مكتبة ريحان*"
    body = (body or "").strip()
    return f"{head}\n\n{body}" if body else head


# ---------------------------------------------------------------------------
# Title resolution (sync; one small read per carry)
#
# THE RULE: the card label must equal the public page's H1. The card label is
# how a reader refers to the object on a later turn, and §2.3.1's premise is that
# the agent sees what the user sees — a card whose name is not the page's name
# breaks that at the source. So every branch below reuses the SAME derivation the
# public page uses; none of them re-derives a title of its own.
# ---------------------------------------------------------------------------


def _regulation_title(supabase: SupabaseClient, regulation_id: Any, page_id: str) -> str:
    """``regulations_v2.clean_title → title`` for one نظام id.

    The chain the /regulations doc page uses for its H1
    (``library_service.py:2901``). Shared by the نظام and the مادة branches so
    the parent name printed on a مادة card is the same string as the نظام card's.
    """
    if not regulation_id:
        return ""
    try:
        res = (
            supabase.table("regulations_v2")
            .select("clean_title, title")
            .eq("id", str(regulation_id))
            .limit(1)
            .execute()
        )
        rows = res.data or []
    except Exception as e:  # noqa: BLE001
        logger.warning("library carry: regulation title lookup failed (%s): %s", page_id, e)
        return ""
    if not rows:
        return ""
    return (rows[0].get("clean_title") or rows[0].get("title") or "").strip()


def _title_regulation(supabase: SupabaseClient, page_id: str) -> str:
    """The نظام page H1 — verified live: /regulations/نظام-العمل renders
    «نظام العمل», which is exactly this chain."""
    content_id = _resolve_content_id(supabase, "regulation", page_id)
    if not content_id:
        return ""
    return _regulation_title(supabase, content_id, page_id)


def _lookup_seo_article(
    supabase: SupabaseClient, page_id: str
) -> tuple[Optional[dict[str, Any]], bool]:
    """The ``seo_articles`` row behind ANY ``page_id`` shape ``_ground_article``
    accepts. Returns ``(row, ambiguous)``.

    ONE lookup, TWO consumers — the title and the ``simple_search`` identity.
    They used to walk separate code paths of different width, which is how the
    identity bridge came to cover fewer shapes than the route accepts (a silent
    Case-A downgrade: right body, right title, HTTP 200, no object). Sharing the
    row also means the label on the card and the id the agent opens can never
    describe two different مواد.

    The four shapes, from ``_ground_article``'s docstring:
      * ``{reg_slug}/{article_slug}`` — what the مادة page sends. Unambiguous.
      * ``{regulation_id}#{article_no}`` — the ``seo_item_meta`` gate key.
      * the ``seo_articles`` row uuid.
      * a bare article slug — LEGACY and genuinely AMBIGUOUS: «المادة-80» exists
        in ~1,769 regulations, so the row returned is an arbitrary one. That is
        tolerable for a title (fail-soft, and the body is the point) but NOT for
        an identity, so it is reported via the second element and the bridge
        declines it. ``ambiguous`` is decided empirically — the query asks for
        TWO rows, and a second row means the slug names more than one مادة.
    """
    cols = "id, slug, article_label, article_no, regulation_id"
    try:
        if "/" in page_id:
            reg_slug, _, art_slug = page_id.partition("/")
            content_id = _resolve_content_id(supabase, "regulation", reg_slug.strip())
            if not content_id:
                return None, False
            res = (
                supabase.table("seo_articles")
                .select(cols)
                .eq("regulation_id", str(content_id))
                .eq("slug", art_slug.strip())
                .limit(1)
                .execute()
            )
            return (res.data or [None])[0], False
        if "#" in page_id:
            reg_id, _, no = page_id.partition("#")
            if not (no.strip().isdigit() and _looks_like_uuid(reg_id)):
                return None, False
            res = (
                supabase.table("seo_articles")
                .select(cols)
                .eq("regulation_id", reg_id)
                .eq("article_no", int(no.strip()))
                .limit(1)
                .execute()
            )
            return (res.data or [None])[0], False
        if _looks_like_uuid(page_id):
            res = (
                supabase.table("seo_articles")
                .select(cols)
                .eq("id", page_id)
                .limit(1)
                .execute()
            )
            return (res.data or [None])[0], False
        # Bare slug — legacy. Two rows asked for so ambiguity is DETECTED
        # rather than assumed away.
        res = (
            supabase.table("seo_articles")
            .select(cols)
            .eq("slug", page_id)
            .limit(2)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return None, False
        ambiguous = len(rows) > 1
        if ambiguous:
            logger.warning(
                "library carry: bare article slug %r matches >1 نظام — carrying the "
                "page without a simple_search identity (the searcher will resolve it)",
                page_id,
            )
        return rows[0], ambiguous
    except Exception as e:  # noqa: BLE001
        logger.warning("library carry: article lookup failed (%s): %s", page_id, e)
        return None, False


def _article_label(art: dict[str, Any]) -> str:
    """``seo_articles.article_label``, falling back to «المادة {no}» — the same
    label the مادة page prints as its ``<h1>``."""
    label = (art.get("article_label") or "").strip()
    if not label and art.get("article_no") is not None:
        label = f"المادة {art['article_no']}"
    return label


def _title_article(supabase: SupabaseClient, page_id: str) -> str:
    """«المادة 80 من نظام العمل» — the مادة page's own composed heading.

    The separator is « من », not « — ». Verified live on
    /regulations/نظام-العمل/المادة-80: the page composes
    ``` `${doc.article_label} من ${doc.regulation.title}` ``` for its <title>/OG
    (``regulations/[slug]/[article]/page.tsx:55``) and renders the two halves
    stacked in the header block (the نظام as a link above, ``:179-181``). An em
    dash matched neither, on 100% of carries.

    ``articles_v2``/``regulations_v2`` are VIEWS (no FK), so the parent نظام is a
    second fetch, never a PostgREST embed.
    """
    art, _ambiguous = _lookup_seo_article(supabase, page_id)
    if not art:
        return ""
    label = _article_label(art)
    if not label:
        return ""
    reg_title = _regulation_title(supabase, art.get("regulation_id"), page_id)
    return f"{label} من {reg_title}" if reg_title else label


def _title_judgment(supabase: SupabaseClient, page_id: str) -> str:
    """``judgment_subject`` over the FULL title chain — the /judgments page H1.

    The four title-source columns (``short_summary → summary → facts → ruling``)
    are all selected on purpose: the page H1 walks that same chain, and selecting
    fewer would give the ~1k summary-less rulings a different title here than on
    their own page (``library_service.py:4302-4317``).

    NOT ``judgment_display_title``: that appends « — {court} {year}هـ», which is
    the <title> and the SEO grid card, not the H1. See the import comment.
    """
    content_id = _resolve_content_id(supabase, "judgment", page_id)
    if not content_id:
        return ""
    try:
        res = (
            supabase.table("cases")
            .select(
                "court, court_level, case_number, judgment_number, date_hijri, "
                "short_summary, summary, facts, ruling"
            )
            .eq("id", str(content_id))
            .limit(1)
            .execute()
        )
        rows = res.data or []
    except Exception as e:  # noqa: BLE001
        logger.warning("library carry: judgment title lookup failed (%s): %s", page_id, e)
        return ""
    if not rows:
        return ""
    try:
        return (judgment_subject(rows[0]) or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.warning("library carry: judgment_subject failed (%s): %s", page_id, e)
        return ""


def _title_blog(supabase: SupabaseClient, page_id: str) -> str:
    """``blog_posts.title`` → ``question_text``, keyed on the share token — the
    same live-post filter ``_ground_blog`` uses.

    This chain IS the rendered ``<h1>``, in every one of the four render
    branches, and it was re-verified live rather than assumed:

      * ``display_mode='title'`` → ``BlogArticleView`` H1 = ``title ||
        question_text`` (``BlogArticleView.tsx:94,137``).
      * ``display_mode='question'`` **with** a title → ``PublicAnswerView`` H1 =
        ``title``; the question sits above it in the «السؤال» card, which is not
        a heading (``PublicAnswerView.tsx:77-80,140``).
      * question mode with NO title → ``showHeading`` is false, the page renders
        no ``<h1>`` at all and the السؤال card leads — so ``question_text`` is
        the label a reader would use.
      * neither → nothing to show; ``resolve_title`` falls back to «مدونة».

    ⚠ ``postHeadline`` (``blog/[token]/page.tsx:12-16``) is a DIFFERENT string —
    it prefers ``question_text`` in question mode — but it feeds ``<title>``, OG
    and the Article schema headline, none of which is the page's heading. Live
    check on token ``9687fb4c…``: ``<h1>`` = «أثر إصلاح السيارة على دعوى تعويض
    تأمينية» (= ``title``) while ``<title>`` = «عندي قضية تأمينية…». Measured
    over all 100 live posts, this chain matches the rendered H1 100/100 and
    ``postHeadline`` matches it 9/100. Do not "fix" this to ``postHeadline``.
    """
    try:
        res = (
            supabase.table("blog_posts")
            .select("title, question_text")
            .eq("token", page_id)
            .is_("deleted_at", "null")
            .limit(1)
            .execute()
        )
        rows = res.data or []
    except Exception as e:  # noqa: BLE001
        logger.warning("library carry: blog title lookup failed (%s): %s", page_id, e)
        return ""
    if not rows:
        return ""
    row = rows[0]
    return (row.get("title") or "").strip() or (row.get("question_text") or "").strip()


def resolve_title(supabase: SupabaseClient, page_type: str, page_id: str) -> str:
    """Human title for the carried page — the label the chip and the WI card show.

    Every branch is fail-soft: a lookup miss falls through to a slug-derived
    title, because the BODY is what the carry is for and a title hiccup must not
    cost the user their object.

    The cap is applied HERE and only here, on a word boundary with «…», so a long
    page name is visibly shortened rather than silently corrupted.
    """
    resolver = {
        "regulation": _title_regulation,
        "article": _title_article,
        "judgment": _title_judgment,
        "blog": _title_blog,
    }.get(page_type)

    title = ""
    if resolver is not None:
        title = (resolver(supabase, page_id) or "").strip()
    if not title:
        title = _title_from_slug(page_type, page_id)
    return _truncate_title(title)


# ---------------------------------------------------------------------------
# Conversation housekeeping
# ---------------------------------------------------------------------------


def _retitle_new_conversation(
    supabase: SupabaseClient,
    conv: dict,
    item_title: str,
) -> None:
    """Rename a still-pristine «محادثة جديدة» after the page it now carries.

    Twin of ``blog_service._retitle_new_conversation`` and for the same reason:
    the carry flows (the anon return path especially) create the conversation
    BEFORE the user commits a message, so an abandoned carry would sit in the
    sidebar disguised as an empty chat while silently holding a نظام.

    Only fires while the conversation is untouched (default title AND zero
    messages), and the UPDATE re-checks the title server-side so a concurrent
    first-message auto-title is never clobbered. Best-effort — a rename hiccup
    must not fail the carry.
    """
    try:
        if (conv.get("title_ar") or "").strip() != _DEFAULT_CONVO_TITLE:
            return
        if conv.get("message_count"):
            return
        title = _truncate_title(item_title or "", 60)
        if not title:
            return
        (
            supabase.table("conversations")
            .update({"title_ar": title})
            .eq("conversation_id", conv["conversation_id"])
            .eq("title_ar", _DEFAULT_CONVO_TITLE)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception(
            "library carry: conversation retitle failed for %s: %s",
            conv.get("conversation_id"), e,
        )


def find_existing_library_item(
    supabase: SupabaseClient,
    *,
    user_id: str,
    conversation_id: str,
    page_type: str,
    page_id: str,
    page_key: Optional[str] = None,
) -> Optional[dict]:
    """The live carry of this page in this conversation, if any.

    Dedup key is ``metadata.source_page_type`` + ``metadata.source_page_id``
    (§8 / §12a C3) — kind-agnostic, so it still matches if the carrier's kind
    ever changes. What CHANGED is the matching, not the pin: the ``page_id``
    half is matched through its NORMALIZED form (``metadata.source_page_key``,
    written by every carry since this fix) so a page carried twice under two
    spellings of its own identity is one card, not two.

    Why it needed fixing: ``page_id`` is a public URL segment with several legal
    spellings for the same object — «نظام-العمل» and its uuid, and for a مادة the
    composite slug, the ``{reg_id}#{no}`` gate key and the ``seo_articles`` uuid.
    Matching the literal string made re-carrying the SAME page a second full copy
    of its body in the agent's context. Measured: 5 carries of 4 distinct objects
    produced 8 rows.

    TWO passes, key first then the literal pair. The second is not a fallback for
    correctness but for HISTORY: rows written before this fix carry no
    ``source_page_key``, and a user whose conversation holds one must not get a
    duplicate the first time they re-carry.

    ``user_id`` is filtered even though the caller already verified conversation
    ownership: the service-role client bypasses RLS, so belt AND braces.
    Fail-soft — a lookup error means "no dupe", and the worst case is a second
    card, never a lost carry.
    """

    def _first(column: str, value: str) -> Optional[dict]:
        res = (
            supabase.table("workspace_items")
            .select("*")
            .eq("conversation_id", conversation_id)
            .eq("user_id", user_id)
            .eq("metadata->>source_page_type", page_type)
            .eq(column, value)
            .is_("deleted_at", "null")
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        return rows[0] if rows else None

    try:
        if page_key:
            hit = _first("metadata->>source_page_key", page_key)
            if hit is not None:
                return hit
        return _first("metadata->>source_page_id", page_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("library carry: dedup lookup failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Page identity — ONE resolution, TWO uses (the dedup key and the bridge)
# ---------------------------------------------------------------------------


class PageIdentity(NamedTuple):
    """What a carried page IS, independent of how its URL was spelled.

    ``key``
        The normalized dedup key, never empty. Written to
        ``metadata.source_page_key`` and matched by
        ``find_existing_library_item``, so two spellings of one page are one
        card. Shaped like the ``ref_id()`` it corresponds to (``regdoc:`` /
        ``article:`` / ``case:`` / ``blog:``) purely so it is readable in a row
        dump; nothing parses it.

    ``obj``
        The ``ResolvedObject``-shaped dict for
        ``metadata.simple_search_object``, WITHOUT its ``title`` (the caller
        adds that), or ``None`` when the object could not be identified —
        genuine miss, unreachable corpus row, or an ambiguous legacy slug. The
        runner then falls back to its searcher, which is the documented safe
        degradation.
    """

    key: str
    obj: Optional[dict[str, Any]]


def _fallback_key(page_type: str, page_id: str) -> str:
    """Key for a page whose identity did not resolve — the literal pair, which
    is exactly the pre-fix behaviour: idempotent for the same spelling, and
    never claiming two pages are the same object."""
    return f"{page_type}:{page_id}"


def resolve_page_identity(
    supabase: SupabaseClient, page_type: str, page_id: str
) -> PageIdentity:
    """Resolve ``(page_type, page_id)`` to its normalized key + row identity.

    Covers **every** ``page_id`` shape ``create_library_item`` accepts, which is
    the shape set ``fetch_grounding`` accepts. It did not: the article branch
    required ``"/" in page_id`` and returned ``None`` for the ``seo_articles``
    uuid and the ``{reg_id}#{no}`` gate key — both of which ground and title
    perfectly well. A carry then looked flawless (right body, right title, HTTP
    200) while silently degrading Case B to a Case-A re-search, with nothing
    anywhere saying so.

    Fail-soft by contract: anything unresolvable yields ``obj=None`` (+ a
    ``_fallback_key``). A guessed object is worse than no object — the searcher
    fallback is correct and cheap, opening the wrong نظام is not.
    """
    try:
        if page_type == "regulation":
            reg_id = _resolve_content_id(supabase, "regulation", page_id)
            if not reg_id:
                return PageIdentity(_fallback_key(page_type, page_id), None)
            return PageIdentity(
                f"regdoc:{reg_id}",
                {"level": "regulation_doc", "regulation_id": reg_id},
            )

        if page_type == "article":
            # ONE lookup for all four shapes, shared with the title (so the card
            # label and the identity can never point at different مواد).
            art, ambiguous = _lookup_seo_article(supabase, page_id)
            if not art or ambiguous:
                return PageIdentity(_fallback_key(page_type, page_id), None)
            reg_id = str(art.get("regulation_id") or "")
            # article_number is TEXT ("81", "1-1", "81 مكرر") — never coerce.
            article_no = str(art.get("article_no") or "").strip()
            if not reg_id or not article_no:
                return PageIdentity(_fallback_key(page_type, page_id), None)
            res = (
                supabase.table("articles_v2")
                .select("id")
                .eq("regulation_id", reg_id)
                .eq("article_number", article_no)
                .limit(1)
                .execute()
            )
            rows = getattr(res, "data", None) or []
            if not rows or not rows[0].get("id"):
                # The مادة exists in the SEO sidecar but has no articles_v2 row
                # to open. Still a stable identity for DEDUP — the same مادة under
                # three spellings is still one card — but no bridge.
                return PageIdentity(f"article:{reg_id}#{article_no}", None)
            article_id = str(rows[0]["id"])
            return PageIdentity(
                f"article:{article_id}",
                {
                    "level": "article",
                    "article_id": article_id,
                    "regulation_id": reg_id,
                    "article_number": article_no,
                },
            )

        if page_type == "judgment":
            case_id = _resolve_content_id(supabase, "judgment", page_id)
            if not case_id:
                return PageIdentity(_fallback_key(page_type, page_id), None)
            # case_ref, NOT cases.id: `case:` refs have always carried the ref
            # (ura/enrich._enrich_cases), and ResolvedObject.ref_id() prefers it.
            # Writing the uuid alone would mint a ref_id that resolves to nothing.
            res = (
                supabase.table("cases")
                .select("id, case_ref")
                .eq("id", case_id)
                .limit(1)
                .execute()
            )
            rows = getattr(res, "data", None) or []
            if not rows:
                return PageIdentity(_fallback_key(page_type, page_id), None)
            row_id = str(rows[0].get("id") or "")
            case_ref = str(rows[0].get("case_ref") or "")
            return PageIdentity(
                f"case:{case_ref or row_id}",
                {"level": "judgment", "case_id": row_id, "case_ref": case_ref},
            )

        if page_type == "blog":
            # No simple_search level for a post — but the token IS the identity,
            # so the dedup key is exact. Hex, hence casefold-safe.
            return PageIdentity(f"blog:{page_id.casefold()}", None)
    except Exception as exc:  # noqa: BLE001 — identity is an optimisation
        logger.warning(
            "library_item: page identity unresolved for %s/%s: %s",
            page_type, page_id, exc,
        )
    return PageIdentity(_fallback_key(page_type, page_id), None)


def build_simple_search_object(
    supabase: SupabaseClient,
    page_type: str,
    page_id: str,
    title: str,
) -> dict[str, Any] | None:
    """The pre-resolved identity `simple_search` reads off the carried item.

    ``source_page_id`` is a **slug** and
    ``agents.simple_search.runner.resolved_from_attachment`` needs row ids — a
    slug alone drops the turn back to the searcher, silently turning Case B into
    Case A. So the ids are resolved here, once, at carry time, and the family
    starts the turn already knowing which object the reader was holding.

    (It does NOT let the family skip its searcher: §2.3 was corrected on
    2026-08-16 and ``runner.py`` now runs the searcher regardless, treating the
    carried page as a demoted handle it may ignore. The value here is that the
    handle is a real object rather than a slug.)

    Returns a ``ResolvedObject``-shaped dict for
    ``metadata.simple_search_object``, or ``None``. ``blog`` has no
    simple_search level and always returns None.
    """
    obj = resolve_page_identity(supabase, page_type, page_id).obj
    return {**obj, "title": title} if obj else None


# ---------------------------------------------------------------------------
# The carrier
# ---------------------------------------------------------------------------


def create_library_item(
    supabase: SupabaseClient,
    *,
    user_id: str,
    conversation_id: str,
    page_type: str,
    page_id: str,
) -> tuple[dict, bool]:
    """Carry the public library page ``(page_type, page_id)`` into the
    conversation as a ``kind='references'`` workspace item. Returns
    ``(item_row, already_attached)``.

    ``references`` (not ``note`` / ``agent_search``) because the card is a
    CITED OBJECT the user brought in, not an output anyone authored — and
    because that kind is exempt from the 15-item cap, so a reader who carries
    six regulations into one chat does not lose their workspace to it.

    Body is ``ask_service.fetch_grounding`` verbatim under a two-line frame.
    That is a BOUNDED slice by construction — ``MAX_CONTEXT_CHARS`` (6,000)
    caps every type — which is what §8's «never the whole regulation» asks for.
    (Note the shapes are not literally summaries: a regulation grounds on its
    first four DOCUMENT-ORDER chunks, a judgment on its composed narrative.
    Both are capped; see the module docstring of ``ask_service``.)

    Ownership of the conversation is verified here. Idempotent per
    conversation + page: a live carry of the same page returns
    ``already_attached=True`` with the existing row and writes nothing.
    """
    # Lazy import — matches the workspace_service ↔ message_service convention
    # and keeps this module importable without pulling the message stack in.
    from backend.app.services.message_service import verify_conversation_ownership
    from backend.app.services.workspace_service import create_workspace_item

    page_type = normalize_page_type(page_type)
    page_id = normalize_page_id(page_id)

    if not page_id:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail="معرف الصفحة غير صالح",
        )
    if page_type not in SUPPORTED_PAGE_TYPES:
        # circular / form / calculator / topic / service have no grounder — an
        # empty item is worse than a refusal, so refuse (§8 «Coverage today»).
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail="لا يمكن إحضار هذا النوع من الصفحات إلى المحادثة",
        )

    conv = verify_conversation_ownership(supabase, conversation_id, user_id)

    # ONE identity resolution per carry, BEFORE the dedup probe — the normalized
    # key is what makes the probe spelling-independent, and the same result is
    # the simple_search bridge below, so the card and the object can never
    # disagree.
    identity = resolve_page_identity(supabase, page_type, page_id)

    existing = find_existing_library_item(
        supabase,
        user_id=user_id,
        conversation_id=conversation_id,
        page_type=page_type,
        page_id=page_id,
        page_key=identity.key,
    )
    if existing is not None:
        # Pre-fix debris: a re-carry into a still-abandoned conversation gets
        # the honest title too.
        _retitle_new_conversation(supabase, conv, existing.get("title") or "")
        return existing, True

    body = fetch_grounding(supabase, page_type, page_id) or ""
    if not body.strip():
        # Unknown slug, unpublished post, or a page whose text we cannot reach.
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.ARTIFACT_NOT_FOUND,
            detail="تعذّر العثور على محتوى هذه الصفحة",
        )

    title = resolve_title(supabase, page_type, page_id)
    content_md = build_content(title, page_type, body)

    metadata: dict[str, Any] = {
        "subtype": "library_page",
        "source_page_type": page_type,
        "source_page_id": page_id,
        # The §8 pin keeps both fields above verbatim (they are the public
        # identity, and ``source_page_path`` is built from them); this is the
        # normalized form the dedup probe matches on.
        "source_page_key": identity.key,
    }
    path = _public_path(page_type, page_id)
    if path:
        metadata["source_page_path"] = path

    # Pre-resolved identity so simple_search starts the turn holding the real
    # object rather than a slug. Absent → the runner falls back to its searcher,
    # which is the documented safe degradation, not a failure — but it IS a
    # degradation, so a bridgeable type that did not resolve says so out loud
    # instead of leaving a Case-A downgrade with no signal anywhere.
    if identity.obj:
        metadata["simple_search_object"] = {**identity.obj, "title": title}
    elif page_type in _BRIDGEABLE_PAGE_TYPES:
        logger.warning(
            "library carry: no simple_search identity for %s/%s — the item is "
            "carried, but the family will have to re-resolve it (Case-A downgrade)",
            page_type, page_id,
        )

    item = create_workspace_item(
        supabase,
        user_id,
        kind="references",
        created_by="user",
        title=title,
        conversation_id=conversation_id,
        content_md=content_md,
        # Pre-filled from the PAGE TEXT (not the frame) so the router context is
        # populated without an analyzer pass — the trigger only fires on NULL.
        summary=make_snippet(body, _SUMMARY_CHARS),
        metadata=metadata,
    )

    _retitle_new_conversation(supabase, conv, title)
    return item, False
