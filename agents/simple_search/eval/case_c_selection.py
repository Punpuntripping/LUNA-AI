"""Case-C selection + ambiguity — the REAL searcher over real WI refs.

Phase 1 (selection): for each sampled WI ref, phrase the question the way a user
reading that card would, and assert the searcher hands back THAT ref's identity.

Phase 2 (ambiguity, §2.3.1): two refs of one real WI that plausibly match one
phrasing. The searcher must ``ask_user``, not guess — guessing wrong on a ruling
spends a judgment unlock on the wrong document (§7.3 / D12).

The searcher is run directly (``agent.run``) rather than through the runner, so
nothing is unfolded, nothing is published, and no unlock is spent: the searcher
hands off identity only (§2.1.6).
"""
from __future__ import annotations

import argparse
import asyncio
import json

from case_c_common import USER_ID, hr, service_client, short  # noqa: E402

from pydantic_ai import DeferredToolRequests  # noqa: E402

from agents.simple_search.prompts import build_searcher_user_message  # noqa: E402
from agents.simple_search.searcher import (  # noqa: E402
    SEARCHER_LIMITS,
    SearcherDecision,
    SearcherDeps,
    collect_case_c_candidates,
    create_searcher_agent,
)
from agents.tool_repository.unfold_workspace_item import resolve_used_sources  # noqa: E402

SCRATCH_CONVO = "3101cee8-301e-4a12-86f5-ad4e8d01d450"

WI_CASES = "91680d79-b236-411b-a7d3-ef5f5c15453f"   # 18 rulings + 10 regs
WI_MIXED = "94903243-cd68-4f92-98b1-6bf453d6c61e"   # regs + 3 rulings + تعميم + خدمات
WI_IDENT = "24b01fd8-4eae-4917-a21f-1b20f5e75f9b"   # 17 regs + 2 تعاميم + 4 خدمات
WI_WIDOW = "2d197e0c-5112-4af8-bab9-0de50dc01651"   # regs + 3 rulings + 4 خدمات

# (id, wi, question phrased off the card, expected ref n, domain)
SELECTION = [
    # ---- cases: the panel with 18 rulings — the interesting one
    ("c1", WI_CASES, "اش الحكم اللي في المراجع عن نزاع شريكين في شركة محاصة مغاسل عبر تطبيق إلكتروني؟ اعطيني تفاصيله", 11, "cases"),
    ("c2", WI_CASES, "ابغى تفاصيل الحكم اللي عن شراكة مكب النفايات", 14, "cases"),
    ("c3", WI_CASES, "الحكم اللي عن نزاع الشراكة في العيادات الطبية — ورّني تفاصيله", 22, "cases"),
    ("c4", WI_CASES, "افتح لي الحكم اللي عن أرباح شراكة تصنيع وبيع المنظفات", 36, "cases"),
    ("c5", WI_MIXED, "الحكم اللي عن أمانة العاصمة المقدسة والحجز التنفيذي، اعطيني تفاصيله", 32, "cases"),
    # ---- regulations: identify by the CARD'S OWN doc_type chip (§2.3.1 #1)
    ("r1", WI_CASES, "ورّني الدليل اللي في المراجع عن أعمال مصفي الأموال المشتركة", 3, "regulations"),
    ("r2", WI_WIDOW, "افتح لي التنظيم اللي عن صندوق النفقة", 10, "regulations"),
    ("r3", WI_IDENT, "ابغى اللائحة التنفيذية لنظام حماية البيانات الشخصية اللي في المراجع", 20, "regulations"),
    ("r4", WI_CASES, "اعطيني نص المادة الحادية عشرة من نظام المحاكم التجارية اللي في المراجع", 6, "regulations"),
    # ---- circulars
    ("k1", WI_IDENT, "افتح لي التعميم رقم 13/ت/6251", 9, "circulars"),
    ("k2", WI_MIXED, "ورّني تعميم مبادئ التمويل المسؤول للأفراد من البنك المركزي", 36, "circulars"),
    # ---- services (compliance)
    ("s1", WI_WIDOW, "ورّني خدمة تمويل كنف من بنك التنمية الاجتماعية", 17, "compliance"),
    ("s2", WI_MIXED, "افتح لي خدمة إثبات السداد من وزارة العدل", 15, "compliance"),
]

# (id, wi, question, the set of refs that plausibly match, note)
AMBIGUITY = [
    ("a1", WI_MIXED,
     "الحكم اللي في المراجع عن دعوى تعويض عن أضرار — اعطيني تفاصيله",
     [8, 9],
     "two rulings, both «دعوى تعويض عن أضرار…» — guessing spends an unlock on the wrong one"),
    ("a2", WI_CASES,
     "اش الحكم اللي في المراجع وعن نزاع تاجرين؟ اعطيني تفاصيله",
     [10, 11, 13, 33, 35, 37],
     "the plan's own example sentence — 8+ of the 18 rulings are two-partner disputes"),
    ("a3", WI_IDENT,
     "ابغى اللائحة التنفيذية لنظام الأحوال المدنية اللي عن الفصل الثامن",
     [1, 2, 27],
     "three refs render the same chip + regulation + «الفصل الثامن»"),
    ("a4", WI_IDENT,
     "افتح لي خدمة طلب هوية بدل تالف",
     [11, 13],
     "two refs whose service-name cards are byte-identical"),
]


def build_deps(sb, wi_id: str):
    """Deps with the real case-C candidate list + a handle→n map for grading."""
    deps = SearcherDeps(supabase=sb, user_id=USER_ID, conversation_id=SCRATCH_CONVO)
    text_to_ns: dict[str, list[int]] = {}
    for line in resolve_used_sources(sb, wi_id):
        text_to_ns.setdefault(line.text, []).append(line.n)
    handle_n: dict[str, list[int]] = {}
    for obj, preview in collect_case_c_candidates(sb, [wi_id]):
        handle = deps.register_candidate(obj, preview)
        handle_n[handle] = text_to_ns.get(preview, [])
    return deps, handle_n


