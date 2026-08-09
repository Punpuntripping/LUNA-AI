"""Sample publisher for the ``/judgments`` wing of the SEO public library.

Publishes judgments (``public.cases``, 30,531 rows) by writing a PERMANENT
Arabic ``slug`` into the ``seo_item_meta`` SIDECAR (migration 095) under
``content_type='judgment'``. Publishing a judgment IS setting that slug: the anon
endpoints, the hub listers and the sitemap all key off a slugged sidecar row, so
this one flip is authoritative.

Sibling of ``scripts/build_seo_slugs.py`` (regulation / service / circular) and
``scripts/publish_articles.py`` (مادة). Same contracts as both:

  * MERGE-upsert on the composite PK ``(content_type, content_id)`` writing ONLY
    ``slug`` + ``updated_at`` — any ``seo_tier`` / ``gate_override`` already on
    the row SURVIVES untouched (gating via ``scripts/set_gate.py`` and publishing
    here are independent knobs).
  * ``--dry-run`` is the DEFAULT. Nothing is written without ``--apply``.
  * Slugs are PERMANENT and are NEVER rewritten. A judgment that already carries
    a slug is skipped, not recomputed — published URLs must not move.
  * ``public.cases`` is PIPELINE-OWNED: this script only ever READS it. It is
    never ALTERed and never written to. (Note it is the JUDGMENTS corpus — not
    ``lawyer_cases``, the private per-user table, which this script never touches.)

WHERE THE TITLES AND SLUGS COME FROM
------------------------------------
``cases`` has no title and no slug column, so both are DERIVED by
``shared/seo/judgment_naming.py`` — the single source of truth, imported here and
never forked. This script calls :func:`judgment_slug_base`; the read path
(``backend/app/services/library_service.py``) calls ``judgment_subject`` /
``judgment_display_title`` on the same row and must land on the same subject, or
a published URL would render an H1 that no longer matches its own address.

Slug shape: ``{up-to-9-subject-words}-{stable-ref}``, e.g.
``دعوى-بطلان-حكم-تحكيم-صادر-في-نزاع-تعاقدي-ap-447264886``. The tail is the
corpus's own unique ``case_ref`` (unique across all 30,531 rows — verified), so
distinct judgments essentially never collide even though dispute subjects repeat
heavily. Long Arabic ``case_ref`` values collapse to a deterministic blake2s
digest inside the naming module.

THE 300-ROW CEILING IS GONE — WHAT REPLACED IT
----------------------------------------------
This script used to publish a 100-row SAMPLE and warn above 300, because
``library_service.SAMPLE_MODE_MAX_IDS`` capped how many published rows the hub
listers could paginate: above the cap ``_published_ids()`` returned ``None`` and
the wing fell back to paging the whole 30,531-row corpus, silently discarding
every unslugged row (~1 card per 9-card page).

That ceiling no longer binds this wing. ``/judgments`` paginates the
published-only view ``library_judgments_ranked`` (corpus ⋈ slugged sidecar), where
every row is published BY CONSTRUCTION, so no page can come back short at any
published count. The old ``if total > 300`` warning here was doubly wrong — the
live constant had already moved to 1000, and the concept it guarded is retired —
so it is DELETED rather than re-pointed at the new number.

``--limit`` is therefore a policy knob, not a safety rail. The planned ramp
(plan ``.claude/plans/library_court_sections_publish_ramp.md`` §3.1) is
``--limit 10000``. What still bounds a run is the corpus (29,425 eligible rows)
and the per-feed allocation below.

SELECTION POLICY (deterministic, re-runnable, tunable via the constants below)
-----------------------------------------------------------------------------
The published set must look like the corpus rather than like its biggest bucket
(التجارية alone is 19,473 of the 29,425 eligible rows). Five stages:

0. FEED ALLOCATION (``_FEED_ALLOCATION``, the outermost layer). ``cases`` is four
   scraped feeds glued into one table, and they differ so much in metadata that
   a single global quota cannot serve them (see the CORPUS SURPRISE section).
   ``--limit`` is split across feeds FIRST; everything below then runs
   independently inside each feed's allocation. Feeds are identified with
   :func:`shared.library.courts.feed_for_court` — the ONE court normalizer in the
   repo, shared with the ``/judgments/courts/{slug}`` sections — never by
   re-deriving prefixes off ``cases.court``.

1. ELIGIBILITY (hard gate). A row must have ``short_summary`` AND ``facts`` AND
   ``ruling`` AND ``reasoning``. A judgment page with no ruling is a dead page,
   and with no reasoning the gate has nothing worth protecting. ``reasoning`` was
   promoted from "preferred" to required because it is near-universal — 29,425 of
   the 29,470 rows that pass the other three also have it, so the gate costs 45
   rows and buys a guarantee. The derived subject must also carry at least
   ``_MIN_SUBJECT_WORDS`` words, which rejects rows whose summary degrades to the
   naming module's «حكم قضائي» fallback. Eligibility is NOT what bounds a 10k run:
   29,425 of 30,531 rows pass it.

2. QUALITY SCORE (soft preference, orders rows inside a court). Chiefly
   ``referenced_regulations``: those power the internal-linking mesh between
   ``/judgments`` and ``/regulations``, and only 18,737 of 29,425 eligible rows
   have any, so it is a real discriminator (weight 3). ``legal_domains`` (facets),
   ``date_gregorian`` (hub ordering) and ``city`` add 1 each. A capped USAGE
   BONUS (≤ ``_USAGE_BONUS_MAX``) rides on top — see THE USAGE BONUS below.

3. COURT-LEVEL MIX (``_LEVEL_MIX``): 60 first_instance / 30 appeal / 10 supreme
   per 100, scaled by largest remainder to each FEED's allocation. Without it the
   selection would be ~80% first-instance commercial. Levels that cannot fill
   their quota hand the shortfall back and the other levels of the same feed top
   it up — which is not an edge case here but the norm: ديوان المظالم is 100%
   first_instance and لجان التأمين is 100% appeal, so most of their quota arrives
   through the top-up path.

4. SPREAD, inside each level: courts are drained ROUND-ROBIN, one row per court
   per pass, but each court's total allowance is weighted by the SQUARE ROOT of
   its bucket size and then clamped to ``_COURT_CAP_FRAC`` of the level quota
   (floor 1, so no court is ever shut out). sqrt is the whole trick: proportional
   allotment would hand التجارية ~39 of 60 first-instance seats, while a flat
   round-robin hands it 3 — sqrt lands it near 16, which keeps the flagship court
   leading without burying the others. A soft ceiling of ``_DOMAIN_CAP_FRAC`` per
   primary ``legal_domains`` entry runs on top, enforced by a bounded lookahead:
   when a court's turn comes, the first row within the window whose domain is
   still under its share wins, else the head is taken anyway. All ceilings are
   advisory — they shape the selection, they never stall the fill or make the run
   non-deterministic.

   ⚠ THE DOMAIN CEILING IS PER FEED AND SCALE-FREE. See THE SECTOR TRAP below.

CORPUS SURPRISE THAT DRIVES THE WEIGHTING
-----------------------------------------
``legal_domains`` and ``date_gregorian`` are NOT sparse at row level — they are
populated per SOURCE FEED. All 20,671 وزارة العدل rows carry both; all 4,669
ديوان المظالم, all 4,966 هيئة الزكاة والضريبة and all 225 لجان التأمين rows carry
NEITHER. So "spread across courts" and "populate the domain facets / date sort"
are in direct tension: every seat handed to a committee court is a seat with no
facet and no date. A flat round-robin inverted the corpus profile — 66% of the
sample had no domain where the corpus is 67% WITH one. The sqrt weighting and the
feed allocation are what hold the balance. If the pipeline later backfills domains
for the committee feeds, raise ``_COURT_CAP_FRAC`` / flatten the weighting.

THE SECTOR TRAP (three ways this silently does nothing)
--------------------------------------------------------
"Diversify by the sectors" can only ever steer the وزارة العدل slice — that is a
property of the data, not a choice. Getting it to steer ANYTHING took three fixes,
and each failure mode is silent:

 1. SCOPE. ``_DOMAIN_CAP_FRAC`` used to be computed over the WHOLE run. At 100
    rows that was harmless. At 10,000 the ~2,224 seats held by the three
    domain-less feeds all land under one ``_NO_DOMAIN`` label while the ceiling
    for every REAL domain stays ``0.30 × 10000 = 3000`` — i.e. 38.6% of the 7,776
    seats where sectors exist. The ceiling is now per feed, and switched OFF
    entirely for feeds with no ``legal_domains``, so their lookahead is a no-op
    instead of a saturated scan.

 2. REACH. Scoping alone changed nothing measurable, because the lookahead was a
    fixed 30 rows against a 15,106-row bucket. Measured at ``--limit 10000``: no
    ceiling at all → المعاملات التجارية takes 65.8% of the وزارة العدل slice; the
    scoped ceiling with a 30-row window → 65.6%. The window is now a fraction of
    the bucket (``_DOMAIN_LOOKAHEAD_DIV``), which brings it to 46.5%.

 3. SHAPE. A ceiling expressed as ``0.30 × quota`` is a DIFFERENT NUMBER at every
    ``--limit``, so it prefers different rows at every ``--limit`` and a bigger
    run stops being a superset of a smaller one (measured: 4 of 200 rows dropped
    going 200 → 400). It is therefore a RUNNING RATIO — a domain is blocked once
    it holds 30% of what its feed has picked SO FAR — which is scale-free and
    keeps a bigger ``--limit`` a pure top-up.

The ceiling holds at exactly 30% up to ``--limit 2000`` and then degrades as the
corpus runs out of non-commercial rows: وزارة العدل has 19,766 eligible rows and
only 5,605 of them are outside المعاملات التجارية, so 7,776 seats cannot be 30%
commercial without publishing essentially every non-commercial row it owns.
That is reported, not hidden — the domain table flags every over-ceiling entry.

THE USAGE BONUS (why it is a bonus and never a sort key)
---------------------------------------------------------
"Publish what has already been used" reads ``workspace_item_references``
(``domain='cases'``, ``item_id`` → ``cases.id``). ⚠ THAT TABLE HAS REFERENCED
**229 DISTINCT JUDGMENTS, EVER** — 354 reference rows from 5 dev/demo accounts
across 39 conversations. It is pipeline traffic from our own testing, NOT market
demand, and it can rank a top-229; it cannot select 10,000. So it is a capped
additive term on ``_quality_score`` (worth at most ``_USAGE_BONUS_MAX``, i.e. less
than the ``referenced_regulations`` weight) and never an ordering key of its own.
The two-stage dampening is lifted from ``scripts/build_usage_rank.py``
(``load_usage_scores``): a conversation votes at most once, and one account can
contribute at most ``_USAGE_USER_CAP``, so no single demo session can pull rows
into a public listing.

Ordering is stable at every step: ``date_gregorian`` DESC NULLS LAST, then ``id``
as the tie-break, with the quality score ahead of both inside a court bucket. The
same rows are therefore chosen on every run, which is what makes the script
idempotent: a re-run selects the same rows, finds them slugged, and writes
nothing. Raising ``--limit`` TOPS UP — it never moves a published URL.

READ STRATEGY
-------------
Three reads, all paged 1000 at a time on a UNIQUE ordering key (PostgREST caps a
response at 1000 rows and truncates SILENTLY, and paging a non-uniquely-ordered
set duplicates rows across page boundaries):

  * candidates — small columns + ``short_summary`` (the one text column the slug
    derivation needs), eligibility enforced server-side as ``not.is.null``
    (verified equivalent to a trim check: the corpus has zero non-null-but-blank
    values in these four columns). ~29k rows, ordered by ``id``.
  * the HAS-REFS id set — ``referenced_regulations`` averages ~895 bytes and peaks
    at 71 KB, so it is never selected. ``_quality_score`` only reads it as a
    BOOLEAN, so what comes back is a bare id list filtered ``neq.[]`` server-side
    (~18.7k uuids, ≈0.7 MB, vs ≈26 MB for the column itself).

    This replaced a per-court-head probe that fetched the real column for the top
    N rows of each bucket. That probe was fine at ``--limit 100`` and breaks at
    10,000 twice over: it moved ~18 MB in ~200 round-trips, and its depth
    (``0.35 × limit``) sat BELOW the ~4.6k rows a single court legitimately draws
    once the smaller courts in its level run dry — so the biggest bucket starved
    against a cap that was never meant to bind.
  * the usage refs — 354 rows, ordered by ``ref_pk``, plus a chunked
    ``workspace_items`` lookup for the (user, conversation) grouping keys.

REVERSIBILITY
-------------
``--unpublish-all --apply`` clears ``slug`` on every ``content_type='judgment'``
sidecar row, retiring the whole wing. It is an UPDATE, not a DELETE: the rows
survive with their ``seo_tier`` / ``gate_override`` intact, and no other
content_type is touched. Re-running the publisher afterwards re-derives the same
slugs from the same module, so unpublish/republish is a round trip.

⚠ AFTER ``--apply``, PURGE ISR. This script does not POST ``/api/revalidate``.
Without it the hub keeps serving the pre-publish bake. Purge ``/judgments``,
every ``/judgments/courts/{slug}``, ``/library`` and their ``page/{n}`` variants.

Run from the repo root:
  python scripts/build_judgment_slugs.py                    # dry-run, 100 rows
  python scripts/build_judgment_slugs.py --limit 10000      # dry-run, the ramp
  python scripts/build_judgment_slugs.py --limit 10000 --apply
  python scripts/build_judgment_slugs.py --feed-alloc zatca=0.15 --limit 10000
  python scripts/build_judgment_slugs.py --list             # what is live now
  python scripts/build_judgment_slugs.py --unpublish-all --apply   # retire

Env: SUPABASE_URL / SUPABASE_SERVICE_KEY (via shared.config / shared.db.client).
Optional PUBLIC_WEB_URL — only used to print prospective page URLs.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple, Optional

# Make the repo root importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows consoles default to cp1252, which can't encode Arabic — force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # noqa: BLE001
    pass

from shared.db.client import get_supabase_client
from shared.library.courts import (
    COURT_LABELS,
    FEED_BOG,
    FEED_INSURANCE,
    FEED_MOJ,
    FEED_ZATCA,
    feed_for_court,
    slug_for_court,
)
from shared.seo.judgment_naming import (
    court_level_label,
    judgment_display_title,
    judgment_slug_base,
    judgment_subject,
)

CONTENT_TYPE = "judgment"
CORPUS_TABLE = "cases"
REFS_TABLE = "workspace_item_references"
WORKSPACE_ITEMS_TABLE = "workspace_items"

# Read-page size (PostgREST caps a single response at 1000 rows by default).
_READ_PAGE = 1000
# Upsert / update batch size on --apply.
_WRITE_BATCH = 500
# Chunk size for `.in_("id", [...])` lookups (URL-length safety).
_ID_CHUNK = 100

# ── selection policy knobs (see the module docstring) ──────────────────────
# Court-level mix per 100 published rows; scaled to each FEED quota by largest
# remainder. Two of the four feeds are single-level, so the cross-level top-up
# inside _fill_feed carries most of their allocation — that is expected.
_LEVEL_MIX: dict[str, int] = {"first_instance": 60, "appeal": 30, "supreme": 10}
# Deterministic tie-break order for quota rounding and shortfall top-up.
_LEVEL_ORDER = ("first_instance", "appeal", "supreme")
# Hard ceiling on how much of a level's quota one court may take (0.35 → 21 of 60).
# Binds only when the sqrt weighting alone would concentrate too hard.
_COURT_CAP_FRAC = 0.35
# Soft ceiling on how much of ONE FEED'S SELECTION a single primary legal domain
# may hold. ⚠ PER FEED, never global — see "THE SECTOR TRAP" in the docstring.
#
# ⚠ IT IS A RUNNING RATIO, NOT A ROW COUNT: a domain is blocked once it already
# holds this share of what the feed has picked SO FAR. That is deliberate and
# load-bearing. A ceiling expressed as `0.30 × quota` is a different number at
# every --limit, so it prefers different rows at every --limit, and a bigger run
# stops being a superset of a smaller one (measured: 4 of 200 rows dropped
# between --limit 200 and --limit 400). A running ratio is scale-free — the
# predicate at pick k is identical whether the run asked for 200 rows or 10,000 —
# which is what lets the ceiling actually bind AND keeps re-running with a bigger
# --limit a pure top-up.
_DOMAIN_CAP_FRAC = 0.30
# How deep to look inside a court bucket for a row under the domain ceiling.
#
# ⚠ A FIXED 30 MAKES THE CEILING ABOVE A NO-OP AT SCALE, which is the whole trap:
# 30 rows is 0.2% of التجارية's 15,106-row bucket, and that bucket is sorted by
# quality whose head is overwhelmingly المعاملات التجارية — so once that domain
# saturates, a 30-row window essentially never contains an alternative and every
# turn falls through to the head. Measured at --limit 10000: NO ceiling at all
# gives المعاملات التجارية 5,118 of the 7,776 وزارة العدل seats (65.8%), and the
# ceiling with a 30-row window gives 5,098 (65.6%). Twenty rows of "diversity".
#
# So the window is a fraction of the BUCKET (scale-free, like the ratio above),
# with 30 as a floor for small buckets.
#
# Measured on the وزارة العدل slice at --limit 10000 (7,776 seats; the feed holds
# 19,766 eligible rows, only 5,605 of them NOT المعاملات التجارية):
#
#   window      top domain        non-commercial      refs mesh   select time
#   bucket/8    4,741  61.0%      3,035 of 5,605        84.1%        12s
#   bucket/4    4,345  55.9%      3,431 of 5,605        84.1%        14s
#   bucket/2    3,612  46.5%      4,164 of 5,605        84.1%        17s   <- shipped
#   bucket/1    2,373  30.5%      5,403 of 5,605        79.6%        40s
#
# bucket/2 is where diversification stops being free. bucket/1 does hold the
# stated 30%, but only by publishing 96% of every non-commercial row the feed
# has — at which point the ceiling has stopped SHAPING the selection and is just
# draining a bucket, and it costs 4.5 points of citation mesh (450 judgments that
# would no longer link into /regulations) to do it. The ceiling is advisory; the
# report prints the over-ceiling line so a 10k run is reviewed, not assumed.
_DOMAIN_LOOKAHEAD_MIN = 30
_DOMAIN_LOOKAHEAD_DIV = 2
# Sentinel for "this feed carries no legal_domains at all" — the lookahead is
# skipped entirely rather than scanning a window that can never satisfy it.
_DOMAIN_CAP_OFF: Optional[int] = None
# Derived subjects shorter than this are the naming module's fallback, not a title.
_MIN_SUBJECT_WORDS = 3
# Facet label for rows with no legal_domains (they still compete for a slot).
_NO_DOMAIN = "(بدون تصنيف)"
# Slugs longer than this are flagged in the run summary — not rejected, just loud.
_SLUG_SANITY_MAX = 110
# --list prints this many rows before summarising; at 10k published, dumping the
# whole wing is 10k lines and 100 sequential metadata round-trips.
_LIST_PREVIEW = 50

# ── feed allocation (stage 0) ──────────────────────────────────────────────
# Pseudo-feed for a raw `cases.court` value that shared.library.courts does not
# claim (today: the single `court = ''` row, which is ineligible anyway). It gets
# NO allocation, but stays available to the cross-feed top-up so a drifted corpus
# degrades into "publish fewer of these" rather than "fail to reach --limit".
_FEED_OTHER = "(unclaimed)"

# Processing / reporting order. Volume-descending, mirroring COURT_ORDER's rule.
_FEED_ORDER = (FEED_MOJ, FEED_ZATCA, FEED_BOG, FEED_INSURANCE, _FEED_OTHER)

_FEED_LABELS: dict[str, str] = {
    FEED_MOJ: "وزارة العدل",
    FEED_ZATCA: "لجان الزكاة والضريبة",
    FEED_BOG: "ديوان المظالم",
    FEED_INSURANCE: "لجان التأمين",
    _FEED_OTHER: "(محاكم غير مصنّفة)",
}

# CLI-friendly aliases for --feed-alloc. The canonical FEED_* values are accepted
# too; `bog` exists because "board_of_grievances" is a mouthful on a command line.
_FEED_ALIASES: dict[str, str] = {
    **{f: f for f in _FEED_ORDER},
    "moj": FEED_MOJ,
    "justice": FEED_MOJ,
    "bog": FEED_BOG,
    "grievances": FEED_BOG,
    "zatca": FEED_ZATCA,
    "tax": FEED_ZATCA,
    "insurance": FEED_INSURANCE,
    "other": _FEED_OTHER,
}

# Allocation spec sentinels. `_ALLOC_ALL` = every eligible row this feed has;
# `_ALLOC_REST` = whatever the fixed specs left over.
_ALLOC_ALL = "all"
_ALLOC_REST = "rest"

# THE DEFAULT ALLOCATION (plan §3.1, user decision 2026-08-08). A float in (0,1)
# is a share of --limit; an int is an absolute row count; the two sentinels above
# do what they say. Every spec is clamped to the feed's eligible supply and the
# shortfall is redistributed, so the run still reaches --limit.
#
# At the planned `--limit 10000` this resolves EXACTLY to the plan's table:
#
#   لجان التأمين          0.03 → 300 wanted, 224 in the corpus → all 224
#                                (the 76-row shortfall redistributes to وزارة العدل)
#   ديوان المظالم         0.10 → 1,000   of 4,501 eligible
#   لجان الزكاة والضريبة  0.10 → 1,000   of 4,934 eligible
#   وزارة العدل           rest → 7,776   of 19,766 eligible
#
# لجان التأمين is specified as a SHARE rather than `all` on purpose: "publish the
# whole insurance feed" is the intent at 10k, and 3% clears its 224 rows with room
# for corpus growth, while a literal `all` would swallow a small smoke run
# (`--limit 200` would go 224 → trimmed to ~160 of 200 seats, crowding out the
# other three feeds). Use `--feed-alloc insurance=all` to force the literal form.
_FEED_ALLOCATION: dict[str, Any] = {
    FEED_INSURANCE: 0.03,
    FEED_BOG: 0.10,
    FEED_ZATCA: 0.10,
    FEED_MOJ: _ALLOC_REST,
    _FEED_OTHER: 0,
}

# ── usage bonus knobs ──────────────────────────────────────────────────────
# §4.1 per-reference quality points, identical to scripts/build_usage_rank.py.
_USAGE_POINTS: dict[tuple[bool, Any], float] = {
    (True, "high"): 1.00,
    (True, "medium"): 0.60,
    (False, "high"): 0.30,
    (False, "medium"): 0.15,
}
# The reranker's keep-only contract emits high/medium only; anything else scores
# at the floor rather than being dropped, so an upstream vocabulary change
# degrades the bonus instead of silently deleting evidence.
_USAGE_POINTS_FALLBACK = 0.15
# Stage-2 dampener: the most one account may contribute to one judgment.
_USAGE_USER_CAP = 10.0
# Bonus conversion. ⚠ Deliberately capped BELOW the referenced_regulations weight
# of 3: usage nudges the order inside a court bucket, it never overrides the
# citation-mesh signal, and it can never act as a sort key of its own.
_USAGE_BONUS_PER_POINT = 0.5
_USAGE_BONUS_MAX = 2.0

# Small columns + short_summary (the only text column the slug derivation needs).
_CANDIDATE_COLS = (
    "id",
    "case_ref",
    "court",
    "court_level",
    "city",
    "case_number",
    "judgment_number",
    "date_hijri",
    "date_gregorian",
    "legal_domains",
    "short_summary",
)
# Eligibility gate, enforced server-side. Verified equivalent to a trim check:
# the corpus has zero non-null-but-blank values in any of these four columns.
_REQUIRED_COLS = ("short_summary", "facts", "ruling", "reasoning")


# ── row helpers ────────────────────────────────────────────────────────────
def _primary_domain(row: dict) -> str:
    """The row's first ``legal_domains`` entry — its facet for spread purposes."""
    domains = row.get("legal_domains") or []
    for d in domains:
        d = (d or "").strip()
        if d:
            return d
    return _NO_DOMAIN


