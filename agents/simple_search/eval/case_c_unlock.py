"""Unlock accounting for the Case-C ruling path — §7.3 / D12. **LIVE ledger.**

D12: "Ruling analysis consumes the ungating; regulations are not metered."
§7.3: "A ruling opened by simple_search spends the SAME single unlock the
/judgments page uses, so a user who unlocked it there does not pay twice."

**Re-pointed 2026-08-16 (Case-C re-run).** The first cut of this script called
``unfold(sb, obj)`` with **no resolver** and claimed in its own docstring to be
running "the exact call ``_synthesize_group`` makes". Both halves went stale the
moment the D12 charge landed: ``_synthesize_group`` now passes
``judgment_access=judgment_access_resolver(supabase, user_id)``, and a bare
``unfold`` returns the *refusal*, not the body — so the script reported
"NOT CHARGED (0 unlocks)" for a path that charges correctly. It now wires the
real resolver and asserts the five properties the fix claims, in one pass:

===== ================================================================= =======
  A   no resolver at all                          → refusal, no body     Δ 0
  B   a ruling this user has NEVER unlocked       → body served          Δ +1
  C   the SAME ruling immediately again           → body served          Δ 0
  D   a ruling already unlocked on /judgments     → body served, free    Δ 0
  E   a نظام chunk (D12: regulations unmetered)   → body served          Δ 0
===== ================================================================= =======

**This script writes to the live ledger** (that is the only way to measure a
charge). Exactly one row — B's — is new, and the ``finally`` block deletes it and
re-verifies the ledger against the snapshot taken at start. D's pre-existing row
is asserted byte-identical afterwards, never touched.

    .venv/Scripts/python.exe agents/simple_search/eval/case_c_unlock.py
"""
from __future__ import annotations

import asyncio
import inspect
import json

from case_c_common import USER_ID, hr, service_client, short  # noqa: E402

from agents.simple_search.models import ResolvedObject  # noqa: E402
from agents.simple_search.runner import (  # noqa: E402
    _synthesize_group,
    judgment_access_resolver,
)
from agents.simple_search.searcher import collect_case_c_candidates  # noqa: E402
from agents.simple_search.unfold import unfold  # noqa: E402

WI_CASES = "91680d79-b236-411b-a7d3-ef5f5c15453f"
WI_IDENT = "24b01fd8-4eae-4917-a21f-1b20f5e75f9b"

#: B — the exact ruling the 2026-08-16 baseline measured at "17 before, 17 after"
#: (WI 91680d79 · n=10). Same target ⇒ a true before/after on the same row.
TARGET_FRESH = ("7595673f-8d5b-4bbe-acdc-f0e30a5de36e", "17642_fi_4471164070")
#: D — already on this user's shelf since 2026-08-02, unlocked with
#: ``surface='library'`` (i.e. from the /judgments page). §7.3's whole claim.
TARGET_UNLOCKED = ("f02fdba7-503d-4c16-9dfd-ffbf49ec7b70", "17642_fi_4630585068")


def ledger(sb, content_type: str | None = None) -> list[dict]:
    q = (sb.table("library_unlocks")
         .select("unlock_id, content_type, content_id, surface, cost, unlocked_at")
         .eq("user_id", USER_ID))
    if content_type:
        q = q.eq("content_type", content_type)
    return list((q.execute()).data or [])


def ids(rows: list[dict]) -> set[str]:
    return {str(r["unlock_id"]) for r in rows}


def case_body(sb, case_id: str) -> str:
    row = (sb.table("cases").select("content, summary").eq("id", case_id)
           .limit(1).execute()).data or [{}]
    return str(row[0].get("content") or "")


