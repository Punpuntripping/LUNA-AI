"""OPEN ITEM A — is fp-05 *structurally identical* to the calibration positive?

Plan §13e claims the surviving `manual` wrong-doc commit is unfixable by any
threshold:

    «نظام الذكاء الاصطناعي السعودي» (coverage 0.75, BM25 15.88, singleton) is
    **structurally identical to the calibration positive** «نظام العمل التطوعي
    السعودي» — coverage 0.75, BM25 rung, singleton — with the opposite correct
    answer, so **no coverage floor can separate them.**

That is a strong claim and it is the fix lane's own, so this file tests it
instead of repeating it. It dumps, side by side, **every signal `decide()` can
see** for both queries — the whole ranked pool, each candidate's rung, coverage,
rung-native score, pin flag, plus the pool shape (`n above floor`, margin to the
second) — and then states plainly which of those signals differ.

"No threshold can separate them" is true only if the two are identical on
*every* signal the gate has access to. One differing signal is a fix; zero is
a proof.

    python agents/simple_search/eval/rerun_structural_pair.py
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from rerun_common import hr, service_client

from agents.simple_search.manual_search import (  # noqa: E402
    _MIN_TITLE_COVERAGE,
    decide,
    manual_search_core,
)

# The pair. Same class of string («نظام X السعودي»), opposite correct answers.
POSITIVE = ("reg-10", "نظام العمل التطوعي السعودي", "MUST RESOLVE (the law exists)")
NEGATIVE = ("fp-05", "نظام الذكاء الاصطناعي السعودي", "MUST REFUSE (no such law)")

# The signals `decide()` can actually read off a candidate.
_SIGNALS = ("rung", "coverage", "score", "pin", "title", "id")


async def _profile(query: str) -> dict:
    sb = service_client()
    cands = await manual_search_core(sb, query, "regs")
    d = decide(cands, "regs")
    above = [c for c in cands if float(c.get("coverage", 0.0)) >= _MIN_TITLE_COVERAGE]
    top = cands[0] if cands else {}
    second = cands[1] if len(cands) > 1 else {}
    return {
        "query": query,
        "status": d.status,
        "gate": d.gate,
        "confidence": d.confidence,
        "winner": (d.winner or {}).get("title", ""),
        "n_candidates": len(cands),
        "n_above_floor": len(above),
        "top_rung": top.get("rung", ""),
        "top_coverage": round(float(top.get("coverage", 0.0)), 4),
        "top_score": round(float(top.get("score", 0.0)), 4),
        "top_pin": bool(top.get("pin")),
        "second_coverage": round(float(second.get("coverage", 0.0)), 4) if second else None,
        "second_score": round(float(second.get("score", 0.0)), 4) if second else None,
        "coverage_margin": (round(float(top.get("coverage", 0.0))
                                  - float(second.get("coverage", 0.0)), 4)
                            if second else None),
        "candidates": [
            {k: (round(float(c[k]), 4) if k in ("coverage", "score") else c.get(k))
             for k in _SIGNALS if k in c}
            for c in cands
        ],
    }


async def main() -> int:
    hr("OPEN ITEM A — «structurally identical»? Every signal decide() can see.")
    out = {}
    for fid, query, label in (POSITIVE, NEGATIVE):
        p = await _profile(query)
        out[fid] = p
        print(f"\n### {fid} — {label}")
        print(f"    «{query}»")
        print(f"    → {p['status']}/{p['gate']}  conf={p['confidence'] or '-'}  "
              f"winner=«{p['winner']}»")
        print(f"    pool: n={p['n_candidates']}  above floor({_MIN_TITLE_COVERAGE})="
              f"{p['n_above_floor']}")
        for i, c in enumerate(p["candidates"], 1):
            mark = "*" if c.get("coverage", 0) >= _MIN_TITLE_COVERAGE else " "
            print(f"      C{i}{mark} rung={str(c.get('rung')):12s} "
                  f"cov={c.get('coverage'):<6} score={c.get('score'):<10} "
                  f"pin={str(c.get('pin')):5s} «{str(c.get('title'))[:64]}»")

    a, b = out[POSITIVE[0]], out[NEGATIVE[0]]
    hr("SIGNAL-BY-SIGNAL — a difference here is a fix; zero differences is a proof")
    keys = ["status", "gate", "confidence", "n_candidates", "n_above_floor",
            "top_rung", "top_coverage", "top_score", "top_pin",
            "second_coverage", "second_score", "coverage_margin"]
    same, diff = [], []
    print(f"{'signal':18s}{'positive (reg-10)':>26s}{'negative (fp-05)':>26s}   verdict")
    print("-" * 84)
    for k in keys:
        va, vb = a.get(k), b.get(k)
        v = "SAME" if va == vb else "DIFFERS"
        (same if va == vb else diff).append(k)
        print(f"{k:18s}{str(va):>26s}{str(vb):>26s}   {v}")
    print("-" * 84)
    print(f"\nidentical on : {same}")
    print(f"differs on   : {diff}")
    print(f"\nVERDICT: {'no signal separates them — the claim HOLDS' if not diff else 'the two are NOT identical; the differing signals above are what a fix could use'}")

    Path(__file__).with_name("rerun_structural_pair_results.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\nwrote rerun_structural_pair_results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
