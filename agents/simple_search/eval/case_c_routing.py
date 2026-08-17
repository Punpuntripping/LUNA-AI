"""Case-C routing axis — the REAL router, with a real WI attached (§1.1 / D6).

Three families, ≥3 paraphrases each, all fired at ``agents.router.run_router``:

* «اش الحكم اللي في WI-N وعن …؟ اعطيني تفاصيله»  → expect ``simple_search``
* «قارن الحكمين اللي في WI-N»                     → expect ``deep_search`` (D6)
* «فصّل أكثر في المذكرة» on a ``writing``-produced WI → expect ``writing``
  (the provenance-tag rule must still win)

Context (workspace summaries + message history) is loaded from the user's REAL
conversations so the WI aliases and the provenance tags are the live ones; the
``conversation_id`` handed to ``run_router`` is the scratch conversation, so any
``save_memo`` side effect lands there and not in the read-only corpus.
"""
from __future__ import annotations

import asyncio
import json

from case_c_common import USER_ID, hr, service_client, short  # noqa: E402

from agents.models import DispatchAgent  # noqa: E402
from agents.router.context import load_router_context  # noqa: E402
from agents.router.router import run_router  # noqa: E402

SCRATCH_CONVO = "3101cee8-301e-4a12-86f5-ad4e8d01d450"  # [EVAL-CASE-C]

# Real source conversations (read-only) whose context we borrow.
S_CASES = "2ff014cd-9197-4c15-8991-c1ec045a5902"   # WI-2: 18 rulings + 10 regs
S_HEIRS = "e70cecfa-56ec-4788-b820-69b1d0b0ad1b"   # WI-3: 8 rulings · WI-4: agent_writing

CASES = [
    # (id, source convo, question, expected family)
    # ---- axis 1: point at a ruling cited inside a prior WI → simple_search
    ("ss1", S_CASES, "اش الحكم اللي في WI-2 وعن نزاع تاجرين؟ اعطيني تفاصيله", "simple_search"),
    ("ss2", S_CASES, "افتح لي الحكم اللي في WI-2 عن شركة المحاصة، ابغى تفاصيله كاملة", "simple_search"),
    ("ss3", S_CASES, "في WI-2 فيه حكم تجاري عن شريكين تنازعوا على رأس المال — ورّني نصه", "simple_search"),
    ("ss4", S_HEIRS, "الحكم اللي في WI-3 عن إقرار الورثة، اعطيني تفاصيله", "simple_search"),
    # ---- axis 2: comparison is NEVER this family (§0 D6)
    ("ds1", S_CASES, "قارن الحكمين اللي في WI-2", "deep_search"),
    ("ds2", S_CASES, "قارن بين حكم الابتدائية وحكم الاستئناف اللي في WI-2 وش الفرق بينهم", "deep_search"),
    ("ds3", S_HEIRS, "وازن بين الأحكام اللي في WI-3 وأيها أقوى سنداً", "deep_search"),
    # ---- axis 3: provenance must still win on a writing-produced WI
    ("wr1", S_HEIRS, "فصّل أكثر في المذكرة", "writing"),
    ("wr2", S_HEIRS, "وسّع التحليل القانوني اللي كتبته وأضف قسم عن الدفوع", "writing"),
    ("wr3", S_HEIRS, "حدّث المذكرة السابقة وزد فيها تفصيل عن أثر الإقرار", "writing"),
]


async def main() -> None:
    sb = service_client()
    hr("ROUTING AXIS — real router, real workspace context")
    ctx_cache: dict[str, object] = {}
    results = []

    for cid, src, question, expected in CASES:
        if src not in ctx_cache:
            ctx_cache[src] = load_router_context(sb, USER_ID, src, None)
        ctx = ctx_cache[src]
        aliases = {
            s.get("wi_seq"): (s.get("kind"), short(s.get("title") or "", 45))
            for s in ctx.workspace_item_summaries
        }
        rr = await run_router(
            question,
            sb,
            USER_ID,
            SCRATCH_CONVO,             # writes (save_memo) land in the scratch convo
            None,
            ctx.case_memory_md,
            ctx.case_metadata,
            ctx.user_preferences,
            ctx.message_history,
            workspace_item_summaries=ctx.workspace_item_summaries,
            compaction_summary_md=ctx.compaction_summary_md,
            user_call_name=ctx.user_call_name,
            welcome=None,
        )
        out = rr.output
        if isinstance(out, DispatchAgent):
            got = out.agent_family
            detail = {
                "task_label": out.task_label,
                "target_wi": out.target_wi,
                "attached_wis": out.attached_wis,
                "subtype": getattr(out, "subtype", None),
            }
        else:
            got = "chat_response"
            detail = {"message": short(getattr(out, "message", ""), 300)}

        ok = got == expected
        results.append({"id": cid, "question": question, "expected": expected,
                        "got": got, "ok": ok, "detail": detail,
                        "aliases": {str(k): v for k, v in aliases.items()}})
        print(f"\n[{cid}] {'PASS' if ok else 'FAIL'}  expected={expected} got={got}")
        print(f"    Q: {question}")
        print(f"    → {json.dumps(detail, ensure_ascii=False)[:400]}")

    hr("CONFUSION MATRIX")
    fams = ["simple_search", "deep_search", "writing", "memory", "chat_response"]
    matrix = {e: {g: 0 for g in fams} for e in ["simple_search", "deep_search", "writing"]}
    for r in results:
        matrix[r["expected"]][r["got"]] = matrix[r["expected"]].get(r["got"], 0) + 1
    hdr = "expected \\ got".ljust(16) + "".join(f.rjust(16) for f in fams)
    print(hdr)
    for e, row in matrix.items():
        print(e.ljust(16) + "".join(str(row[f]).rjust(16) for f in fams))
    passed = sum(1 for r in results if r["ok"])
    print(f"\n{passed}/{len(results)} correct")

    with open("agents/simple_search/eval/case_c_routing_results.json", "w",
              encoding="utf-8") as fh:
        json.dump({"results": results, "matrix": matrix}, fh, ensure_ascii=False, indent=2)
    print("dump → agents/simple_search/eval/case_c_routing_results.json")


if __name__ == "__main__":
    asyncio.run(main())