async def main() -> int:
    sb = service_client()
    checks: list[dict] = []
    written: set[str] = set()

    # ── snapshot BEFORE anything ────────────────────────────────────────────
    base_all = ledger(sb)
    base_j = [r for r in base_all if r["content_type"] == "judgment"]
    hr("BASELINE LEDGER")
    print(f"all unlocks: {len(base_all)} · judgment: {len(base_j)} · "
          f"regulation: {sum(1 for r in base_all if r['content_type']=='regulation')}")
    d_row_before = next(
        (r for r in base_j if r["content_id"] == TARGET_UNLOCKED[0]), None)
    print(f"D's pre-existing row: {json.dumps(d_row_before, ensure_ascii=False)}")

    # ── 0. STRUCTURAL — the argument cannot be forgotten ────────────────────
    hr("0 · STRUCTURAL — is a free ruling still reachable by omission?")
    sig = inspect.signature(_synthesize_group)
    p = sig.parameters["judgment_access"]
    kwonly = p.kind is inspect.Parameter.KEYWORD_ONLY
    nodefault = p.default is inspect.Parameter.empty
    print(f"_synthesize_group.judgment_access: kind={p.kind.name} "
          f"default={'<none>' if nodefault else p.default!r}")
    print(f"  keyword-only: {kwonly} · no default: {nodefault} "
          f"→ omitting it is a TypeError: {kwonly and nodefault}")
    checks.append({"id": "struct", "want": "kwonly & no default",
                   "ok": kwonly and nodefault})

    grant = judgment_access_resolver(sb, USER_ID)

    try:
        # ── A. no resolver → refusal, no body, no row ───────────────────────
        hr("A · NO RESOLVER — render_judgment must refuse rather than serve")
        cid, cref = TARGET_FRESH
        content = case_body(sb, cid)
        before = ledger(sb, "judgment")
        res = await unfold(sb, ResolvedObject(level="judgment", case_id=cid,
                                              case_ref=cref))
        after = ledger(sb, "judgment")
        leaked = bool(content) and content[:160].strip()[:100] in res.text
        print(f"ok={res.ok} chars={res.chars} notes={res.notes}")
        print(f"  text: {short(res.text, 200)}")
        print(f"  Δ ledger = {len(after) - len(before)} · body leaked: {leaked}")
        checks.append({"id": "A_refuses", "want": "no body, Δ0",
                       "ok": (not leaked) and len(after) == len(before),
                       "detail": {"ok": res.ok, "notes": res.notes,
                                  "chars": res.chars}})

        # ── B. a never-unlocked ruling → +1 row, body served ────────────────
        hr("B · CHARGE — a ruling this user has never unlocked")
        before = ledger(sb, "judgment")
        res = await unfold(sb, ResolvedObject(level="judgment", case_id=cid,
                                              case_ref=cref),
                           judgment_access=grant)
        after = ledger(sb, "judgment")
        new = [r for r in after if r["unlock_id"] not in ids(before)]
        written |= ids(new)
        served = bool(content) and content[:160].strip()[:100] in res.text
        print(f"ok={res.ok} rung={res.rung} chars={res.chars} notes={res.notes}")
        print(f"  cases.content = {len(content)} chars · full body served: {served}")
        print(f"  Δ ledger = +{len(new)}")
        for r in new:
            print("   NEW " + json.dumps(r, ensure_ascii=False))
        checks.append({"id": "B_charges", "want": "body + exactly +1 row",
                       "ok": served and len(new) == 1,
                       "detail": {"new_rows": new, "notes": res.notes}})

        # ── C. the SAME ruling again → delta 0 ──────────────────────────────
        hr("C · NO DOUBLE CHARGE — the same ruling immediately again")
        before = ledger(sb, "judgment")
        res = await unfold(sb, ResolvedObject(level="judgment", case_id=cid,
                                              case_ref=cref),
                           judgment_access=grant)
        after = ledger(sb, "judgment")
        new2 = [r for r in after if r["unlock_id"] not in ids(before)]
        served = bool(content) and content[:160].strip()[:100] in res.text
        print(f"ok={res.ok} chars={res.chars} notes={res.notes}")
        print(f"  body still served: {served} · Δ ledger = {len(new2)}")
        checks.append({"id": "C_no_double", "want": "body + Δ0",
                       "ok": served and not new2,
                       "detail": {"notes": res.notes}})

        # ── D. already unlocked on /judgments → free, row untouched ─────────
        hr("D · SAME UNLOCK AS /judgments — a ruling bought on the library page")
        ucid, ucref = TARGET_UNLOCKED
        ucontent = case_body(sb, ucid)
        before = ledger(sb, "judgment")
        res = await unfold(sb, ResolvedObject(level="judgment", case_id=ucid,
                                              case_ref=ucref),
                           judgment_access=grant)
        after = ledger(sb, "judgment")
        new3 = [r for r in after if r["unlock_id"] not in ids(before)]
        served = bool(ucontent) and ucontent[:160].strip()[:100] in res.text
        d_row_after = next(
            (r for r in after if r["content_id"] == ucid), None)
        untouched = d_row_after == d_row_before
        print(f"ok={res.ok} chars={res.chars} notes={res.notes}")
        print(f"  body served: {served} · Δ ledger = {len(new3)} · "
              f"existing row byte-identical: {untouched}")
        print(f"  row now: {json.dumps(d_row_after, ensure_ascii=False)}")
        checks.append({"id": "D_free_reuse", "want": "body, Δ0, row untouched",
                       "ok": served and not new3 and untouched,
                       "detail": {"notes": res.notes, "row": d_row_after}})

        # ── E. a نظام → unmetered on every content_type ─────────────────────
        hr("E · REGULATIONS ARE NOT METERED (D12)")
        chunk = next((o for o, _ in collect_case_c_candidates(sb, [WI_IDENT])
                      if o.level == "chunk"), None)
        before_all = ledger(sb)
        res = await unfold(sb, chunk, judgment_access=grant)
        after_all = ledger(sb)
        new4 = [r for r in after_all if r["unlock_id"] not in ids(before_all)]
        print(f"chunk={chunk.chunk_id} reg={chunk.regulation_id} "
              f"doc_type={chunk.doc_type!r}")
        print(f"ok={res.ok} chars={res.chars} notes={res.notes}")
        print(f"  Δ ledger (ALL content_types) = {len(new4)}")
        checks.append({"id": "E_reg_unmetered", "want": "body + Δ0 anywhere",
                       "ok": res.ok and not new4,
                       "detail": {"chars": res.chars, "new_rows": new4}})

        # ── ledger shape ───────────────────────────────────────────────────
        hr("LEDGER SHAPE — the same key /judgments uses")
        all_j = ledger(sb, "judgment")
        surfaces: dict[str, int] = {}
        for r in all_j:
            surfaces[r.get("surface") or "?"] = surfaces.get(r.get("surface") or "?", 0) + 1
        cids = {r["content_id"] for r in all_j}
        matched = (sb.table("cases").select("id").in_("id", list(cids))
                   .execute()).data or []
        print(f"judgment rows: {len(all_j)} · by surface: {surfaces}")
        print(f"  {len(matched)}/{len(cids)} content_ids resolve to a cases.id row")

    finally:
        # ── CLEANUP — remove exactly what B wrote, then re-verify ───────────
        hr("CLEANUP — delete the row(s) this run created")
        # Scoped to TARGET_FRESH's content_id as well as the unlock_id: two other
        # eval lanes are live on this account today, and a row of theirs that
        # happened to land inside B's before/after window must not be deleted by
        # this one. Anything else new is REPORTED, never removed.
        print(f"rows observed as new during B: {sorted(written) or 'none'}")
        for uid in sorted(written):
            sb.table("library_unlocks").delete().eq("unlock_id", uid).eq(
                "content_id", TARGET_FRESH[0]).execute()
        final_all = ledger(sb)
        gone = ids(base_all) - ids(final_all)
        extra = ids(final_all) - ids(base_all)
        restored = not gone and not extra
        print(f"ledger now: {len(final_all)} rows (baseline {len(base_all)})")
        print(f"  baseline rows missing: {sorted(gone) or 'none'}")
        print(f"  rows not in baseline:  {sorted(extra) or 'none'}")
        checks.append({"id": "cleanup", "want": "ledger == baseline",
                       "ok": restored,
                       "detail": {"missing": sorted(gone), "extra": sorted(extra)}})

    hr("VERDICT")
    for c in checks:
        print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['id']:<16} — {c['want']}")
    passed = sum(1 for c in checks if c["ok"])
    print(f"\n{passed}/{len(checks)} checks pass")

    with open("agents/simple_search/eval/case_c_unlock_results.json", "w",
              encoding="utf-8") as fh:
        json.dump({"baseline_rows": len(base_all), "checks": checks,
                   "rows_written_then_deleted": sorted(written)},
                  fh, ensure_ascii=False, indent=2, default=str)
    print("dump → agents/simple_search/eval/case_c_unlock_results.json")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
