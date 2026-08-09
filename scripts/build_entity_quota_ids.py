"""Entity-quota selector for the regulation publish ramp — 502 → 1,188 published.

Emits the id file that ``scripts/build_seo_slugs.py --type regulation --ids-file``
consumes, plus a human-readable report of every choice it made. It does NOT
publish anything: publishing is writing a slug, and only ``build_seo_slugs.py
--apply`` does that. This script picks WHICH regulations should get one.

THE RULE (user decision 2026-08-08, plan §3.2)
----------------------------------------------
    quota_e = max(FLOOR, ceil(PCT × entity_regs))        FLOOR=3, PCT=0.25
    new_e   = max(0, quota_e − published_e)

Applied to every one of the 135 entities, and **ADDITIVE**: an entity already
above its quota keeps everything it has and nothing is ever un-published. The
run is therefore monotonic — it can only grow the published set — which is what
makes it safe to re-run and safe to re-price.

Both knobs are CLI flags (``--pct`` / ``--floor``) and the resulting total is
printed before anything is written, so the ramp can be re-priced without editing
code. At the defaults, against live data: **686 new, 1,188 published, all 135
entities represented** (85 of them hold zero today).

A quota is also bounded by what the entity actually HOLDS. 49 entities own fewer
than 3 regulations, so the floor of 3 asks for 85 documents that do not exist;
the effective quota is ``min(quota_e, entity_regs)``. That bound is the entire
difference between the naive 771 and the real 686 — worth knowing before anyone
"fixes" the arithmetic.

NO PER-ENTITY CEILING — considered and rejected
------------------------------------------------
``build_usage_rank.py`` already interleaves the ranked tail by entity with a
greedy max-heap (``interleave_by_entity``), so one entity holding a large
published set does not dominate browse order. And the entity a ceiling would
bind — ``5000 — أنظمة عامة (هيئة الخبراء بمجلس الوزراء)``, 920 regs → 230 quota —
is the general-statutes bucket. It *should* be the largest allocation.

WITHIN AN ENTITY'S QUOTA, HOW CANDIDATES ARE RANKED
----------------------------------------------------
In order (plan §3.2): in-force status → usage → doc-type bucket → article count
→ ``id`` as the deterministic tie-break.

  1. STATUS. ``in_force`` and ``in_force_amended`` share the top tier (both are
     live law, and ``build_usage_rank._STATUS_W`` already weights them equally).
     ``cancelled`` sorts LAST, so a repealed regulation is published only when
     an entity has literally nothing else. Entities with nothing in force fall
     back to whatever they hold — status is a sort key, never a filter, so
     representation NEVER fails. 91 entities have zero in-force documents and
     every one of them still ends up represented.

  2. USAGE. Read corpus-wide from ``library_reg_usage_refs()`` via
     ``build_usage_rank.load_usage_scores`` — the same two-stage dampened score
     the ranker uses, imported rather than reimplemented so there is one
     definition of "usage".
     ⚠ NOT ``seo_item_meta.rank`` / ``usage_score``: those are written by
     ``build_usage_rank.py`` over the PUBLISHED view only, and live data confirms
     it — 502 rows carry a rank, 0 of them unpublished, 0 unpublished rows carry
     a usage_score. Read from the sidecar, "usage rank" would be NULL for every
     candidate this script considers and the term would be dead weight. Even
     from the RPC it is a thin signal (it separates 11 of the ~3,449 unpublished
     rows), but it is real and it is free.

  3. DOC-TYPE BUCKET, via ``build_usage_rank._DOC_TYPE_W`` — the real 21-value
     corpus vocabulary, not a guess: ``law_statute`` / ``executive_regulation``
     at 5.0 down to ``guide`` / ``standard_spec`` at 1.0 and ``unspecified`` at
     0.0. Folded in with the same module's junk-title penalty, because a
     regulation whose title is a placeholder or an untitled scan makes a card
     that cannot be read, let alone clicked.

  4. ARTICLE COUNT from ``articles_v2`` — a document with مواد has a real
     article index to render; 1,806 of 3,951 regulations have any.

⚠ DO NOT SELECT BY ``reg_ref`` SUFFIX. This was the original proposal and it was
rejected — ``_reg_001`` is SCRAPE ORDER, not importance. نظام العمل is
``17609_reg_122``, while ``17900_reg_001`` is «الدليل الإجرائي لقرار توطين المهن
الهندسية». Entity ``5000`` does not even use that scheme — it is
``5000_regulation_0002``, ordered by Hijri year, so its "first two" would be نظام
توحيد المملكة (1351هـ) and نظام الإقامة (1371هـ). Left here in writing because
somebody will otherwise re-propose it.

⚠ NEVER GROUP BY ``regulations_v2.entity_name``. It is NULL on 1,739 of the 3,951
rows, across 37 distinct ``entity_ref``s — grouping by it would silently collapse
those into one bogus bucket and starve 37 entities. ``public.entities`` (400 rows,
``entity_ref`` unique, no null names) covers EVERY ``entity_ref`` in the corpus;
join it for display and group by ``entity_ref``, which is always safe.

⚠ POSTGREST CLAMPS EVERY RESPONSE TO 1000 ROWS — RPC results included, silently,
by truncation, with no error. Four of this script's five reads exceed it
(``regulations_v2`` 3,951 · ``seo_item_meta`` 3,373 · ``articles_v2`` 51,792 ·
``library_reg_usage_refs()`` 1,923). Every one is paged, and every page is ordered
by a UNIQUE key — paging a non-uniquely-ordered set lets Postgres return tied rows
in a different sequence per request, which duplicates some and drops others.

``regulations_v2``, ``articles_v2`` and ``entities`` are pipeline-owned and READ
ONLY. All SEO state lives in the ``seo_item_meta`` sidecar, and this script does
not write there either.

Deterministic: entities are walked in ``entity_ref`` order, candidates sort with
``id`` as the final tie-break, and the id file is written grouped by entity in
pick order — so a re-run over unchanged data produces a byte-identical file.

Run from the repo root:
  python scripts/build_entity_quota_ids.py                        # report to stdout
  python scripts/build_entity_quota_ids.py --pct 0.30 --floor 5   # re-price
  python scripts/build_entity_quota_ids.py --out ids.txt --report ramp.txt
  # then, after reviewing ramp.txt:
  python scripts/build_seo_slugs.py --type regulation --ids-file ids.txt --apply

Nothing is written unless ``--out`` / ``--report`` name a path (the
``--emit-used-ids`` convention from ``build_usage_rank.py``). The DB is never
written to at all.

Env: SUPABASE_URL / SUPABASE_SERVICE_KEY (via shared.config / shared.db.client).
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Make the repo root importable when run directly...
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# ...and scripts/ itself, so the ranking vocabulary can be imported from its one
# owner (build_usage_rank) instead of being copied and left to drift.
sys.path.insert(0, str(Path(__file__).resolve().parent))

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

# Single source of truth for the doc-type vocabulary, the junk-title rule and the
# dampened usage score. See the module docstring — these are deliberately shared,
# not re-declared, so a corpus vocabulary change lands in one place.
from build_usage_rank import (  # noqa: E402
    DEFAULT_USER_CAP,
    _DOC_TYPE_W,
    _JUNK_TITLE_PENALTY,
    _is_junk_title,
    _title_of,
    load_usage_scores,
)

CONTENT_TYPE = "regulation"

# PostgREST caps a single response at 1000 rows — see the module docstring.
_READ_PAGE = 1000

# Defaults for the rule. Both are CLI flags so the ramp can be re-priced.
DEFAULT_PCT = 0.25
DEFAULT_FLOOR = 3

# Status ordering. Tier 0 is live law; `cancelled` sorts last so a repealed
# document is only ever published when an entity holds nothing else. An
# unrecognised status lands mid-table rather than at either extreme, so a
# pipeline vocabulary change degrades the ordering instead of inverting it.
_STATUS_TIER = {
    "in_force": 0,
    "in_force_amended": 0,
    "under_consultation": 1,
    "consultation_ended": 1,
    "in_progress": 1,
    "cancelled": 2,
}
_STATUS_TIER_DEFAULT = 1
# Statuses that count as "in force" for the report's per-entity summary.
_IN_FORCE = ("in_force", "in_force_amended")

# Columns the selection needs. `clean_title` falls back to `title` exactly as
# build_seo_slugs.py does — 17609_reg_122 (نظام العمل) has a NULL clean_title.
_REG_COLS = "id, reg_ref, entity_ref, clean_title, title, doc_type_bucket, status_class"


# ── paged loaders ──────────────────────────────────────────────────────────


def _paged(
    client,
    table: str,
    cols: str,
    order_col: str,
    eq: tuple[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Every row of ``table``, paged around the 1000-row clamp.

    ``order_col`` MUST be unique over the result set — see the module docstring
    on why a non-unique page order silently corrupts the read.
    """
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        qb = client.table(table).select(cols)
        if eq:
            qb = qb.eq(eq[0], eq[1])
        res = qb.order(order_col).range(offset, offset + _READ_PAGE - 1).execute()
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < _READ_PAGE:
            break
        offset += _READ_PAGE
    return rows


