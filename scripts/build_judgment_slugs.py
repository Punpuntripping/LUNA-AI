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

THE STAGE-1 SAMPLE
------------------
This publishes a SAMPLE (``--limit``, default 100), not the corpus. That is a
hard requirement, not caution: ``library_service.SAMPLE_MODE_MAX_IDS`` is 300 and
the hub listers only paginate correctly over a published set at or below it.
Cross that line and the wing silently switches to full-corpus steady-state mode.

SELECTION POLICY (deterministic, re-runnable, tunable via the constants below)
-----------------------------------------------------------------------------
The point of the sample is to VALIDATE the wing, so it must look like the corpus
rather than like its biggest bucket (التجارية alone is 19,481 of the eligible
rows). Four stages:

1. ELIGIBILITY (hard gate). A row must have ``short_summary`` AND ``facts`` AND
   ``ruling`` AND ``reasoning``. A judgment page with no ruling is a dead page,
   and with no reasoning the gate has nothing worth protecting. ``reasoning`` was
   promoted from "preferred" to required because it is near-universal — 29,425 of
   the 29,470 rows that pass the other three also have it, so the gate costs 45
   rows and buys a guarantee. The derived subject must also carry at least
   ``_MIN_SUBJECT_WORDS`` words, which rejects rows whose summary degrades to the
   naming module's «حكم قضائي» fallback.

2. QUALITY SCORE (soft preference, orders rows inside a court). Chiefly
   ``referenced_regulations``: those power the internal-linking mesh between
   ``/judgments`` and ``/regulations``, and only 18,737 of 29,425 eligible rows
   have any, so it is a real discriminator (weight 3). ``legal_domains`` (facets),
   ``date_gregorian`` (hub ordering) and ``city`` add 1 each.

3. COURT-LEVEL MIX (``_LEVEL_MIX``): 60 first_instance / 30 appeal / 10 supreme
   per 100, scaled to ``--limit`` by largest remainder. Without it the sample
   would be ~80% first-instance commercial. Levels that cannot fill their quota
   (supreme has only 116 eligible rows corpus-wide) hand the shortfall back and
   the other levels top it up, so the run always reaches ``--limit`` if the corpus
   can supply it.

4. SPREAD, inside each level: courts are drained ROUND-ROBIN, one row per court
   per pass, but each court's total allowance is weighted by the SQUARE ROOT of
   its bucket size and then clamped to ``_COURT_CAP_FRAC`` of the level quota
   (floor 1, so no court is ever shut out). sqrt is the whole trick: proportional
   allotment would hand التجارية ~40 of 60 first-instance seats, while a flat
   round-robin hands it 3 — sqrt lands it near 16, which keeps the flagship court
   leading without burying the other 27. A soft global ceiling of
   ``_DOMAIN_CAP_FRAC`` of ``--limit`` per primary ``legal_domains`` entry runs on
   top, enforced by a bounded lookahead (``_DOMAIN_LOOKAHEAD``): when a court's
   turn comes, the first row within the window whose domain is still under the
   ceiling wins, else the head is taken anyway. All ceilings are advisory — they
   shape the sample, they never stall the fill or make the run non-deterministic.

CORPUS SURPRISE THAT DRIVES THE WEIGHTING
-----------------------------------------
``legal_domains`` and ``date_gregorian`` are NOT sparse at row level — they are
populated per SOURCE FEED. All 19,473 التجارية rows carry both; every ديوان
المظالم and every هيئة الزكاة والضريبة committee row carries NEITHER (0 of 8,000+).
So "spread across courts" and "populate the domain facets / date sort" are in
direct tension: every seat handed to a committee court is a seat with no facet and
no date. A flat round-robin inverted the corpus profile — 66% of the sample had no
domain where the corpus is 67% WITH one. The sqrt weighting above is what restores
it. If the pipeline later backfills domains for the committee feeds, raise
``_COURT_CAP_FRAC`` / flatten the weighting to widen the spread again.

