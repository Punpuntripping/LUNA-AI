"""B's mandated pre-check: ``collect_case_c_candidates`` on the chosen WIs.

**No LLM, no unfold, no money.** Verifies, before any model call is spent:

* the ``[n]`` prefix that landed on 2026-08-16 is really on every preview, and
  that the ``[n]`` printed matches the ref row's own ``n`` (casec-01, casec-03);
* whether the collector produces candidates at all for an ``agent_writing`` WI
  whose ref rows were **copied** from a search WI (casec-04 — the code path the
  fixture says has never been exercised);
* whether the SAME ruling cited by TWO attached WIs collapses to one candidate
  or arrives twice (casec-05's identity-dedup claim);
* what the panel's ordinal position actually is when ``n`` is not 1..k
  (casec-03's "candidate order ≠ panel order" hazard).
"""
from __future__ import annotations

import json

from adv_family_common import hr, load, record, service_client, short  # noqa: E402

from agents.simple_search.runner import document_key, group_documents  # noqa: E402
from agents.simple_search.searcher import (  # noqa: E402
    SearcherDeps, collect_case_c_candidates, identity_key,
)

# free (zero cases) 3-ref WI — «افتح المصدر رقم 3» has a real [3] to land on
WI_3REFS = "ba624c3e-7b10-4ef5-bbae-31835f560bb7"
# 4 regs whose ns are 4,5,7,11 — there is NO [3] on this card at all
WI_GAPPY = "a79718f1-6abe-4406-8bc1-8f37e6e4a7bb"
# the only agent_writing WI carrying case refs (9 regs + 5 cases + 3 خدمات)
WI_WRITING = "639cab9b-efa4-4b22-99d9-8dee1ca555b8"
# the OTHER WI citing the same (already-unlocked) dac45545 ruling
WI_SEARCH_SHARING = "06c898ee-8284-4f4c-9bf3-19887d6c846c"
SHARED_CASE = "dac45545-4506-4da7-8bc6-053156b2d3b7"


def dump(sb, wi_ids: list[str], limit: int = 40):
    cands = collect_case_c_candidates(sb, wi_ids)
    for obj, preview in cands[:limit]:
        print(f"  {obj.level:14s} {short(preview, 150)}")
    if len(cands) > limit:
        print(f"  … +{len(cands) - limit} more")
    return cands


def ref_rows(sb, wi_id: str):
    return (sb.table("workspace_item_references")
            .select("n, domain, item_id, ref_id, used").eq("wi_id", wi_id)
            .eq("used", True).order("n").execute()).data or []