def load_regulations(client) -> list[dict[str, Any]]:
    """The corpus (3,951 rows), ordered by the `id` PK."""
    return _paged(client, "regulations_v2", _REG_COLS, "id")


def load_entity_names(client) -> dict[str, str]:
    """``entity_ref -> entity_name`` from ``public.entities``.

    NOT from ``regulations_v2.entity_name``, which is NULL on 1,739 rows across
    37 entity_refs. ``entities`` covers every ref in the corpus.
    """
    rows = _paged(client, "entities", "entity_ref, entity_name", "entity_ref")
    return {
        str(r["entity_ref"]): (r.get("entity_name") or "").strip()
        for r in rows
        if r.get("entity_ref")
    }


def load_published_ids(client) -> set[str]:
    """Regulation ids that carry a slug today — i.e. are already published."""
    rows = _paged(
        client,
        "seo_item_meta",
        "content_id, slug",
        "content_id",
        eq=("content_type", CONTENT_TYPE),
    )
    return {str(r["content_id"]) for r in rows if r.get("slug")}


def load_article_counts(client) -> dict[str, int]:
    """``regulation_id -> article count`` from ``articles_v2`` (51,792 rows).

    A full paged sweep of one column, not a chunked ``in_()`` over the candidate
    ids: at ~13 مواد per regulation, a chunk of 100 regulations returns ~1,300
    article rows and would be truncated by the very clamp we are paging around.
    """
    counts: dict[str, int] = defaultdict(int)
    for r in _paged(client, "articles_v2", "id, regulation_id", "id"):
        rid = r.get("regulation_id")
        if rid:
            counts[str(rid)] += 1
    return dict(counts)


