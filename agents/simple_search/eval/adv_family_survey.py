"""Pick the real WIs each adversarial fixture needs. READ-ONLY, no LLM.

The fixtures name a *setup* ("a WI citing >=2 rulings", "an agent_writing WI
whose refs were projected"), not an id. This script finds them in the eval
account's real corpus and writes the choices into the results file, so every
later probe cites a WI that was chosen by a stated rule rather than by hand.

Money-aware: rulings are grouped into ALREADY-UNLOCKED vs fresh, because a
probe that fans out over a fresh ruling spends real credit.
"""
from __future__ import annotations

import json
from collections import defaultdict

from adv_family_common import (  # noqa: E402
    USER_ID, flush, hr, ledger, load, service_client, short,
)


def main() -> None:
    sb = service_client()
    doc = load()
    unlocked = {str(r["content_id"]) for r in ledger(sb, "judgment")}

    # every WI of this user + its used refs
    wis = []
    start = 0
    while True:
        page = (sb.table("workspace_items")
                .select("item_id, kind, title, conversation_id, created_at")
                .eq("user_id", USER_ID).is_("deleted_at", "null")
                .order("created_at").range(start, start + 999).execute()).data or []
        wis.extend(page)
        if len(page) < 1000:
            break
        start += 1000
    by_id = {str(w["item_id"]): w for w in wis}
    print(f"WIs: {len(wis)} · kinds: "
          f"{json.dumps({k: sum(1 for w in wis if w['kind'] == k) for k in {w['kind'] for w in wis}}, ensure_ascii=False)}")

    refs = []
    start = 0
    while True:
        page = (sb.table("workspace_item_references")
                .select("wi_id, n, domain, ref_id, item_id, used")
                .range(start, start + 999).execute()).data or []
        refs.extend(page)
        if len(page) < 1000:
            break
        start += 1000
    mine = [r for r in refs if str(r["wi_id"]) in by_id]
    print(f"refs (this user): {len(mine)} of {len(refs)} total")

    per_wi: dict[str, list[dict]] = defaultdict(list)
    for r in mine:
        if r.get("used") is False:
            continue
        per_wi[str(r["wi_id"])].append(r)

    def dom_counts(rows):
        c: dict[str, int] = {}
        for r in rows:
            c[r["domain"]] = c.get(r["domain"], 0) + 1
        return c

    picks: dict[str, object] = {}

    # ── 1. WI with EXACTLY 2 case refs — hair-01's «الحكمين» must be literal.
    hr("1 · WIs with exactly 2 case refs (hair-01 / casec-03)")
    two_case = []
    for wi, rows in per_wi.items():
        cases = [r for r in rows if r["domain"] == "cases"]
        if len(cases) == 2:
            cids = [str(r["item_id"] or "") for r in cases]
            two_case.append({
                "wi_id": wi, "kind": by_id[wi]["kind"],
                "title": by_id[wi]["title"], "conversation_id": str(by_id[wi]["conversation_id"]),
                "ns": [r["n"] for r in cases],
                "case_ids": cids,
                "unlocked": [c in unlocked for c in cids],
                "ref_ids": [r["ref_id"] for r in cases],
                "total_refs": len(rows), "domains": dom_counts(rows),
            })
    two_case.sort(key=lambda d: (-sum(d["unlocked"]), d["total_refs"]))
    for d in two_case[:12]:
        print(f"  {d['wi_id']}  kind={d['kind']} refs={d['total_refs']} "
              f"ns={d['ns']} unlocked={d['unlocked']}")
        print(f"      {short(d['title'] or '', 90)}")
        for t in d["ref_ids"]:
            print(f"        · {t}")
    picks["two_case_wis"] = two_case[:12]

    # ── 2. WIs whose refs are ALL non-cases and >=3 refs (casec-01 — free)
    hr("2 · money-free WIs with >=3 refs, zero cases (casec-01)")
    free3 = []
    for wi, rows in per_wi.items():
        if any(r["domain"] == "cases" for r in rows):
            continue
        if len(rows) >= 3:
            free3.append({"wi_id": wi, "kind": by_id[wi]["kind"],
                          "title": by_id[wi]["title"], "n_refs": len(rows),
                          "domains": dom_counts(rows),
                          "ns": sorted(r["n"] for r in rows)[:12],
                          "conversation_id": str(by_id[wi]["conversation_id"])})
    free3.sort(key=lambda d: d["n_refs"])
    for d in free3[:10]:
        print(f"  {d['wi_id']}  refs={d['n_refs']} {json.dumps(d['domains'], ensure_ascii=False)} "
              f"ns={d['ns']}")
        print(f"      {short(d['title'] or '', 100)}")
    picks["free_3ref_wis"] = free3[:10]

    # ── 3. agent_writing WIs that carry refs (casec-04)
    hr("3 · agent_writing WIs carrying refs (casec-04)")
    writing = []
    for wi, rows in per_wi.items():
        if by_id[wi]["kind"] != "agent_writing":
            continue
        writing.append({"wi_id": wi, "title": by_id[wi]["title"], "n_refs": len(rows),
                        "domains": dom_counts(rows),
                        "conversation_id": str(by_id[wi]["conversation_id"]),
                        "case_ids": [str(r["item_id"] or "") for r in rows
                                     if r["domain"] == "cases"]})
    for d in writing:
        print(f"  {d['wi_id']}  refs={d['n_refs']} {json.dumps(d['domains'], ensure_ascii=False)}")
        print(f"      {short(d['title'] or '', 100)}")
    if not writing:
        print("  NONE — every agent_writing WI in this account carries zero ref rows.")
    picks["writing_wis"] = writing
    kinds_with_refs = sorted({by_id[wi]["kind"] for wi in per_wi})
    print(f"  kinds that DO carry refs: {kinds_with_refs}")
    picks["kinds_with_refs"] = kinds_with_refs

    # ── 4. the same ruling cited by TWO WIs (casec-05)
    hr("4 · one ruling cited by >=2 WIs — prefer an already-unlocked one (casec-05)")
    case_to_wis: dict[str, set[str]] = defaultdict(set)
    for wi, rows in per_wi.items():
        for r in rows:
            if r["domain"] == "cases" and r["item_id"]:
                case_to_wis[str(r["item_id"])].add(wi)
    shared = [(cid, sorted(ws)) for cid, ws in case_to_wis.items() if len(ws) >= 2]
    shared.sort(key=lambda t: (t[0] not in unlocked, -len(t[1])))
    for cid, ws in shared[:10]:
        print(f"  case {cid} unlocked={cid in unlocked} in {len(ws)} WIs: {ws[:4]}")
    picks["shared_cases"] = [
        {"case_id": cid, "unlocked": cid in unlocked, "wis": ws} for cid, ws in shared[:10]
    ]

    # ── 5. cases rows carrying an appeal on the SAME row (corpus-01)
    hr("5 · cases rows with appeal_* populated (corpus-01) — prefer unlocked")
    cols = ("id, case_ref, case_number, court, court_level, appeal_judgment_number, "
            "appeal_court, appeal_ruling, appeal_result, summary")
    got = []
    for cid in sorted(unlocked):
        row = (sb.table("cases").select(cols).eq("id", cid).limit(1).execute()).data or []
        if row and (row[0].get("appeal_ruling") or row[0].get("appeal_judgment_number")):
            got.append(row[0])
    for r in got:
        print(f"  {r['id']} case_number={r.get('case_number')} court={short(str(r.get('court')),40)}")
        print(f"      appeal_judgment_number={r.get('appeal_judgment_number')} appeal_court={short(str(r.get('appeal_court')),40)} appeal_result={short(str(r.get('appeal_result')),60)}")
        print(f"      summary: {short(r.get('summary') or '', 150)}")
    picks["unlocked_cases_with_appeal"] = [
        {k: (short(str(v), 300) if k == "summary" else v) for k, v in r.items()} for r in got
    ]
    if not got:
        print("  none of the 17 unlocked rulings carries an appeal — corpus-01 would need a fresh one")

    doc["survey"] = picks
    flush(doc)
    print("\nflushed survey → adv_family_results.json")


if __name__ == "__main__":
    main()