Ordering is stable at every step: ``date_gregorian`` DESC NULLS LAST, then ``id``
as the tie-break, with the quality score ahead of both inside a court bucket. The
same rows are therefore chosen on every run, which is what makes the script
idempotent: a second run selects the same 100 rows, finds them slugged, and
writes nothing.

READ STRATEGY (why it is two-phase)
-----------------------------------
``referenced_regulations`` averages ~895 bytes and peaks at 71 KB, so pulling it
for all 29k candidates would move ~26 MB through PostgREST to pick 100 rows.
Instead the candidate read stays light (small columns + ``short_summary``, the
one text column the slug actually needs), and ``referenced_regulations`` is
probed only for the top ``_PROBE_PER_COURT`` rows of each court bucket — roughly
1,300 rows. Selection draws exclusively from those probed heads, which is sound
because the per-court ceiling is always far below the probe depth. The eligibility
filters run server-side as ``not.is.null`` (verified equivalent to a trim check:
the corpus has zero non-null-but-blank values in these four columns).

REVERSIBILITY
-------------
``--unpublish-all --apply`` clears ``slug`` on every ``content_type='judgment'``
sidecar row, retiring the whole wing. It is an UPDATE, not a DELETE: the rows
survive with their ``seo_tier`` / ``gate_override`` intact, and no other
content_type is touched. Re-running the publisher afterwards re-derives the same
slugs from the same module, so unpublish/republish is a round trip.

Run from the repo root:
  python scripts/build_judgment_slugs.py                    # dry-run, 100 rows
  python scripts/build_judgment_slugs.py --limit 40         # dry-run, 40 rows
  python scripts/build_judgment_slugs.py --apply            # publish the sample
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
from typing import Any, Optional

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
from shared.seo.judgment_naming import (
    court_level_label,
    judgment_display_title,
    judgment_slug_base,
    judgment_subject,
)

CONTENT_TYPE = "judgment"
CORPUS_TABLE = "cases"

# Read-page size (PostgREST caps a single response at 1000 rows by default).
_READ_PAGE = 1000
# Upsert / update batch size on --apply.
_WRITE_BATCH = 500
# Chunk size for `.in_("id", [...])` lookups (URL-length safety).
_ID_CHUNK = 100

# ── selection policy knobs (see the module docstring) ──────────────────────
# Court-level mix per 100 published rows; scaled to --limit by largest remainder.
_LEVEL_MIX: dict[str, int] = {"first_instance": 60, "appeal": 30, "supreme": 10}
# Deterministic tie-break order for quota rounding and shortfall top-up.
_LEVEL_ORDER = ("first_instance", "appeal", "supreme")
# Hard ceiling on how much of a level's quota one court may take (0.35 → 21 of 60).
# Binds only when the sqrt weighting alone would concentrate too hard.
_COURT_CAP_FRAC = 0.35
# Soft ceiling on how much of the WHOLE sample one primary legal domain may take.
_DOMAIN_CAP_FRAC = 0.30
# How deep to look inside a court bucket for a row under the domain ceiling.
_DOMAIN_LOOKAHEAD = 30
# Depth of the referenced_regulations probe per court bucket; selection draws
# only from these heads, so it must stay comfortably above any per-court cap.
# Scaled with --limit by _probe_depth() so a bigger run cannot starve a bucket.
_PROBE_PER_COURT = 40
# Derived subjects shorter than this are the naming module's fallback, not a title.
_MIN_SUBJECT_WORDS = 3
# Facet label for rows with no legal_domains (they still compete for a slot).
_NO_DOMAIN = "(بدون تصنيف)"
# Slugs longer than this are flagged in the run summary — not rejected, just loud.
_SLUG_SANITY_MAX = 110

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


