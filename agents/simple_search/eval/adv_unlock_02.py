"""unlock-02 — the SILENT multi-unlock. One sentence, three unlocks, no mention.

§13h: *"on /judgments each unlock is an explicit click; here one sentence spends
three with no acknowledgment."* The fixture is CONFIRMED-by-code for the charge;
what this probe measures live is the **acknowledgment**, which is the half that
decides whether this is acceptable product behaviour.

Real models end to end — the searcher really picks the three rulings out of the
WI's ten references (5 regulations · 3 cases · 2 compliance), and three real
synthesizers really write the replies. Nothing about the wording is scripted,
because the wording IS the finding.

Target: WI ``b592d479`` «السند النظامي لرفض نقل العامل لشركة أخرى», whose three
cited rulings are **none of them** among the account's 17 existing unlocks — so
every row this probe creates is attributable to it.

Ledger rows are flushed to the results JSON at the instant of the unfold that
caused them, then deleted in the ``finally`` and the ledger re-verified.

    .venv/Scripts/python.exe agents/simple_search/eval/adv_unlock_02.py
"""
from __future__ import annotations

import asyncio
import json

from adv_ms_common import (  # noqa: E402
    USER_ID, WI_THREE_CASES, WI_THREE_RULINGS, WI_THREE_TITLE, flush_probe, hr,
    ledger, ledger_fingerprint, load, record_ledger_row, record_workspace_items,
    save, scratch_id, service_client, short,
)

from agents.simple_search import runner as R  # noqa: E402

FIXTURE = "unlock-02"

#: The fixture's verbatim query. Run FIRST — and it does not survive contact:
#: the searcher cannot resolve the «WI-N» alias (its candidates are labelled
#: C1…Cn) and pauses to ask what WI-2 means. Recorded as `attempt_1`, because a
#: Case-C phrasing the user reads off their own panel failing to resolve is
#: itself a finding — it is just not the MONEY finding this fixture is for.
QUESTION_VERBATIM = "اعطيني تفاصيل الأحكام الثلاثة اللي في WI-2"

#: The same request without the alias, so the money edge can actually be
#: measured. Still one sentence, still Case C, still real models.
QUESTION = "اعطيني تفاصيل الأحكام الثلاثة المذكورة في التقرير المرفق"

#: Words that would constitute ACKNOWLEDGING a spend — a consumed balance, not
#: merely "here is the ruling". Deliberately narrow: «فتح» alone is counted
#: separately below, because «فتحت لك الحكم» is not a statement about cost.
SPEND_WORDS = ["رصيد", "الرصيد", "استهلك", "استهلاك", "خصم", "خُصم", "نقطة",
               "نقاط", "حصة", "متبقٍ", "متبقي", "unlock", "من رصيدك"]
OPEN_WORDS = ["فتح", "فُتح", "فتحت"]


def ids(rows: list[dict]) -> set[str]:
    return {str(r["unlock_id"]) for r in rows}


