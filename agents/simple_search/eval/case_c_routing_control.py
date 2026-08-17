"""Routing control — can the router reach ``simple_search`` AT ALL in this setup?

The Case-C axis scored 0/4 (`case_c_routing.py`): every «اش الحكم اللي في WI-N…»
came back as a direct ``ChatResponse``. Before calling that a routing defect this
script rules out the harness:

* **CTRL-A/B** — a bare §1.1 lookup («اش يقول نظام العمل») with the SAME workspace
  context. If these dispatch ``simple_search``, the family is reachable and the
  Case-C failure is about the WI-attached phrasing, not the wiring.
* **CTRL-C** — the §1.1 «dangerous pair» negative control.
* **CTRL-D/E** — Case-C phrasings with NO workspace context loaded, isolating
  whether the prior-report content in context is what pulls the router to answer
  directly.

It also records the tool calls the router made (``unfold_workspace_item``), which
is the mechanism behind the direct answers.
"""
from __future__ import annotations

import asyncio
import json

from case_c_common import USER_ID, hr, service_client, short  # noqa: E402

from agents.models import DispatchAgent  # noqa: E402
from agents.router.context import load_router_context  # noqa: E402
from agents.router.router import run_router  # noqa: E402

SCRATCH_CONVO = "3101cee8-301e-4a12-86f5-ad4e8d01d450"
S_CASES = "2ff014cd-9197-4c15-8991-c1ec045a5902"

CASES = [
    ("ctrl_a", True,  "اش يقول نظام العمل؟", "simple_search"),
    ("ctrl_b", True,  "اش هي المادة 77 من نظام المحاكم التجارية؟", "simple_search"),
    ("ctrl_c", True,  "اش يقول نظام المعاملات المدنية عن علاقة الإيجار؟", "deep_search"),
    ("ctrl_d", False, "اش الحكم اللي في WI-2 وعن نزاع تاجرين؟ اعطيني تفاصيله", "simple_search"),
    ("ctrl_e", True,  "افتح لي الحكم رقم 439185965 كامل، ابغى نص الحكم نفسه مو الملخص", "simple_search"),
]


async def main() -> None:
    sb = service_client()
    hr("ROUTING CONTROL")
    ctx = load_router_context(sb, USER_ID, S_CASES, None)
    results = []
    for cid, with_ctx, question, expected in CASES:
        rr = await run_router(
            question, sb, USER_ID, SCRATCH_CONVO, None,
            ctx.case_memory_md if with_ctx else None,
            ctx.case_metadata if with_ctx else None,
            ctx.user_preferences,
            ctx.message_history if with_ctx else [],
            workspace_item_summaries=ctx.workspace_item_summaries if with_ctx else [],
            compaction_summary_md=ctx.compaction_summary_md if with_ctx else None,
            user_call_name=ctx.user_call_name,
            welcome=None,
        )
        out = rr.output
        if isinstance(out, DispatchAgent):
            got, detail = out.agent_family, {
                "task_label": out.task_label, "attached_wis": out.attached_wis}
        else:
            got, detail = "chat_response", {
                "message": short(getattr(out, "message", ""), 260)}
        ok = got == expected
        print(f"\n[{cid}] {'PASS' if ok else 'FAIL'} ctx={with_ctx} expected={expected} got={got}")
        print(f"    Q: {question}")
        print(f"    → {json.dumps(detail, ensure_ascii=False)[:340]}")
        results.append({"id": cid, "with_context": with_ctx, "q": question,
                        "expected": expected, "got": got, "ok": ok, "detail": detail})

    with open("agents/simple_search/eval/case_c_routing_control.json", "w",
              encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)
    print(f"\n{sum(1 for r in results if r['ok'])}/{len(results)} correct")


if __name__ == "__main__":
    asyncio.run(main())
