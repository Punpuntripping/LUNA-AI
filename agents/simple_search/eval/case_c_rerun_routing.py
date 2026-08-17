"""Case-C routing RE-RUN — the same 10 fixtures, the twice-patched router prompt.

The fixture list is **imported** from ``case_c_routing`` rather than copied, so
the before/after is a true A/B: identical sentences, identical borrowed context,
identical expectations. Only three things differ:

1. the router prompt (patch 1 at ``:217`` + patch 2 at ``:327``/``:330``/``:331``
   + the scoped Ambiguity check + the "when in doubt, deep_search" tie-break);
2. the scratch ``conversation_id`` (the original one was hard-deleted);
3. **repeats** — the A/B re-run measured this leg as non-deterministic (the same
   sentence answered directly once and dispatched once), so a single firing
   cannot tell a fix from a coin flip.

**Scoring is by FAMILY, never by "did it dispatch".** The tie-break added to the
prompt points at ``deep_search``, and Case C is the leg most at risk from it: a
request to OPEN a cited source that lands on ``deep_search`` is still wrong, and
it would move the raw dispatch rate in the right direction while the behaviour
got worse. So ``simple_search`` expectations that land on ``deep_search`` are
counted, reported and named separately as **tie-break over-fire**.
"""
from __future__ import annotations

import asyncio
import json
from collections import Counter

from case_c_rerun_common import (  # noqa: E402
    USER_ID, ensure_scratch_conversation, hr, service_client, short,
)

from case_c_routing import CASES, S_CASES, S_HEIRS  # noqa: E402,F401 — THE fixture

from agents.models import DispatchAgent  # noqa: E402
from agents.router.context import load_router_context  # noqa: E402
from agents.router.router import run_router  # noqa: E402

#: Repeats per expectation. The ss leg carries the headline and the documented
#: non-determinism, so it gets the most; wr is a regression guard at n=1.
REPEATS = {"simple_search": 3, "deep_search": 3, "writing": 1}

#: Substrings that mark a reply as *the ruling restated from the manifest line*
#: — the original F1 failure mode (and Fix-4's proposed stop-check).
_SNIPPET_MARKERS = ("رقم القضية", "المحكمة:", "## الملخص", "الوقائع", "منطوق الحكم")
#: …versus handing the user a «أي واحد تقصد؟» list, which is the specialist's job.
_ASK_MARKERS = ("أي واحد", "أيها تقصد", "أي حكم", "تقصد؟", "أيّ واحد", "أي منها")


def classify(message: str) -> str:
    """Name the failure mode of a non-dispatching Case-C reply."""
    m = message or ""
    if any(k in m for k in _ASK_MARKERS):
        return "asks_which_source (router disambiguating for the specialist)"
    if any(k in m for k in _SNIPPET_MARKERS):
        return "restates_the_manifest_snippet (original F1)"
    return "other_direct_answer"