def _date_key(row: dict) -> tuple[int, int]:
    """Sort key implementing ``date_gregorian`` DESC NULLS LAST.

    Dated rows sort ahead of undated ones (0 < 1) and, within them, newest first
    (negated ordinal ascending). 41% of the corpus has no ``date_gregorian`` at
    all, so the NULLS-LAST half of this is load-bearing, not theoretical.
    """
    raw = row.get("date_gregorian")
    if not raw:
        return (1, 0)
    try:
        return (0, -date.fromisoformat(str(raw)[:10]).toordinal())
    except (ValueError, TypeError):
        return (1, 0)


def _quality_score(row: dict) -> float:
    """Soft preference score — orders eligible rows inside one court bucket.

    ``referenced_regulations`` dominates deliberately: it is what wires a judgment
    into the /regulations mesh, and it is present on only 64% of eligible rows.

    ⚠ ``_usage`` IS A BONUS TERM, NOT A DEMAND SIGNAL AND NOT A SORT KEY.
    ``workspace_item_references`` has touched 229 distinct judgments in the whole
    life of the product, from 5 dev/demo accounts — it is our own pipeline
    traffic. It is here to break ties toward judgments we know render and cite
    well, capped at ``_USAGE_BONUS_MAX`` so it can never outrank the citation
    mesh, and it is never read anywhere except through this function. Anyone
    later tempted to sort on it, or to widen the cap because "these are the
    popular ones", should re-read that first sentence.
    """
    score = 0.0
    if row.get("_refs_count"):
        score += 3
    if row.get("legal_domains"):
        score += 1
    if row.get("date_gregorian"):
        score += 1
    if (row.get("city") or "").strip():
        score += 1
    usage = float(row.get("_usage") or 0.0)
    if usage > 0:
        score += min(_USAGE_BONUS_MAX, usage * _USAGE_BONUS_PER_POINT)
    return score


