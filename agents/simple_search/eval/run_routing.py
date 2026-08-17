"""Axis 2 scorer — runs the REAL router over :mod:`fixtures_routing`.

Costs money (tier_2 flash, one router call per paraphrase). It writes NOTHING to
the database: ``run_router`` only reads workspace items for the conversation it
is handed, so a scratch conversation with zero items keeps the whole axis
read-only apart from the LLM spend.

Run from the repo root::

    python -m agents.simple_search.eval.run_routing --conversation <uuid>
    python -m agents.simple_search.eval.run_routing --conversation <uuid> --only type1_narrowed
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from dataclasses import dataclass, asdict
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

from agents.models import DispatchAgent  # noqa: E402
from agents.router.router import run_router  # noqa: E402
from agents.simple_search.eval.fixtures_routing import (  # noqa: E402
    ADVERSARIAL_FIXTURES,
    ALL_FIXTURES,
    ROUTE_FIXTURES,
    total_calls,
)
from shared.db.client import get_supabase_client  # noqa: E402

USER_ID = "c5f4cff0-0517-43f0-af59-a9905deab22c"

# How many paraphrases run concurrently. Small — the point is a clean
# measurement, not throughput, and the router is rate-limited upstream.
_CONCURRENCY = 4


@dataclass
class RouteResult:
    rid: str
    cls: str
    query: str
    expect: str
    got: str          # simple_search | deep_search | writing | memory | chat | error
    task_label: str = ""
    chat_preview: str = ""
    verdict: str = ""  # PASS | FAIL
    note: str = ""


async def _one(sem: asyncio.Semaphore, supabase, conversation_id: str,
               rid: str, cls: str, expect: str, query: str) -> RouteResult:
    async with sem:
        try:
            res = await run_router(
                question=query,
                supabase=supabase,
                user_id=USER_ID,
                conversation_id=conversation_id,
                case_id=None,
                case_memory_md=None,
                case_metadata=None,
                user_preferences=None,
                message_history=[],
                workspace_item_summaries=[],
                compaction_summary_md=None,
                user_call_name=None,
                welcome=None,
            )
        except Exception as exc:  # noqa: BLE001
            return RouteResult(rid, cls, query, expect, "error", verdict="FAIL",
                               note=repr(exc)[:250])

    out = res.output
    if isinstance(out, DispatchAgent):
        got = out.agent_family
        r = RouteResult(rid, cls, query, expect, got, task_label=out.task_label)
    else:
        # ChatResponse — the router answered directly instead of dispatching.
        got = "chat"
        r = RouteResult(rid, cls, query, expect, got,
                        chat_preview=(getattr(out, "message", "") or "")[:110])
    r.verdict = "PASS" if got == expect else "FAIL"
    return r


async def run_all(conversation_id: str, only: str | None,
                  fixtures: list | None = None) -> list[RouteResult]:
    fixtures = fixtures if fixtures is not None else ALL_FIXTURES
    supabase = get_supabase_client()
    sem = asyncio.Semaphore(_CONCURRENCY)
    tasks = [
        _one(sem, supabase, conversation_id, f.rid, f.cls, f.expect, q)
        for f in fixtures
        if not only or f.cls == only
        for q in f.paraphrases
    ]
    print(f"dispatching {len(tasks)} router calls (concurrency={_CONCURRENCY})…\n")
    results = await asyncio.gather(*tasks)
    for r in results:
        mark = "OK  " if r.verdict == "PASS" else "MISS"
        extra = r.chat_preview or r.note or r.task_label
        print(f"[{mark}] {r.cls:24s} want={r.expect:13s} got={r.got:13s} "
              f"«{r.query[:62]}» {extra[:70]}")
    return list(results)


def summarize(results: list[RouteResult]) -> None:
    print("\n" + "=" * 104)
    print("CONFUSION MATRIX  (rows = label, cols = what the router did)")
    print("=" * 104)
    cols = ["simple_search", "deep_search", "writing", "memory", "chat", "error"]
    rows = sorted({r.cls for r in results})
    hdr = f"{'label / class':30s}" + "".join(f"{c:>15s}" for c in cols) + f"{'acc':>8s}"
    print(hdr)
    print("-" * len(hdr))
    for cls in rows:
        rs = [r for r in results if r.cls == cls]
        cnt = Counter(r.got for r in rs)
        acc = 100.0 * sum(1 for r in rs if r.verdict == "PASS") / len(rs)
        want = rs[0].expect
        print(f"{cls + ' (→' + want + ')':30s}"
              + "".join(f"{cnt.get(c, 0):>15d}" for c in cols)
              + f"{acc:>7.0f}%")
    print("-" * len(hdr))
    n = len(results)
    p = sum(1 for r in results if r.verdict == "PASS")
    print(f"{'OVERALL':30s}{'':>75s}{100.0 * p / n:>7.0f}%   ({p}/{n})")

    fails = [r for r in results if r.verdict == "FAIL"]
    if fails:
        print("\n" + "=" * 104)
        print("EVERY MISS")
        print("=" * 104)
        for r in fails:
            print(f"  [{r.cls}] want={r.expect} got={r.got}\n      «{r.query}»"
                  + (f"\n      → {r.chat_preview or r.note}" if (r.chat_preview or r.note) else ""))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--conversation", required=True, help="scratch conversation uuid")
    ap.add_argument("--only", default=None)
    ap.add_argument("--set", default="all", choices=["all", "base", "adversarial"])
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    chosen = {"all": ALL_FIXTURES, "base": ROUTE_FIXTURES,
              "adversarial": ADVERSARIAL_FIXTURES}[args.set]
    print(f"fixture set [{args.set}]: {total_calls(chosen)} paraphrases across {len(chosen)} types")
    results = asyncio.run(run_all(args.conversation, args.only, chosen))
    summarize(results)
    if args.json:
        Path(args.json).write_text(
            json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
