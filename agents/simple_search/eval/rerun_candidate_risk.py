"""OPEN ITEM B — the residual risk nobody measured.

Plan §13e, verbatim:

    **Also open:** bug ①'s fix turns three confident wrong answers into
    **candidate tables**, not `not_found` — an LLM could still pick C1 wrongly.

"Could" is a hypothesis. This file measures it, by running the **real searcher
agent** — the actual LLM, the actual tools, the actual prompt — on the queries
whose `manual` leg now returns a `recall_only` candidate table, and reading what
it decided off the returned ``SearcherDecision``.

WHY THE AGENT AND NOT A SIMULATED TABLE
---------------------------------------
The searcher reaches for ``resolve_regulation`` (the `det` leg) FIRST, and on
`fp-01` that leg still commits — «الترتيبات التنظيمية…» at `floor+margin`. So a
bench that hands the model only the manual table would measure a situation the
model is never actually in, and would flatter the fix. The end-to-end run is the
only honest form of the question.

``agent.run`` is called directly rather than through ``run_tracked``: the
tracking wrapper writes an ``llm_calls`` ledger row, and this lane writes
nothing to production tables.

    python agents/simple_search/eval/rerun_candidate_risk.py --reps 3
"""
from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

from rerun_common import USER_ID, hr, service_client

from agents.simple_search.prompts import build_searcher_user_message  # noqa: E402
from agents.simple_search.searcher import (  # noqa: E402
    SEARCHER_LIMITS,
    SearcherDecision,
    SearcherDeps,
    create_searcher_agent,
)

# ids from fixtures_resolution — the documents a commit must NOT land on.
JUNK = {
    "7ee43cf7-73e9-4e58-b5e3-cd738d5872b9": "الترتيبات التنظيمية…مكافحة الفساد المالي والإداري",
    "644e68ad-8bde-447d-b609-1714baab5189": "الترتيبات التنظيمية للهيئة السعودية للبيانات والذكاء الاصطناعي",
}
AMAL = "da51024f-a713-48e7-af87-b6a541f055e4"
AMAL_TATAWUI = "271b646f-42ce-472d-80a9-4248209e57b1"


@dataclass
class Case:
    fid: str
    query: str
    kind: str            # must_refuse | control
    note: str = ""


CASES: list[Case] = [
    # The three §13e names: must-refuse fixtures whose manual leg now returns a
    # `recall_only` candidate table instead of `not_found`.
    Case("fp-01", "نظام الفساد المالي والإداري", "must_refuse",
         "det leg still COMMITS here (floor+margin, 0.5806) — the table is not the only input"),
    Case("fp-02", "نظام حماية الفضاء السيبراني الوطني", "must_refuse",
         "det ambiguous · manual recall_only table"),
    Case("desc-03", "تطبيقات نظام العمل", "must_refuse",
         "manual recall_only table; the named law نظام العمل is real, the OBJECT asked for is not"),
    # The surviving manual wrong-doc — the model is handed a RESOLVED wrong law,
    # not a table, so it is the upper bound on the same risk.
    Case("fp-05", "نظام الذكاء الاصطناعي السعودي", "must_refuse",
         "manual RESOLVES the wrong doc at medium confidence"),
    # Control: if the model refuses everything the numbers above mean nothing.
    Case("reg-10", "نظام العمل التطوعي السعودي", "control",
         "the calibration positive — must still resolve نظام العمل التطوعي"),
]


@dataclass
class RunOut:
    fid: str
    rep: int
    query: str
    kind: str
    outcome: str        # committed_junk | committed_other | aborted | asked | empty | error
    aborted: bool = False
    abort_reason: str = ""
    selected: list[str] = field(default_factory=list)
    resolved_titles: list[str] = field(default_factory=list)
    resolved_ids: list[str] = field(default_factory=list)
    rationale: str = ""
    note: str = ""


async def _one(case: Case, rep: int) -> RunOut:
    sb = service_client()
    deps = SearcherDeps(supabase=sb, user_id=USER_ID, conversation_id="")
    agent = create_searcher_agent()
    try:
        result = await agent.run(
            build_searcher_user_message(case.query),
            deps=deps,
            usage_limits=SEARCHER_LIMITS,
        )
    except Exception as exc:  # noqa: BLE001
        return RunOut(case.fid, rep, case.query, case.kind, "error", note=repr(exc)[:220])

    out = result.output
    if not isinstance(out, SearcherDecision):
        # DeferredToolRequests — the model called ask_user. The safe shape.
        return RunOut(case.fid, rep, case.query, case.kind, "asked",
                      note=type(out).__name__)

    ids = [o.regulation_id or o.primary_id() for o in out.resolved]
    titles = [(o.title or o.subtitle or "") for o in out.resolved]
    r = RunOut(case.fid, rep, case.query, case.kind, "empty",
               aborted=out.aborted, abort_reason=out.abort_reason,
               selected=list(out.selected), resolved_titles=titles,
               resolved_ids=ids, rationale=out.rationale)
    if out.aborted and not out.resolved:
        r.outcome = "aborted"
    elif any(i in JUNK for i in ids):
        r.outcome = "committed_junk"
    elif ids:
        r.outcome = "committed_other"
    return r


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3)
    args = ap.parse_args()

    hr("OPEN ITEM B — would an LLM handed the candidate table pick a wrong candidate?")
    print(f"{len(CASES)} queries × {args.reps} reps = {len(CASES) * args.reps} searcher runs "
          f"(each is several LLM calls)\n")

    outs: list[RunOut] = []
    for case in CASES:
        for rep in range(1, args.reps + 1):
            r = await _one(case, rep)
            outs.append(r)
            print(f"  [{r.fid:8s} r{rep}] {r.outcome:16s} "
                  f"selected={r.selected} «{'; '.join(r.resolved_titles)[:60]}» "
                  f"{('abort=' + r.abort_reason[:50]) if r.aborted else ''}")

    hr("RESIDUAL RISK")
    for case in CASES:
        rs = [r for r in outs if r.fid == case.fid]
        junk = sum(1 for r in rs if r.outcome == "committed_junk")
        other = sum(1 for r in rs if r.outcome == "committed_other")
        safe = sum(1 for r in rs if r.outcome in ("aborted", "asked", "empty"))
        print(f"  {case.fid:8s} [{case.kind:11s}] «{case.query[:38]:38s}» "
              f"junk={junk}/{len(rs)}  other-commit={other}/{len(rs)}  safe={safe}/{len(rs)}")
        print(f"           {case.note}")

    must = [r for r in outs if r.kind == "must_refuse"]
    junk = sum(1 for r in must if r.outcome == "committed_junk")
    anycommit = sum(1 for r in must if r.outcome in ("committed_junk", "committed_other"))
    print(f"\n  must-refuse runs: {len(must)}")
    print(f"  committed to a KNOWN-junk document : {junk}/{len(must)} "
          f"({100.0 * junk / max(len(must), 1):.0f}%)")
    print(f"  committed to ANY document          : {anycommit}/{len(must)} "
          f"({100.0 * anycommit / max(len(must), 1):.0f}%)")

    Path(__file__).with_name("rerun_candidate_risk_results.json").write_text(
        json.dumps([r.__dict__ for r in outs], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("\nwrote rerun_candidate_risk_results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
