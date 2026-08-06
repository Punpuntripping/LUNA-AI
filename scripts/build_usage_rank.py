"""Usage-driven display order for the /regulations wing (migration 116).

Writes `seo_item_meta.rank` — the single integer `list_regulations_hub` orders
by, and therefore the thing that decides what a visitor and a crawler see on
page 1 and what is buried on page 40.

Plan: `.claude/plans/ranking_criteria.md`.

WHAT REPLACED WHAT
------------------
The wing used to order "in-force first, then clean_title", which put one
titleless row and EIGHT «النظام الأساس لشركة … للتأمين التعاوني» incorporation
charters on page 1 — because `clean_title` is NULL on 43% of the corpus and
Arabic titles start with their document type (اللائحة 549 · لائحة 426 · دليل 370
· نظام 323), so alphabetical order sorts by type-word, not by name.

This ranks by how often the deep-search pipeline actually cited each regulation,
read out of `workspace_item_references`.

⚠ THE SIGNAL IS PIPELINE TRAFFIC, NOT MARKET DEMAND. Every reference behind the
first ranking came from 9 accounts over 275 conversations (2026-05-25 →
2026-08-03) — the dev and demo users. It is a strong bootstrap prior, not
evidence of what the market wants, which is exactly why §4.2 of the plan caps
what one account can contribute. Re-run as real traffic arrives.

⚠ ZERO USAGE MEANS UNTESTED, NOT UNIMPORTANT.

THE SCORE (plan §4)
-------------------
Per reference, weighted by the pipeline's own verdict on it:

    used=true  + relevance=high    1.00
    used=true  + relevance=medium  0.60
    used=false + relevance=high    0.30
    used=false + relevance=medium  0.15

then aggregated with two dampeners, so neither one long conversation nor one
busy account can define a public ordering:

    conv_score(reg, conv) = min(1.0, Σ points in that conversation)
    user_score(reg, user) = Σ over that user's conversations
    usage_score(reg)      = Σ over users of min(user_score, USER_CAP)

A conversation votes AT MOST ONCE: breadth of questions beats depth of one.

THE ORDER (plan §5) — TWO SEGMENTS
----------------------------------
Ties are not spread across the score range, they are the bottom of it: 88% of
the corpus scores exactly 0 and another 6.5% scores ≤ 1. So there is no per-tie
machinery, just two segments:

  HEAD (score > TAIL_THRESHOLD)  — `score desc, prominence desc, id`. No
      interleave. Usage earned these positions, and the top of the list is
      genuinely one issuer's: المعاملات المدنية / المرافعات الشرعية / الإثبات are
      the three most-cited codes in Saudi practice and all three are 17642.
      Forcing diversity there would demote them for a WORSE signal.

  TAIL (score <= TAIL_THRESHOLD) — entity-diversified so one issuer never
      supplies two neighbouring cards. Without it the tail is 836 consecutive
      cards from entity 5000, then 396 from 17405, then 304 from 17573.

⚠ THE TAIL INTERLEAVE IS A GREEDY MAX-HEAP, NOT A ROUND-ROBIN. An earlier draft
of the plan specified `row_number() over (partition by entity_ref)` — lay out
slot 1 of every entity, then slot 2. That is wrong on a skewed distribution and
fails silently: entity 5000 has 836 rows and the next largest has 396, so from
slot 397 onward 5000 is the ONLY bucket left and its last ~440 rows come out
consecutive — the exact clustering the interleave exists to prevent, moved to
the end of the list where a dry-run sample never looks. The greedy below always
draws from the largest REMAINING bucket that is not the previous entity, which
is the standard optimal construction and rebalances as buckets drain.

⚠ RANK COVERS PUBLISHED ROWS ONLY. Rank is a property of the displayed list, so
it is computed over rows that HAVE a slug. Ranking the whole corpus and letting
the published set be a subsequence would reintroduce adjacency: a subsequence of
a non-adjacent arrangement is not itself non-adjacent. Publish first, rank
second — `--emit-used-ids` writes the publish list for `build_seo_slugs.py`.

⚠ DETERMINISM IS NOT OPTIONAL. "Diversify" here means interleave, never shuffle.
Every ordering key ends in `id`, and the heap breaks ties on `entity_ref`, so a
re-run over unchanged data produces a byte-identical order. A per-run random
order on an ISR-cached paginated surface means page-2 items reappearing on page
3 and duplicate-content signals across every hub page.

⚠ AFTER `--apply`, PURGE ISR. A rank change reorders every hub page; without
`/api/revalidate` the frontend serves the old order from the Data Cache
indefinitely.

USAGE
-----
  python scripts/build_usage_rank.py                        # dry-run: pages, stats, churn
  python scripts/build_usage_rank.py --apply                # write ranks
  python scripts/build_usage_rank.py --emit-used-ids ids.txt
  python scripts/build_usage_rank.py --user-cap 10 --tail-threshold 1.0 --pages 5

Env: SUPABASE_URL / SUPABASE_SERVICE_KEY (via shared.config / shared.db.client).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from heapq import heapify, heappop, heappush
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

_READ_PAGE = 1000
_WRITE_BATCH = 500

HUB_PAGE_SIZE = 9  # must match library_service.HUB_PAGE_SIZE

# --- §4.1 per-reference quality points --------------------------------------
_POINTS = {
    (True, "high"): 1.00,
    (True, "medium"): 0.60,
    (False, "high"): 0.30,
    (False, "medium"): 0.15,
}
# The reranker's keep-only contract emits high/medium only; anything else is
# unexpected and scored at the floor rather than dropped, so a vocabulary change
# upstream degrades the ranking instead of silently deleting evidence.
_POINTS_FALLBACK = 0.15

DEFAULT_USER_CAP = 10.0
DEFAULT_TAIL_THRESHOLD = 1.0

# --- §4.3 prominence — the INTRA-SEGMENT key, never the primary one ---------
_DOC_TYPE_W = {
    "law_statute": 5.0,
    "executive_regulation": 5.0,
    "regulation_generic": 4.0,
    "decision_decree": 4.0,
    "rules": 3.0,
    "controls": 3.0,
    "requirements": 3.0,
    "technical_regulation": 3.0,
    "organizational_framework": 3.0,
    "policy": 2.0,
    "procedure": 2.0,
    "instructions": 2.0,
    "principles_provisions": 2.0,
    "agreement": 1.0,
    "program_plan": 1.0,
    "report_document": 1.0,
    "translation": 1.0,
    "table_list": 1.0,
    "guide": 1.0,
    "standard_spec": 1.0,
    "unspecified": 0.0,
}
_AUTHORITY_W = {
    "binding_law": 5.0,
    "implementing_regulation": 3.0,
    "administrative_decision": 2.5,
    "support_guidance": 2.0,
    "internal_circular": 1.0,
}
_STATUS_W = {
    "in_force": 2.0,
    "in_force_amended": 2.0,
    "under_consultation": 0.0,
    "consultation_ended": 0.0,
    "in_progress": 0.0,
    "cancelled": -3.0,
}
# A title that cannot be read is a card that cannot be clicked. Large enough to
# dominate every other prominence term.
_JUNK_TITLE_PENALTY = -20.0
# «النظام الأساس لشركة …» — 22 near-identical insurance-company incorporation
# charters. Individually legitimate, collectively a page of noise; demoted
# WITHIN their entity bucket so the interleave does not spread them into every
# round (plan §5.3).
_CHARTER_PREFIX = "النظام الأساس لشركة"
_CHARTER_PENALTY = -8.0


def _title_of(row: dict[str, Any]) -> str:
    return (row.get("clean_title") or row.get("title") or "").strip()


def _is_junk_title(row: dict[str, Any]) -> bool:
    """A title unfit to be a public card: absent, a pipeline placeholder, or
    opening on Latin/digits/punctuation (the corpus's untitled scans and
    standards dumps)."""
    t = _title_of(row)
    if not t or "<no title>" in t:
        return True
    return not ("؀" <= t[0] <= "ۿ")


def _authority_weight(value: Any) -> float:
    """Weight from the `legal_authority` analysis JSON. Populated on ~31% of the
    corpus; absent → 0.0, which is a neutral term, not a penalty."""
    if not value or not isinstance(value, str) or not value.strip().startswith("{"):
        return 0.0
    try:
        level = json.loads(value).get("authority_level")
    except (ValueError, AttributeError):
        return 0.0
    return _AUTHORITY_W.get(level, 0.0)


def prominence(row: dict[str, Any], chunk_count: int) -> float:
    """Metadata quality score — orders rows that usage cannot separate."""
    p = _DOC_TYPE_W.get(row.get("doc_type_bucket") or "", 0.0)
    p += _authority_weight(row.get("legal_authority"))
    p += _STATUS_W.get(row.get("status_class") or "", 0.0)
    p += 2.0 if chunk_count >= 20 else (1.0 if chunk_count >= 5 else 0.0)
    if _is_junk_title(row):
        p += _JUNK_TITLE_PENALTY
    if _title_of(row).startswith(_CHARTER_PREFIX):
        p += _CHARTER_PENALTY
    return p


# --- loaders ----------------------------------------------------------------


def _rpc_rows(client, fn: str, order_cols: tuple[str, ...]) -> list[dict[str, Any]]:
    """All rows from a set-returning RPC.

    ⚠ PostgREST caps a single response at 1000 rows, and that cap applies to RPC
    results exactly as it does to table selects — silently, by truncation, with
    no error. `library_reg_usage_refs()` returns 1,894 rows and
    `library_reg_chunk_counts()` returns 3,952; reading either without paging
    drops the tail and quietly under-scores every regulation in it.

    `order_cols` must be a UNIQUE key over the result. Paging an unordered — or
    a non-uniquely ordered — set lets Postgres return tied rows in a different
    sequence per request, which duplicates some rows across page boundaries and
    drops others.
    """
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        qb = client.rpc(fn, {})
        for col in order_cols:
            qb = qb.order(col)
        res = qb.range(offset, offset + _READ_PAGE - 1).execute()
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < _READ_PAGE:
            break
        offset += _READ_PAGE
    return rows


def load_usage_scores(client, user_cap: float) -> dict[str, float]:
    """`regulation_id -> usage_score` via the §4.2 two-stage dampening.

    Reads `library_reg_usage_refs()`, which already rolls the chunk-level
    references up to regulations (the raw `item_id` is a chunk id — joining it
    to the regulations corpus returns nothing at all) and returns md5 grouping
    keys rather than user identifiers.
    """
    per_conv: dict[tuple[str, str, str], float] = defaultdict(float)
    # The RPC's GROUP BY key is exactly this tuple, so it is unique.
    order = ("regulation_id", "user_key", "conv_key", "used", "relevance")
    for r in _rpc_rows(client, "library_reg_usage_refs", order):
        reg = str(r.get("regulation_id") or "")
        if not reg:
            continue
        key = (reg, str(r.get("user_key") or ""), str(r.get("conv_key") or ""))
        w = _POINTS.get((bool(r.get("used")), r.get("relevance")), _POINTS_FALLBACK)
        per_conv[key] += w * int(r.get("n_refs") or 0)

    per_user: dict[tuple[str, str], float] = defaultdict(float)
    for (reg, user, _conv), pts in per_conv.items():
        per_user[(reg, user)] += min(1.0, pts)

    scores: dict[str, float] = defaultdict(float)
    for (reg, _user), pts in per_user.items():
        scores[reg] += min(user_cap, pts)
    return dict(scores)


def load_chunk_counts(client) -> dict[str, int]:
    return {
        str(r.get("regulation_id")): int(r.get("chunk_count") or 0)
        for r in _rpc_rows(client, "library_reg_chunk_counts", ("regulation_id",))
        if r.get("regulation_id")
    }


def load_published(client) -> list[dict[str, Any]]:
    """Published regulations with their current rank, from the 116 view."""
    cols = (
        "id, entity_ref, clean_title, title, doc_type_bucket, status_class, "
        "legal_authority, slug, rank"
    )
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        res = (
            client.table("library_regulations_ranked")
            .select(cols)
            .order("id")
            .range(offset, offset + _READ_PAGE - 1)
            .execute()
        )
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < _READ_PAGE:
            break
        offset += _READ_PAGE
    return rows


# --- ordering ---------------------------------------------------------------


def interleave_by_entity(
    rows: list[dict[str, Any]], sort_key
) -> tuple[list[dict[str, Any]], int]:
    """Greedy max-heap interleave — see the module docstring for why this is not
    a round-robin.

    Always draws from the largest REMAINING entity bucket whose entity differs
    from the previously placed one, which rebalances as buckets drain. Returns
    `(ordered_rows, violations)`; a violation is a forced same-entity neighbour,
    which happens only when one entity is all that is left (mathematically
    unavoidable — a no-two-adjacent arrangement exists iff the largest bucket is
    at most ceil(n/2)).

    Deterministic: buckets are pre-sorted by `sort_key`, and heap ties break on
    `entity_ref`, so equal-size buckets always resolve the same way.
    """
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        buckets[str(r.get("entity_ref") or "")].append(r)
    for b in buckets.values():
        b.sort(key=sort_key)

    cursor: dict[str, int] = defaultdict(int)
    heap = [(-len(items), ent) for ent, items in buckets.items()]
    heapify(heap)

    out: list[dict[str, Any]] = []
    violations = 0
    prev: Optional[str] = None
    while heap:
        neg_n, ent = heappop(heap)
        if ent == prev and heap:
            # Largest bucket is the one we just placed — take the runner-up and
            # put the largest back, so it stays available for the next slot.
            alt = heappop(heap)
            heappush(heap, (neg_n, ent))
            neg_n, ent = alt
        elif ent == prev:
            violations += 1  # nothing else left; forced neighbour

        out.append(buckets[ent][cursor[ent]])
        cursor[ent] += 1
        if neg_n + 1 < 0:
            heappush(heap, (neg_n + 1, ent))
        prev = ent
    return out, violations


def build_order(
    rows: list[dict[str, Any]],
    scores: dict[str, float],
    chunks: dict[str, int],
    tail_threshold: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Two segments (plan §5): scored head in score order, tail interleaved."""
    for r in rows:
        rid = str(r.get("id"))
        r["_score"] = scores.get(rid, 0.0)
        r["_prom"] = prominence(r, chunks.get(rid, 0))

    def key(r: dict[str, Any]):
        return (-r["_score"], -r["_prom"], str(r.get("id")))

    head = sorted((r for r in rows if r["_score"] > tail_threshold), key=key)
    tail_rows = [r for r in rows if r["_score"] <= tail_threshold]
    tail, violations = interleave_by_entity(tail_rows, key)

    ordered = head + tail
    stats = {
        "head": len(head),
        "tail": len(tail),
        "violations": violations,
        "scored": sum(1 for r in rows if r["_score"] > 0),
        "zero": sum(1 for r in rows if r["_score"] <= 0),
    }
    return ordered, stats


