"""Which published regulations does the article-coverage rule flip to chunks?

READ-ONLY AUDIT. Writes nothing, has no ``--apply``, and touches no table — it
models `.claude/plans/regulation_article_coverage_fallback.md` §3 against live
data and prints the answer.

WHAT IT MODELS
--------------
``get_regulation_doc`` used to commit the whole reading surface to the article
view on the mere existence of one ``seo_articles`` row, however little of the
document that row set covered. The rule (plan §3):

    missing     = max(article_no) - count(rows)
    missing_pct = missing / max(article_no)
    trustworthy = NOT (missing > MIN_MISSING AND missing_pct > THRESHOLD)

Past both thresholds the page renders from ``chunks_v2`` instead — anon doc page,
authed full reveal and unlock price alike — and the price stops being weighted by
characters and starts being weighted by CHUNKS at 1 chunk ≈ 3 مواد.

    cost_now = clamp(ceil(articles / 25), 1, 8)   if the reg has articles
               clamp(ceil(chars / 25_000), 1, 8)  otherwise      [being deleted]
    cost_new = clamp(ceil(articles / 25), 1, 8)   if the index is TRUSTED
               clamp(ceil(chunks * 3 / 25), 1, 8) otherwise            [new]

⚠ GAPS ARE COUNTED FROM THE NUMBERING, NEVER FROM ROW HEALTH. On the document
this rule exists for (``17900_reg_128_p2`` — اللائحة التنفيذية لنظام العمل ج2)
every present row is healthy: 0 rows non-``extracted``, 0 with NULL text. The
damage is 164 مواد that are simply ABSENT, so any completeness test written
against ``extraction_status`` or ``article_text`` scores that page a perfect 100
and reports nothing.

⚠ THE ZERO-CHUNK LINE IS THE ONE TO READ. A regulation with no chunks KEEPS its
article surface however bad the gaps are — a partial document beats a blank one
(plan §3 hard guard). This audit counts those separately and shouts if there are
any: a flip with nothing to flip TO would blank a live public page.

⚠ THE COST CONSTANTS ARE MIRRORED HERE ON PURPOSE, NOT IMPORTED. The audit has to
price the BEFORE state, and ``CHARS_PER_UNLOCK`` is deleted from
``library_service`` by the very change being measured — importing would make the
"now" column silently become the "new" column the moment the service lands, i.e.
the report would show zero movement and look like a passing audit. If the service
constants move, update the mirror deliberately.

⚠ ``--threshold`` / ``--min-missing`` re-model the rule without editing code.
Both conditions must hold to flip, so raising either one only ever shrinks the
flip set.

COST
----
The "now" price of a chunk-only regulation needs ``sum(length(content))``, which
PostgREST can only produce by shipping every chunk BODY over the wire — the exact
round-trip cost the plan's §4.4 ``count()`` removes. That scan runs once per
chunk-only regulation (~170 requests) and is why this script takes a couple of
minutes. Nothing in the "new" column needs it.

USAGE
-----
  python scripts/check_article_coverage.py
  python scripts/check_article_coverage.py --threshold 0.20 --min-missing 5

Env: SUPABASE_URL / SUPABASE_SERVICE_KEY (via shared.config / shared.db.client).
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any
from uuid import UUID

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

# PostgREST caps a single response at 1000 rows, silently, by truncation.
_READ_PAGE = 1000
# `in.(...)` batch size — the URL-length trap; library_service uses 150.
_IN_BATCH = 100

# --- mirrored from backend/app/services/library_service.py (see docstring) ---
UNLOCK_COST_MIN = 1
UNLOCK_COST_MAX = 8
ARTICLES_PER_UNLOCK = 25
ARTICLES_PER_CHUNK = 3
CHARS_PER_UNLOCK = 25_000  # legacy — the "now" column only

DEFAULT_THRESHOLD = 0.10
DEFAULT_MIN_MISSING = 3


def clamp_cost(value: float) -> int:
    return max(UNLOCK_COST_MIN, min(UNLOCK_COST_MAX, int(value)))


def coverage_is_trustworthy(
    n_rows: int, max_no: int, threshold: float, min_missing: int
) -> bool:
    """Plan §3, as a pure function of the two numbers that decide it."""
    if n_rows <= 0 or max_no <= 0:
        return False
    missing = max_no - n_rows
    if missing <= min_missing:
        return True
    return (missing / max_no) <= threshold


# --- loaders ----------------------------------------------------------------


def _is_uuid(value: str) -> bool:
    try:
        UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def load_published_ids(client) -> tuple[list[str], int]:
    """Every ``content_id`` on a published regulation sidecar row.

    "Published" is exactly "has a slug" — that is what puts a page on the public
    wing and therefore what makes a wrong reading surface visible. Returns
    ``(ids, skipped)``; ``skipped`` counts non-uuid content_ids (a مادة key
    ``'{uuid}#{no}'`` under the wrong content_type would land here).
    """
    ids: list[str] = []
    skipped = 0
    offset = 0
    while True:
        res = (
            client.table("seo_item_meta")
            .select("content_id, slug")
            .eq("content_type", "regulation")
            .not_.is_("slug", "null")
            .order("content_id")
            .range(offset, offset + _READ_PAGE - 1)
            .execute()
        )
        batch = res.data or []
        for r in batch:
            cid = str(r.get("content_id") or "")
            if _is_uuid(cid):
                ids.append(cid)
            elif cid:
                skipped += 1
        if len(batch) < _READ_PAGE:
            break
        offset += _READ_PAGE
    return ids, skipped


def load_titles(client, ids: list[str]) -> dict[str, dict[str, Any]]:
    """``{regulation_id: {reg_ref, title}}`` from the corpus, in `in.()` batches."""
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(ids), _IN_BATCH):
        batch = ids[i : i + _IN_BATCH]
        res = (
            client.table("regulations_v2")
            .select("id, reg_ref, clean_title, title")
            .in_("id", batch)
            .execute()
        )
        for r in res.data or []:
            out[str(r.get("id"))] = {
                "reg_ref": str(r.get("reg_ref") or "-"),
                "title": (r.get("clean_title") or r.get("title") or "").strip(),
            }
    return out


def load_article_stats(client) -> dict[str, tuple[int, int]]:
    """``{regulation_id: (row_count, max_article_no)}`` over ALL of seo_articles.

    PostgREST cannot express ``GROUP BY regulation_id``, so the aggregate is done
    client-side over ~51k two-column rows (~51 paged requests). Cheaper than one
    count()+max() round trip per regulation, and it is the same read
    ``_seo_articles_for_regulation`` performs per document.

    ⚠ The page ORDER must be a unique key or paging silently duplicates some rows
    across boundaries and drops others. ``(regulation_id, article_no)`` is unique
    in this table; ``id`` is appended so a future duplicate cannot break paging.
    """
    counts: dict[str, int] = defaultdict(int)
    maxima: dict[str, int] = defaultdict(int)
    offset = 0
    while True:
        res = (
            client.table("seo_articles")
            .select("regulation_id, article_no, id")
            .order("regulation_id")
            .order("article_no")
            .order("id")
            .range(offset, offset + _READ_PAGE - 1)
            .execute()
        )
        batch = res.data or []
        for r in batch:
            rid = str(r.get("regulation_id") or "")
            if not rid:
                continue
            no = int(r.get("article_no") or 0)
            counts[rid] += 1
            if no > maxima[rid]:
                maxima[rid] = no
        if len(batch) < _READ_PAGE:
            break
        offset += _READ_PAGE
    return {rid: (n, maxima[rid]) for rid, n in counts.items()}


def load_chunk_counts(client) -> dict[str, int]:
    """``{regulation_id: chunk_count}`` via the 116 RPC (paged — it returns ~4k
    rows and the 1000-row cap applies to RPC results exactly as to selects)."""
    out: dict[str, int] = {}
    offset = 0
    while True:
        res = (
            client.rpc("library_reg_chunk_counts", {})
            .order("regulation_id")
            .range(offset, offset + _READ_PAGE - 1)
            .execute()
        )
        batch = res.data or []
        for r in batch:
            rid = r.get("regulation_id")
            if rid:
                out[str(rid)] = int(r.get("chunk_count") or 0)
        if len(batch) < _READ_PAGE:
            break
        offset += _READ_PAGE
    return out


def total_chunk_chars(client, regulation_id: str) -> int:
    """``sum(length(content))`` over a regulation's chunks — the LEGACY weighting.

    Replicates the loop `unlock_cost` runs today, bodies and all, because that is
    the number the "now" column has to be. Deleted by plan §4.4.
    """
    total = 0
    offset = 0
    while True:
        res = (
            client.table("chunks_v2")
            .select("content")
            .eq("regulation_id", str(regulation_id))
            .range(offset, offset + _READ_PAGE - 1)
            .execute()
        )
        batch = res.data or []
        total += sum(len(r.get("content") or "") for r in batch)
        if len(batch) < _READ_PAGE:
            break
        offset += _READ_PAGE
    return total


# --- audit ------------------------------------------------------------------


def audit(
    client, threshold: float, min_missing: int, progress: bool = True
) -> list[dict[str, Any]]:
    """One row per published regulation, with both prices and the verdict."""
    ids, skipped = load_published_ids(client)
    if skipped:
        print(f"  ⚠ skipped {skipped} published rows with a non-uuid content_id")
    print(f"  published regulation rows : {len(ids)}")

    titles = load_titles(client, ids)
    articles = load_article_stats(client)
    chunks = load_chunk_counts(client)

    rows: list[dict[str, Any]] = []
    char_scans = [rid for rid in ids if articles.get(rid, (0, 0))[0] == 0]
    if progress and char_scans:
        print(
            f"  scanning chunk bodies for {len(char_scans)} chunk-only "
            "regulations (legacy char price)…"
        )

    for done, rid in enumerate(ids, 1):
        n_rows, max_no = articles.get(rid, (0, 0))
        n_chunks = chunks.get(rid, 0)
        meta = titles.get(rid, {})
        trustworthy = coverage_is_trustworthy(n_rows, max_no, threshold, min_missing)
        # Plan §4.3 guard: nothing to fall back to → keep the article surface.
        uses_articles = n_rows > 0 and (trustworthy or n_chunks == 0)

        if n_rows > 0:
            cost_now = clamp_cost(math.ceil(n_rows / ARTICLES_PER_UNLOCK))
        else:
            chars = total_chunk_chars(client, rid)
            cost_now = clamp_cost(math.ceil(chars / CHARS_PER_UNLOCK)) if chars else (
                UNLOCK_COST_MIN
            )
            if progress and done % 50 == 0:
                print(f"    …{done}/{len(ids)}")

        if uses_articles:
            cost_new = clamp_cost(math.ceil(n_rows / ARTICLES_PER_UNLOCK))
        else:
            cost_new = clamp_cost(
                math.ceil(n_chunks * ARTICLES_PER_CHUNK / ARTICLES_PER_UNLOCK)
            )

        missing = max(0, max_no - n_rows)
        rows.append(
            {
                "id": rid,
                "reg_ref": meta.get("reg_ref", "-"),
                "title": meta.get("title", ""),
                "rows": n_rows,
                "max_no": max_no,
                "missing": missing,
                "pct": (missing / max_no) if max_no else 0.0,
                "chunks": n_chunks,
                "rejected": n_rows > 0 and not trustworthy,
                "guarded": n_rows > 0 and not trustworthy and n_chunks == 0,
                "chunk_priced": not uses_articles,
                "cost_now": cost_now,
                "cost_new": cost_new,
            }
        )
    return rows


# --- reporting --------------------------------------------------------------


def _fmt_title(title: str, width: int = 44) -> str:
    t = " ".join((title or "—").split())
    return t if len(t) <= width else t[: width - 1] + "…"


def report(rows: list[dict[str, Any]], threshold: float, min_missing: int) -> None:
    flipped = sorted(
        (r for r in rows if r["rejected"]), key=lambda r: -r["pct"]
    )
    with_articles = [r for r in rows if r["rows"] > 0]
    chunk_priced = [r for r in rows if r["chunk_priced"]]
    zero_chunk = [r for r in flipped if r["guarded"]]

    print(
        f"\n  REGULATIONS THE RULE FLIPS TO CHUNKS ({len(flipped)}) "
        "— by gap % desc\n"
    )
    print(
        f"  {'reg_ref':<22}{'rows':>5}{'max':>6}{'gaps':>6}{'pct':>7}"
        f"{'chunks':>8}{'cost':>7}  title"
    )
    for r in flipped:
        delta = f"{r['cost_now']}→{r['cost_new']}"
        flag = "  ⚠ 0 chunks — GUARD HELD, article surface kept" if r["guarded"] else ""
        print(
            f"  {r['reg_ref']:<22}{r['rows']:>5}{r['max_no']:>6}{r['missing']:>6}"
            f"{r['pct'] * 100:>6.1f}%{r['chunks']:>8}{delta:>7}  "
            f"{_fmt_title(r['title'])}{flag}"
        )

    up = [r for r in chunk_priced if r["cost_new"] > r["cost_now"]]
    down = [r for r in chunk_priced if r["cost_new"] < r["cost_now"]]
    same = [r for r in chunk_priced if r["cost_new"] == r["cost_now"]]
    mean_now = sum(r["cost_now"] for r in chunk_priced) / max(1, len(chunk_priced))
    mean_new = sum(r["cost_new"] for r in chunk_priced) / max(1, len(chunk_priced))
    all_now = sum(r["cost_now"] for r in rows) / max(1, len(rows))
    all_new = sum(r["cost_new"] for r in rows) / max(1, len(rows))

    print(
        f"\n  SUMMARY  (missing > {min_missing} AND missing/max > "
        f"{threshold:.0%})\n"
        f"    published regulations          : {len(rows)}\n"
        f"      with a seo_articles index    : {len(with_articles)}\n"
        f"        index trusted (unchanged)  : {len(with_articles) - len(flipped)}\n"
        f"        index rejected → FLIPS     : {len(flipped)}\n"
        f"      chunk-only (no articles)     : {len(rows) - len(with_articles)}\n"
        f"    flipping with ZERO chunks      : {len(zero_chunk)}"
        f"{'   ← must be 0' if not zero_chunk else '   ← RED FLAG, see below'}"
    )
    if zero_chunk:
        # A flip with nothing to flip TO. The §3 guard keeps the article surface
        # so no page blanks, but the corpus has a document with neither a
        # trustworthy index nor a chunk body and that is worth a person looking.
        print("\n  ⚠⚠ ZERO-CHUNK FLIPS — the guard case fired. Investigate:")
        for r in zero_chunk:
            print(f"      {r['reg_ref']:<22}{_fmt_title(r['title'])}")

    print(
        f"\n    chunk-priced regulations       : {len(chunk_priced)}"
        f"  ({len(flipped) - len(zero_chunk)} flipped + "
        f"{len(chunk_priced) - len(flipped) + len(zero_chunk)} chunk-only)\n"
        f"      price up                     : {len(up)}\n"
        f"      price down                   : {len(down)}\n"
        f"      price same                   : {len(same)}\n"
        f"      mean cost                    : {mean_now:.2f} → {mean_new:.2f}\n"
        f"    mean cost, ALL published       : {all_now:.2f} → {all_new:.2f}"
    )

    movers = sorted(
        (r for r in chunk_priced if r["cost_new"] != r["cost_now"]),
        key=lambda r: -(r["cost_new"] - r["cost_now"]),
    )
    if movers:
        print("\n    largest price moves:")
        for r in movers[:5] + ([] if len(movers) <= 10 else movers[-5:]):
            print(
                f"      {r['cost_now']} → {r['cost_new']}  "
                f"{r['reg_ref']:<22}{_fmt_title(r['title'], 40)}"
            )


def main() -> None:
    ap = argparse.ArgumentParser(
        description="READ-ONLY audit: which published regulations does the "
        "article-coverage rule flip from the article surface to chunks?"
    )
    ap.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"max tolerated missing ratio (default {DEFAULT_THRESHOLD})",
    )
    ap.add_argument(
        "--min-missing",
        type=int,
        default=DEFAULT_MIN_MISSING,
        help=f"absolute floor, gaps must EXCEED it (default {DEFAULT_MIN_MISSING})",
    )
    args = ap.parse_args()

    print(
        "check_article_coverage — READ-ONLY, writes nothing.  "
        f"threshold={args.threshold} min_missing={args.min_missing}"
    )
    client = get_supabase_client()
    rows = audit(client, args.threshold, args.min_missing)
    if not rows:
        print("  no published regulations — nothing to audit.")
        return
    report(rows, args.threshold, args.min_missing)


if __name__ == "__main__":
    main()