async def main() -> int:
    sb = service_client()
    convo = scratch_id()

    hr(f"{FIXTURE} — «{QUESTION}»")
    print(f"scratch conversation: {convo}")
    print(f"attached WI: {WI_THREE_RULINGS} — «{WI_THREE_TITLE}»")
    print("  refs on the card: 10 total (5 regulations · 3 cases · 2 compliance)")
    for cid, cref, court in WI_THREE_CASES:
        print(f"  ruling: {cid} · {cref} · {court}")

    base = ledger_fingerprint(sb)
    base_j = ledger(sb, "judgment")
    print(f"\nledger before: total {base['total']} · judgment {len(base_j)}")

    # ── Watch every unfold; flush every row the instant it appears ───────────
    real_unfold = R.unfold
    unfold_log: list[dict] = []

    async def _watched_unfold(supabase, obj, *, judgment_access=None):
        before = ledger(supabase, "judgment")
        res = await real_unfold(supabase, obj, judgment_access=judgment_access)
        after = ledger(supabase, "judgment")
        new = [r for r in after if str(r["unlock_id"]) not in ids(before)]
        for r in new:
            record_ledger_row(str(r["unlock_id"]), content_id=str(r["content_id"]),
                              fixture=FIXTURE, surface=r.get("surface"),
                              unlocked_at=str(r.get("unlocked_at")))
        unfold_log.append({
            "level": obj.level, "case_id": obj.case_id or "",
            "case_ref": obj.case_ref or "", "ok": res.ok, "chars": res.chars,
            "notes": list(res.notes), "ledger_delta": len(new),
            "new_unlock_ids": [str(r["unlock_id"]) for r in new],
            "new_content_ids": [str(r["content_id"]) for r in new],
        })
        print(f"  unfold[{len(unfold_log)}] {obj.level} {obj.case_ref or obj.case_id} "
              f"→ ok={res.ok} chars={res.chars} Δledger={len(new)} notes={res.notes}")
        return res

    attached = [{
        "item_id": WI_THREE_RULINGS, "kind": "agent_search",
        "title": WI_THREE_TITLE, "metadata": {},
    }]

    result = None
    try:
        R.unfold = _watched_unfold  # type: ignore[assignment]
        hr("RUN — real searcher, real synthesizers, real ledger")
        result = await R.run_simple_search(
            QUESTION, sb, USER_ID, convo, None,
            attached_items=attached, recent_messages=[],
        )
        record_workspace_items(list(result.created_item_ids), fixture=FIXTURE)
    finally:
        R.unfold = real_unfold  # type: ignore[assignment]

    # ── Ledger accounting ───────────────────────────────────────────────────
    hr("LEDGER")
    after_j = ledger(sb, "judgment")
    turn_new = [r for r in after_j if str(r["unlock_id"]) not in ids(base_j)]
    charged = {str(r["content_id"]) for r in turn_new}
    expected = {c[0] for c in WI_THREE_CASES}
    print(f"judgment rows: {len(base_j)} → {len(after_j)}  (Δ +{len(turn_new)})")
    for r in turn_new:
        known = "expected" if str(r["content_id"]) in expected else "UNEXPECTED"
        print(f"  NEW {r['unlock_id']} · {r['content_id']} · {known}")
    print(f"charged == the WI's three rulings: {charged == expected}")

    # ── The acknowledgment question ─────────────────────────────────────────
    hr("THE REPLY — does ANY of it say three unlocks were spent?")
    replies = list(result.chat_messages)
    print(f"chat replies: {len(replies)} · workspace items: {len(result.created_item_ids)}")
    print(f"paused={result.paused} aborted={result.aborted} "
          f"abort_reason={result.abort_reason!r}")
    joined = "\n\n".join(replies)
    for i, m in enumerate(replies, 1):
        print(f"\n--- reply {i} ({len(m)} chars) ---")
        print(short(m, 700))

    spend_hits = sorted({w for w in SPEND_WORDS if w in joined})
    open_hits = sorted({w for w in OPEN_WORDS if w in joined})
    counts_three = any(t in joined for t in ("ثلاثة أحكام", "الأحكام الثلاثة",
                                             "ثلاثة مصادر", "٣ أحكام"))
    print(f"\nspend-acknowledgment words present: {spend_hits or 'NONE'}")
    print(f"  (bare 'open' words, not a cost statement): {open_hits or 'none'}")
    print(f"  says 'three rulings' anywhere: {counts_three}")

    acknowledged = bool(spend_hits)
    checks = [
        {"id": "three_unlocks", "want": "exactly 3 new ledger rows",
         "ok": len(turn_new) == 3},
        {"id": "correct_rulings", "want": "the 3 rows are the WI's 3 rulings",
         "ok": charged == expected},
        {"id": "one_message_spent_three", "want":
            "a single user sentence caused all 3", "ok": len(turn_new) == 3},
        {"id": "silence_confirmed", "want":
            "NO reply text acknowledges the spend (the finding)",
         "ok": not acknowledged},
    ]
    hr("VERDICT")
    for c in checks:
        print(f"  [{'CONFIRMED' if c['ok'] else 'NOT-CONFIRMED'}] {c['id']:<26} — {c['want']}")

    flush_probe(
        FIXTURE,
        verdict=("CONFIRMED — 3 unlocks spent by one sentence, no reply mentions it"
                 if len(turn_new) == 3 and not acknowledged
                 else f"PARTIAL — delta={len(turn_new)} acknowledged={acknowledged}"),
        question=QUESTION,
        question_verbatim_attempt_1=QUESTION_VERBATIM,
        attempt_1_outcome=(
            "PAUSED — the searcher could not resolve the alias «WI-2» and called "
            "ask_user: «ما المقصود بـ WI-2؟ ... لم أجد هذا المرجع في المصادر "
            "المرفقة الحالية.» Zero ledger writes. Pause row "
            "76a293b8-b8b2-4b0b-ac68-0f2059562a92, deleted."
        ),
        models="REAL (searcher + 3 synthesizers)",
        attached_wi=WI_THREE_RULINGS,
        attached_wi_title=WI_THREE_TITLE,
        wi_ref_mix={"regulations": 5, "cases": 3, "compliance": 2},
        unfolds=unfold_log,
        ledger_before_judgment=len(base_j),
        ledger_after_judgment=len(after_j),
        ledger_delta=len(turn_new),
        charged_case_ids=sorted(charged),
        expected_case_ids=sorted(expected),
        paused=result.paused, aborted=result.aborted,
        abort_reason=result.abort_reason,
        chat_messages=replies,
        created_item_ids=list(result.created_item_ids),
        acknowledgment={
            "spend_words_found": spend_hits,
            "open_words_found": open_hits,
            "says_three_rulings": counts_three,
            "acknowledged_spend": acknowledged,
        },
        checks=checks,
    )

    # ── CLEANUP ─────────────────────────────────────────────────────────────
    hr("CLEANUP")
    doc = load()
    mine = [r for r in doc["ledger_rows_created"]
            if r["fixture"] == FIXTURE and not r["deleted"]]
    print(f"rows to delete: {[r['unlock_id'] for r in mine] or 'none'}")
    for r in mine:
        sb.table("library_unlocks").delete().eq("unlock_id", r["unlock_id"]).eq(
            "content_id", r["content_id"]).execute()
        r["deleted"] = True
        save(doc)
        print(f"  deleted {r['unlock_id']} (flushed)")

    final = ledger_fingerprint(sb)
    restored = final["ids"] == base["ids"]
    print(f"\nledger now {final['total']} rows (baseline {base['total']}) · "
          f"byte-identical: {restored}")

    doc = load()
    for p in doc["probes"]:
        if p["fixture"] == FIXTURE:
            p["cleanup"] = {"rows_deleted": [r["unlock_id"] for r in mine],
                            "ledger_restored": restored,
                            "final_total": final["total"],
                            "final_judgment": final["by_content_type"].get("judgment")}
    save(doc)
    print(json.dumps({"delta": len(turn_new), "acknowledged": acknowledged,
                      "restored": restored}, ensure_ascii=False))
    return 0 if restored else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
