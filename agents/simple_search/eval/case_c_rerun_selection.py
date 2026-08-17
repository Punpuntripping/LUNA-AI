"""Case-C selection + ambiguity RE-RUN — the same 17 fixtures, post-fix code.

Selection is re-measured for one reason: the F3/F4 fix added
``searcher._enrich_identities``, a batched join that now runs on **every**
``collect_case_c_candidates`` call and rewrites ``obj.title`` / ``subtitle`` /
``doc_type`` / ``source_url`` / ``regulation_id``. The candidate list the
searcher reasons over therefore changed shape between the two runs, and a
13/13 baseline is exactly the kind of number a "harmless" identity change can
quietly cost.

The fixture lists (``SELECTION``, ``AMBIGUITY``) and the runner (``run_one``)
are **imported**, not copied. The only mutation is the scratch conversation id,
which the original run's (now hard-deleted) constant still points at.
"""
from __future__ import annotations

import asyncio
import json

from case_c_rerun_common import ensure_scratch_conversation, hr, service_client, short  # noqa: E402

import case_c_selection as SEL  # noqa: E402 — THE fixture


async def main() -> None:
    sb = service_client()
    convo = ensure_scratch_conversation(sb)
    SEL.SCRATCH_CONVO = convo  # the original id was hard-deleted after that run
    hr(f"CASE-C SELECTION RE-RUN — scratch convo {convo}")
    report: dict = {"conversation_id": convo, "selection": [], "ambiguity": []}

    hr("PHASE 1 — SELECTION")
    for cid, wi, q, want_n, domain in SEL.SELECTION:
        try:
            r = await SEL.run_one(sb, wi, q)
        except Exception as exc:  # noqa: BLE001
            print(f"[{cid}] ERROR {type(exc).__name__}: {exc}")
            report["selection"].append({"id": cid, "domain": domain, "q": q,
                                        "want": want_n, "verdict": "ERROR",
                                        "error": f"{type(exc).__name__}: {exc}"[:300]})
            continue
        ok = r["kind"] == "decision" and r["ns"] == [want_n]
        near = r["kind"] == "decision" and want_n in r["ns"]
        verdict = "PASS" if ok else ("PARTIAL" if near else "FAIL")
        print(f"\n[{cid}] {verdict}  domain={domain} want=[{want_n}] got={r['ns']} "
              f"kind={r['kind']}")
        print(f"    Q: {q}")
        if r["kind"] == "decision":
            print(f"    data_type={r['data_type']} aborted={r['aborted']} "
                  f"selected={r['selected']} rationale={short(r.get('rationale',''),140)}")
            for o in r["resolved"]:
                print("    obj: " + json.dumps(
                    {k: v for k, v in o.items() if v}, ensure_ascii=False)[:300])
        else:
            print(f"    ask_user: {short(r['question'], 200)}")
        report["selection"].append({
            "id": cid, "domain": domain, "q": q, "want": want_n, "got": r["ns"],
            "verdict": verdict, "kind": r["kind"],
            "detail": {k: v for k, v in r.items() if k not in ("deps", "handle_n")},
        })

    hr("PHASE 2 — AMBIGUITY → ask_user")
    for cid, wi, q, plausible, note in SEL.AMBIGUITY:
        try:
            r = await SEL.run_one(sb, wi, q)
        except Exception as exc:  # noqa: BLE001
            print(f"[{cid}] ERROR {type(exc).__name__}: {exc}")
            report["ambiguity"].append({"id": cid, "q": q, "asked": None,
                                        "error": f"{type(exc).__name__}: {exc}"[:300]})
            continue
        asked = r["kind"] == "ask_user"
        print(f"\n[{cid}] {'PASS (asked)' if asked else 'FAIL (guessed)'}  {note}")
        print(f"    Q: {q}")
        if asked:
            print(f"    ask_user: {short(r['question'], 260)}")
        else:
            print(f"    picked n={r['ns']} of plausible {plausible} "
                  f"aborted={r['aborted']} rationale={short(r.get('rationale',''),180)}")
        report["ambiguity"].append({
            "id": cid, "q": q, "plausible": plausible, "note": note,
            "asked": asked, "got": r["ns"],
            "detail": {k: v for k, v in r.items() if k not in ("deps", "handle_n")},
        })

    hr("SUMMARY")
    sel = report["selection"]
    p = sum(1 for r in sel if r.get("verdict") == "PASS")
    pa = sum(1 for r in sel if r.get("verdict") == "PARTIAL")
    print(f"selection: {p} PASS / {pa} PARTIAL / {len(sel) - p - pa} other of {len(sel)}")
    by_dom: dict[str, list[str]] = {}
    for r in sel:
        by_dom.setdefault(r["domain"], []).append(r.get("verdict", "ERROR"))
    for d, vs in by_dom.items():
        print(f"  {d}: {vs.count('PASS')}/{len(vs)} PASS  {vs}")
    amb = report["ambiguity"]
    print(f"ambiguity: {sum(1 for r in amb if r.get('asked'))}/{len(amb)} asked  "
          f"{[(r['id'], r.get('asked')) for r in amb]}")

    with open("agents/simple_search/eval/case_c_rerun_selection_results.json", "w",
              encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, default=str)
    print("dump → agents/simple_search/eval/case_c_rerun_selection_results.json")


if __name__ == "__main__":
    asyncio.run(main())
