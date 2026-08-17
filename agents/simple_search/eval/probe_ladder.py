"""Forensic probe — dump the FULL candidate table, per rung, for one query.

``run_resolution`` scores verdicts; this answers *why*. It prints every
candidate ``manual_search_core`` produced with the ladder rung that produced it,
its coverage, its rung-native score and its pin flag, then the gate decision —
which is the only way to tell a Gate-2 false-positive apart from a ladder
composition failure.

Run from the repo root::

    python -m agents.simple_search.eval.probe_ladder "نظام الفساد المالي والإداري"
    python -m agents.simple_search.eval.probe_ladder "نظام الإقامة المميزة" --type regs
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

from dotenv import load_dotenv

load_dotenv()

from agents.simple_search.manual_search import (  # noqa: E402
    _MIN_TITLE_COVERAGE,
    _bm25,
    _CORPUS_BY_TYPE,
    _from_bm25,
    _from_regs_ilike,
    _query_terms,
    decide,
    manual_search_core,
    rank_candidates,
)
from agents.tool_repository.fetch_article import (  # noqa: E402
    _fetch_reg_candidates,
    _rank_candidates,
    resolve_regulation_id,
)
from shared.db.client import get_supabase_client  # noqa: E402


async def probe(query: str, data_type: str) -> None:
    supabase = get_supabase_client()
    print(f"\nQUERY  «{query}»   type={data_type}")
    print(f"terms  {_query_terms(query)}   floor={_MIN_TITLE_COVERAGE}")

    # --- rung ① in isolation, so a rung-boundary failure is visible ---------
    if data_type in ("regs", "article"):
        corpus = _CORPUS_BY_TYPE[data_type]
        bm = _from_bm25(
            await asyncio.to_thread(_bm25, supabase, query, corpus), query, data_type
        )
        d1 = decide(rank_candidates(bm), data_type)
        print(f"\n-- rung ① BM25 alone: {len(bm)} rows → {d1.status}/{d1.gate}")
        for c in rank_candidates(bm):
            print(f"     cov={c['coverage']:.2f} score={c['score']:>9.2f} "
                  f"pin={str(c['pin']):5s} «{c['title'][:70]}»")

        il = _from_regs_ilike(
            await asyncio.to_thread(_fetch_reg_candidates, supabase, query),
            query, data_type,
        )
        print(f"\n-- rung ② ILIKE alone: {len(il)} rows "
              f"(score is always 0.0 — a RECALL rung with no ranking signal)")
        for c in rank_candidates(il):
            print(f"     cov={c['coverage']:.2f} score={c['score']:>9.2f} "
                  f"pin={str(c['pin']):5s} «{c['title'][:70]}»")

    # --- the real composed ladder -------------------------------------------
    cands = await manual_search_core(supabase, query, data_type)
    d = decide(cands, data_type)
    print(f"\n-- COMPOSED LADDER: {len(cands)} candidates → {d.status}/{d.gate}"
          f"  conf={d.confidence or '-'}")
    for i, c in enumerate(cands, 1):
        flag = "  <== WINNER" if d.winner and c is d.winner else ""
        above = "*" if c["coverage"] >= _MIN_TITLE_COVERAGE else " "
        print(f"  C{i} {above} cov={c['coverage']:.2f} score={c['score']:>9.2f} "
              f"rung={c['rung']:6s} pin={str(c['pin']):5s} «{c['title'][:64]}»{flag}")

    # --- the deterministic leg for the same string --------------------------
    if data_type in ("regs", "article"):
        rows = await asyncio.to_thread(_fetch_reg_candidates, supabase, query)
        ranked = _rank_candidates(query, rows)
        res = await asyncio.to_thread(resolve_regulation_id, supabase, query)
        verdict = ("resolved" if res.reg_id else
                   ("ambiguous" if res.ambiguous else "not_found"))
        print(f"\n-- det leg (fetch_article): {len(ranked)} candidates → {verdict}"
              f"  exact={res.exact}  «{res.display}»")
        for c in ranked[:6]:
            print(f"     score={c.score:.4f} exact={str(c.exact):5s} «{c.display[:66]}»")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--type", default="regs", dest="data_type")
    args = ap.parse_args()
    asyncio.run(probe(args.query, args.data_type))


if __name__ == "__main__":
    main()