def main() -> None:
    sb = service_client()
    doc = load()
    scratch_wi = doc["scratch_wi"]["wi_id"]

    # ── 1. [n] prefix present, and equal to the ref row's own n ──────────────
    hr("S1 · [n] prefix — casec-01's fix, verified structurally")
    findings = {}
    for label, wi in (("3refs", WI_3REFS), ("gappy", WI_GAPPY),
                      ("scratch2rulings", scratch_wi)):
        rows = ref_rows(sb, wi)
        want_ns = [int(r["n"]) for r in rows]
        cands = dump(sb, [wi])
        got_ns = []
        for _, p in cands:
            got_ns.append(int(p[1:p.index("]")]) if p.startswith("[") and "]" in p else -1)
        ok = got_ns == want_ns
        print(f"  {label} {wi}: ref ns={want_ns} preview ns={got_ns} → "
              f"{'MATCH' if ok else 'MISMATCH'}")
        findings[label] = {"wi": wi, "ref_ns": want_ns, "preview_ns": got_ns,
                           "prefixed": all(n > 0 for n in got_ns), "match": ok,
                           "n_candidates": len(cands)}
    findings["gappy_note"] = (
        "WI a79718f1 prints [4][5][7][11] — «المصدر رقم 3» has no counterpart on "
        "this card at all; the only correct answer is to say so / ask."
    )
    record("struct-n-prefix", {
        "fixture": "casec-01 (structural half)",
        "verdict": "PASS" if all(v["match"] for k, v in findings.items()
                                 if isinstance(v, dict)) else "FAIL",
        "detail": findings,
    })

    # ── 2. panel ordinal vs candidate order (casec-03) ───────────────────────
    hr("S2 · candidate order vs panel order (casec-03)")
    rows = ref_rows(sb, scratch_wi)
    cands = collect_case_c_candidates(sb, [scratch_wi])
    order = [(i + 1, p[:p.index("]") + 1]) for i, (_, p) in enumerate(cands)]
    aligned = all(f"[{rows[i]['n']}]" == tag for i, (_, tag) in enumerate(order))
    print(f"  scratch WI: candidate slot → panel tag = {order} · aligned={aligned}")
    record("struct-panel-order", {
        "fixture": "casec-03 (structural half)",
        "verdict": "PASS" if aligned else "FAIL",
        "detail": {"wi": scratch_wi, "slot_to_tag": order, "aligned": aligned,
                   "note": "on THIS card n==slot, so an ordinal pick cannot be "
                           "told apart from a panel pick; the hazard only shows "
                           "on gappy cards like a79718f1 ([4][5][7][11])."},
    })

    # ── 3. agent_writing copied refs (casec-04) ──────────────────────────────
    hr("S3 · agent_writing WI — do the COPIED ref rows join? (casec-04)")
    rows = ref_rows(sb, WI_WRITING)
    print(f"  ref rows (used): {len(rows)} · domains="
          f"{json.dumps({d: sum(1 for r in rows if r['domain'] == d) for d in {r['domain'] for r in rows}}, ensure_ascii=False)}")
    nulls = [r for r in rows if not r.get("item_id")]
    print(f"  NULL item_id rows: {len(nulls)}")
    cands = dump(sb, [WI_WRITING])
    case_c = [(o, p) for o, p in cands if o.level == "judgment"]
    want_cases = sum(1 for r in rows if r["domain"] == "cases")
    ok = len(cands) == len(rows) and len(case_c) == want_cases
    print(f"  candidates={len(cands)} of {len(rows)} rows · "
          f"judgment candidates={len(case_c)} of {want_cases} case rows → "
          f"{'JOIN WORKS' if ok else 'JOIN LOSES ROWS'}")
    record("struct-writing-refs", {
        "fixture": "casec-04 (structural half)",
        "verdict": "PASS" if ok else "FAIL",
        "detail": {"wi": WI_WRITING, "used_rows": len(rows),
                   "null_item_id": len(nulls), "candidates": len(cands),
                   "judgment_candidates": len(case_c), "case_rows": want_cases,
                   "previews": [short(p, 140) for _, p in case_c]},
    })

    # ── 4. dedup across two WIs citing ONE ruling (casec-05) ─────────────────
    hr("S4 · same ruling in TWO WIs — one candidate or two? (casec-05)")
    pair = [WI_SEARCH_SHARING, WI_WRITING]
    cands = collect_case_c_candidates(sb, pair)
    dupes = [(o, p) for o, p in cands
             if o.level == "judgment" and str(o.case_id) == SHARED_CASE]
    print(f"  total candidates over both WIs: {len(cands)}")
    print(f"  candidates for the shared ruling {SHARED_CASE}: {len(dupes)}")
    for o, p in dupes:
        print(f"    identity_key={identity_key(o)} document_key={document_key(o)}")
        print(f"      {short(p, 140)}")
    # what would the FAN-OUT do if the searcher handed back both?
    groups = group_documents([o for o, _ in dupes])
    print(f"  group_documents([both]) → {len(groups)} group(s) "
          f"→ {len(groups)} synthesizer(s), {len(groups)} unlock attempt(s)")
    # and would the deps register them as two handles?
    deps = SearcherDeps(supabase=sb, user_id="x", conversation_id="x")
    handles = [deps.register_candidate(o, p) for o, p in dupes]
    print(f"  register_candidate handles: {handles}")
    record("struct-dedup", {
        "fixture": "casec-05 (structural half)",
        "verdict": ("PASS-collapses-at-fanout" if len(groups) == 1 else "FAIL"),
        "detail": {
            "wis": pair, "shared_case": SHARED_CASE,
            "candidate_rows_for_shared_ruling": len(dupes),
            "distinct_handles": handles,
            "identity_keys": sorted({identity_key(o) for o, _ in dupes}),
            "document_keys": sorted({document_key(o) for o, _ in dupes}),
            "groups_if_both_selected": len(groups),
            "note": ("collect_case_c_candidates does NOT dedup — the ruling is "
                     "offered to the searcher TWICE, under two handles. The "
                     "collapse, if it happens, is group_documents' doing at "
                     "fan-out time, one layer later."),
        },
    })


if __name__ == "__main__":
    main()
