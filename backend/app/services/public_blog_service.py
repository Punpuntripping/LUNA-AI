"""Public blog wing (مدونة ريحان) — versioned reads/writes + the subject taxonomy.

Plan: ``.claude/plans/blog_subjects.md`` (rev 2). Tables: migrations 153 + 154;
the atomic version flip is migration 155's ``append_public_blog_version()``. All
applied to prod.

WHY THIS IS NOT ``blog_service``
--------------------------------
``blog_posts`` is a FROZEN share-link snapshot: publishing freezes
``content_md`` + the resolved ``Reference[]`` so editing or deleting the source
artifact can never change a link already sent to someone. 99 unlisted links in
the wild depend on that.

``public_blogs`` needs the opposite — an SEO agent rewrites a published article
(plan D15). So it is a **versioned** table: every rewrite APPENDS a row,
``root_id`` is the logical blog, and the slug addresses whichever version is
``is_current``. The two tables never mix; this module never touches
``blog_posts``.

THE THREE PREDICATES (do not confuse them)
------------------------------------------
``is_current``    which version the slug serves. Exactly one per ``root_id``,
                  enforced by the partial unique index ``idx_public_blogs_current``.
``is_published``  the row is readable at all. ``false`` = an unpublished draft
                  (a low-confidence article the publish gate held back).
``is_public``     the row is LISTED — gallery, subject feeds, sitemap.
                  Retraction (plan D11/§5) flips this and nothing else.

⚠ **A retracted blog stays readable by direct link.** ``get_by_slug`` therefore
does NOT filter on ``is_public`` — only the LIST paths do. Retract delists; it
does not delete and does not unpublish, and the URL keeps resolving for anyone
holding the link (plan §5). The frontend reads the returned ``is_public`` to
decide ``robots: noindex`` (plan §7), which is what actually deindexes it — a
live 200 never does.

That also means the by-slug read cannot lean on the RLS SELECT policy from
migration 153: that policy requires ``is_public``, so a retracted row is
invisible to anon/authenticated roles. Every function here is handed the
**service-role** client (``deps.get_supabase``), which bypasses RLS, and
re-states each predicate explicitly in the query. The filters below ARE the
access contract, not a convenience.

All functions are SYNCHRONOUS and are invoked from route handlers via ``run_db``
(same convention as the rest of the backend). All error messages are Arabic.
"""
from __future__ import annotations

import logging
import math
import re
import unicodedata
import uuid
from typing import Any, Optional

from supabase import Client as SupabaseClient

from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.services.blog_service import make_snippet, strip_frozen_source_views

logger = logging.getLogger(__name__)


# The three types (plan D3). Carried by the BLOG, never by the subject.
BLOG_TYPES = frozenset({"laws_explanation", "judicial_research", "compliance"})

# Literal path segments under /blog/ that no blog may claim. ``subjects`` is the
# full subject index page (plan §3). This is the compliance_entity_sections
# lesson as code: reserved slugs are refused by the dispatcher's WRITER, never
# discovered by its reader.
RESERVED_BLOG_SLUGS = frozenset({"subjects"})

# ASCII kebab-case — the shape every SUBJECT slug takes (migration 154's CHECK)
# and, since migration 164, the shape an English BLOG slug takes too. The two
# vocabularies no longer differ in shape; migration 164's triggers keep them
# from ever holding the same VALUE, which is what the /blog/{ref} dispatch
# actually needs. A pure-ASCII blog slug must match this exactly.
_ASCII_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# A legacy ``blog_posts`` share token (``blog_service._BARE_TOKEN_RE``). The
# dispatcher tries that table for this shape, so no blog may take it.
_TOKEN_SHAPE_RE = re.compile(r"^[0-9a-f]{32}$")

_MAX_SLUG_LEN = 200

# ``public_blogs.review_status`` (migration 157) — the CHECK's full domain.
# ✅ ENFORCED since migration 159. ``approved`` is a precondition of EVERY public
# read, stated in FIVE places — four here (``list_gallery``, ``_visible_root_ids``
# which counts for the subject vocabulary, ``list_blogs_for_subject`` and
# ``_fetch_current_row``, the by-slug read behind both the article and its
# metered source reveal) and one in ``library_service.sitemap_blog_urls``.
# Missing any one of them leaks a pending draft, so they are listed here rather
# than left to be rediscovered by grep.
#
# ⚠ ``get_by_job_id`` is deliberately NOT gated: it is the service-authed
# read-back that hands a publisher the row it just wrote, which is ``pending``
# by definition. Gating it would break the idempotent re-drive.
#
# A pending row therefore 404s by slug rather than serving with ``noindex``. The
# hold has to be invisible — a resolving URL is a shareable URL, and nothing has
# linked a never-approved article yet, so there is no link in the wild to keep
# alive. That is the opposite of RETRACTION, which keeps serving precisely
# because links already exist (see ``get_by_slug``).
REVIEW_STATUSES = frozenset({"pending", "approved"})

# What a freshly generated article is worth: nobody has read it yet.
# ⚠ Migrations 157 and 159 each backfilled the already-LIVE rows to ``approved``
# so that switching the gate on could not retroactively un-publish them; 159 is
# the one that ran with enforcement, and it scoped itself to rows that were
# actually public (``is_current AND is_public AND is_published``) precisely so a
# deliberately held draft would not be auto-approved by the deploy that closed
# the gate. Keeping the honest ``pending`` here is what makes the hold real:
# writing ``approved`` would be a lie about a human decision that did not happen.
#
# ``append_public_blog_version()`` (migration 155) carries ``cur.review_status``
# forward, so an SEO rewrite of an approved article stays approved and does not
# fall off the site on every revision.
DEFAULT_REVIEW_STATUS = "pending"

# Bound on a full scan of the join table. public_blog_subjects is one row per
# (blog, subject) and the wing is designed to reach ~100 subjects over a few
# hundred blogs, so this is far above the real ceiling; it exists so a runaway
# table can never turn a public read into an unbounded fetch.
_JOIN_SCAN_CAP = 20000

_CARD_FIELDS = (
    "root_id, slug, title, type, content_md, view_count, created_at, updated_at"
)

_DETAIL_FIELDS = (
    "blog_id, root_id, version_no, slug, title, type, subtype, question_text, "
    "content_md, references_json, is_public, is_published, view_count, "
    "created_at, updated_at"
)

# get_by_job_id's projection — _DETAIL_FIELDS plus the provenance columns a
# re-driven publisher needs to rebuild its result payload WITHOUT regenerating.
_JOB_LOOKUP_FIELDS = _DETAIL_FIELDS + ", job_id, confidence, source_item_id"

__all__ = [
    "BLOG_TYPES",
    "RESERVED_BLOG_SLUGS",
    "REVIEW_STATUSES",
    "DEFAULT_REVIEW_STATUS",
    "normalize_frozen_references",
    # reads
    "list_gallery",
    "list_subjects",
    "get_subject_by_slug",
    "list_blogs_for_subject",
    "get_by_slug",
    "get_body_by_slug",
    "get_by_job_id",
    "get_references_by_slug",
    "list_related_by_topic",
    "JobAlreadyPublishedError",
    # writes
    "insert_public_blog",
    "assert_subjects_known",
    "append_version",
    "set_public",
    "attach_subjects",
    "detach_subject",
    "set_subjects",
    # helpers
    "assert_slug_available",
    "extract_headline",
    "split_headline",
]


# ---------------------------------------------------------------------------
# HEADLINE EXTRACTION (plan §6, "The H1 / title contract")
# ---------------------------------------------------------------------------

# ATX H1 only: exactly one '#', at least one space, some text. '##' is a section
# heading and must survive — those become the §4 TOC entries.
_H1_RE = re.compile(r"^#(?!#)\s+(.+?)\s*#*\s*$")


def split_headline(content_md: str) -> tuple[Optional[str], str]:
    """Split a leading ATX H1 off the body. Returns ``(headline|None, body)``.

    Only the FIRST non-empty line is considered, and only if it is an ATX H1
    (``# text``). ``##`` headings are section headings — they become the §4 TOC
    entries and must survive untouched. A body opening with a fenced code block
    is left alone. Pure function; never raises.
    """
    body = content_md or ""
    lines = body.split("\n")
    first = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if first is None:
        return None, body

    candidate = lines[first].strip()
    if candidate.startswith("```") or candidate.startswith("~~~"):
        return None, body

    m = _H1_RE.match(candidate)
    if not m:
        return None, body

    rest = lines[first + 1 :]
    # Drop the blank lines the headline left behind.
    while rest and not rest[0].strip():
        rest.pop(0)
    return m.group(1).strip() or None, "\n".join(rest)