def _quality_score(row: dict) -> int:
    """Soft preference score — orders eligible rows inside one court bucket.

    ``referenced_regulations`` dominates deliberately: it is what wires a judgment
    into the /regulations mesh, and it is present on only 64% of eligible rows.
    """
    score = 0
    if row.get("_refs_count"):
        score += 3
    if row.get("legal_domains"):
        score += 1
    if row.get("date_gregorian"):
        score += 1
    if (row.get("city") or "").strip():
        score += 1
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


def _probe_referenced_regulations(client, ids: list[str]) -> dict[str, int]:
    """Fetch ``referenced_regulations`` for a bounded id set → ``{id: count}``.

    Deliberately NOT run over the full candidate pool: the column averages ~895
    bytes and peaks at 71 KB, so probing only the court-bucket heads turns a
    ~26 MB read into a ~1 MB one.
    """
    counts: dict[str, int] = {}
    for i in range(0, len(ids), _ID_CHUNK):
        chunk = ids[i : i + _ID_CHUNK]
        res = (
            client.table(CORPUS_TABLE)
            .select("id, referenced_regulations")
            .in_("id", chunk)
            .execute()
        )
        for r in res.data or []:
            refs = r.get("referenced_regulations")
            counts[str(r.get("id"))] = len(refs) if isinstance(refs, list) else 0
    return counts


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


def _probe_depth(limit: int) -> int:
    """How deep to probe each court bucket, scaled so a big run cannot starve one.

    Selection draws only from the probed head, so the depth must stay above the
    largest allowance any single court can receive (``_COURT_CAP_FRAC`` of a level
    quota, and a level quota never exceeds ``limit``).
    """
    return max(_PROBE_PER_COURT, int(limit * _COURT_CAP_FRAC) + 5)