# --- reporting --------------------------------------------------------------


def report(ordered: list[dict[str, Any]], stats: dict[str, Any], pages: int) -> None:
    print(
        f"\n  published rows     : {len(ordered)}\n"
        f"  with usage > 0     : {stats['scored']}\n"
        f"  zero usage         : {stats['zero']}\n"
        f"  head / tail        : {stats['head']} / {stats['tail']}\n"
        f"  adjacency forced   : {stats['violations']}"
    )

    # Same-entity neighbours in the FINAL order — the number the diversity rule
    # is actually judged on (head repeats are expected and allowed, §5.2).
    adjacent = sum(
        1
        for a, b in zip(ordered, ordered[1:])
        if a.get("entity_ref") and a.get("entity_ref") == b.get("entity_ref")
    )
    print(f"  same-entity pairs  : {adjacent} (head repeats are by design)")

    print(f"\n  first {pages} pages as they would render ({HUB_PAGE_SIZE}/page):")
    for i, r in enumerate(ordered[: pages * HUB_PAGE_SIZE]):
        if i % HUB_PAGE_SIZE == 0:
            print(f"\n  --- page {i // HUB_PAGE_SIZE + 1} ---")
        title = _title_of(r)[:52]
        print(
            f"    {i + 1:>4}. [{r['_score']:>6.2f}] "
            f"{str(r.get('entity_ref') or '-'):<7} {title}"
        )

    churn = sum(1 for i, r in enumerate(ordered) if r.get("rank") != i + 1)
    moved = [
        (r.get("rank"), i + 1, _title_of(r)[:44])
        for i, r in enumerate(ordered)
        if r.get("rank") is not None and r.get("rank") != i + 1
    ]
    print(f"\n  rank churn         : {churn} of {len(ordered)} rows change position")
    if moved:
        print("  largest moves:")
        for old, new, t in sorted(moved, key=lambda m: -abs(m[0] - m[1]))[:10]:
            print(f"    {old:>4} -> {new:<4}  {t}")