def _base_sort_key(row: dict) -> tuple:
    """Pre-probe ordering: newest first, ``id`` as the deterministic tie-break."""
    return (_date_key(row), str(row.get("id")))


def _ranked_sort_key(row: dict) -> tuple:
    """Post-probe ordering: quality first, then newest, then ``id``."""
    return (-_quality_score(row), _date_key(row), str(row.get("id")))


# ── sidecar reads ──────────────────────────────────────────────────────────
def _load_existing(client) -> tuple[dict[str, Optional[str]], set[str]]:
    """Return ``(content_id -> slug, {taken slugs})`` for judgments.

    Mirrors ``build_seo_slugs._load_existing``. Slugs already in the sidecar are
    treated as TAKEN so collision dedupe stays stable across re-runs, and their
    content_ids mark rows that must never be recomputed.
    """
    existing: dict[str, Optional[str]] = {}
    taken: set[str] = set()
    offset = 0
    while True:
        res = (
            client.table("seo_item_meta")
            .select("content_id, slug")
            .eq("content_type", CONTENT_TYPE)
            .order("content_id")
            .range(offset, offset + _READ_PAGE - 1)
            .execute()
        )
        batch = res.data or []
        for r in batch:
            cid = r.get("content_id")
            if cid is None:
                continue
            slug = r.get("slug")
            existing[str(cid)] = slug
            if slug:
                taken.add(slug)
        if len(batch) < _READ_PAGE:
            break
        offset += _READ_PAGE
    return existing, taken