def obj_to_ns(deps, handle_n, obj) -> list[int]:
    for h, cand in deps.candidates.items():
        if cand is obj or cand.model_dump() == obj.model_dump():
            return handle_n.get(h, [])
    return []


async def run_one(sb, wi_id: str, question: str):
    deps, handle_n = build_deps(sb, wi_id)
    agent = create_searcher_agent()
    result = await agent.run(
        build_searcher_user_message(question), deps=deps, usage_limits=SEARCHER_LIMITS,
    )
    out = result.output
    if isinstance(out, DeferredToolRequests):
        q = ""
        for call in getattr(out, "calls", None) or []:
            args = getattr(call, "args", None)
            if isinstance(args, dict) and args.get("question"):
                q = str(args["question"])
            elif isinstance(args, str):
                q = args
        return {"kind": "ask_user", "question": q, "ns": [], "deps": deps,
                "handle_n": handle_n}
    assert isinstance(out, SearcherDecision)
    ns = []
    for obj in out.resolved:
        ns.extend(obj_to_ns(deps, handle_n, obj))
    return {"kind": "decision", "aborted": out.aborted,
            "abort_reason": out.abort_reason, "selected": out.selected,
            "data_type": out.data_type, "rationale": out.rationale,
            "resolved": [o.model_dump() for o in out.resolved], "ns": ns,
            "deps": deps, "handle_n": handle_n}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["selection", "ambiguity", "both"], default="both")
    args = ap.parse_args()
    sb = service_client()
    report: dict = {"selection": [], "ambiguity": []}

    if args.phase in ("selection", "both"):
        hr("PHASE 1 — SELECTION (does the right ref get picked?)")
        for cid, wi, q, want_n, domain in SELECTION:
            try:
                r = await run_one(sb, wi, q)
            except Exception as exc:  # noqa: BLE001
                print(f"[{cid}] ERROR {exc}")
                report["selection"].append({"id": cid, "domain": domain, "q": q,
                                            "want": want_n, "error": str(exc)[:300]})
                continue
            ok = r["kind"] == "decision" and r["ns"] == [want_n]
            near = r["kind"] == "decision" and want_n in r["ns"]
            verdict = "PASS" if ok else ("PARTIAL" if near else "FAIL")
            print(f"\n[{cid}] {verdict}  domain={domain} want=[{want_n}] got={r['ns']} "
                  f"kind={r['kind']}")
            print(f"    Q: {q}")
            if r["kind"] == "decision":
                print(f"    data_type={r['data_type']} aborted={r['aborted']} "
                      f"selected={r['selected']} rationale={short(r.get('rationale',''),120)}")
                for o in r["resolved"]:
                    print("    obj: " + json.dumps(
                        {k: v for k, v in o.items() if v and k != "title"},
                        ensure_ascii=False)[:220])
            else:
                print(f"    ask_user: {short(r['question'], 200)}")
            report["selection"].append({
                "id": cid, "domain": domain, "q": q, "want": want_n,
                "got": r["ns"], "verdict": verdict, "kind": r["kind"],
                "detail": {k: v for k, v in r.items()
                           if k not in ("deps", "handle_n")},
            })

    if args.phase in ("ambiguity", "both"):
        hr("PHASE 2 — AMBIGUITY → ask_user (§2.3.1; a wrong guess spends an unlock)")
        for cid, wi, q, plausible, note in AMBIGUITY:
            try:
                r = await run_one(sb, wi, q)
            except Exception as exc:  # noqa: BLE001
                print(f"[{cid}] ERROR {exc}")
                report["ambiguity"].append({"id": cid, "q": q, "error": str(exc)[:300]})
                continue
            asked = r["kind"] == "ask_user"
            print(f"\n[{cid}] {'PASS (asked)' if asked else 'FAIL (guessed)'}  {note}")
            print(f"    Q: {q}")
            if asked:
                print(f"    ask_user: {short(r['question'], 240)}")
            else:
                print(f"    picked n={r['ns']} of plausible {plausible} "
                      f"aborted={r['aborted']} rationale={short(r.get('rationale',''),160)}")
            report["ambiguity"].append({
                "id": cid, "q": q, "plausible": plausible, "note": note,
                "asked": asked, "got": r["ns"],
                "detail": {k: v for k, v in r.items() if k not in ("deps", "handle_n")},
            })

    hr("SUMMARY")
    sel = report["selection"]
    if sel:
        p = sum(1 for r in sel if r.get("verdict") == "PASS")
        pa = sum(1 for r in sel if r.get("verdict") == "PARTIAL")
        print(f"selection: {p} PASS / {pa} PARTIAL / {len(sel) - p - pa} FAIL of {len(sel)}")
        by_dom: dict[str, list[str]] = {}
        for r in sel:
            by_dom.setdefault(r["domain"], []).append(r.get("verdict", "ERROR"))
        for d, vs in by_dom.items():
            print(f"  {d}: {vs.count('PASS')}/{len(vs)} PASS  {vs}")
    amb = report["ambiguity"]
    if amb:
        print(f"ambiguity: {sum(1 for r in amb if r.get('asked'))}/{len(amb)} asked")

    with open("agents/simple_search/eval/case_c_selection_results.json", "w",
              encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, default=str)
    print("dump → agents/simple_search/eval/case_c_selection_results.json")


if __name__ == "__main__":
    asyncio.run(main())
