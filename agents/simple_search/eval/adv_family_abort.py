"""PART A — the §13g abort guard, live for the first time, BOTH directions.

The guard was shipped with two unit tests and never run against real models.
Four probes, all through the REAL ``run_simple_search``:

===== ================================================================ ==========
 A1    hair-01 «اش الحكمين اللي في WI-2؟»            OVER-fire probe    fan out 2
 A2    «قارن الحكمين اللي في WI-2» (router miss)     UNDER-fire probe   ABORT
 A3    corpus-01 «الحكم الابتدائي والاستئنافي»       phantom plurality  1 document
 A4    hair-03 «قارن المادة 77 بالمادة 78»           same-doc compare   no abort
===== ================================================================ ==========

A1–A3 attach the lane's scratch WI, whose two rulings are **already unlocked**,
so the whole of part A is designed to move the ledger by **zero**. That is also
an assertion: A2 aborting correctly means no unfold ran, and the ledger is
checked before and after every single probe to prove it — an abort that fanned
out anyway would show up as a charge even if the returned object looked clean.

A4 is Case A over regulations (unmetered by D12) and touches nothing.
"""
from __future__ import annotations

import argparse
import asyncio
import json

from adv_family_common import (  # noqa: E402
    USER_ID, hr, ledger, load, record, service_client, short,
)

from agents.simple_search.runner import run_simple_search  # noqa: E402


async def probe(sb, doc, pid: str, *, fixture: str, question: str,
                attach_wi: str | None, expect: str, recent=None) -> dict:
    """Run ONE turn and flush its result before returning. Never batched."""
    before = ledger(sb, "judgment")
    before_ids = {str(r["unlock_id"]) for r in before}
    items = [{"item_id": attach_wi}] if attach_wi else []

    hr(f"{pid} · {fixture}")
    print(f"Q: {question}")
    print(f"attached: {attach_wi or '(none — case A)'} · expect: {expect}")

    err = None
    try:
        res = await run_simple_search(
            question, sb, USER_ID, doc["scratch_conversation_id"], None,
            attached_items=items, recent_messages=recent or [],
            user_preferences={}, emit_sse=None,
        )
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        res = None

    after = ledger(sb, "judgment")
    after_ids = {str(r["unlock_id"]) for r in after}
    # Set difference, not a count difference: lane 3 shares this ledger and is
    # running concurrently, so the COUNT drifts under us (a -3 was observed).
    # Only rows whose unlock_id is new are rows THIS probe could have caused.
    new_rows = [r for r in after if str(r["unlock_id"]) not in before_ids]
    vanished = sorted(before_ids - after_ids)

    payload: dict = {
        "fixture": fixture, "question": question, "attached": attach_wi,
        "expect": expect, "error": err,
        "ledger": {"judgment_before": len(before), "judgment_after": len(after),
                   "delta": len(after) - len(before),
                   "new_rows": new_rows,
                   "vanished_not_mine": vanished},
    }
    if res is not None:
        payload["result"] = {
            "aborted": res.aborted, "abort_reason": res.abort_reason,
            "paused": res.paused, "question_text": res.question_text,
            "n_chat_messages": len(res.chat_messages),
            "n_created_items": len(res.created_item_ids),
            "created_item_ids": list(res.created_item_ids),
            "chat_messages": list(res.chat_messages),
        }
        print(f"→ aborted={res.aborted!r} reason={res.abort_reason!r} "
              f"paused={res.paused!r}")
        print(f"→ replies={len(res.chat_messages)} cards={len(res.created_item_ids)} "
              f"Δjudgment-unlocks={len(after) - len(before)}")
        for i, m in enumerate(res.chat_messages, 1):
            print(f"   reply {i} ({len(m)} chars): {short(m, 400)}")
        if res.question_text:
            print(f"   ask_user: {short(res.question_text, 300)}")
    else:
        print(f"→ ERROR {err}")
    if new_rows:
        print(f"   !! NEW LEDGER ROWS: {json.dumps(new_rows, ensure_ascii=False)}")
    return payload


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma-separated probe ids")
    args = ap.parse_args()
    only = {s.strip() for s in args.only.split(",") if s.strip()}

    sb = service_client()
    doc = load()
    wi = doc["scratch_wi"]["wi_id"]

    plan = [
        ("A1-hair-01", dict(
            fixture="hair-01 — OVER-fire: two independent lookups must FAN OUT",
            question="اش الحكمين اللي في WI-2؟",
            attach_wi=wi,
            expect="NOT aborted; 2 synthesizers → 2 replies; Δunlocks 0 (both pre-unlocked)")),
        ("A2-abort-direct", dict(
            fixture="§13g UNDER-fire: «قارن الحكمين» fed straight to the searcher",
            question="قارن الحكمين اللي في WI-2",
            attach_wi=wi,
            expect="aborted=True with a reason; 0 replies; 0 cards; Δunlocks 0")),
        ("A3-corpus-01", dict(
            fixture="corpus-01 — appeal lives on the SAME cases row",
            question="اش قال الحكم الابتدائي والاستئنافي في قضية ورثة وكالة عامر للسفر والسياحة؟",
            attach_wi=wi,
            expect="ONE document, ONE synthesizer, no phantom-plurality abort")),
        ("A4-hair-03", dict(
            fixture="hair-03 searcher side — same-document comparison",
            question="قارن المادة 77 بالمادة 78 من نظام العمل",
            attach_wi=None,
            expect="no abort; D5 groups both مواد into ONE synthesizer")),
    ]

    for pid, kw in plan:
        if only and pid not in only:
            continue
        payload = await probe(sb, doc, pid, **kw)
        record(pid, payload | {"verdict": "SEE-DETAIL"})


if __name__ == "__main__":
    asyncio.run(main())