def _take_from_court(bucket: list[dict], domain_counts: Counter, domain_cap: int) -> Optional[dict]:
    """Pop one row from a court bucket, honouring the soft domain ceiling.

    Scans up to ``_DOMAIN_LOOKAHEAD`` rows for one whose primary domain is still
    under the ceiling; if the whole window is saturated it takes the head anyway.
    The ceiling shapes the sample — it must never stall the fill, or a corpus
    that is 62% commercial could never reach ``--limit``.
    """
    if not bucket:
        return None
    window = min(len(bucket), _DOMAIN_LOOKAHEAD)
    for j in range(window):
        if domain_counts[_primary_domain(bucket[j])] < domain_cap:
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

    ``sizes`` must be the FULL bucket sizes, not the probe-truncated heads —
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
    domain_counts: Counter,
    domain_cap: int,
) -> list[dict]:
    """Round-robin across a level's courts until ``quota`` rows are taken.

    One row per court per pass (that is what produces the spread), with each court
    limited to its sqrt-weighted allowance and visited biggest-allowance-first.
    When a whole pass adds nothing because every court has spent its allowance but
    the quota is still short, every allowance widens by 1 and the pass retries —
    the allowance shapes the sample, so it yields rather than under-filling the
    level. Terminates on an empty corpus, a met quota, or the safety bound.
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
            row = _take_from_court(courts[court], domain_counts, domain_cap)
            if row is None:
                continue
            picked.append(row)
            counts[court] += 1
            domain_counts[_primary_domain(row)] += 1
            progressed = True
        if not progressed:
            widenings += 1
            if widenings > quota:
                break  # safety: the corpus cannot fill this quota
            for court in allow:
                allow[court] += 1
    return picked


def select_sample(client, limit: int) -> tuple[list[dict], dict]:
    """Pick the ``limit`` judgments to publish. Deterministic and re-runnable.

    Returns ``(rows, stats)``. See the module docstring for the full policy; the
    stages here are: eligibility → bucket by (level, court) → probe the heads for
    ``referenced_regulations`` → re-rank by quality → quota'd round-robin fill →
    cross-level top-up.
    """
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

    # Bucket by level → court, newest first.
    buckets: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in eligible:
        level = (row.get("court_level") or "").strip() or "(unknown)"
        court = (row.get("court") or "").strip() or "(بدون محكمة)"
        buckets[level][court].append(row)
    for courts in buckets.values():
        for rows in courts.values():
            rows.sort(key=_base_sort_key)

    # FULL bucket sizes, captured BEFORE the probe truncation below — the sqrt
    # weighting must see that التجارية is 15,106 rows and not a 40-row head.
    sizes: dict[str, dict[str, int]] = {
        level: {court: len(rows) for court, rows in courts.items()}
        for level, courts in buckets.items()
    }

    # Stage 2 — probe referenced_regulations for the head of each court bucket
    # only, then re-rank those heads by quality. Rows past the probe depth are
    # dropped from contention (a court's allowance is always well below it).
    depth = _probe_depth(limit)
    probe_ids: list[str] = []
    for courts in buckets.values():
        for court, rows in courts.items():
            del rows[depth:]
            probe_ids.extend(str(r.get("id")) for r in rows)
    refs_counts = _probe_referenced_regulations(client, probe_ids)
    for courts in buckets.values():
        for rows in courts.values():
            for r in rows:
                r["_refs_count"] = refs_counts.get(str(r.get("id")), 0)
            rows.sort(key=_ranked_sort_key)

    # Stages 3+4 — quota'd, spread-aware fill.
    quotas = _level_quotas(limit)
    domain_counts: Counter = Counter()
    domain_cap = max(1, int(round(limit * _DOMAIN_CAP_FRAC)))
    selected: list[dict] = []
    per_level: dict[str, int] = {}

    for level in _LEVEL_ORDER:
        courts = buckets.get(level) or {}
        got = _fill_level(
            courts, sizes.get(level, {}), quotas.get(level, 0), domain_counts, domain_cap
        )
        selected.extend(got)
        per_level[level] = len(got)

    # Cross-level top-up: supreme holds only 116 eligible rows corpus-wide, so a
    # level under-delivering is normal, not exceptional. Levels with the most
    # candidates left absorb the shortfall first.
    shortfall = limit - len(selected)
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
            extra = _fill_level(
                buckets[level], sizes.get(level, {}), shortfall, domain_counts, domain_cap
            )
            selected.extend(extra)
            per_level[level] = per_level.get(level, 0) + len(extra)
            shortfall -= len(extra)

    # Present the sample the way the hub will: newest first, id as tie-break.
    selected.sort(key=_base_sort_key)

    stats = {
        "candidates": len(candidates),
        "eligible": len(eligible),
        "rejected_subject": rejected_subject,
        "quotas": quotas,
        "per_level": per_level,
        "domain_cap": domain_cap,
        "selected": len(selected),
    }
    return selected, stats


# ── reporting ──────────────────────────────────────────────────────────────
def _print_breakdown(rows: list[dict], stats: dict) -> None:
    """Print the per-level / per-court / per-domain spread of a selection."""
    quotas = stats["quotas"]
    print("\n--- distribution: court_level ---")
    for level in _LEVEL_ORDER:
        n = sum(1 for r in rows if (r.get("court_level") or "") == level)
        label = court_level_label(level) or level
        print(f"  {level:<15} {label:<16} {n:>4}   (target {quotas.get(level, 0)})")
    other = sum(1 for r in rows if (r.get("court_level") or "") not in _LEVEL_ORDER)
    if other:
        print(f"  {'(other)':<15} {'':<16} {other:>4}")

    print("\n--- distribution: court ---")
    for court, n in Counter((r.get("court") or "(بدون محكمة)") for r in rows).most_common():
        print(f"  {n:>4}  {court}")

    print(f"\n--- distribution: primary legal domain (soft cap {stats['domain_cap']}) ---")
    for dom, n in Counter(_primary_domain(r) for r in rows).most_common():
        # The cap is advisory: a court whose whole lookahead window shares one
        # domain still yields a row rather than stalling the fill, so an entry
        # CAN sit over the cap. Flagged rather than silently over-running.
        over = "  <- over soft cap (court had no alternative)" if n > stats["domain_cap"] else ""
        print(f"  {n:>4}  {dom}{over}")

    dated = sum(1 for r in rows if r.get("date_gregorian"))
    refs = sum(1 for r in rows if r.get("_refs_count"))
    cities = len({(r.get("city") or "").strip() for r in rows if (r.get("city") or "").strip()})
    print(
        f"\n--- quality ---\n"
        f"  with referenced_regulations : {refs}/{len(rows)}\n"
        f"  with date_gregorian         : {dated}/{len(rows)}\n"
        f"  distinct cities             : {cities}"
    )


# ── commands ───────────────────────────────────────────────────────────────
def cmd_list(client, base: str) -> None:
    """Print every currently-published judgment slug with its court context."""
    published = _published(client)
    print(f"\nPublished judgments: {len(published)}")
    if not published:
        print("  (none — publish a sample with --apply)\n")
        return

    meta = _load_cases_meta(client, [cid for cid, _ in published])
    rows = sorted(
        published,
        key=lambda p: _base_sort_key(meta.get(p[0], {})),
    )
    for cid, slug in rows:
        m = meta.get(cid) or {}
        court = (m.get("court") or "?")[:42]
        level = (m.get("court_level") or "?")
        hijri = (m.get("date_hijri") or "?")
        print(f"  {slug}\n      {court}  |  {level}  |  {hijri}")
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


def cmd_publish(client, limit: int, apply: bool, base: str) -> None:
    """Select the sample, derive slugs, and (on ``--apply``) upsert the sidecar."""
    existing, taken = _load_existing(client)
    rows, stats = select_sample(client, limit)

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
        written = 0
        for i in range(0, len(payloads), _WRITE_BATCH):
            batch = payloads[i : i + _WRITE_BATCH]
            client.table("seo_item_meta").upsert(
                batch, on_conflict="content_type,content_id"
            ).execute()
            written += len(batch)
        print(f"\n  APPLIED: upserted {written} judgment slug row(s).")
    elif payloads:
        print(f"\n  DRY-RUN: would upsert {len(payloads)} slug row(s) (pass --apply to write).")
    else:
        print("\n  nothing to write (every selected judgment already has a slug).")

    total = len(_published(client))
    print(f"  published judgments now   : {total}")
    if total > 300:
        print(
            "  WARNING: over SAMPLE_MODE_MAX_IDS (300) — the hub listers have left "
            "sample mode. Trim the wing or finish the full-corpus rollout."
        )
    if base and payloads:
        print(f"  URL shape                 : {base}/judgments/<slug>")
    print()


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Publish a representative SAMPLE of judgments to the SEO library."
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=100,
        help="how many judgments to publish this run (default: 100; the wing must "
        "stay at or below 300 to keep the hub listers in sample mode)",
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
        "--unpublish-all",
        action="store_true",
        help="clear ALL judgment slugs from the sidecar (requires --apply)",
    )
    args = ap.parse_args()

    if args.limit < 1:
        ap.error("--limit must be >= 1")
    if args.list_only and args.unpublish_all:
        ap.error("--list and --unpublish-all are mutually exclusive")

    # PUBLIC_WEB_URL is cosmetic here (printed URLs only) — never block a run on it.
    base = ""
    try:
        from shared.config import get_settings

        base = (get_settings().PUBLIC_WEB_URL or "").rstrip("/")
    except Exception:  # noqa: BLE001
        pass

    client = get_supabase_client()

    if args.list_only:
        cmd_list(client, base)
        return

    mode = "APPLY" if args.apply else "DRY-RUN"
    if args.unpublish_all:
        print(f"build_judgment_slugs — mode={mode}, action=unpublish-all")
        cmd_unpublish_all(client, args.apply)
        return

    print(f"build_judgment_slugs — mode={mode}, limit={args.limit}")
    cmd_publish(client, args.limit, args.apply, base)
    if not args.apply:
        print("(no rows written — re-run with --apply to persist)\n")


if __name__ == "__main__":
    main()