def _dedupe(base: str, taken: set[str]) -> str:
    """Return ``base`` or the first free ``base-{n}`` (n>=2).

    Byte-identical in behaviour to ``build_seo_slugs._dedupe`` — the sidecar's
    partial unique index is ``(content_type, slug)``, so judgments dedupe within
    their own namespace. Adds nothing to ``taken``; the caller records the winner.
    """
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _published(client) -> list[tuple[str, str]]:
    """Currently-published judgments as ``[(content_id, slug), ...]``."""
    out: list[tuple[str, str]] = []
    offset = 0
    while True:
        res = (
            client.table("seo_item_meta")
            .select("content_id, slug")
            .eq("content_type", CONTENT_TYPE)
            .not_.is_("slug", "null")
            .order("content_id")
            .range(offset, offset + _READ_PAGE - 1)
            .execute()
        )
        batch = res.data or []
        for r in batch:
            cid, slug = r.get("content_id"), r.get("slug")
            if cid and slug:
                out.append((str(cid), slug))
        if len(batch) < _READ_PAGE:
            break
        offset += _READ_PAGE
    return out


# ── corpus reads (never written to) ────────────────────────────────────────
def _load_candidates(client) -> list[dict]:
    """Page every ELIGIBLE judgment, light columns only, in stable ``id`` order.

    The four ``not.is.null`` filters are the hard eligibility gate; running them
    server-side is what keeps this read to ~29k small rows instead of the whole
    corpus. Ordering by ``id`` (not by date) is required for correct pagination —
    the real ordering happens in Python once the probe has run.
    """
    select = ", ".join(_CANDIDATE_COLS)
    rows: list[dict] = []
    offset = 0
    while True:
        # Rebuilt per page: a supabase-py builder accumulates filters if reused.
        q = client.table(CORPUS_TABLE).select(select)
        for col in _REQUIRED_COLS:
            q = q.not_.is_(col, "null")
        res = q.order("id").range(offset, offset + _READ_PAGE - 1).execute()
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < _READ_PAGE:
            break
        offset += _READ_PAGE
    return rows


def _load_refs_ids(client) -> set[str]:
    """Ids of ELIGIBLE judgments whose ``referenced_regulations`` is non-empty.

    ``_quality_score`` reads this column as a BOOLEAN only, so the column itself
    is never selected: it averages ~895 bytes and peaks at 71 KB, and the whole
    eligible pool would be ~26 MB. Filtering ``neq.[]`` server-side and returning
    bare ids is ~0.7 MB for the same information.

    ``referenced_regulations`` is ``jsonb`` and is non-NULL on every corpus row
    today (10,688 empty arrays, 18,737 non-empty). Should a NULL ever appear,
    ``neq`` excludes it — which is the right answer: no array, no citation mesh.

    Paged 1000 at a time ordered by ``id``. Both halves matter: PostgREST
    truncates a larger response SILENTLY, and paging without a unique order lets
    Postgres return tied rows in a different sequence per request, which drops
    some rows and duplicates others across page boundaries.
    """
    ids: set[str] = set()
    offset = 0
    while True:
        # Rebuilt per page: a supabase-py builder accumulates filters if reused.
        q = client.table(CORPUS_TABLE).select("id")
        for col in _REQUIRED_COLS:
            q = q.not_.is_(col, "null")
        res = (
            q.neq("referenced_regulations", "[]")
            .order("id")
            .range(offset, offset + _READ_PAGE - 1)
            .execute()
        )
        batch = res.data or []
        ids.update(str(r.get("id")) for r in batch if r.get("id"))
        if len(batch) < _READ_PAGE:
            break
        offset += _READ_PAGE
    return ids


# ── usage signal (a BONUS input — read the warnings) ───────────────────────
def _load_case_refs(client) -> list[dict]:
    """Every ``workspace_item_references`` row for ``domain='cases'``.

    354 rows today, but paged on ``ref_pk`` (the table's uuid PK, therefore a
    unique order) so growth cannot silently truncate at the 1000-row clamp.
    """
    rows: list[dict] = []
    offset = 0
    while True:
        res = (
            client.table(REFS_TABLE)
            .select("ref_pk, wi_id, item_id, used, relevance")
            .eq("domain", "cases")
            .order("ref_pk")
            .range(offset, offset + _READ_PAGE - 1)
            .execute()
        )
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < _READ_PAGE:
            break
        offset += _READ_PAGE
    return rows


def _load_ref_owners(client, wi_ids: list[str]) -> dict[str, tuple[str, str]]:
    """``wi_id -> (user_id, conversation_id)`` for the two dampening stages."""
    out: dict[str, tuple[str, str]] = {}
    for i in range(0, len(wi_ids), _ID_CHUNK):
        chunk = wi_ids[i : i + _ID_CHUNK]
        res = (
            client.table(WORKSPACE_ITEMS_TABLE)
            .select("item_id, user_id, conversation_id")
            .in_("item_id", chunk)
            .execute()
        )
        for r in res.data or []:
            out[str(r.get("item_id"))] = (
                str(r.get("user_id") or ""),
                str(r.get("conversation_id") or ""),
            )
    return out


def load_usage_scores(client, user_cap: float = _USAGE_USER_CAP) -> dict[str, float]:
    """``cases.id -> usage score``, dampened exactly as ``build_usage_rank`` does.

    ⚠ THE SIGNAL IS OUR OWN PIPELINE TRAFFIC, NOT MARKET DEMAND — 354 references
    to 229 distinct judgments from 5 accounts over 39 conversations. Read
    ``_quality_score``'s docstring before giving this any more weight.

    Two dampeners, mirroring ``build_usage_rank.load_usage_scores:267-293``:

        conv_score(case, conv) = min(1.0, Σ points in that conversation)
        user_score(case, user) = Σ over that user's conversations
        usage(case)            = Σ over users of min(user_cap, user_score)

    A conversation votes AT MOST ONCE — breadth of questions beats depth of one —
    and no single account can define a public listing. Unlike the regulations
    path there is no chunk roll-up: for ``domain='cases'`` the raw ``item_id`` IS
    a ``cases.id`` (verified: 229 of 229 join).
    """
    refs = _load_case_refs(client)
    if not refs:
        return {}
    owners = _load_ref_owners(client, sorted({str(r.get("wi_id")) for r in refs if r.get("wi_id")}))

    per_conv: dict[tuple[str, str, str], float] = defaultdict(float)
    for r in refs:
        case_id = str(r.get("item_id") or "")
        if not case_id:
            continue
        user, conv = owners.get(str(r.get("wi_id")), ("", ""))
        weight = _USAGE_POINTS.get(
            (bool(r.get("used")), r.get("relevance")), _USAGE_POINTS_FALLBACK
        )
        per_conv[(case_id, user, conv)] += weight

    per_user: dict[tuple[str, str], float] = defaultdict(float)
    for (case_id, user, _conv), pts in per_conv.items():
        per_user[(case_id, user)] += min(1.0, pts)

    scores: dict[str, float] = defaultdict(float)
    for (case_id, _user), pts in per_user.items():
        scores[case_id] += min(user_cap, pts)
    return dict(scores)


def _load_cases_meta(client, ids: list[str]) -> dict[str, dict]:
    """Fetch display metadata for a bounded id set (used by ``--list``)."""
    out: dict[str, dict] = {}
    for i in range(0, len(ids), _ID_CHUNK):
        chunk = ids[i : i + _ID_CHUNK]
        res = (
            client.table(CORPUS_TABLE)
            .select("id, court, court_level, city, date_hijri, date_gregorian, case_ref")
            .in_("id", chunk)
            .execute()
        )
        for r in res.data or []:
            out[str(r.get("id"))] = r
    return out


