"""PART B — Case-C matching (``casec-*``), after the structural pre-check passed.

Two drivers, chosen per probe by where the money is:

* **full turn** (``run_simple_search``) wherever every ruling that could be
  opened is already unlocked, or no ruling is involved at all — B1, B1b, B2, B4;
* **searcher-direct** (``agent.run``, identity only, no unfold, no publish, no
  charge) for **B3/casec-04**, whose WI cites five rulings of which only one is
  unlocked. casec-04's expectation is *"the Case-C join works over the writing
  WI's copied refs"* — a property of the candidate list, which the searcher's
  own selection settles. Running the full turn there would put a real unlock on
  a coin-flip, and the §13h fixture is explicit that the untested thing is the
  join, not the answer.

Every probe grades the selection back to the **panel's ``[n]``**, because that
is the number the user is reading when they say «رقم 3» or «الثاني».
"""
from __future__ import annotations

import argparse
import asyncio
import json

from adv_family_common import (  # noqa: E402
    USER_ID, hr, ledger, load, record, service_client, short,
)

from pydantic_ai import DeferredToolRequests  # noqa: E402

from agents.simple_search.prompts import build_searcher_user_message  # noqa: E402
from agents.simple_search.runner import run_simple_search  # noqa: E402
from agents.simple_search.searcher import (  # noqa: E402
    SEARCHER_LIMITS, SearcherDecision, SearcherDeps, collect_case_c_candidates,
    create_searcher_agent, identity_key,
)

WI_3REFS = "ba624c3e-7b10-4ef5-bbae-31835f560bb7"      # 3 regs, ns 1,2,3
WI_GAPPY = "a79718f1-6abe-4406-8bc1-8f37e6e4a7bb"      # 4 regs, ns 4,5,7,11 — NO [3]
WI_WRITING = "639cab9b-efa4-4b22-99d9-8dee1ca555b8"    # agent_writing, 5 cases
WI_SHARING = "06c898ee-8284-4f4c-9bf3-19887d6c846c"    # also cites dac45545
SHARED_CASE = "dac45545-4506-4da7-8bc6-053156b2d3b7"   # UNLOCKED since 2026-08


def panel_map(sb, wi_ids: list[str]) -> dict[str, list[str]]:
    """identity_key → the ``[n]`` tags that identity carries on the panels."""
    out: dict[str, list[str]] = {}
    for obj, preview in collect_case_c_candidates(sb, wi_ids):
        tag = preview[:preview.index("]") + 1] if preview.startswith("[") else "?"
        out.setdefault(identity_key(obj), []).append(tag)
    return out


async def full_turn(sb, doc, pid, *, fixture, question, wis, expect):
    before = ledger(sb, "judgment")
    before_ids = {str(r["unlock_id"]) for r in before}
    hr(f"{pid} · {fixture}")
    print(f"Q: {question}\nattached: {wis}\nexpect: {expect}")
    err, res = None, None
    try:
        res = await run_simple_search(
            question, sb, USER_ID, doc["scratch_conversation_id"], None,
            attached_items=[{"item_id": w} for w in wis],
            recent_messages=[], user_preferences={}, emit_sse=None,
        )
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
    after = ledger(sb, "judgment")
    after_ids = {str(r["unlock_id"]) for r in after}
    # Set difference, not a count difference — lane 3 shares this ledger.
    new_rows = [r for r in after if str(r["unlock_id"]) not in before_ids]
    vanished = sorted(before_ids - after_ids)
    payload = {"fixture": fixture, "question": question, "attached": wis,
               "expect": expect, "driver": "run_simple_search", "error": err,
               "ledger": {"before": len(before), "after": len(after),
                          "delta": len(after) - len(before), "new_rows": new_rows,
                          "vanished_not_mine": vanished}}
    if res is not None:
        payload["result"] = {
            "aborted": res.aborted, "abort_reason": res.abort_reason,
            "paused": res.paused, "question_text": res.question_text,
            "n_chat_messages": len(res.chat_messages),
            "n_created_items": len(res.created_item_ids),
            "created_item_ids": list(res.created_item_ids),
            "chat_messages": list(res.chat_messages)}
        print(f"→ aborted={res.aborted} paused={res.paused} "
              f"replies={len(res.chat_messages)} cards={len(res.created_item_ids)} "
              f"Δunlocks={len(after)-len(before)}")
        for i, m in enumerate(res.chat_messages, 1):
            print(f"   reply {i} ({len(m)} chars): {short(m, 420)}")
        if res.question_text:
            print(f"   ask_user: {short(res.question_text, 320)}")
        # which panel refs did the published cards actually cite?
        cited = []
        for iid in res.created_item_ids:
            rows = (sb.table("workspace_item_references")
                    .select("n, domain, item_id, ref_id").eq("wi_id", iid)
                    .order("n").execute()).data or []
            cited.append({"card": iid, "refs": rows})
        payload["published_refs"] = cited
        print(f"   published refs: {json.dumps(cited, ensure_ascii=False)[:500]}")
    else:
        print(f"→ ERROR {err}")
    if new_rows:
        print(f"   !! NEW LEDGER ROWS: {json.dumps(new_rows, ensure_ascii=False)}")
    return payload