async def main() -> None:
    sb = service_client()
    convo = ensure_scratch_conversation(sb)
    hr(f"CASE-C ROUTING RE-RUN — scratch convo {convo}")
    print(f"fixtures: {len(CASES)} sentences · repeats {REPEATS}")

    ctx_cache: dict[str, object] = {}
    results: list[dict] = []

    for cid, src, question, expected in CASES:
        if src not in ctx_cache:
            ctx_cache[src] = load_router_context(sb, USER_ID, src, None)
        ctx = ctx_cache[src]
        for rep in range(REPEATS.get(expected, 1)):
            try:
                rr = await run_router(
                    question, sb, USER_ID, convo, None,
                    ctx.case_memory_md, ctx.case_metadata, ctx.user_preferences,
                    ctx.message_history,
                    workspace_item_summaries=ctx.workspace_item_summaries,
                    compaction_summary_md=ctx.compaction_summary_md,
                    user_call_name=ctx.user_call_name,
                    welcome=None,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[{cid}#{rep}] ERROR {type(exc).__name__}: {exc}")
                results.append({"id": cid, "rep": rep, "q": question,
                                "expected": expected, "got": "ERROR",
                                "ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]})
                continue
            out = rr.output
            if isinstance(out, DispatchAgent):
                got = out.agent_family
                detail = {"task_label": out.task_label, "target_wi": out.target_wi,
                          "attached_wis": out.attached_wis,
                          "subtype": getattr(out, "subtype", None)}
                mode = ""
            else:
                got = "chat_response"
                msg = str(getattr(out, "message", "") or "")
                detail = {"message_chars": len(msg), "message": msg}
                mode = classify(msg)
            ok = got == expected
            tie_break = (expected == "simple_search" and got == "deep_search")
            results.append({"id": cid, "rep": rep, "q": question,
                            "expected": expected, "got": got, "ok": ok,
                            "tie_break_overfire": tie_break,
                            "failure_mode": mode, "detail": detail})
            tag = "PASS" if ok else ("FAIL/TIE-BREAK-OVERFIRE" if tie_break else "FAIL")
            print(f"\n[{cid}#{rep}] {tag}  expected={expected} got={got}")
            print(f"    Q: {question}")
            if got == "chat_response":
                print(f"    mode: {mode}")
                print(f"    → chat ({detail['message_chars']} chars): "
                      f"{short(detail['message'], 260)}")
            else:
                print(f"    → {json.dumps(detail, ensure_ascii=False)[:320]}")

    # ── scoring ──────────────────────────────────────────────────────────────
    hr("CONFUSION MATRIX — by FAMILY (runs, not sentences)")
    fams = ["simple_search", "deep_search", "writing", "memory", "chat_response", "ERROR"]
    matrix = {e: {g: 0 for g in fams} for e in ("simple_search", "deep_search", "writing")}
    for r in results:
        matrix[r["expected"]][r["got"]] = matrix[r["expected"]].get(r["got"], 0) + 1
    print("expected \\ got".ljust(16) + "".join(f.rjust(15) for f in fams))
    for e, row in matrix.items():
        print(e.ljust(16) + "".join(str(row[f]).rjust(15) for f in fams))

    hr("PER-LEG SCORE")
    for leg in ("simple_search", "deep_search", "writing"):
        rows = [r for r in results if r["expected"] == leg]
        print(f"  {leg:<14} {sum(1 for r in rows if r['ok'])}/{len(rows)} runs")
    overfire = [r for r in results if r.get("tie_break_overfire")]
    print(f"\n  TIE-BREAK OVER-FIRE (simple_search → deep_search): {len(overfire)} run(s)")
    for r in overfire:
        print(f"    [{r['id']}#{r['rep']}] {short(r['q'], 90)}")

    hr("FAILURE MODES of the non-dispatching replies")
    print(json.dumps(dict(Counter(
        r["failure_mode"] for r in results if r.get("failure_mode")
    )), ensure_ascii=False, indent=2))

    hr("PER-SENTENCE")
    for cid, _src, question, expected in CASES:
        rows = [r for r in results if r["id"] == cid]
        got = Counter(r["got"] for r in rows)
        print(f"  [{cid}] want={expected:<14} "
              f"{sum(1 for r in rows if r['ok'])}/{len(rows)}  {dict(got)}")

    passed = sum(1 for r in results if r["ok"])
    print(f"\nTOTAL {passed}/{len(results)} runs correct")

    with open("agents/simple_search/eval/case_c_rerun_routing_results.json", "w",
              encoding="utf-8") as fh:
        json.dump({"conversation_id": convo, "repeats": REPEATS,
                   "results": results, "matrix": matrix}, fh,
                  ensure_ascii=False, indent=2)
    print("dump → agents/simple_search/eval/case_c_rerun_routing_results.json")


if __name__ == "__main__":
    asyncio.run(main())