# ── selection ──────────────────────────────────────────────────────────────
def _level_quotas(limit: int) -> dict[str, int]:
    """Scale ``_LEVEL_MIX`` to ``limit`` by largest remainder (sums to ``limit``).

    Largest-remainder rather than plain rounding so the quotas always add up
    exactly — a rounding shortfall would silently under-publish.
    """
    total_weight = sum(_LEVEL_MIX.values())
    exact = {lvl: limit * w / total_weight for lvl, w in _LEVEL_MIX.items()}
    quotas = {lvl: int(v) for lvl, v in exact.items()}
    shortfall = limit - sum(quotas.values())
    # Hand out the remaining seats to the largest fractional parts; ties broken by
    # _LEVEL_ORDER so the result never depends on dict iteration luck.
    ranked = sorted(
        exact,
        key=lambda lvl: (-(exact[lvl] - int(exact[lvl])), _LEVEL_ORDER.index(lvl)),
    )
    for lvl in ranked[:shortfall]:
        quotas[lvl] += 1
    return quotas


def _feed_quotas(limit: int, supply: dict[str, int], alloc: dict[str, Any]) -> dict[str, int]:
    """Split ``limit`` across the four source feeds — stage 0, above everything.

    Spec forms (see ``_FEED_ALLOCATION``): a float in (0,1) is a share of
    ``limit``, an int is an absolute row count, ``_ALLOC_ALL`` is the feed's whole
    eligible supply, ``_ALLOC_REST`` is the balance.

    Three passes, in this order and for this reason:

      1. Fixed specs resolve and are CLAMPED TO SUPPLY. لجان التأمين asks for 300
         at ``--limit 10000`` and the corpus holds 224, so its quota lands at 224.
      2. ``_ALLOC_REST`` feeds divide what is left — which is how that 76-row
         insurance shortfall reaches وزارة العدل instead of shrinking the run.
      3. Anything still missing (a fixed feed short of supply, several short
         feeds at once) is redistributed to whichever feeds still have spare
         rows, deepest spare first. A quota that exceeds its supply must never
         under-fill the TOTAL — that is the whole contract of this function.

    The final trim exists for small runs: an ``all``/absolute spec can overshoot
    ``--limit`` on its own, and shaving the largest quota down to the runner-up
    keeps every feed represented instead of zeroing the tail.
    """
    order = [f for f in _FEED_ORDER if f in alloc]
    order += [f for f in sorted(alloc) if f not in _FEED_ORDER]
    pos = {f: i for i, f in enumerate(order)}
    quotas: dict[str, int] = {f: 0 for f in order}
    rest = [f for f in order if alloc.get(f) == _ALLOC_REST]

    # 1 — fixed specs, clamped to what the feed can actually supply.
    for feed in order:
        if feed in rest:
            continue
        spec = alloc.get(feed, 0)
        if spec == _ALLOC_ALL:
            want = supply.get(feed, 0)
        elif isinstance(spec, float) and 0.0 < spec < 1.0:
            want = int(round(limit * spec))
        else:
            want = int(spec or 0)
        quotas[feed] = max(0, min(want, supply.get(feed, 0)))

    # 2 — the balance goes to the `rest` feed(s), largest supply first.
    left = max(0, limit - sum(quotas.values()))
    if rest:
        ranked = sorted(rest, key=lambda f: (-supply.get(f, 0), pos[f]))
        base, extra = divmod(left, len(ranked))
        for i, feed in enumerate(ranked):
            quotas[feed] = min(base + (1 if i < extra else 0), supply.get(feed, 0))

    # 3 — redistribute whatever the clamps left on the table.
    short = limit - sum(quotas.values())
    if short > 0:
        for feed in sorted(order, key=lambda f: (-(supply.get(f, 0) - quotas[f]), pos[f])):
            if short <= 0:
                break
            take = min(short, max(0, supply.get(feed, 0) - quotas[feed]))
            quotas[feed] += take
            short -= take

    # 4 — trim an overshoot, largest quota down to the runner-up each pass.
    excess = sum(quotas.values()) - limit
    while excess > 0:
        ranked = sorted(order, key=lambda f: (-quotas[f], pos[f]))
        top = ranked[0]
        if quotas[top] <= 0:
            break
        runner = quotas[ranked[1]] if len(ranked) > 1 else 0
        cut = min(excess, quotas[top], max(1, quotas[top] - runner))
        quotas[top] -= cut
        excess -= cut
    return quotas


class _DomainPolicy:
    """One feed's sector-diversification state. ⚠ ONE PER FEED, NEVER SHARED.

    ``frac is _DOMAIN_CAP_OFF`` marks a feed that carries no ``legal_domains`` at
    all (ديوان المظالم, الزكاة والضريبة, التأمين — 0 of 9,860 rows between them);
    the lookahead is then skipped rather than scanning a window that can never be
    satisfied.
    """

    __slots__ = ("frac", "counts")

    def __init__(self, frac: Optional[float]) -> None:
        self.frac = frac
        self.counts: Counter = Counter()

    @classmethod
    def for_feed(cls, has_domains: bool) -> "_DomainPolicy":
        return cls(_DOMAIN_CAP_FRAC if has_domains else _DOMAIN_CAP_OFF)

    def allows(self, domain: str) -> bool:
        """Is ``domain`` still under its share of what this feed has picked?

        ``count < frac × (picked + 1)`` — the +1 asks "may this domain take the
        NEXT seat", which is what lets the very first pick of any domain through
        and then paces it as the feed fills.
        """
        if self.frac is _DOMAIN_CAP_OFF:
            return True
        return self.counts[domain] < self.frac * (sum(self.counts.values()) + 1)

    def took(self, domain: str) -> None:
        self.counts[domain] += 1

    def cap_at(self, total: int) -> Optional[int]:
        """The row count the ratio works out to over ``total`` picks — reporting
        only. Nothing in the fill reads this."""
        return None if self.frac is _DOMAIN_CAP_OFF else int(round(total * self.frac))