async def searcher_direct(sb, doc, pid, *, fixture, question, wis, expect):
    """Identity only — no unfold, no publish, nothing metered."""
    hr(f"{pid} · {fixture}")
    print(f"Q: {question}\nattached: {wis}\nexpect: {expect}")
    deps = SearcherDeps(supabase=sb, user_id=USER_ID,
                        conversation_id=doc["scratch_conversation_id"])
    handle_key: dict[str, str] = {}
    handle_tag: dict[str, str] = {}
    for obj, preview in collect_case_c_candidates(sb, wis):
        h = deps.register_candidate(obj, preview)
        handle_key[h] = identity_key(obj)
        handle_tag[h] = preview[:preview.index("]") + 1] if preview.startswith("[") else "?"
    agent = create_searcher_agent()
    out = (await agent.run(build_searcher_user_message(question), deps=deps,
                           usage_limits=SEARCHER_LIMITS)).output
    payload = {"fixture": fixture, "question": question, "attached": wis,
               "expect": expect, "driver": "searcher-direct (no unfold, no charge)",
               "n_candidates": len(deps.candidates)}
    if isinstance(out, DeferredToolRequests):
        q = ""
        for call in getattr(out, "calls", None) or []:
            a = getattr(call, "args", None)
            q = str(a.get("question")) if isinstance(a, dict) and a.get("question") else (a if isinstance(a, str) else q)
        payload["outcome"] = {"kind": "ask_user", "question": q}
        print(f"→ ask_user: {short(q, 320)}")
    else:
        assert isinstance(out, SearcherDecision)
        keys = [identity_key(o) for o in out.resolved]
        tags = [handle_tag.get(h, "?") for h in out.selected]
        payload["outcome"] = {"kind": "decision", "aborted": out.aborted,
                              "abort_reason": out.abort_reason,
                              "selected": list(out.selected),
                              "selected_panel_tags": tags,
                              "data_type": out.data_type,
                              "rationale": out.rationale,
                              "resolved_identity_keys": keys,
                              "resolved": [o.model_dump() for o in out.resolved]}
        print(f"→ decision aborted={out.aborted} data_type={out.data_type}")
        print(f"   selected={out.selected} → panel tags {tags}")
        print(f"   resolved identity keys: {keys}")
        print(f"   rationale: {short(out.rationale or '', 300)}")
    return payload


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    sb = service_client()
    doc = load()
    scratch = doc["scratch_wi"]["wi_id"]

    async def run(pid, fn, **kw):
        if only and pid not in only:
            return
        payload = await fn(sb, doc, pid, **kw)
        record(pid, payload | {"verdict": "SEE-DETAIL"})

    await run("B1-casec-01", full_turn,
              fixture="casec-01 — «افتح المصدر رقم 3» must land on the [3] preview",
              question="افتح المصدر رقم 3",
              wis=[WI_3REFS],
              expect="the [3] candidate: نظام مكافحة جرائم المعلوماتية — المادة الثامنة–السادسة عشرة")

    await run("B1b-casec-01-gappy", full_turn,
              fixture="casec-01 control — a card whose panel prints [4][5][7][11], no [3]",
              question="افتح المصدر رقم 3",
              wis=[WI_GAPPY],
              expect="say there is no [3] / ask — NOT the third candidate in internal order")

    await run("B2-casec-03", full_turn,
              fixture="casec-03 — «افتح الحكم الثاني في القائمة» (both rulings pre-unlocked)",
              question="افتح الحكم الثاني في القائمة",
              wis=[scratch],
              expect="the panel's [2] (التأميني) or ask_user — never a silent guess")

    await run("B3-casec-04", searcher_direct,
              fixture="casec-04 — agent_writing WI, refs COPIED from a search WI",
              question="اش الحكم اللي في المذكرة؟",
              wis=[WI_WRITING],
              expect="the copied case rows are candidates at all; 5 rulings ⇒ ask_user is correct")

    await run("B4-casec-05", full_turn,
              fixture="casec-05 — the SAME ruling cited by two attached WIs",
              question="اعطيني تفاصيل حكم ورثة وكالة عامر للسفر والسياحة",
              wis=[WI_SHARING, WI_WRITING],
              expect="one document after dedup → ONE synthesizer, ONE card, Δunlocks 0 (pre-unlocked)")


if __name__ == "__main__":
    asyncio.run(main())
