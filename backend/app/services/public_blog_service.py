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
import re
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

# ASCII kebab-case — the shape a SUBJECT slug takes (migration 154's CHECK), and
# the shape migration 153 forbids a blog slug from taking. Blog slugs are Arabic
# (plan D4), which is what makes the /blog/{ref} dispatch unambiguous by
# construction rather than by convention.
_ASCII_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

_MAX_SLUG_LEN = 200

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
    "normalize_frozen_references",
    # reads
    "list_gallery",
    "list_subjects",
    "get_subject_by_slug",
    "list_blogs_for_subject",
    "get_by_slug",
    "get_by_job_id",
    "get_references_by_slug",
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
      4. any ASCII kebab-case slug at all — that shape is reserved to subjects
         by construction (migration 153's CHECK), so minting one would fail on a
         constraint later even if no subject holds it today.

    Then a uniqueness pre-check: one live slug per current, non-deleted row.

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

    if _ASCII_SLUG_RE.match(normalized):
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail="رابط المدونة يجب أن يكون بالعربية",
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
    except Exception as e:  # noqa: BLE001
        logger.exception("Error checking public blog slug uniqueness: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء التحقق من رابط المدونة",
        )
    if taken.data:
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
    """The /blog gallery feed: current, public, published, not deleted; newest
    first. Card dicts (never the full body — the snippet stands in for it).

    Every predicate is stated explicitly because the service-role client
    bypasses RLS. Dropping any one of them leaks a draft or a retracted article
    into the gallery AND (via plan §7) back into the sitemap.
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
    """
    try:
        result = (
            supabase.table("public_blogs")
            .select("root_id")
            .eq("is_current", True)
            .eq("is_public", True)
            .eq("is_published", True)
            .is_("deleted_at", "null")
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


def _fetch_current_row(
    supabase: SupabaseClient, slug: str, fields: str
) -> Optional[dict[str, Any]]:
    """THE by-slug read predicate, stated once, for every reader of this wing.

    ``is_current`` + ``is_published`` + not deleted, and deliberately **no
    ``is_public`` filter** — see ``get_by_slug``. Every caller that addresses a
    blog by its slug goes through here, so the article and its metered source
    reveal can never drift into disagreeing about which rows exist.

    Returns the row, or ``None`` when nothing resolves. ``fields`` is the
    PostgREST projection the caller needs; nothing else varies.
    """
    key = (slug or "").strip()
    if not key:
        return None

    try:
        result = (
            supabase.table("public_blogs")
            .select(fields)
            .eq("slug", key)
            .eq("is_current", True)
            .eq("is_published", True)
            .is_("deleted_at", "null")
            .limit(1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error fetching public blog by slug: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب المدونة",
        )

    rows = result.data or []
    return rows[0] if rows else None


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
) -> dict[str, Any]:
    """Append version N+1 for a logical blog and make it the current one.

    ONE round trip to ``append_public_blog_version()`` (migration 155), which
    runs in a single implicit transaction: it locks the current version
    ``FOR UPDATE``, demotes it, and inserts N+1 as current. The flip is
    all-or-nothing — there is no window in which the slug resolves to nothing
    and no orphan row to compensate for, which is why this function has no
    rollback logic. Two concurrent appends serialize on the row lock instead of
    racing to ``idx_public_blogs_current``.

    ⚠ **The slug is carried over UNCHANGED and is not a parameter.** A published
    slug is permanent across every version: there is no redirect layer, so a
    rename 404s (``corpus_supersession_retirement`` learned that expensively).
    A rewrite may change ``title``; it must never change ``slug``.

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
