"""Axis 2 — the TIE-BREAK OVER-FIRE measurement.

Reuses ``run_routing.run_all`` verbatim (same router entry point, same
concurrency, same PASS rule) so this is the existing scorer pointed at the new
fixtures — not a second scorer that could disagree with the first for its own
reasons.

What it adds is the reporting the watch item needs: **`simple_search` share,
reported separately from accuracy.** An over-firing tie-break shows up as
`simple_search` share collapsing while every question still gets answered, so a
raw accuracy number on a deep_search-labeled set cannot see it.

    python agents/simple_search/eval/rerun_routing_tiebreak.py --conversation <uuid>
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from rerun_common import hr  # noqa: F401  (path bootstrap + utf-8 console)

from agents.simple_search.eval.fixtures_routing_tiebreak import (  # noqa: E402
    TIEBREAK_FIXTURES,
    total_calls,
)
from agents.simple_search.eval.run_routing import run_all, summarize  # noqa: E402

# The three bare-article paraphrases name no نظام. A ChatResponse asking WHICH
# law is defensible there and is NOT a tie-break over-fire; deep_search is.
_BARE = {
    "اعطيني نص المادة الخامسة",
    "اعطيني نص المادة العاشرة",
    "ابغى نص المادة الخامسة",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--conversation", required=True)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    print(f"TIE-BREAK WATCH: {total_calls()} paraphrases across "
          f"{len(TIEBREAK_FIXTURES)} unambiguous simple_search anchors")
    results = asyncio.run(run_all(args.conversation, None, TIEBREAK_FIXTURES))
    summarize(results)

    hr("SIMPLE_SEARCH SHARE — the number the tie-break can quietly destroy")
    named = [r for r in results if r.query not in _BARE]
    bare = [r for r in results if r.query in _BARE]
    for label, rs in (("law NAMED (over-fire = any non-simple_search)", named),
                      ("law NOT named (over-fire = deep_search only)", bare)):
        if not rs:
            continue
        c = Counter(r.got for r in rs)
        share = 100.0 * c.get("simple_search", 0) / len(rs)
        print(f"\n{label}   n={len(rs)}")
        print(f"  simple_search {c.get('simple_search', 0):>2d}   "
              f"deep_search {c.get('deep_search', 0):>2d}   "
              f"chat {c.get('chat', 0):>2d}   other "
              f"{len(rs) - c.get('simple_search', 0) - c.get('deep_search', 0) - c.get('chat', 0):>2d}")
        print(f"  simple_search share: {share:.1f}%")

    over = [r for r in results if r.got == "deep_search"]
    print(f"\nTIE-BREAK OVER-FIRES (deep_search on an unambiguous lookup): {len(over)}")
    for r in over:
        print(f"  «{r.query}»  task_label={r.task_label[:70]}")

    path = args.json or str(Path(__file__).with_name("rerun_routing_tiebreak_results.json"))
    Path(path).write_text(
        json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