def write_ranks(client, ordered: list[dict[str, Any]]) -> int:
    now_iso = datetime.now(timezone.utc).isoformat()
    payloads = [
        {
            "content_type": "regulation",
            "content_id": str(r.get("id")),
            "rank": i + 1,
            "usage_score": round(float(r["_score"]), 4),
            "updated_at": now_iso,
        }
        for i, r in enumerate(ordered)
    ]
    written = 0
    for i in range(0, len(payloads), _WRITE_BATCH):
        batch = payloads[i : i + _WRITE_BATCH]
        client.table("seo_item_meta").upsert(
            batch, on_conflict="content_type,content_id"
        ).execute()
        written += len(batch)
    return written


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Compute and write seo_item_meta.rank for /regulations."
    )
    ap.add_argument("--apply", action="store_true", help="write (DEFAULT is dry-run)")
    ap.add_argument("--user-cap", type=float, default=DEFAULT_USER_CAP)
    ap.add_argument("--tail-threshold", type=float, default=DEFAULT_TAIL_THRESHOLD)
    ap.add_argument("--pages", type=int, default=5, help="pages to print (default 5)")
    ap.add_argument(
        "--emit-used-ids",
        metavar="PATH",
        help="write every regulation id with usage > 0 (the publish list for "
        "build_seo_slugs.py --ids-file) and exit",
    )
    args = ap.parse_args()

    client = get_supabase_client()
    scores = load_usage_scores(client, args.user_cap)

    if args.emit_used_ids:
        ids = sorted(rid for rid, s in scores.items() if s > 0)
        Path(args.emit_used_ids).write_text("\n".join(ids) + "\n", encoding="utf-8")
        print(f"wrote {len(ids)} regulation ids -> {args.emit_used_ids}")
        return

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(
        f"build_usage_rank — mode={mode}, user_cap={args.user_cap}, "
        f"tail_threshold={args.tail_threshold}"
    )
    print(f"  regulations with usage (corpus-wide): {len(scores)}")

    chunks = load_chunk_counts(client)
    rows = load_published(client)
    if not rows:
        print("  no published regulations — nothing to rank.")
        return

    ordered, stats = build_order(rows, scores, chunks, args.tail_threshold)
    report(ordered, stats, max(1, args.pages))

    if args.apply:
        n = write_ranks(client, ordered)
        print(f"\n  APPLIED: wrote rank for {n} rows.")
        print("  ⚠ now purge ISR (/api/revalidate) or the hub serves the old order.")
    else:
        print(f"\n  DRY-RUN: would write rank for {len(ordered)} rows (--apply to write).")


if __name__ == "__main__":
    main()