# ── selection ──────────────────────────────────────────────────────────────


def quota_for(entity_regs: int, pct: float, floor: int) -> int:
    """``max(floor, ceil(pct × regs))``, bounded by what the entity holds.

    The bound is not a detail: 49 entities own fewer than `floor` regulations, so
    without it the plan's totals come out 85 too high (771 instead of 686).
    """
    return min(entity_regs, max(floor, math.ceil(pct * entity_regs)))


def prominence(row: dict[str, Any]) -> float:
    """Doc-type weight plus the junk-title penalty — both from build_usage_rank."""
    p = _DOC_TYPE_W.get(row.get("doc_type_bucket") or "", 0.0)
    if _is_junk_title(row):
        p += _JUNK_TITLE_PENALTY
    return p


def select(
    regs: list[dict[str, Any]],
    published: set[str],
    usage: dict[str, float],
    articles: dict[str, int],
    pct: float,
    floor: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Walk every entity and pick its ``new_e`` candidates.

    Returns ``(per_entity_plans, totals)``. Entities are walked in ``entity_ref``
    order and candidates carry ``id`` as their final sort term, so the result is
    byte-stable across runs.
    """
    by_entity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in regs:
        by_entity[str(r.get("entity_ref") or "")].append(r)

    plans: list[dict[str, Any]] = []
    for ref in sorted(by_entity):
        rows = by_entity[ref]
        pub_here = sum(1 for r in rows if str(r["id"]) in published)
        quota = quota_for(len(rows), pct, floor)
        want = max(0, quota - pub_here)

        # Candidates are the entity's UNPUBLISHED rows — the published ones keep
        # their slugs untouched (the rule is additive).
        cands = [r for r in rows if str(r["id"]) not in published]
        for r in cands:
            rid = str(r["id"])
            r["_status_tier"] = _STATUS_TIER.get(
                r.get("status_class") or "", _STATUS_TIER_DEFAULT
            )
            r["_usage"] = usage.get(rid, 0.0)
            r["_prom"] = prominence(r)
            r["_articles"] = articles.get(rid, 0)
        cands.sort(
            key=lambda r: (
                r["_status_tier"],
                -r["_usage"],
                -r["_prom"],
                -r["_articles"],
                str(r["id"]),
            )
        )
        chosen = cands[:want]

        plans.append(
            {
                "entity_ref": ref,
                "regs": len(rows),
                "published": pub_here,
                "in_force": sum(
                    1 for r in rows if (r.get("status_class") or "") in _IN_FORCE
                ),
                "quota": quota,
                "want": want,
                "chosen": chosen,
                # want > len(cands) can only happen if the corpus shrinks under a
                # stale published set; surfaced rather than silently absorbed.
                "short": max(0, want - len(chosen)),
            }
        )

    totals = {
        "entities": len(plans),
        "corpus": sum(p["regs"] for p in plans),
        "published_now": sum(p["published"] for p in plans),
        "new": sum(len(p["chosen"]) for p in plans),
        "zero_before": sum(1 for p in plans if p["published"] == 0),
        "zero_after": sum(
            1 for p in plans if p["published"] + len(p["chosen"]) == 0
        ),
        "short": sum(p["short"] for p in plans),
    }
    totals["published_after"] = totals["published_now"] + totals["new"]
    return plans, totals


# ── reporting ──────────────────────────────────────────────────────────────


def _why(row: dict[str, Any]) -> str:
    """The sort terms that won this row its slot, in the order they applied."""
    status = row.get("status_class") or "?"
    bucket = row.get("doc_type_bucket") or "?"
    bits = [status, bucket, f"{row['_articles']} مادة"]
    if row["_usage"] > 0:
        bits.append(f"usage {row['_usage']:.2f}")
    if _is_junk_title(row):
        bits.append("JUNK TITLE")
    return " · ".join(bits)


def _totals_block(totals: dict[str, Any], pct: float, floor: int) -> str:
    return (
        f"  rule                    : quota = max({floor}, ceil({pct} × entity_regs)), "
        f"bounded by what the entity holds\n"
        f"  entities                : {totals['entities']}\n"
        f"  corpus regulations      : {totals['corpus']}\n"
        f"  published now           : {totals['published_now']}\n"
        f"  NEW to publish          : {totals['new']}\n"
        f"  published after         : {totals['published_after']}\n"
        f"  entities at 0 before    : {totals['zero_before']}\n"
        f"  entities at 0 after     : {totals['zero_after']}"
        + (f"\n  QUOTA SHORTFALL         : {totals['short']}" if totals["short"] else "")
    )


def render_report(
    plans: list[dict[str, Any]],
    totals: dict[str, Any],
    names: dict[str, str],
    pct: float,
    floor: int,
) -> str:
    """The full per-entity report — what a human reads before 686 pages go live."""
    out: list[str] = [
        "build_entity_quota_ids — regulation publish ramp",
        f"generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        _totals_block(totals, pct, floor),
        "",
        "Additive: no regulation is ever un-published by this plan.",
        "",
    ]
    # Biggest allocations first — that is the part worth arguing about.
    for p in sorted(
        plans, key=lambda p: (-len(p["chosen"]), -p["regs"], p["entity_ref"])
    ):
        ref = p["entity_ref"]
        name = names.get(ref) or "(unknown entity)"
        out.append("=" * 78)
        out.append(f"{ref} — {name}")
        out.append(
            f"  corpus {p['regs']} | published {p['published']} | in force "
            f"{p['in_force']} | quota {p['quota']} | NEW {len(p['chosen'])}"
        )
        if p["short"]:
            out.append(f"  ⚠ SHORT {p['short']} — fewer candidates than the quota asks")
        if not p["chosen"]:
            out.append("  (already at or above quota — nothing to add)")
            out.append("")
            continue
        if p["in_force"] == 0:
            out.append("  NOTE: entity has nothing in force — falling back to what it holds")
        for i, r in enumerate(p["chosen"], 1):
            title = (_title_of(r) or "(untitled)")[:66]
            out.append(f"  {i:>4}. {r['id']}  [{_why(r)}]")
            out.append(f"        {title}")
        out.append("")
    return "\n".join(out) + "\n"


def render_ids(
    plans: list[dict[str, Any]],
    totals: dict[str, Any],
    names: dict[str, str],
    pct: float,
    floor: int,
) -> str:
    """The id file for ``build_seo_slugs.py --type regulation --ids-file``.

    Its parser ignores blank lines and ``#`` comments, so the file carries its own
    provenance header and per-entity grouping. Written in ``entity_ref`` order,
    pick order within an entity — byte-stable across re-runs.
    """
    stamp = datetime.now(timezone.utc).isoformat()
    out: list[str] = [
        "# regulation publish ramp — build_entity_quota_ids.py",
        f"# generated: {stamp}",
        f"# rule: quota = max({floor}, ceil({pct} x entity_regs)), additive",
        f"# {totals['new']} new ids; published {totals['published_now']} "
        f"-> {totals['published_after']}",
        "#",
        "# consume with:",
        "#   python scripts/build_seo_slugs.py --type regulation "
        "--ids-file <this file> --apply",
        "# reverse with:",
        "#   python scripts/build_seo_slugs.py --type regulation --unpublish "
        "--ids-file <this file> --apply",
        "",
    ]
    for p in sorted(plans, key=lambda p: p["entity_ref"]):
        if not p["chosen"]:
            continue
        name = names.get(p["entity_ref"]) or "(unknown entity)"
        out.append(f"# --- {p['entity_ref']} {name} ({len(p['chosen'])}) ---")
        out.extend(str(r["id"]) for r in p["chosen"])
    return "\n".join(out) + "\n"


# ── main ───────────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Select the regulation publish ramp by per-entity quota and "
        "emit the id file for build_seo_slugs.py --ids-file."
    )
    ap.add_argument(
        "--pct",
        type=float,
        default=DEFAULT_PCT,
        help=f"share of an entity's corpus to publish (default: {DEFAULT_PCT})",
    )
    ap.add_argument(
        "--floor",
        type=int,
        default=DEFAULT_FLOOR,
        help=f"minimum published per entity, so every entity is represented "
        f"(default: {DEFAULT_FLOOR})",
    )
    ap.add_argument(
        "--out",
        metavar="PATH",
        help="write the id file here (default: print totals only, write nothing)",
    )
    ap.add_argument(
        "--report",
        metavar="PATH",
        help="write the full per-entity report here instead of stdout",
    )
    ap.add_argument(
        "--top",
        type=int,
        default=15,
        help="how many entities to show in the stdout summary (default: 15)",
    )
    args = ap.parse_args()

    if not 0 < args.pct <= 1:
        ap.error("--pct must be in (0, 1]")
    if args.floor < 0:
        ap.error("--floor must be >= 0")

    client = get_supabase_client()

    print("build_entity_quota_ids — reading corpus (paged; PostgREST clamps at 1000)...")
    regs = load_regulations(client)
    names = load_entity_names(client)
    published = load_published_ids(client)
    usage = load_usage_scores(client, DEFAULT_USER_CAP)
    articles = load_article_counts(client)
    print(
        f"  regulations {len(regs)} | entities {len(names)} | published "
        f"{len(published)} | usage-scored {sum(1 for v in usage.values() if v > 0)} | "
        f"regs with مواد {len(articles)}"
    )

    plans, totals = select(regs, published, usage, articles, args.pct, args.floor)

    # The numbers, before anything is written.
    print(f"\n=== PLAN (pct={args.pct}, floor={args.floor}) ===")
    print(_totals_block(totals, args.pct, args.floor))

    print(f"\n  largest allocations (top {args.top}):")
    print(f"    {'entity':<8} {'regs':>5} {'pub':>5} {'quota':>6} {'NEW':>5}  name")
    for p in sorted(plans, key=lambda p: (-len(p["chosen"]), -p["regs"]))[: args.top]:
        name = (names.get(p["entity_ref"]) or "?")[:44]
        print(
            f"    {p['entity_ref']:<8} {p['regs']:>5} {p['published']:>5} "
            f"{p['quota']:>6} {len(p['chosen']):>5}  {name}"
        )

    if totals["zero_after"]:
        print(
            f"\n  ⚠ {totals['zero_after']} entit(ies) would still have zero published — "
            f"representation FAILED."
        )
    else:
        print("\n  every entity ends up with at least one published regulation.")

    report = render_report(plans, totals, names, args.pct, args.floor)
    if args.report:
        Path(args.report).write_text(report, encoding="utf-8")
        print(f"\n  report -> {args.report}")
    else:
        print("\n" + report)

    if args.out:
        Path(args.out).write_text(
            render_ids(plans, totals, names, args.pct, args.floor), encoding="utf-8"
        )
        print(f"  {totals['new']} ids -> {args.out}")
        print(
            f"\n  Nothing is published yet. Review the report, then:\n"
            f"    python scripts/build_seo_slugs.py --type regulation "
            f"--ids-file {args.out} --apply\n"
            f"  and remember the sequence: deploy -> publish -> "
            f"build_usage_rank -> purge ISR.\n"
        )
    else:
        print("\n  (no --out given — no id file written)\n")


if __name__ == "__main__":
    main()