def _take_from_court(bucket: list[dict], domains: _DomainPolicy) -> Optional[dict]:
    """Pop one row from a court bucket, honouring the soft domain ceiling.

    Scans a bucket-relative window for the first row whose primary domain is
    still under its share; if the whole window is saturated it takes the head
    anyway. The ceiling shapes the selection — it must never stall the fill, or a
    corpus that is 72% commercial could never reach ``--limit``.
    """
    if not bucket:
        return None
    if domains.frac is _DOMAIN_CAP_OFF:
        return bucket.pop(0)
    window = min(len(bucket), max(_DOMAIN_LOOKAHEAD_MIN, len(bucket) // _DOMAIN_LOOKAHEAD_DIV))
    for j in range(window):
        if domains.allows(_primary_domain(bucket[j])):
            return bucket.pop(j)
    return bucket.pop(0)


def _court_allowances(sizes: dict[str, int], quota: int, court_cap: int) -> dict[str, int]:
    """Split ``quota`` across a level's courts by SQRT of their corpus size.

    Proportional allotment would let one court swallow the level (التجارية is 15,106
    of the 23,420 eligible first-instance rows → ~39 of 60 seats); a flat split
    ignores that it IS the corpus and hands it 3. sqrt sits between the two — big
    courts stay visibly bigger, small courts stay present. Every court gets at
    least 1 seat, no court exceeds ``court_cap``, and leftover seats are handed out
    by largest fractional remainder so the allotment sums to ``quota`` exactly.

    ``sizes`` must be the FULL bucket sizes, captured before any draining —
    truncation would flatten every large court to the same weight.
    """
    weights = {c: sizes[c] ** 0.5 for c in sizes if sizes[c] > 0}
    if not weights:
        return {}
    total = sum(weights.values())
    exact = {c: quota * w / total for c, w in weights.items()}
    allow = {c: min(court_cap, max(1, int(exact[c]))) for c in weights}

    # Hand out (or, when the floors already overshoot, simply leave) the balance.
    # Ties broken by size then name so the result never depends on dict order.
    order = sorted(
        weights,
        key=lambda c: (-(exact[c] - int(exact[c])), -sizes[c], c),
    )
    used = sum(allow.values())
    idx = 0
    # Bounded: every pass either places a seat or finds every court at its cap.
    while used < quota and idx < len(order) * quota:
        court = order[idx % len(order)]
        if allow[court] < min(court_cap, sizes[court]):
            allow[court] += 1
            used += 1
        elif all(allow[c] >= min(court_cap, sizes[c]) for c in order):
            break
        idx += 1
    return allow


def _fill_level(
    courts: dict[str, list[dict]],
    sizes: dict[str, int],
    quota: int,
    domains: _DomainPolicy,
) -> list[dict]:
    """Round-robin across a level's courts until ``quota`` rows are taken.

    One row per court per pass (that is what produces the spread), with each court
    limited to its sqrt-weighted allowance and visited biggest-allowance-first.
    When a whole pass adds nothing because every court has spent its allowance but
    the quota is still short, every allowance widens by 1 and the pass retries —
    the allowance shapes the selection, so it yields rather than under-filling the
    level. Terminates on an empty corpus, a met quota, or the safety bound.

    At feed scale the widening path is the norm, not the exception: وزارة العدل's
    first_instance quota is ~4,666 while العامة (55 rows) and العمالية (23) empty
    out early, so التجارية's 0.35 cap widens seat by seat until the level is full.
    That is bounded by ``widenings > quota`` and stays linear.
    """
    if quota <= 0 or not courts:
        return []
    picked: list[dict] = []
    counts: Counter = Counter()
    court_cap = max(1, int(round(quota * _COURT_CAP_FRAC)))
    allow = _court_allowances(sizes, quota, court_cap)
    widenings = 0

    while len(picked) < quota and any(courts.values()):
        progressed = False
        for court in sorted(courts, key=lambda c: (-allow.get(c, 0), -sizes.get(c, 0), c)):
            if len(picked) >= quota:
                break
            if counts[court] >= allow.get(court, 0) or not courts[court]:
                continue
            row = _take_from_court(courts[court], domains)
            if row is None:
                continue
            picked.append(row)
            counts[court] += 1
            domains.took(_primary_domain(row))
            progressed = True
        if not progressed:
            widenings += 1
            if widenings > quota:
                break  # safety: the corpus cannot fill this quota
            for court in allow:
                allow[court] += 1
    return picked


def _fill_feed(
    buckets: dict[str, dict[str, list[dict]]],
    sizes: dict[str, dict[str, int]],
    quota: int,
    domains: _DomainPolicy,
) -> tuple[list[dict], dict[str, int], dict[str, int]]:
    """Fill ONE feed's allocation: level mix → sqrt court spread → level top-up.

    ``domains`` is the feed's OWN :class:`_DomainPolicy`. Passing a shared one
    here is the bug that signature exists to prevent — see "THE SECTOR TRAP" in
    the module docstring.

    Returns ``(rows, per_level_counts, level_quotas)``.
    """
    quotas = _level_quotas(quota)
    picked: list[dict] = []
    per_level: dict[str, int] = {}
    if quota <= 0:
        return picked, per_level, quotas

    for level in _LEVEL_ORDER:
        courts = buckets.get(level) or {}
        got = _fill_level(courts, sizes.get(level, {}), quotas.get(level, 0), domains)
        picked.extend(got)
        per_level[level] = len(got)

    # Cross-level top-up. Two of the four feeds are single-level (ديوان المظالم is
    # 100% first_instance, لجان التأمين 100% appeal), so most of their allocation
    # arrives here rather than through the 60/30/10 quotas. Levels with the most
    # candidates left absorb the shortfall first.
    shortfall = quota - len(picked)
    if shortfall > 0:
        order = sorted(
            buckets,
            key=lambda lvl: (
                -sum(len(v) for v in buckets[lvl].values()),
                _LEVEL_ORDER.index(lvl) if lvl in _LEVEL_ORDER else 99,
            ),
        )
        for level in order:
            if shortfall <= 0:
                break
            extra = _fill_level(buckets[level], sizes.get(level, {}), shortfall, domains)
            picked.extend(extra)
            per_level[level] = per_level.get(level, 0) + len(extra)
            shortfall -= len(extra)
    return picked, per_level, quotas


def select_sample(
    client, limit: int, alloc: Optional[dict[str, Any]] = None
) -> tuple[list[dict], dict]:
    """Pick the ``limit`` judgments to publish. Deterministic and re-runnable.

    Returns ``(rows, stats)``. See the module docstring for the full policy; the
    stages here are: eligibility → score (citation mesh + capped usage bonus) →
    bucket by (feed, level, court) → allocate ``limit`` across feeds → fill each
    feed independently → cross-feed top-up.
    """
    alloc = dict(alloc or _FEED_ALLOCATION)
    candidates = _load_candidates(client)

    # Stage 1 — eligibility. The four field gates already ran server-side; what
    # remains is rejecting rows whose derived subject is the naming fallback.
    eligible: list[dict] = []
    rejected_subject = 0
    for row in candidates:
        if len(judgment_subject(row).split()) < _MIN_SUBJECT_WORDS:
            rejected_subject += 1
            continue
        eligible.append(row)

    # Stage 2 — the two soft signals, both attached before any ranking so the
    # ordering is identical at every --limit (which is what makes a bigger run a
    # superset of a smaller one rather than a re-shuffle).
    refs_ids = _load_refs_ids(client)
    usage = load_usage_scores(client)
    for row in eligible:
        rid = str(row.get("id"))
        row["_refs_count"] = 1 if rid in refs_ids else 0
        row["_usage"] = usage.get(rid, 0.0)

    # Stage 0's data: bucket feed → level → court, ranked by quality.
    # feed_for_court() is the shared normalizer; an unclaimed raw court value is
    # NOT dropped, it lands in _FEED_OTHER (no allocation, still available to the
    # cross-feed top-up) and is reported.
    feeds: dict[str, dict[str, dict[str, list[dict]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    unclaimed: Counter = Counter()
    for row in eligible:
        raw_court = (row.get("court") or "").strip()
        feed = feed_for_court(raw_court)
        if feed is None:
            feed = _FEED_OTHER
            unclaimed[raw_court or "(بدون محكمة)"] += 1
        level = (row.get("court_level") or "").strip() or "(unknown)"
        feeds[feed][level][raw_court or "(بدون محكمة)"].append(row)
    for levels in feeds.values():
        for courts in levels.values():
            for rows in courts.values():
                rows.sort(key=_ranked_sort_key)

    # FULL bucket sizes, captured BEFORE any draining — the sqrt weighting must
    # see that التجارية is 15,106 rows, not what is left of it mid-fill.
    sizes: dict[str, dict[str, dict[str, int]]] = {
        feed: {
            level: {court: len(rows) for court, rows in courts.items()}
            for level, courts in levels.items()
        }
        for feed, levels in feeds.items()
    }
    supply = {
        feed: sum(len(rows) for courts in levels.values() for rows in courts.values())
        for feed, levels in feeds.items()
    }
    # A feed named in the allocation but absent from the corpus supplies nothing.
    for feed in alloc:
        supply.setdefault(feed, 0)

    # Stage 0 — split --limit across the feeds, then fill each independently.
    feed_quotas = _feed_quotas(limit, supply, alloc)
    selected: list[dict] = []
    per_feed: dict[str, int] = {}
    per_feed_level: dict[str, dict[str, int]] = {}
    policies: dict[str, _DomainPolicy] = {}

    for feed in [f for f in _FEED_ORDER if f in feeds] + [
        f for f in sorted(feeds) if f not in _FEED_ORDER
    ]:
        quota = feed_quotas.get(feed, 0)
        # ⚠ PER-FEED domain ceiling. Computed globally it would spend 30% of the
        # WHOLE run on one domain — 3,000 of 10,000, i.e. 38.6% of the 7,776
        # وزارة العدل seats that are the only place sectors exist at all.
        has_domains = any(
            r.get("legal_domains")
            for courts in feeds[feed].values()
            for rows in courts.values()
            for r in rows
        )
        policies[feed] = _DomainPolicy.for_feed(has_domains)
        got, levels_got, _lq = _fill_feed(feeds[feed], sizes[feed], quota, policies[feed])
        selected.extend(got)
        per_feed[feed] = len(got)
        per_feed_level[feed] = levels_got

    # Cross-feed top-up. The quota pass already redistributes against SUPPLY, so
    # this only fires if a feed's selection machinery under-delivered against a
    # quota it should have met. Feeds with the most candidates left go first.
    shortfall = limit - len(selected)
    if shortfall > 0:
        order = sorted(
            feeds,
            key=lambda f: (
                -sum(len(v) for courts in feeds[f].values() for v in courts.values()),
                f,
            ),
        )
        for feed in order:
            if shortfall <= 0:
                break
            extra, levels_got, _lq = _fill_feed(
                feeds[feed], sizes[feed], shortfall, policies[feed]
            )
            selected.extend(extra)
            per_feed[feed] = per_feed.get(feed, 0) + len(extra)
            for lvl, n in levels_got.items():
                per_feed_level[feed][lvl] = per_feed_level[feed].get(lvl, 0) + n
            shortfall -= len(extra)

    # Present the selection the way the hub will: newest first, id as tie-break.
    selected.sort(key=_base_sort_key)

    stats = {
        "candidates": len(candidates),
        "eligible": len(eligible),
        "rejected_subject": rejected_subject,
        "feed_supply": supply,
        "feed_quotas": feed_quotas,
        "per_feed": per_feed,
        "per_feed_level": per_feed_level,
        "domain_policies": policies,
        "unclaimed_courts": dict(unclaimed),
        "usage_corpus": len(usage),
        "usage_eligible": sum(1 for r in eligible if r.get("_usage")),
        "refs_corpus": len(refs_ids),
        "selected": len(selected),
        "alloc": alloc,
    }
    return selected, stats


# ── reporting ──────────────────────────────────────────────────────────────
def _feed_of(row: dict) -> str:
    """The report's feed key for one selected row (same rule as the selector)."""
    return feed_for_court((row.get("court") or "").strip()) or _FEED_OTHER


def _print_breakdown(rows: list[dict], stats: dict) -> None:
    """Print the full distribution report behind a selection.

    This is what a 10,000-row publish decision is reviewed from, so it reports
    every axis the allocation reasons about — feed, court SECTION (the
    ``/judgments/courts/{slug}`` pages this feeds), raw court, level, and the
    sector spread of the وزارة العدل slice, which is the only slice where sectors
    exist at all.
    """
    total = len(rows) or 1
    supply, quotas, per_feed = stats["feed_supply"], stats["feed_quotas"], stats["per_feed"]

    print("\n--- distribution: SOURCE FEED (stage 0 allocation) ---")
    print(f"  {'feed':<24} {'spec':>8} {'quota':>7} {'got':>7} {'share':>7} {'eligible':>9}")
    for feed in _FEED_ORDER:
        if not (supply.get(feed) or quotas.get(feed) or per_feed.get(feed)):
            continue
        got = per_feed.get(feed, 0)
        spec = stats["alloc"].get(feed, 0)
        spec_s = spec if isinstance(spec, str) else (f"{spec:g}")
        label = _FEED_LABELS.get(feed, feed)
        print(
            f"  {label:<24} {spec_s:>8} {quotas.get(feed, 0):>7} {got:>7} "
            f"{got / total:>6.1%} {supply.get(feed, 0):>9}"
        )
    if stats["unclaimed_courts"]:
        # A raw court value shared.library.courts does not claim: the corpus grew
        # a feed. These rows have no /judgments/courts/ section to live in.
        print("  ⚠ unclaimed raw court values (no court section exists for these):")
        for court, n in sorted(stats["unclaimed_courts"].items(), key=lambda kv: -kv[1]):
            print(f"      {n:>6}  {court}")

    print("\n--- distribution: court_level, per feed ---")
    print(f"  {'feed':<24} " + " ".join(f"{lvl:>15}" for lvl in _LEVEL_ORDER) + f" {'other':>7}")
    for feed in _FEED_ORDER:
        if not per_feed.get(feed):
            continue
        got = [r for r in rows if _feed_of(r) == feed]
        cells = [
            sum(1 for r in got if (r.get("court_level") or "") == lvl) for lvl in _LEVEL_ORDER
        ]
        other = sum(1 for r in got if (r.get("court_level") or "") not in _LEVEL_ORDER)
        label = _FEED_LABELS.get(feed, feed)
        print(f"  {label:<24} " + " ".join(f"{c:>15}" for c in cells) + f" {other:>7}")
    print(f"  {'TOTAL':<24} " + " ".join(
        f"{sum(1 for r in rows if (r.get('court_level') or '') == lvl):>15}"
        for lvl in _LEVEL_ORDER
    ))

    # The court SECTION is the browsable unit — 12 buckets, city stripped. This is
    # the axis the /judgments/courts/{slug} pages render, so it is reported ahead
    # of the raw strings.
    print("\n--- distribution: court section (shared/library/courts.py) ---")
    sections: Counter = Counter()
    for r in rows:
        sections[slug_for_court((r.get("court") or "").strip()) or _FEED_OTHER] += 1
    for slug, n in sections.most_common():
        print(f"  {n:>6}  {COURT_LABELS.get(slug, slug)}")

    print("\n--- distribution: raw cases.court ---")
    for court, n in Counter((r.get("court") or "(بدون محكمة)") for r in rows).most_common():
        print(f"  {n:>6}  {court}")

    # ⚠ SECTORS EXIST ON ONE FEED. Reporting them over the whole selection would
    # show a healthy-looking spread built mostly out of «(بدون تصنيف)».
    moj_rows = [r for r in rows if _feed_of(r) == FEED_MOJ]
    policy = stats["domain_policies"].get(FEED_MOJ)
    moj_cap = policy.cap_at(len(moj_rows)) if policy else None
    cap_s = "off" if moj_cap is None else f"{_DOMAIN_CAP_FRAC:.0%} ≈ {moj_cap} rows"
    print(
        f"\n--- distribution: primary legal domain — {_FEED_LABELS[FEED_MOJ]} slice only "
        f"({len(moj_rows)} rows, soft ceiling {cap_s}) ---"
    )
    moj_total = len(moj_rows) or 1
    for dom, n in Counter(_primary_domain(r) for r in moj_rows).most_common():
        # The ceiling is advisory: a court whose whole lookahead window shares one
        # domain still yields a row rather than stalling the fill, so an entry CAN
        # sit over it — and at 10k it does, because the feed does not hold enough
        # non-commercial rows to fill 7,776 seats at 30%. Flagged, never silent.
        over = (
            f"  <- over the soft ceiling by {n - moj_cap} "
            "(the feed has no more rows of another sector to reach for)"
            if moj_cap is not None and n > moj_cap
            else ""
        )
        print(f"  {n:>6}  {n / moj_total:>6.1%}  {dom}{over}")
    off_feed_domains = sum(1 for r in rows if _feed_of(r) != FEED_MOJ and r.get("legal_domains"))
    if off_feed_domains:
        print(
            f"  NOTE: {off_feed_domains} row(s) OUTSIDE {_FEED_LABELS[FEED_MOJ]} now carry "
            "legal_domains — the pipeline backfilled a feed. Re-check the per-feed caps."
        )

    picked_usage = sum(1 for r in rows if r.get("_usage"))
    dated = sum(1 for r in rows if r.get("date_gregorian"))
    refs = sum(1 for r in rows if r.get("_refs_count"))
    cities = len({(r.get("city") or "").strip() for r in rows if (r.get("city") or "").strip()})
    print(
        f"\n--- quality ---\n"
        f"  with referenced_regulations : {refs}/{len(rows)} ({refs / total:.1%}) "
        f"of {stats['refs_corpus']} in the eligible corpus\n"
        f"  with date_gregorian         : {dated}/{len(rows)} ({dated / total:.1%})\n"
        f"  distinct cities             : {cities}"
    )
    print(
        f"\n--- usage signal (BONUS ONLY — pipeline traffic, not demand) ---\n"
        f"  judgments ever referenced   : {stats['usage_corpus']} corpus-wide, "
        f"{stats['usage_eligible']} of them eligible\n"
        f"  picked up by this selection : {picked_usage}/{stats['usage_eligible']}"
    )


# ── commands ───────────────────────────────────────────────────────────────
def cmd_list(client, base: str, preview: int = _LIST_PREVIEW) -> None:
    """Print the currently-published judgment slugs with their court context.

    Only the first ``preview`` rows are detailed. At the ~10,000 published rows
    this wing now targets, the full dump is 10k lines of console and 100
    sequential ``cases`` metadata round-trips to produce them — so the metadata
    read is scoped to what actually gets printed.
    """
    published = _published(client)
    print(f"\nPublished judgments: {len(published)}")
    if not published:
        print("  (none — publish with --apply)\n")
        return

    shown = published[:preview] if preview > 0 else published
    meta = _load_cases_meta(client, [cid for cid, _ in shown])
    for cid, slug in sorted(shown, key=lambda p: _base_sort_key(meta.get(p[0], {}))):
        m = meta.get(cid) or {}
        court = (m.get("court") or "?")[:42]
        level = m.get("court_level") or "?"
        hijri = m.get("date_hijri") or "?"
        print(f"  {slug}\n      {court}  |  {level}  |  {hijri}")
    if len(published) > len(shown):
        print(f"\n  … {len(published) - len(shown)} more (raise --list-preview to see them)")
    if base:
        print(f"\n  (URL shape: {base}/judgments/<slug>)")
    print()


def cmd_unpublish_all(client, apply: bool) -> None:
    """Clear ``slug`` on every judgment sidecar row — retires the whole wing.

    An UPDATE, never a DELETE: the sidecar rows survive with any ``seo_tier`` /
    ``gate_override`` intact, and the filter is pinned to ``content_type='judgment'``
    so no other wing can be caught by it.
    """
    published = _published(client)
    print(f"\nunpublish-all: {len(published)} published judgment(s) found.")
    if not published:
        print("  nothing to clear.\n")
        return
    if not apply:
        print(f"  DRY-RUN: would clear {len(published)} slug(s) (pass --apply to write).\n")
        return

    now = datetime.now(timezone.utc).isoformat()
    ids = [cid for cid, _ in published]
    cleared = 0
    for i in range(0, len(ids), _ID_CHUNK):
        chunk = ids[i : i + _ID_CHUNK]
        (
            client.table("seo_item_meta")
            .update({"slug": None, "updated_at": now})
            .eq("content_type", CONTENT_TYPE)
            .in_("content_id", chunk)
            .execute()
        )
        cleared += len(chunk)
    print(f"  APPLIED: cleared {cleared} judgment slug(s). The wing is now unpublished.\n")


def cmd_publish(
    client, limit: int, apply: bool, base: str, alloc: Optional[dict[str, Any]] = None
) -> None:
    """Select the rows, derive slugs, and (on ``--apply``) upsert the sidecar."""
    existing, taken = _load_existing(client)
    rows, stats = select_sample(client, limit, alloc)

    print(
        f"\n--- candidate funnel ---\n"
        f"  eligible corpus rows      : {stats['candidates']}"
        f"  (has short_summary+facts+ruling+reasoning)\n"
        f"  rejected: weak subject    : {stats['rejected_subject']}\n"
        f"  eligible for selection    : {stats['eligible']}\n"
        f"  selected this run         : {stats['selected']} (--limit {limit})"
    )
    if stats["selected"] < limit:
        print(f"  NOTE: corpus could only supply {stats['selected']} of {limit} rows.")

    now_iso = datetime.now(timezone.utc).isoformat()
    payloads: list[dict] = []
    samples: list[tuple[str, str]] = []
    already = 0
    collisions = 0
    longest = ("", 0)

    for row in rows:
        cid = str(row.get("id"))
        # Never rewrite an existing slug — URLs are permanent.
        if existing.get(cid):
            already += 1
            slug = existing[cid] or ""
            if len(slug) > longest[1]:
                longest = (slug, len(slug))
            continue

        base_slug = judgment_slug_base(row)
        final = _dedupe(base_slug, taken)
        if final != base_slug:
            collisions += 1
        taken.add(final)
        if len(final) > longest[1]:
            longest = (final, len(final))

        payloads.append(
            {
                "content_type": CONTENT_TYPE,
                "content_id": cid,
                "slug": final,
                "updated_at": now_iso,
            }
        )
        if len(samples) < 15:
            samples.append((final, judgment_display_title(row)))

    _print_breakdown(rows, stats)

    if samples:
        print("\n--- sample derived slugs ---")
        for slug, title in samples:
            print(f"  {slug}\n      {title}")

    print(
        f"\n--- slugs ---\n"
        f"  already slugged (skipped) : {already}\n"
        f"  new slugs                 : {len(payloads)}\n"
        f"    - collisions (-2/-3…)   : {collisions}\n"
        f"  longest slug              : {longest[1]} chars"
    )
    if longest[1] > _SLUG_SANITY_MAX:
        print(f"  WARNING: exceeds the {_SLUG_SANITY_MAX}-char sanity bound → {longest[0]}")

    if apply and payloads:
        # _WRITE_BATCH is 500: PostgREST returns the upserted representation, and
        # that response is subject to the same 1000-row clamp as any read, so the
        # batch size must stay under it however large the run gets.
        written = 0
        batches = (len(payloads) + _WRITE_BATCH - 1) // _WRITE_BATCH
        for i in range(0, len(payloads), _WRITE_BATCH):
            batch = payloads[i : i + _WRITE_BATCH]
            client.table("seo_item_meta").upsert(
                batch, on_conflict="content_type,content_id"
            ).execute()
            written += len(batch)
            print(f"    … batch {i // _WRITE_BATCH + 1}/{batches}: {written}/{len(payloads)}")
        print(f"\n  APPLIED: upserted {written} judgment slug row(s).")
    elif payloads:
        print(f"\n  DRY-RUN: would upsert {len(payloads)} slug row(s) (pass --apply to write).")
    else:
        print("\n  nothing to write (every selected judgment already has a slug).")

    # NOTE: no published-count ceiling is checked here any more. /judgments
    # paginates library_judgments_ranked (published-only), so SAMPLE_MODE_MAX_IDS
    # no longer applies to this wing — see the module docstring.
    total = len(_published(client))
    print(f"  published judgments now   : {total}")
    if base and payloads:
        print(f"  URL shape                 : {base}/judgments/<slug>")
    if apply and payloads:
        print(
            "\n  ⚠ NEXT STEP — PURGE ISR. This script does not revalidate. POST\n"
            "    /api/revalidate for /judgments, every /judgments/courts/{slug},\n"
            "    /library and their page/{n} variants, or the hub keeps serving\n"
            "    the pre-publish bake."
        )
    print()


def parse_feed_alloc(pairs: Optional[list[str]]) -> dict[str, Any]:
    """Overlay ``FEED=SPEC`` CLI pairs onto ``_FEED_ALLOCATION``.

    SPEC is ``all``, ``rest``, a float in (0,1) read as a share of ``--limit``,
    or an integer read as an absolute row count. Raises ``ValueError`` with a
    usable message; the caller turns that into an argparse error.
    """
    alloc = dict(_FEED_ALLOCATION)
    for raw in pairs or []:
        key, sep, spec = raw.partition("=")
        if not sep:
            raise ValueError(f"--feed-alloc expects FEED=SPEC, got {raw!r}")
        feed = _FEED_ALIASES.get(key.strip().lower())
        if feed is None:
            raise ValueError(
                f"unknown feed {key.strip()!r}; known: {', '.join(sorted(_FEED_ALIASES))}"
            )
        spec = spec.strip().lower()
        if spec in (_ALLOC_ALL, _ALLOC_REST):
            alloc[feed] = spec
        else:
            try:
                alloc[feed] = float(spec) if "." in spec else int(spec)
            except ValueError as exc:
                raise ValueError(f"bad allocation spec {spec!r} for {feed}") from exc
            if isinstance(alloc[feed], float) and not 0.0 < alloc[feed] < 1.0:
                raise ValueError(f"a fractional share must be in (0,1), got {spec}")
            if isinstance(alloc[feed], int) and alloc[feed] < 0:
                raise ValueError(f"an absolute allocation must be >= 0, got {spec}")
    return alloc


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Publish judgments to the SEO public library, allocated per source feed."
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=100,
        help="how many judgments to publish this run (default: 100; the ramp target "
        "is 10000). No hub ceiling applies — /judgments paginates the published-only "
        "library_judgments_ranked view — so this is bounded only by the 29,425 "
        "eligible corpus rows.",
    )
    ap.add_argument(
        "--feed-alloc",
        action="append",
        metavar="FEED=SPEC",
        help="override the per-feed allocation, repeatable. FEED is one of "
        f"{', '.join(sorted(_FEED_ALIASES))}; SPEC is 'all', 'rest', a share in "
        "(0,1), or an absolute row count. Default: "
        + ", ".join(f"{f}={_FEED_ALLOCATION[f]}" for f in _FEED_ORDER if f in _FEED_ALLOCATION),
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="actually write (DEFAULT is a dry-run that writes nothing)",
    )
    ap.add_argument(
        "--list",
        dest="list_only",
        action="store_true",
        help="print the currently-published judgment slugs and exit",
    )
    ap.add_argument(
        "--list-preview",
        type=int,
        default=_LIST_PREVIEW,
        help=f"rows --list details before summarising (default: {_LIST_PREVIEW}; 0 = all)",
    )
    ap.add_argument(
        "--unpublish-all",
        action="store_true",
        help="clear ALL judgment slugs from the sidecar (requires --apply)",
    )
    args = ap.parse_args()

    if args.limit < 1:
        ap.error("--limit must be >= 1")
    if args.list_only and args.unpublish_all:
        ap.error("--list and --unpublish-all are mutually exclusive")
    try:
        alloc = parse_feed_alloc(args.feed_alloc)
    except ValueError as exc:
        ap.error(str(exc))

    # PUBLIC_WEB_URL is cosmetic here (printed URLs only) — never block a run on it.
    base = ""
    try:
        from shared.config import get_settings

        base = (get_settings().PUBLIC_WEB_URL or "").rstrip("/")
    except Exception:  # noqa: BLE001
        pass

    client = get_supabase_client()

    if args.list_only:
        cmd_list(client, base, args.list_preview)
        return

    mode = "APPLY" if args.apply else "DRY-RUN"
    if args.unpublish_all:
        print(f"build_judgment_slugs — mode={mode}, action=unpublish-all")
        cmd_unpublish_all(client, args.apply)
        return

    alloc_s = ", ".join(f"{f}={alloc[f]}" for f in _FEED_ORDER if f in alloc)
    print(f"build_judgment_slugs — mode={mode}, limit={args.limit}\n  feed allocation: {alloc_s}")
    cmd_publish(client, args.limit, args.apply, base, alloc)
    if not args.apply:
        print("(no rows written — re-run with --apply to persist)\n")


if __name__ == "__main__":
    main()
