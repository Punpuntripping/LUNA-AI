"""Router — the government-services boundary (plan §9 / D8), standalone.

Reuses ``run_routing.run_all`` verbatim (same router entry point, same PASS
rule) pointed at :mod:`fixtures_routing_services`. No DB setup: the router is
handed no workspace items and no history, so with a conversation id that has
no items nothing is read and nothing is written. Costs one router call per
fixture per rep.

``run_router`` swallows provider failures into a ChatResponse
«عذراً، حدث خطأ…», which ``run_all`` would score as ``chat``. Those are
re-labelled ``error`` here so a dead provider never reads as a routing miss.

    .venv/Scripts/python.exe agents/simple_search/eval/rerun_routing_services.py --reps 2
"""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from rerun_common import hr  # noqa: F401  (path bootstrap + utf-8 console + .env)

from agents.simple_search.eval.fixtures_routing_services import (  # noqa: E402
    SERVICE_ROUTE_FIXTURES,
    total_calls,
)
from agents.simple_search.eval.run_routing import run_all, summarize  # noqa: E402

_ROUTER_ERROR_PREFIX = "عذراً، حدث خطأ"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--conversation", default=None,
                    help="conversation uuid (default: a fresh random uuid with no items)")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--only", default=None, help="one class: service_direct | service_regulatory | service_multi")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    conversation_id = args.conversation or str(uuid.uuid4())
    print(f"SERVICES BOUNDARY: {total_calls(args.reps)} router calls "
          f"({len(SERVICE_ROUTE_FIXTURES)} fixtures × {args.reps} reps)")
    results = asyncio.run(
        run_all(conversation_id, args.only, SERVICE_ROUTE_FIXTURES * args.reps)
    )
    for r in results:
        if r.got == "chat" and r.chat_preview.startswith(_ROUTER_ERROR_PREFIX):
            r.got, r.verdict, r.note = "error", "FAIL", "router fallback (provider/run error)"
    summarize(results)

    hr("PER FIXTURE")
    by_q: dict[str, list] = {}
    for r in results:
        by_q.setdefault(r.query, []).append(r)
    rid_of = {q: f.rid for f in SERVICE_ROUTE_FIXTURES for q in f.paraphrases}
    for q, rs in by_q.items():
        c = Counter(r.got for r in rs)
        ok = sum(r.verdict == "PASS" for r in rs)
        print(f"{rid_of[q]:<22} want={rs[0].expect:<13} {ok}/{len(rs)}  {dict(c)}")

    path = args.json or str(Path(__file__).with_name("rerun_routing_services_results.json"))
    Path(path).write_text(
        json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