def extract_headline(
    content_md: str, title: Optional[str] = None
) -> tuple[str, str]:
    """Resolve the title and strip the headline from the body. ``(title, body)``.

    ``BlogArticleView`` renders ``title`` as a centred hero AND the body below
    it, so an H1 left inside ``content_md`` double-renders the headline and adds
    a stray level-1 TOC entry. The editorial aggregator writes the headline as
    the first line of the synthesis (plan §6); this is the publish-path half of
    that contract.

    Rules:
      * A supplied ``title`` WINS — but the H1 line is still stripped, so the
        body never carries a headline the hero already shows.
      * No supplied title and no H1 ⇒ 400 (Arabic). ``public_blogs.title`` is
        NOT NULL and a blog with no headline is not publishable.
    """
    extracted, body = split_headline(content_md)
    resolved = (title or "").strip() or (extracted or "")
    if not resolved:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail="لا يمكن نشر مدونة بدون عنوان",
        )
    return resolved, body


# ---------------------------------------------------------------------------
# SLUG REFUSAL (plan §3, "Mint-time refusal")
# ---------------------------------------------------------------------------


def assert_slug_available(supabase: SupabaseClient, slug: str) -> str:
    """Refuse a blog slug that the /blog/{ref} dispatcher could not resolve to
    a blog. Returns the normalized slug, or raises a clean Arabic 400/409.

    Four refusals, in this order:
      1. empty / over-long — ``public_blogs_slug_shape`` backstops it;
      2. a RESERVED literal (``subjects``) — that segment is the subject index;
      3. a slug that collides with an existing ``blog_subjects.slug`` — subjects
         WIN the dispatch (plan D6), so such a blog would be unreachable;
      4. a malformed shape — pure ASCII that is not kebab-case, or a 32-hex
         legacy-token lookalike (migration 164's CHECK backstops both).

    Then two uniqueness pre-checks: one live slug per current, non-deleted row,
    and no slug another blog USED to have (``public_blog_slug_aliases``) —
    taking one would hijack that blog's redirect.

    The DB CHECKs are the backstop. THIS is the gate — a publisher must get a
    400 that names the problem, not an opaque 23514 constraint error out of
    PostgREST.
    """
    normalized = (slug or "").strip()

    if not normalized or len(normalized) > _MAX_SLUG_LEN:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail="رابط المدونة غير صالح",
        )

    if normalized in RESERVED_BLOG_SLUGS:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail="هذا الرابط محجوز ولا يمكن استخدامه لمدونة",
        )

    # Collision with the browse vocabulary. Checked against EVERY subject, active
    # or not: a retired subject can be reactivated, and the slug would then start
    # shadowing the blog silently.
    try:
        existing = (
            supabase.table("blog_subjects")
            .select("subject_id")
            .eq("slug", normalized)
            .limit(1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error checking blog subject slug collision: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء التحقق من رابط المدونة",
        )
    if existing.data:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail="هذا الرابط يخص موضوعاً في المدونة ولا يمكن استخدامه لمقال",
        )

    if _TOKEN_SHAPE_RE.match(normalized) or (
        normalized.isascii() and not _ASCII_SLUG_RE.match(normalized)
    ):
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail="رابط المدونة يجب أن يكون بالعربية أو بحروف إنجليزية صغيرة مفصولة بشرطات",
        )

    try:
        taken = (
            supabase.table("public_blogs")
            .select("blog_id")
            .eq("slug", normalized)
            .eq("is_current", True)
            .is_("deleted_at", "null")
            .limit(1)
            .execute()
        )
        aliased = (
            supabase.table("public_blog_slug_aliases")
            .select("root_id")
            .eq("slug", normalized)
            .limit(1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error checking public blog slug uniqueness: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء التحقق من رابط المدونة",
        )
    if taken.data or aliased.data:
        raise LunaHTTPException(
            status_code=409,
            code=ErrorCode.VALIDATION_ERROR,
            detail="هذا الرابط مستخدم لمدونة أخرى",
        )

    return normalized


# ---------------------------------------------------------------------------
# SUBJECT JOIN HELPERS
# ---------------------------------------------------------------------------


def _subjects_for_roots(
    supabase: SupabaseClient, root_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """``root_id -> [{slug, label_ar}]`` for the given logical blogs.

    Only ACTIVE subjects are projected: a chip for a retired subject would link
    to a page that 404s (``get_subject_by_slug`` filters on ``is_active``).
    Best-effort — a failure here yields empty chip rows rather than failing an
    anonymous read of the article itself.
    """
    ids = [r for r in dict.fromkeys(root_ids) if r]
    if not ids:
        return {}

    try:
        joins = (
            supabase.table("public_blog_subjects")
            .select("root_id, subject_id")
            .in_("root_id", ids)
            .limit(_JOIN_SCAN_CAP)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("public blog subject join lookup failed: %s", e)
        return {}

    join_rows = joins.data or []
    subject_ids = list({r.get("subject_id") for r in join_rows if r.get("subject_id")})
    if not subject_ids:
        return {}

    try:
        subs = (
            supabase.table("blog_subjects")
            .select("subject_id, slug, label_ar")
            .in_("subject_id", subject_ids)
            .eq("is_active", True)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("public blog subject vocabulary lookup failed: %s", e)
        return {}

    by_id = {
        r["subject_id"]: {"slug": r.get("slug"), "label_ar": r.get("label_ar")}
        for r in (subs.data or [])
        if r.get("subject_id")
    }

    out: dict[str, list[dict[str, Any]]] = {}
    for row in join_rows:
        subject = by_id.get(row.get("subject_id"))
        if subject is None:
            continue  # inactive subject — not a chip
        out.setdefault(row["root_id"], []).append(subject)
    for chips in out.values():
        chips.sort(key=lambda s: s.get("slug") or "")
    return out


def _to_card(row: dict[str, Any], subjects: list[dict[str, Any]]) -> dict[str, Any]:
    """Project a ``public_blogs`` row into a gallery card dict."""
    return {
        "slug": row.get("slug"),
        "title": row.get("title"),
        "type": row.get("type"),
        "snippet": make_snippet(row.get("content_md") or ""),
        "subjects": subjects,
        "view_count": int(row.get("view_count") or 0),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _attach_cards(
    supabase: SupabaseClient, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    live = [r for r in rows if r.get("slug")]
    chips = _subjects_for_roots(supabase, [r.get("root_id") for r in live])
    return [_to_card(r, chips.get(r.get("root_id"), [])) for r in live]


# ---------------------------------------------------------------------------
# FROZEN REFERENCES — the strip, plus the has_source backfill
# ---------------------------------------------------------------------------
#
# ``ReferencePanel`` gates the «عرض المصدر» affordance on
# ``(!!itemId || !!blogToken) && ref.has_source === true``. The blog token (the
# slug, here) is passed; ``has_source`` is what was missing. The publish path now
# freezes it (``deepsearch_api.service._publish_to_public_blog``), but the two
# articles already live carry 15 references with no such key, and they must start
# offering the reveal without a data migration — hence a read-time derivation.

_REF_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# ``ref_id`` prefix → the ``domain`` it must be paired with, for every citation
# family the slug-keyed reveal can rebuild from a FROZEN entry.
#
# Both halves are load-bearing and they are checked TOGETHER on purpose:
# ``reference_resolver.resolve_ref`` dispatches on the PREFIX (and charges), while
# ``references_service.build_reference_source_view`` dispatches on the DOMAIN (and
# builds the body). An entry whose two disagree would be charged by the first and
# refused by the second — the one outcome this flag exists to prevent.
_REVEAL_PREFIX_DOMAIN = {
    "reg": "regulations",            # reg:<chunks_v2.id>
    "case": "cases",                 # case:<case_ref>  — NOT a uuid
    "circular": "circulars",         # circular:<circulars.id>
    "article": "articles",           # article:<articles_v2.id>
    "regdoc": "regulation_docs",     # regdoc:<regulations_v2.id>
}


def _derive_has_source(entry: dict[str, Any]) -> bool:
    """Can the slug-keyed reveal actually serve a body for this frozen entry?

    ⚠ **CONSERVATIVE BY CONSTRUCTION.** A ``True`` the reveal endpoint then
    refuses sells the reader an unlock we cannot deliver, so every branch below
    answers the question the endpoint will ask, with the information the endpoint
    will have — which is the entry itself and nothing else
    (``blog.reveal_reference_source`` builds its row from
    ``entry['ref_id'] / ['domain'] / ['item_id']``, never from the blog).

    What the endpoint needs, and therefore what is checked:

    * ``resolve_ref`` must return non-``None`` — it parses the ``ref_id``
      PREFIX and requires a uuid tail for every family except ``case:``, whose
      tail is a ``case_ref``;
    * ``build_reference_source_view`` must return non-``None`` — it dispatches on
      ``domain`` and its shell builders re-parse the same prefix.

    Three deliberate conservatisms:

    1. **A missing prefix is False**, even though ``resolve_ref`` would fall back
       to ``domain`` and resolve. The shell builders do NOT have that fallback
       (``_reg_chunk_id_from_row`` and friends require the literal prefix once
       ``item_id`` is absent), so a prefix-less entry is exactly the shape that
       gets CHARGED and then 404s.
    2. **``domain='compliance'`` is False** unless the entry carries a
       ``services.id``. ``_build_compliance_shells`` has no ref_id fallback at
       all — the ``compliance:<sha1>`` hash is not a service handle — and a
       frozen ``Reference`` has no ``item_id`` field, so the body cannot be
       rebuilt. (No charge is at stake there: services are ``always_free``. The
       reader would simply get «تعذّر عرض هذا المصدر» from a button we promised.)
    3. **Unknown domains and malformed entries are False.**

    The ONE axis this cannot cover is EXISTENCE: a ``reg:<uuid>`` whose chunk was
    re-chunked away still looks resolvable here. Answering that honestly costs one
    DB round-trip per citation on an anonymous, uncached page read (15 on the live
    articles), and it would still be a TOCTOU. It is also the safe direction to be
    wrong in: a vanished source fails at ``resolve_ref``, which is BEFORE
    ``resolve_access``, so the reader gets a refusal card and is never charged.
    The publish-time flag has the identical exposure — a source can vanish after
    the snapshot is frozen — so this adds no failure mode the wing did not have.

    ``source_type`` is deliberately NOT consulted: it is a display discriminator
    for the card, and the reveal route never reads it.
    """
    if not isinstance(entry, dict):
        return False

    ref_id = str(entry.get("ref_id") or "").strip()
    domain = str(entry.get("domain") or "").strip().lower()
    item_id = str(entry.get("item_id") or "").strip()

    if domain == "compliance":
        return bool(_REF_UUID_RE.match(item_id))

    prefix, sep, tail = ref_id.partition(":")
    if not sep:
        return False
    prefix = prefix.strip().lower()
    tail = tail.strip()

    if _REVEAL_PREFIX_DOMAIN.get(prefix) != domain:
        return False

    # ``case:<case_ref>`` is the one family whose tail is not a uuid — it is the
    # court's own reference string, which ``_enrich_cases`` looks up by.
    if prefix == "case":
        return bool(tail)

    return bool(_REF_UUID_RE.match(tail))


def _library_urls_for_entries(
    supabase: SupabaseClient, entries: list[dict[str, Any]]
) -> dict[int, str]:
    """``{n: url}`` for frozen entries — «افتح في ريحان», the in-app exit.

    ⚠ **NAVIGATION, never a metered unlock.** It is a path to a page that
    enforces its own access tier, so it is resolved for free, for every card, and
    is never charged and never gated on entitlement here. It is also never
    GUESSED: a reference with no published library page gets ``None``, because a
    button into a 404 is strictly worse than no button.

    The derivation is `library_items_service`'s, not a second one — the same sync
    resolver ``fetch_item_references_payload`` reaches through its async wrapper.
    It reads exactly ``n`` / ``domain`` / ``item_id`` / ``ref_id`` off each row,
    which is precisely what a frozen entry carries, so the entries ARE valid rows
    and no adapter shape is invented in between. ≤7 batched round-trips for the
    whole panel, independent of reference count.

    Sync, like everything else in this module: the resolver is itself the SYNC
    half of ``library_items_service`` (that module's ``_``-prefix marks "touches
    Supabase, runs under ``run_db``", not "private"), so calling it from inside
    this ``run_db`` hop costs no extra thread and no extra round-trip.

    Fail-soft to ``{}``: a blocked sidecar must cost the reader a link, never the
    article.
    """
    if not entries:
        return {}
    try:
        # Imported lazily: ``library_items_service`` reaches back into
        # ``references_service`` at call time, and this module is imported from
        # the API layer early. A function-local import is the same posture the
        # two of them already take toward each other.
        from backend.app.services import library_items_service

        return library_items_service._public_page_urls_for_reference_rows(
            supabase, entries
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("public blog library url resolution failed: %s", e)
        return {}


def normalize_frozen_references(
    references: Any, supabase: Optional[SupabaseClient] = None
) -> list[dict[str, Any]]:
    """THE public projection of a frozen ``references_json``, for this wing.

    Three things, in this order:

    1. :func:`strip_frozen_source_views` — a stored ``source_view`` on an
       anonymously-served page is an unmetered mirror of full corpus text.
       Reused verbatim rather than reimplemented; the legacy ``blog_posts`` wing
       runs the same function and must keep behaving exactly as it does.
    2. **``has_source`` backfill** for any entry that lacks the key. Entries that
       HAVE it keep it — a publish-time flag was computed from the enrichment
       itself (``references_service``'s ``resolvable_ns``) and is strictly better
       information than anything derivable from the entry's shape. This only ever
       fills a hole; it never overrides. A stored ``False`` is a real answer and
       survives; a present-but-``null`` is not an answer at all (the client tests
       ``has_source === true``, so a null reads as "no reveal") and is derived
       like a missing key.

    3. **``library_url`` backfill**, and ONLY when ``supabase`` is supplied and
       at least one entry lacks the key. Same rule as step 2 — fill the hole,
       never override — and the same reason: the two live articles were frozen
       before the publish path captured it. This one cannot be derived from the
       entry's shape (it needs the sidecar), so it costs a bounded, batched,
       fail-soft lookup. Articles published after the fix carry the key on every
       entry and skip it entirely.

    Note the two paths through step 1: an entry that carried a ``source_view``
    comes back with ``has_source=True`` already set by the stripper, so it never
    reaches the derivation. Entries with ``source_view is None`` — every row this
    wing has ever written — fall through untouched, which is the exact hole the
    live articles fell into.

    ⚠ **``supabase`` is passed by the ARTICLE read and withheld by the REVEAL
    read**, and that asymmetry is deliberate rather than drift. ``library_url``
    is rendered on the card, so ``get_by_slug`` resolves it; the reveal endpoint
    resolves its OWN ``library_url`` after unlocking and never reads this key, so
    making a citation click pay for a sidecar lookup would buy nothing. The keys
    that decide what the panel OFFERS — the strip and ``has_source`` — are
    computed identically on both paths, which is the part that must never drift.
    """
    out: list[dict[str, Any]] = []
    for entry in strip_frozen_source_views(references):
        if isinstance(entry.get("has_source"), bool):
            out.append(entry)
            continue
        out.append({**entry, "has_source": _derive_has_source(entry)})

    if supabase is None:
        return out

    # Indices, not the dicts: an entry that needed no ``has_source`` backfill is
    # still the very object PostgREST handed us, and stamping a key onto it in
    # place would mutate the caller's row. Copy-on-write instead.
    missing = [i for i, e in enumerate(out) if "library_url" not in e]
    if not missing:
        return out

    urls = _library_urls_for_entries(supabase, [out[i] for i in missing])
    for i in missing:
        entry = out[i]
        try:
            n = int(entry.get("n"))
        except (TypeError, ValueError):
            n = -1
        # ``None`` when nothing resolved — the key is always PRESENT afterwards,
        # matching what ``fetch_item_references_payload`` freezes, so the client
        # reads one shape whichever era the row is from.
        out[i] = {**entry, "library_url": urls.get(n)}
    return out


# ---------------------------------------------------------------------------
# READ — the anonymous surface
# ---------------------------------------------------------------------------


def list_gallery(
    supabase: SupabaseClient,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """The /blog gallery feed: current, public, published, APPROVED, not
    deleted; newest first. Card dicts (never the full body — the snippet stands
    in for it).

    Every predicate is stated explicitly because the service-role client
    bypasses RLS. Dropping any one of them leaks a draft, an unreviewed article
    or a retracted one into the gallery AND (via plan §7) back into the sitemap.
    """
    limit = max(1, min(int(limit or 50), 100))
    offset = max(0, int(offset or 0))

    try:
        result = (
            supabase.table("public_blogs")
            .select(_CARD_FIELDS)
            .eq("is_current", True)
            .eq("is_public", True)
            .eq("is_published", True)
            .is_("deleted_at", "null")
            .eq("review_status", "approved")
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

    return _attach_cards(supabase, result.data or [])


def _visible_root_ids(supabase: SupabaseClient) -> set[str]:
    """Every ``root_id`` whose current version qualifies for the gallery.

    The counting half of the subject vocabulary. Deliberately NOT a count(*)
    per subject: PostgREST has no group-by, and one bounded scan of two small
    tables beats N round-trips over a ~100-row vocabulary.

    ⚠ Its predicate must stay IDENTICAL to ``list_gallery``'s, review gate
    included. This is the count a subject card shows and the ``>= 1`` filter
    that decides whether a subject reaches the hub and the sitemap at all; if it
    counted pending rows the hub would advertise a subject whose listing then
    renders empty.
    """
    try:
        result = (
            supabase.table("public_blogs")
            .select("root_id")
            .eq("is_current", True)
            .eq("is_public", True)
            .eq("is_published", True)
            .is_("deleted_at", "null")
            .eq("review_status", "approved")
            .limit(_JOIN_SCAN_CAP)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("public blog root scan failed: %s", e)
        return set()
    return {r["root_id"] for r in (result.data or []) if r.get("root_id")}


def list_subjects(supabase: SupabaseClient) -> list[dict[str, Any]]:
    """The browse vocabulary — ACTIVE subjects with their public-blog counts.

    Ordered by ``sort_rank`` then label. ``blog_count`` counts only blogs whose
    current version qualifies for the gallery, which is the number the hub cap
    (plan D13) and the ``>=1`` sitemap filter (plan §7) both key on: *a listed
    section with an empty urlset is a file Google refetches hourly to learn
    nothing.* Filtering is the caller's job; this returns the full vocabulary
    with honest counts.
    """
    try:
        result = (
            supabase.table("blog_subjects")
            .select("subject_id, slug, label_ar, description_ar, sort_rank")
            .eq("is_active", True)
            .order("sort_rank", desc=False)
            .order("slug", desc=False)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error listing blog subjects: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب مواضيع المدونة",
        )

    subjects = result.data or []
    if not subjects:
        return []

    visible = _visible_root_ids(supabase)
    counts: dict[str, int] = {}
    if visible:
        try:
            joins = (
                supabase.table("public_blog_subjects")
                .select("root_id, subject_id")
                .limit(_JOIN_SCAN_CAP)
                .execute()
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("blog subject count scan failed: %s", e)
            joins = None
        for row in (joins.data if joins is not None else None) or []:
            if row.get("root_id") in visible and row.get("subject_id"):
                counts[row["subject_id"]] = counts.get(row["subject_id"], 0) + 1

    return [
        {
            "slug": s.get("slug"),
            "label_ar": s.get("label_ar"),
            "description_ar": s.get("description_ar"),
            "sort_rank": int(s.get("sort_rank") or 0),
            "blog_count": int(counts.get(s.get("subject_id"), 0)),
        }
        for s in subjects
        if s.get("slug")
    ]


def get_subject_by_slug(
    supabase: SupabaseClient, slug: str
) -> Optional[dict[str, Any]]:
    """One ACTIVE subject by slug, or ``None``.

    Inactive is indistinguishable from unknown on purpose — retiring a subject
    (``is_active=false``, never a delete) must take its page down the same way
    a typo does.
    """
    key = (slug or "").strip()
    if not key:
        return None
    try:
        result = (
            supabase.table("blog_subjects")
            .select("subject_id, slug, label_ar, description_ar, sort_rank")
            .eq("slug", key)
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error fetching blog subject: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب الموضوع",
        )
    rows = result.data or []
    return rows[0] if rows else None


def list_blogs_for_subject(
    supabase: SupabaseClient,
    subject_id: str,
    limit: int = 50,
    offset: int = 0,
) -> tuple[int, list[dict[str, Any]]]:
    """Blogs carrying one subject. Returns ``(total, cards)``.

    Same visibility predicate as the gallery, newest first (plan §12.3: no
    usage-rank equivalent exists for blogs and this plan does not build one).
    ``total`` is the FULL qualifying count, not the page size — the subject page
    header and the ``>=1`` sitemap filter both need the real number.

    The join is keyed on ``root_id``, the LOGICAL blog, so an SEO rewrite never
    has to re-file its subjects.
    """
    limit = max(1, min(int(limit or 50), 100))
    offset = max(0, int(offset or 0))

    try:
        joins = (
            supabase.table("public_blog_subjects")
            .select("root_id")
            .eq("subject_id", subject_id)
            .limit(_JOIN_SCAN_CAP)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error listing blogs for subject: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب مدونات الموضوع",
        )

    root_ids = [r["root_id"] for r in (joins.data or []) if r.get("root_id")]
    if not root_ids:
        return 0, []

    try:
        result = (
            supabase.table("public_blogs")
            .select(_CARD_FIELDS, count="exact")
            .in_("root_id", root_ids)
            .eq("is_current", True)
            .eq("is_public", True)
            .eq("is_published", True)
            .is_("deleted_at", "null")
            .eq("review_status", "approved")
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error listing blogs for subject: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب مدونات الموضوع",
        )

    rows = result.data or []
    total = getattr(result, "count", None)
    return int(total if total is not None else len(rows)), _attach_cards(supabase, rows)


# ---------------------------------------------------------------------------
# «اقرأ تاليًا» — relatedness by the أنظمة two articles both cite
# ---------------------------------------------------------------------------
#
# THE RELATEDNESS RULE IS THE SHARED نظام, not the subject chip and not the type.
# `public_blogs.type` reads `judicial_research` on 23 of the 25 live rows, so it
# discriminates nothing, and a subject («أبحاث قضائية») is an editorial shelf,
# not a topic. What a reader means by "more like this" on this wing is *another
# article about نظام العمل* — and the frozen `references_json` already says which
# أنظمة an article is built on, per entry, in `regulation_title`.
#
# ⚠ ONLY `domain == "regulations"` ENTRIES COUNT, and only those carrying a
# `doc_type`. On a `cases` entry `regulation_title` holds the COURT that issued
# the judgment — «وزارة العدل», «ديوان المظالم» — which nearly every judicial
# article cites. Keying on the field unfiltered would relate all of them to all
# of them through the courthouse, which is the most confident possible way to be
# useless.

# Tatweel + Arabic diacritics. Stripped for the topic KEY only — the corpus is
# inconsistent about harakah and these are never displayed from here.
_TOPIC_DIACRITICS_RE = re.compile(r"[ـً-ْٰۖ-ۭ]")

# ⚠ NOT EVERY CITED DOCUMENT IS A TOPIC, and `doc_type` is what tells them apart.
# A نظام or a لائحة is the subject matter itself: two articles citing «نظام العمل»
# are both about labour. A دليل is a cross-cutting booklet — «دليل الخدمات
# المقدمة للوافدين» is cited by a labour article AND by a mortgage-restructuring
# article, because both touch an expat, not because they share a topic.
#
# Measured on the live wing, this is not hypothetical: ranking on citation
# rarity alone put «تقادم الديون» (commercial debt) ABOVE «تشغيل عمال دون نقل
# خدماتهم» in the strip of a labour-claim article — the debt piece scored higher
# because the booklet it shared was rarer than نظام العمل is. Rarity measures how
# unusual a citation is; it cannot tell you whether the citation is ABOUT
# anything.
#
# Binding instruments (the folded forms `_fold_topic` produces). Everything else
# — دليل, إجراءات, تقرير/وثيقة — is guidance and carries `_TOPIC_GUIDANCE_WEIGHT`.
_TOPIC_BINDING_TYPES: frozenset[str] = frozenset(
    {
        "نظام",
        "لائحه تنفيذيه",
        "لائحه",
        "ضوابط",
        "تعليمات",
        "قواعد",
        "امر ملكي",
        "قرار",
    }
)
_TOPIC_GUIDANCE_WEIGHT = 0.35

# The scan bound for relatedness, ordered newest-first so the cap (if it is ever
# reached) keeps the articles most likely to be worth surfacing. Deliberately far
# below `_JOIN_SCAN_CAP`: that one bounds a two-column join table, while this
# scan drags `references_json` — tens of KB per row — so the honest ceiling is
# hundreds of rows, not tens of thousands. The wing is at 25.
_RELATED_SCAN_CAP = 500

# How many candidates a strip can hold before it stops being a recommendation.
_RELATED_MAX = 12


def _fold_topic(text: str) -> str:
    """Comparison form for a نظام title: NFKC, diacritics gone, alef/ya/ta-marbuta
    unified, whitespace collapsed.

    ⚠ THE FOLD IS WHAT MAKES THIS ONE TOPIC INSTEAD OF TWO. The corpus splits the
    same نظام across alef spellings — the `fetch_article` pin-resolution notes the
    same hazard, where a bare-alef corpus title silently answered a hamza query
    with the wrong law. Here the cost of not folding is quieter but the same
    shape: «نظام الإثبات» and «نظام الاثبات» would be two unrelated topics and the
    strip would simply come back short.

    Mirrors `shared/library/guide_titles._fold`, which is private to that module
    and scoped to guide CHANNELS. Copied rather than imported because the two
    answer different questions and must be free to diverge.
    """
    folded = unicodedata.normalize("NFKC", text or "")
    folded = _TOPIC_DIACRITICS_RE.sub("", folded)
    folded = (
        folded.replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("ى", "ي")
        .replace("ة", "ه")
    )
    return re.sub(r"\s+", " ", folded).strip().lower()


def _topic_keys(references: Any) -> dict[str, float]:
    """``{folded نظام title: weight}`` for one article — what it is ABOUT.

    Reads the FROZEN `references_json` verbatim — the same bytes the article's
    «المراجع» panel renders — so an article's topics can never disagree with its
    own citations. Tolerant of every shape a stored column can take: a non-list,
    a non-dict entry, a missing key. A row that yields nothing simply has no
    topics and drops out of the feature.

    The weight is the instrument class (see `_TOPIC_BINDING_TYPES`): 1.0 for a
    binding نظام/لائحة/ضوابط, `_TOPIC_GUIDANCE_WEIGHT` for a دليل. A title cited
    twice under different `doc_type`s keeps the STRONGER reading — the corpus
    labels the same document inconsistently, and taking the max means an
    inconsistency can only ever cost precision, never silently demote a real نظام.
    """
    if not isinstance(references, list):
        return {}
    keys: dict[str, float] = {}
    for entry in references:
        if not isinstance(entry, dict):
            continue
        if (entry.get("domain") or "") != "regulations":
            continue
        # The `doc_type` guard is the second half of the courthouse filter: a
        # `regulations`-domain entry always carries one («نظام», «لائحة تنفيذية»,
        # «دليل»), so a blank there is a malformed entry, not a نظام.
        doc_type = _fold_topic(entry.get("doc_type") or "")
        if not doc_type:
            continue
        folded = _fold_topic(entry.get("regulation_title") or "")
        if not folded:
            continue
        weight = (
            1.0 if doc_type in _TOPIC_BINDING_TYPES else _TOPIC_GUIDANCE_WEIGHT
        )
        keys[folded] = max(keys.get(folded, 0.0), weight)
    return keys


def list_related_by_topic(
    supabase: SupabaseClient, slug: str, limit: int = 6
) -> list[dict[str, Any]]:
    """«اقرأ تاليًا» for one article: other public blogs citing the same أنظمة.

    Empty list when the article is unknown, cites no نظام, or shares none with
    anything else. ⚠ **IT NEVER PADS WITH RECENCY.** A strip that fills itself
    with whatever was published last teaches the reader within two articles that
    it means nothing, and then the real matches below it go unclicked too.

    QUALIFYING (the floor, and the reason this strip can come back empty): a
    candidate must share at least one BINDING instrument — a نظام, a لائحة,
    ضوابط — or else at least two topics of any class. Sharing exactly one دليل is
    not a topic in common; it is two articles that happened to cite the same
    booklet, and «تقادم الديون» under a labour-claim article is what that looks
    like to a reader.

    RANKING: the sum over shared topics of `instrument_weight / √df`. The rarity
    term stops «نظام العمل» — cited by a third of the wing — from flattening the
    order, so two articles on «ضوابط التمويل الاستهلاكي» rank above two that
    merely both touch labour; the instrument weight stops rarity from promoting a
    rare-but-generic booklet over the law the article is actually about. Ties go
    to the newest.

    The SOURCE article is resolved through `_fetch_current_row` (no `is_public`
    filter), so a RETRACTED article still gets a strip — its link works, so its
    page should be whole. The CANDIDATES use the full gallery predicate: a
    retracted or pending article must never be surfaced by one that links to it.
    """
    limit = max(1, min(int(limit or 6), _RELATED_MAX))

    row = _fetch_current_row(supabase, slug, "root_id, references_json")
    if row is None:
        return []
    mine = _topic_keys(row.get("references_json"))
    if not mine:
        return []
    self_root = row.get("root_id")

    try:
        scan = (
            supabase.table("public_blogs")
            .select("root_id, references_json, created_at")
            .eq("is_current", True)
            .eq("is_public", True)
            .eq("is_published", True)
            .is_("deleted_at", "null")
            .eq("review_status", "approved")
            .order("created_at", desc=True)
            .limit(_RELATED_SCAN_CAP)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        # Best-effort: «اقرأ تاليًا» is a trailing strip, never the page. A
        # failure here renders an article without it rather than a 500 on a
        # public URL that Google is crawling.
        logger.warning("public blog related scan failed for %s: %s", slug, e)
        return []

    # One pass to collect each candidate's topics, one to count how common each
    # topic is across the scan — the `df` the rarity weight divides by.
    candidates: list[tuple[str, dict[str, float], str]] = []
    doc_freq: dict[str, int] = {}
    for other in scan.data or []:
        root_id = other.get("root_id")
        if not root_id or root_id == self_root:
            continue
        topics = _topic_keys(other.get("references_json"))
        if not topics:
            continue
        candidates.append((root_id, topics, other.get("created_at") or ""))
        for topic in topics:
            doc_freq[topic] = doc_freq.get(topic, 0) + 1

    scored: list[tuple[float, str, str]] = []
    for root_id, topics, created_at in candidates:
        shared = set(mine) & set(topics)
        if not shared:
            continue
        # The floor: one shared binding instrument, or two shared anything.
        if not (
            any(mine[t] == 1.0 for t in shared) or len(shared) >= 2
        ):
            continue
        score = sum(mine[t] / math.sqrt(doc_freq.get(t, 1)) for t in shared)
        scored.append((score, created_at, root_id))
    if not scored:
        return []

    # Both terms descend together — strongest overlap, then newest — so one
    # `reverse=True` says it, and the ISO `created_at` string sorts
    # chronologically without parsing.
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    top_roots = [s[2] for s in scored[:limit]]

    try:
        result = (
            supabase.table("public_blogs")
            .select(_CARD_FIELDS)
            .in_("root_id", top_roots)
            .eq("is_current", True)
            .eq("is_public", True)
            .eq("is_published", True)
            .is_("deleted_at", "null")
            .eq("review_status", "approved")
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("public blog related card fetch failed for %s: %s", slug, e)
        return []

    # PostgREST answers an `in_` in its own order, so the ranking is re-applied
    # here — otherwise the scoring above would decide WHICH articles appear and
    # nothing would decide the order they appear in.
    rank = {root_id: i for i, root_id in enumerate(top_roots)}
    rows = sorted(
        (r for r in (result.data or []) if r.get("root_id") in rank),
        key=lambda r: rank[r["root_id"]],
    )
    return _attach_cards(supabase, rows)


def _fetch_current_row(
    supabase: SupabaseClient, slug: str, fields: str
) -> Optional[dict[str, Any]]:
    """THE by-slug read predicate, stated once, for every reader of this wing.

    ``is_current`` + ``is_published`` + ``review_status='approved'`` + not
    deleted, and deliberately **no ``is_public`` filter** — see ``get_by_slug``.
    Every caller that addresses a blog by its slug goes through here, so the
    article and its metered source reveal can never drift into disagreeing about
    which rows exist.

    ⚠ The two omissions are opposites and both deliberate. ``is_public`` is left
    out so a RETRACTED article keeps resolving for links already in the wild.
    ``review_status`` is filtered IN so a PENDING one never resolves at all:
    nothing has linked it yet, and a URL that resolves during the editorial hold
    is a URL that can be shared past it.

    ⚠ **A FORMER slug resolves too** (migration 164). When no current row holds
    ``slug``, ``public_blog_slug_aliases`` is asked which blog used to, and that
    blog's current row is returned under the SAME predicates. The row carries
    its current ``slug``, so a caller that must redirect can compare. That is
    how an English slug swapped in at publish leaves the Arabic address — and
    every link already shared with it — working.

    Returns the row, or ``None`` when nothing resolves. ``fields`` is the
    PostgREST projection the caller needs; nothing else varies.
    """
    key = (slug or "").strip()
    if not key:
        return None

    def _current(column: str, value: str) -> list[dict[str, Any]]:
        return (
            supabase.table("public_blogs")
            .select(fields)
            .eq(column, value)
            .eq("is_current", True)
            .eq("is_published", True)
            .is_("deleted_at", "null")
            .eq("review_status", "approved")
            .limit(1)
            .execute()
        ).data or []

    try:
        rows = _current("slug", key)
        if not rows:
            alias = (
                supabase.table("public_blog_slug_aliases")
                .select("root_id")
                .eq("slug", key)
                .limit(1)
                .execute()
            ).data or []
            if alias:
                rows = _current("root_id", alias[0]["root_id"])
    except Exception as e:  # noqa: BLE001
        logger.exception("Error fetching public blog by slug: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب المدونة",
        )

    return rows[0] if rows else None


# Inline markdown image: `![alt](url)`, the shape a published blog's marketing
# cards take. Mirrors `frontend/lib/markdown/images.ts`'s `MARKDOWN_IMAGE` — the
# leading `!` is what separates an image from a `[نص](url)` LINK, which must
# survive because a link's text is prose.
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_BLANK_RUN_RE = re.compile(r"\n{3,}")


def _strip_markdown_images(markdown: str) -> str:
    """Drop every markdown image, keep the prose.

    ⚠ A PUBLISHED BLOG CARRIES ITS MARKETING CARDS INSIDE `content_md` — the
    cover, the «أبرز النقاط» panels — as images pointing at the public
    `blog-cards` bucket. On the page they ARE the article. Handed to a model as
    grounding they are a wall of Supabase storage URLs: context the answer
    cannot use, spent out of a budget `MAX_CONTEXT_CHARS` then truncates. The
    measured cost on a live article was the cover URL landing in the FIRST 120
    characters of the context window.

    This is the same rule `stripMarkdownImages` applies to «نسخ المقال», and for
    the same reason — every text surface over a public blog has to strip these
    or the alt text and the URL leak into it.

    Simpler than the TypeScript one on purpose: it does not skip fenced code
    blocks. That carve-out exists so a reader copying sample code keeps it
    verbatim; this output is never read by a human, and an image inside a fence
    is no more useful to a model than one outside it.
    """
    if "![" not in markdown:
        return markdown
    kept: list[str] = []
    for line in markdown.splitlines():
        if "![" not in line:
            kept.append(line)
            continue
        stripped = _MARKDOWN_IMAGE_RE.sub("", line)
        # A line that held nothing but cards disappears rather than leaving a
        # blank where a paragraph used to look like it was.
        if not stripped.strip():
            continue
        kept.append(re.sub(r"[^\S\n]{2,}", " ", stripped).rstrip())
    return _BLANK_RUN_RE.sub("\n\n", "\n".join(kept)).strip()


def get_body_by_slug(
    supabase: SupabaseClient, slug: str
) -> Optional[dict[str, Any]]:
    """``{title, content_md}`` for one blog by slug, or ``None``.

    ``content_md`` is PROSE — the marketing cards are stripped (see
    ``_strip_markdown_images``). Both consumers want it that way: the model
    cannot use a storage URL, and a workspace note rendering one is noise beside
    the three other items in the pane.

    The reader for the BLOG-KEYED backend paths that are not page renders:
    ``ask_service._ground_blog`` (the «اسأل ريحان» popup's page context) and
    ``library_item_service._title_blog`` / ``build_content`` (the workspace carry).
    Both used to see this wing as empty — they query ``blog_posts`` by TOKEN, and
    a public blog has no token (plan D17), so a slug grounded on ``""``.

    ⚠ **Not ``get_by_slug``, and the difference is the whole reason this exists.**
    That one bumps ``view_count`` — the same counter whose ``updated_at`` trigger
    made the wing unindexable (plan §5B.3). Grounding an answer and titling a
    workspace item are not page views, and a popup question that silently
    inflated the number the hub ranks on would be that bug's second edition.
    ``get_references_by_slug`` is kept apart from ``get_by_slug`` for exactly
    this reason; this is the third reader on the same side of that line.

    Same visibility rule as every by-slug read (``_fetch_current_row``): a
    RETRACTED article still grounds, because its direct link still works and a
    reader who is on the page must be able to ask about what they can see.
    """
    row = _fetch_current_row(supabase, slug, "title, content_md")
    if row is None:
        return None
    return {
        "title": (row.get("title") or "").strip(),
        "content_md": _strip_markdown_images((row.get("content_md") or "").strip()),
    }


def get_references_by_slug(
    supabase: SupabaseClient, slug: str
) -> Optional[list[dict[str, Any]]]:
    """The frozen citation set of one blog, for the metered source reveal.

    ``None`` = no such blog (the route 404s); ``[]`` = a blog that cites
    nothing. Same visibility rule as ``get_by_slug`` via ``_fetch_current_row``,
    so a retracted article's references stay reachable exactly as its body does.

    Two things this is NOT, both on purpose:

      * **Not ``get_by_slug``.** That one bumps ``view_count``; a click on
        «عرض المصدر» is not a page view, and would inflate the counter the hub
        ranks on. It also fetches the whole body and the subject chips, none of
        which a reveal needs.
      * **Not the raw column.** ``normalize_frozen_references`` runs here too — a
        stored ``source_view`` would be an unmetered, anon-readable mirror of
        full corpus text, which is the entire hole the reveal meter closes.

    The SAME projection as ``get_by_slug`` on purpose: the article's panel and
    the reveal it calls must never disagree about which entries exist or which
    of them claim a body. ``has_source`` is inert for the reveal itself — it
    reads ``ref_id``/``domain`` and re-resolves from scratch — but drifting the
    two projections is how the panel starts offering a button this endpoint
    refuses.
    """
    row = _fetch_current_row(supabase, slug, "blog_id, references_json")
    if row is None:
        return None
    return normalize_frozen_references(row.get("references_json")) or []


def get_by_slug(supabase: SupabaseClient, slug: str) -> Optional[dict[str, Any]]:
    """One blog by slug — the CURRENT version. ``None`` when nothing resolves.

    ⚠ **No ``is_public`` filter, deliberately.** A retracted blog (plan D11/§5)
    keeps resolving at its URL: retract delists, it does not delete and does not
    unpublish. The row's ``is_public`` rides along in the projection so the
    frontend can set ``robots: noindex`` on it (plan §7) — that, not a 404, is
    what deindexes a retracted article.

    ``is_published`` IS filtered: false there means an unpublished draft (the
    ``publish_policy`` / ``min_confidence`` gate held a low-confidence article
    back), which has never been readable by anyone.

    Best-effort ``view_count`` increment — a failed bump NEVER fails the read.
    """
    key = (slug or "").strip()
    row = _fetch_current_row(supabase, key, _DETAIL_FIELDS)
    if row is None:
        return None

    try:
        (
            supabase.table("public_blogs")
            .update({"view_count": int(row.get("view_count") or 0) + 1})
            .eq("blog_id", row["blog_id"])
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("public blog view_count increment failed for %s: %s", key, e)

    chips = _subjects_for_roots(supabase, [row.get("root_id")])

    return {
        "is_public": bool(row.get("is_public")),
        "slug": row.get("slug"),
        "title": row.get("title"),
        "type": row.get("type"),
        "subjects": chips.get(row.get("root_id"), []),
        "content_md": row.get("content_md") or "",
        # Keyed ``references`` to match ``BlogPostPublicResponse`` — the column
        # is ``references_json`` but ``BlogArticleView``/``ReferencePanel`` read
        # ``references``, so the two blog surfaces share one client contract.
        #
        # Defensive strip: a stored ``source_view`` would mint an unmetered,
        # anon-readable mirror of full corpus text on a public page. Writes on
        # this wing never capture one, so this normally passes through unchanged
        # — it costs nothing and closes the hole if one ever lands.
        #
        # ⚠ It also backfills the two keys the publish path used to drop, on the
        # rows frozen before it captured them:
        #
        # * ``has_source`` — without it ``ReferencePanel`` renders the card and
        #   no «عرض المصدر» button at all: the reveal is not refused, it is
        #   ABSENT, a deleted feature rather than a metered one. Never gate the
        #   KEY on entitlement — anon must SEE the button and get the 402
        #   «سجّل مجاناً» card from the reveal endpoint.
        # * ``library_url`` — «افتح في ريحان», free navigation. ``supabase`` is
        #   handed over HERE because this is the read whose output renders the
        #   card; the reveal read deliberately does not (see the docstring).
        "references": normalize_frozen_references(
            row.get("references_json"), supabase
        ),
        "question_text": row.get("question_text") or "",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


# ---------------------------------------------------------------------------
# WRITE — versioned publish path (service-role only; no RLS write policy exists)
# ---------------------------------------------------------------------------


def _assert_type(blog_type: str) -> str:
    value = (blog_type or "").strip()
    if value not in BLOG_TYPES:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail="نوع المدونة غير معروف",
        )
    return value


class JobAlreadyPublishedError(RuntimeError):
    """``idx_public_blogs_job`` refused a second v1 for the same editorial job.

    Migration 156. Raised by :func:`insert_public_blog` so the caller can treat
    it as **success** — another attempt at the same job already published, which
    is the outcome the caller wanted — rather than as a failed insert. The row
    the winner wrote is fetched with :func:`get_by_job_id`.

    Deliberately NOT a ``LunaHTTPException``: nothing about this reaches an HTTP
    caller, and mapping it to a 409 would put it one ``except`` clause away from
    the SLUG conflict, which means something entirely different.
    """

    def __init__(self, job_id: str) -> None:
        super().__init__(f"job {job_id} has already published a public blog")
        self.job_id = job_id


def get_by_job_id(
    supabase: SupabaseClient, job_id: str
) -> Optional[dict[str, Any]]:
    """The v1 row a given editorial job published, or ``None``.

    ⚠ **Not filtered on ``is_current``.** ``job_id`` lives on v1 only (migration
    155's ``append_public_blog_version`` does not copy it), so by the time a job
    is re-driven an SEO rewrite may already have superseded that row. The honest
    answer to "did this job publish?" is still yes, and ``root_id`` — the thing
    every later call addresses — is identical on every version.

    No view-count bump: this is a control-plane read, not a reader arriving.
    """
    if not job_id:
        return None
    try:
        result = (
            supabase.table("public_blogs")
            .select(_JOB_LOOKUP_FIELDS)
            .eq("job_id", job_id)
            .is_("deleted_at", "null")
            .limit(1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error loading public blog by job_id: %s", e)
        return None
    rows = result.data or []
    return rows[0] if rows else None


def insert_public_blog(
    supabase: SupabaseClient,
    *,
    slug: str,
    blog_type: str,
    question_text: str,
    content_md: str,
    author_user_id: str,
    title: Optional[str] = None,
    references_json: Optional[list[dict[str, Any]]] = None,
    subtype: Optional[str] = None,
    source_item_id: Optional[str] = None,
    confidence: Optional[str] = None,
    revision_note: Optional[str] = None,
    is_public: bool = True,
    is_published: bool = True,
    job_id: Optional[str] = None,
    review_status: str = DEFAULT_REVIEW_STATUS,
    generation_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Write **version 1** of a public blog. Returns the inserted row.

    ⚠ **The caller generates the uuid and sets ``blog_id = root_id`` to the same
    value.** ``root_id`` self-references ``blog_id``, so a v1 row is its own
    root; the FK is DEFERRABLE precisely so that self-reference in a single
    INSERT cannot trip on statement ordering (migration 153's header). Letting
    the column default mint ``blog_id`` would leave no way to name ``root_id``.

    ``title`` is optional here because the aggregator writes the headline as the
    first line of the body — ``extract_headline`` lifts it out and strips it so
    ``BlogArticleView``'s hero does not double-render it (plan §6). A supplied
    title wins; the H1 line is stripped either way.

    The slug is refused at mint time (``assert_slug_available``) rather than left
    to the DB CHECKs, so a publisher gets a 400 that names the problem.

    ``is_public`` defaults **true** — inverted from ``blog_posts`` (plan D17). A
    public blog is open the moment it exists; there is no token, the slug is the
    whole address. ``publish_public=false`` still lands the row, unlisted.

    ``review_status`` (migration 157) is stamped EXPLICITLY rather than left to
    the column default, so the value a new article carries is readable at this
    call site instead of in a migration. It is state only: nothing in this
    module filters on it, and this write does not change what is visible.

    ``generation_context`` (migration 157) is the first draft plus the complete
    context the aggregator worked from, frozen for a later editor. ⚠ **v1 ONLY.**
    ``append_public_blog_version`` carries both columns forward unchanged
    (migration 158), so a rewrite must never re-stamp them — the whole point is
    that they describe the GENERATION, not the current prose.

    ⚠ **SERVICE-ROLE ONLY, and never reader-facing.** ``SELECT
    (generation_context)`` is revoked from anon/authenticated because migration
    153 gives anon a row-level policy on this table and RLS filters rows, not
    columns. Nothing in this module may add it to ``_CARD_FIELDS`` or
    ``_DETAIL_FIELDS``: it carries verbatim corpus bodies, and putting them on a
    public read would mint an unmetered corpus feed on a public table.

    ``job_id`` (migration 156) is the editorial job that produced this blog, and
    it is the ONLY thing standing between a re-driven job and a duplicate
    article. ⚠ **The slug cannot do that job.** It is derived from the
    aggregator's headline, which is non-deterministic, so a second attempt mints
    a DIFFERENT slug, sails past ``assert_slug_available``, and publishes a
    second blog — measured in production on 2026-09-02: two rows, 60 seconds
    apart, from one POST. A unique index on ``job_id`` is what makes this write
    idempotent; a read-then-write check inside this function could not, because
    its window is exactly the width of one pipeline run, which is precisely when
    a re-drive happens. Violating it raises :class:`JobAlreadyPublishedError`,
    which the caller must treat as SUCCESS.
    """
    # ⚠ ORDER MATTERS: "have I already published?" is a strictly EARLIER
    # question than "is this slug free?", and asking them the other way round
    # turns a successful re-drive into a failure. A second attempt that happens
    # to regenerate the SAME headline mints the SAME slug, so
    # ``assert_slug_available`` would 409 on the article this very job already
    # published — reporting a conflict with itself, failing the job, and leaving
    # a live URL behind a "failed" record. (A second attempt with a DIFFERENT
    # headline sails past that check instead and is caught by the index below;
    # both re-drives now converge on JobAlreadyPublishedError.)
    #
    # This pre-check is a fast path, NOT the guarantee: it is a read before a
    # write, so two concurrent attempts can both pass it. ``idx_public_blogs_job``
    # is what actually holds.
    if job_id:
        already = get_by_job_id(supabase, job_id)
        if already is not None:
            logger.info(
                "public blog insert skipped: job %s already published %s",
                job_id, already.get("root_id"),
            )
            raise JobAlreadyPublishedError(job_id)

    resolved_slug = assert_slug_available(supabase, slug)
    resolved_type = _assert_type(blog_type)
    resolved_title, body = extract_headline(content_md, title)

    blog_id = str(uuid.uuid4())
    payload: dict[str, Any] = {
        "blog_id": blog_id,
        "root_id": blog_id,          # v1: the row is its own root.
        "version_no": 1,
        "is_current": True,
        "revision_note": revision_note,
        "slug": resolved_slug,
        "title": resolved_title,
        "type": resolved_type,
        "question_text": question_text or "",
        "content_md": body,
        "references_json": references_json or [],
        "subtype": subtype,
        "source_item_id": source_item_id,
        "author_user_id": author_user_id,
        "confidence": confidence,
        "is_public": bool(is_public),
        "is_published": bool(is_published),
        "job_id": job_id,
        "review_status": _assert_review_status(review_status),
        # ``None`` is a first-class value here, not a missing one: a publish
        # whose forensic rows never landed writes an honest NULL rather than a
        # half-filled object that reads as complete (plan: the column is
        # provenance, the article is the product).
        "generation_context": generation_context,
    }

    try:
        result = supabase.table("public_blogs").insert(payload).execute()
    except Exception as e:  # noqa: BLE001
        # The per-job idempotency index (migration 156). Another attempt at this
        # same job already published; that is SUCCESS, not a failed write, so it
        # gets its own exception type rather than the generic 500 below.
        if job_id and _is_job_conflict(e):
            logger.warning(
                "public blog insert refused: job %s already published", job_id
            )
            raise JobAlreadyPublishedError(job_id) from e
        logger.exception("Error inserting public blog: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء نشر المدونة",
        )

    if not result.data:
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء نشر المدونة",
        )
    return result.data[0]


def _assert_review_status(value: Optional[str]) -> str:
    """Coerce to a value migration 157's CHECK accepts. Never raises.

    An unknown value is a programming error, and the DB would answer it with a
    23514 that fails the whole publish — losing a finished article over a
    metadata field that nothing reads yet. Falling back to ``pending`` keeps the
    article and leaves the loudest possible trace.
    """
    resolved = (value or "").strip()
    if resolved in REVIEW_STATUSES:
        return resolved
    logger.error(
        "public blog insert: unknown review_status %r; storing %r",
        value, DEFAULT_REVIEW_STATUS,
    )
    return DEFAULT_REVIEW_STATUS


def _is_job_conflict(exc: BaseException) -> bool:
    """Is this the ``idx_public_blogs_job`` unique violation (migration 156)?

    Matched on the index NAME first and the SQLSTATE second — the same defensive
    posture ``append_version`` already takes, because postgrest-py's error shape
    has drifted between versions. Mistaking a different 23505 (the slug index or
    the version index) for the job conflict would hand this job somebody else's
    blog, so the match has to be narrow rather than merely "a unique violation".
    """
    text = str(exc)
    if "idx_public_blogs_job" in text:
        return True
    return _pg_error_code(exc) == "23505" and "job_id" in text


def _pg_error_code(exc: BaseException) -> str:
    """Best-effort SQLSTATE out of a postgrest ``APIError``.

    postgrest-py's error shape has drifted between versions (``.code`` is
    sometimes the SQLSTATE, sometimes a PGRST code, sometimes absent), so the
    caller matches on the code AND on the message text — the same defensive
    posture ``run_db.is_transient_db_error`` and ``payment_service`` already use.
    """
    return str(getattr(exc, "code", "") or "")


def append_version(
    supabase: SupabaseClient,
    root_id: str,
    *,
    content_md: str,
    title: Optional[str] = None,
    revision_note: Optional[str] = None,
    blog_type: Optional[str] = None,
    confidence: Optional[str] = None,
    slug: Optional[str] = None,
) -> dict[str, Any]:
    """Append version N+1 for a logical blog and make it the current one.

    ONE round trip to ``append_public_blog_version()`` (migration 155), which
    runs in a single implicit transaction: it locks the current version
    ``FOR UPDATE``, demotes it, and inserts N+1 as current. The flip is
    all-or-nothing — there is no window in which the slug resolves to nothing
    and no orphan row to compensate for, which is why this function has no
    rollback logic. Two concurrent appends serialize on the row lock instead of
    racing to ``idx_public_blogs_current``.

    ⚠ **``slug`` left as ``None`` carries the current slug UNCHANGED.** A new
    one (migration 164) moves the blog: the function files the old slug in
    ``public_blog_slug_aliases`` and every by-slug read redirects it, so links
    already shared keep working. Only an address nobody holds yet is free to
    move without cost — the marketing publish swaps the Arabic submit-time slug
    for the rewriter's English one exactly once, at «نشر».

    ⚠ **``references_json`` is carried VERBATIM by the function** and is not a
    parameter either (plan D18): the citation set of a published blog is CLOSED,
    which is what makes an SEO rewrite checkable rather than merely instructed.
    ``question_text``, ``subtype``, ``source_item_id``, ``author_user_id`` and
    both visibility flags ride along the same way. Subjects are keyed on
    ``root_id``, so an appended version inherits them without being re-filed.

    ``title``/``blog_type``/``confidence`` left as ``None`` mean "carry the
    current value" — the function COALESCEs them.
    """
    resolved_type = _assert_type(blog_type) if blog_type else None

    # Title precedence on a rewrite: explicit title -> the new body's H1 -> the
    # version being replaced. The H1 is stripped from the body either way (plan
    # §6) so ``BlogArticleView``'s hero does not double-render it. ``None`` is
    # handed to the RPC deliberately: that is how "carry the current title" is
    # spelled, and it keeps the fallback in ONE place instead of two.
    extracted, body = split_headline(content_md or "")
    resolved_title = (title or "").strip() or extracted or None

    try:
        result = supabase.rpc(
            "append_public_blog_version",
            {
                "p_root_id": root_id,
                "p_content_md": body,
                "p_title": resolved_title,
                "p_revision_note": revision_note,
                "p_type": resolved_type,
                "p_confidence": confidence,
                "p_slug": (slug or "").strip() or None,
            },
        ).execute()
    except Exception as e:  # noqa: BLE001
        code = _pg_error_code(e)
        text = str(e)
        # The root has no current version: unknown blog, or every version soft
        # deleted. PostgREST maps plpgsql's no_data_found (P0002) to HTTP 404.
        if code == "P0002" or "no_data_found" in text or "no current version" in text:
            raise LunaHTTPException(
                status_code=404,
                code=ErrorCode.ARTIFACT_NOT_FOUND,
                detail="المدونة غير موجودة",
            )
        # A new ``slug`` another blog or a subject already holds (migration
        # 164's triggers, or the current-slug index) — a naming problem, not a
        # concurrent edit, so it must not read like one.
        if slug and code in ("23505", "23514") and (
            "slug" in text or "idx_public_blogs_slug" in text
        ):
            raise LunaHTTPException(
                status_code=409,
                code=ErrorCode.VALIDATION_ERROR,
                detail="هذا الرابط مستخدم لمدونة أخرى أو غير صالح",
            )
        # A unique-index violation can still reach us — a concurrent INSERT of a
        # brand-new blog claiming this slug is not covered by the row lock. Do
        # not swallow it: the index failing loudly is the guarantee.
        if code == "23505" or "duplicate key" in text or "idx_public_blogs" in text:
            logger.error("public blog version conflict: root_id=%s (%s)", root_id, text)
            raise LunaHTTPException(
                status_code=409,
                code=ErrorCode.VALIDATION_ERROR,
                detail="تم تحديث المدونة من جهة أخرى، حاول مجدداً",
            )
        logger.exception("Error appending public blog version: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء تحديث المدونة",
        )

    # A non-SETOF composite comes back as one object; tolerate a list in case a
    # postgrest version wraps it.
    data = result.data
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict) or not data.get("blog_id"):
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء تحديث المدونة",
        )
    return data


def set_public(supabase: SupabaseClient, root_id: str, value: bool) -> None:
    """Flip ``is_public`` on the CURRENT version — the retraction write (D11).

    ⚠ **NOT owner-scoped, and that is the point.** The in-app publish/unpublish
    routes filter by ``user_id``, so a moderator hitting editorial-bot's row
    would get a 404, not a 403 — no user-facing flag can fix that. Here the
    service key is the authority; the calling route is what must be gated.

    Delists ONLY: ``deleted_at`` and ``is_published`` are untouched, so the URL
    keeps resolving (see ``get_by_slug``). Because a delisted page is still a
    live 200, this does NOT deindex — ``robots: noindex`` on the frontend does
    (plan §7).
    """
    try:
        result = (
            supabase.table("public_blogs")
            .update({"is_public": bool(value)})
            .eq("root_id", root_id)
            .eq("is_current", True)
            .is_("deleted_at", "null")
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error setting public blog visibility: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء تحديث حالة النشر",
        )

    if not result.data:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.ARTIFACT_NOT_FOUND,
            detail="المدونة غير موجودة",
        )


# ---------------------------------------------------------------------------
# SUBJECT ATTACH / DETACH (keyed on root_id — the LOGICAL blog)
# ---------------------------------------------------------------------------


def _resolve_subject_ids(
    supabase: SupabaseClient, slugs: list[str]
) -> list[tuple[str, str]]:
    """``[(slug, subject_id)]`` for ACTIVE subjects. Unknown slug ⇒ 400.

    Plan §5: an unknown subject slug is a **400, not a silent drop** — a blog
    that publishes with no subject is invisible in the browse tree and nobody
    notices until the traffic does not arrive.
    """
    wanted = [s.strip() for s in (slugs or []) if (s or "").strip()]
    wanted = list(dict.fromkeys(wanted))
    if not wanted:
        return []

    try:
        result = (
            supabase.table("blog_subjects")
            .select("subject_id, slug")
            .in_("slug", wanted)
            .eq("is_active", True)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error resolving blog subjects: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب مواضيع المدونة",
        )

    found = {r["slug"]: r["subject_id"] for r in (result.data or []) if r.get("slug")}
    missing = [s for s in wanted if s not in found]
    if missing:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail=f"موضوع غير معروف في المدونة: {', '.join(missing)}",
        )
    return [(s, found[s]) for s in wanted]


def assert_subjects_known(
    supabase: SupabaseClient, slugs: list[str]
) -> list[str]:
    """Validate subject slugs WITHOUT writing anything. Unknown slug ⇒ 400.

    The editorial API calls this at SUBMIT time so marketing gets an immediate,
    named 400 instead of discovering the typo 1–4 minutes later, after a full
    deep_search run has already been paid for. Plan §5: an unknown subject slug
    is a 400, **never a silent drop** — a blog that publishes with no subject is
    invisible in the browse tree and nobody notices until the traffic does not
    arrive.

    Returns the deduplicated, order-preserving slug list that would be filed.
    """
    return [slug for slug, _sid in _resolve_subject_ids(supabase, slugs)]


def attach_subjects(
    supabase: SupabaseClient, root_id: str, slugs: list[str]
) -> list[str]:
    """File a blog under the given subjects. Idempotent. Returns the slugs filed.

    Keyed on ``root_id`` (migration 154): subjects belong to the LOGICAL blog,
    so appending an SEO version never re-files them.
    """
    pairs = _resolve_subject_ids(supabase, slugs)
    if not pairs:
        return []

    rows = [{"root_id": root_id, "subject_id": sid} for _slug, sid in pairs]
    try:
        (
            supabase.table("public_blog_subjects")
            .upsert(rows, on_conflict="root_id,subject_id", ignore_duplicates=True)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error attaching blog subjects: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء ربط مواضيع المدونة",
        )
    return [slug for slug, _sid in pairs]


def detach_subject(supabase: SupabaseClient, root_id: str, slug: str) -> None:
    """Unfile a blog from one subject. Silent when it was not filed there."""
    pairs = _resolve_subject_ids(supabase, [slug])
    if not pairs:
        return
    try:
        (
            supabase.table("public_blog_subjects")
            .delete()
            .eq("root_id", root_id)
            .eq("subject_id", pairs[0][1])
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error detaching blog subject: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء تحديث مواضيع المدونة",
        )


def set_subjects(
    supabase: SupabaseClient, root_id: str, slugs: list[str]
) -> list[str]:
    """Replace a blog's whole subject set. Every slug is validated FIRST.

    Resolution before deletion is deliberate: an unknown slug must 400 with the
    blog still filed where it was, never leave it unfiled under a subject it can
    no longer be found by.
    """
    pairs = _resolve_subject_ids(supabase, slugs)
    keep = {sid for _slug, sid in pairs}

    try:
        existing = (
            supabase.table("public_blog_subjects")
            .select("subject_id")
            .eq("root_id", root_id)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error loading blog subjects: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء تحديث مواضيع المدونة",
        )

    stale = [
        r["subject_id"]
        for r in (existing.data or [])
        if r.get("subject_id") and r["subject_id"] not in keep
    ]
    if stale:
        try:
            (
                supabase.table("public_blog_subjects")
                .delete()
                .eq("root_id", root_id)
                .in_("subject_id", stale)
                .execute()
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("Error clearing blog subjects: %s", e)
            raise LunaHTTPException(
                status_code=500,
                code=ErrorCode.INTERNAL_ERROR,
                detail="حدث خطأ أثناء تحديث مواضيع المدونة",
            )

    if not pairs:
        return []

    rows = [{"root_id": root_id, "subject_id": sid} for _slug, sid in pairs]
    try:
        (
            supabase.table("public_blog_subjects")
            .upsert(rows, on_conflict="root_id,subject_id", ignore_duplicates=True)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error setting blog subjects: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء تحديث مواضيع المدونة",
        )
    return [slug for slug, _sid in pairs]
