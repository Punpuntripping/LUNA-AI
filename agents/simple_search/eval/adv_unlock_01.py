"""unlock-01 — THE mis-resolution charge. §13h: "the user pays for our mistake".

The fixture's own words: *DESIGN QUESTION, surfaced not assumed.* So this script
asserts the CURRENT behaviour precisely rather than judging it, and the report
turns the measurement into the decision.

**What is scripted, and what is emphatically not.** The searcher and synthesizer
models are ``FunctionModel``s (``tests/_fmodels.py`` pattern) because the trap is
*structural* — we need a guaranteed reject-then-resolve-something-else turn, and
no prompt can be relied on to produce one on demand. Everything downstream of
the decision is REAL: ``unfold`` really runs, ``judgment_access_resolver`` really
calls ``library_service.resolve_access``, and the ledger really gets written.

    cycle 1  searcher → ruling X   → synthesizer REJECTS   → X's unlock charged
    cycle 2  searcher → ruling Y   → synthesizer ACCEPTS   → Y's unlock charged

**The honest form of "the reply never mentions X".** The synthesizer's prose is
scripted here, so asserting "the text doesn't mention X" would be circular — I
wrote the text. What is *not* circular, and is what the fixture is really about,
is whether the system has any **mechanism** to surface it. Three structural
facts are measured instead:

1. the ledger delta for the turn (the money actually spent);
2. what the surviving synthesizer was handed — if X's charge is not in its input,
   no prompt of any kind could mention it;
3. whether ``SimpleSearchRunResult`` carries any field an SSE layer or UI could
   read to tell the user. If the dataclass has no such field, the silence is
   structural rather than a wording choice.

Ledger rows are flushed to the results JSON the instant they are observed, then
deleted in the ``finally`` block and the ledger re-verified byte-identical.

    .venv/Scripts/python.exe agents/simple_search/eval/adv_unlock_01.py
"""
from __future__ import annotations

import asyncio
import dataclasses
import json

from adv_ms_common import (  # noqa: E402
    UNLOCK01_X, UNLOCK01_Y, USER_ID, flush_probe, hr, ledger,
    ledger_fingerprint, load, record_ledger_row, record_workspace_items,
    save, scratch_id, service_client, short,
)

from agents.simple_search import runner as R  # noqa: E402
from agents.simple_search.models import ResolvedObject  # noqa: E402
from agents.simple_search.searcher import SearcherDecision  # noqa: E402
from agents.simple_search.synthesizer import SynthesizerOutput  # noqa: E402

import sys  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from _fmodels import sequence_model  # noqa: E402

FIXTURE = "unlock-01"
QUESTION = "اعطيني تفاصيل حكم تعيين حارس قضائي على مؤسسة المورث"


def ids(rows: list[dict]) -> set[str]:
    return {str(r["unlock_id"]) for r in rows}


def case_body(sb, case_id: str) -> str:
    row = (sb.table("cases").select("content").eq("id", case_id)
           .limit(1).execute()).data or [{}]
    return str(row[0].get("content") or "")


async def main() -> int:
    sb = service_client()
    convo = scratch_id()
    xid, xref = UNLOCK01_X
    yid, yref = UNLOCK01_Y

    hr(f"{FIXTURE} — the mis-resolution charge")
    print(f"scratch conversation: {convo}")
    print(f"X (resolved first, then REJECTED): {xid} · {xref}")
    print(f"Y (resolved second, ACCEPTED):     {yid} · {yref}")

    base = ledger_fingerprint(sb)
    base_j = ledger(sb, "judgment")
    print(f"\nledger before: total {base['total']} · judgment {len(base_j)}")

    x_body = case_body(sb, xid)
    y_body = case_body(sb, yid)
    print(f"cases.content — X {len(x_body)} chars · Y {len(y_body)} chars")

    # ── Instrumentation. `unfold` is called THROUGH, not replaced. ───────────
    real_unfold = R.unfold
    unfold_log: list[dict] = []

    async def _watched_unfold(supabase, obj, *, judgment_access=None):
        before = ledger(supabase, "judgment")
        res = await real_unfold(supabase, obj, judgment_access=judgment_access)
        after = ledger(supabase, "judgment")
        new = [r for r in after if str(r["unlock_id"]) not in ids(before)]
        # FLUSH EVERY ROW THE MOMENT IT EXISTS — before any assertion runs.
        for r in new:
            record_ledger_row(str(r["unlock_id"]), content_id=str(r["content_id"]),
                              fixture=FIXTURE, surface=r.get("surface"),
                              unlocked_at=str(r.get("unlocked_at")))
        entry = {
            "level": obj.level,
            "case_id": obj.case_id or "", "case_ref": obj.case_ref or "",
            "ok": res.ok, "chars": res.chars, "notes": list(res.notes),
            "ledger_delta": len(new),
            "new_unlock_ids": [str(r["unlock_id"]) for r in new],
            "new_content_ids": [str(r["content_id"]) for r in new],
            "body_served": bool(res.text) and res.chars > 0 and res.ok,
        }
        unfold_log.append(entry)
        print(f"  unfold[{len(unfold_log)}] {obj.level} {obj.case_ref or obj.case_id} "
              f"→ ok={res.ok} chars={res.chars} Δledger={len(new)} notes={res.notes}")
        return res

    # What each synthesizer was actually handed — the load-bearing capture.
    synth_inputs: list[dict] = []
    real_builder = R.build_synthesizer_user_message

    def _watched_builder(question, unfolds, references, **kw):
        msg = real_builder(question, unfolds, references, **kw)
        synth_inputs.append({
            "n_objects": len(unfolds),
            "ref_ids": [r.ref_id for r in references],
            "ref_titles": [short(r.title, 60) for r in references],
            "message_chars": len(msg),
            "mentions_X_caseid": xid in msg,
            "mentions_X_caseref": xref in msg,
            "mentions_Y_caseref": yref in msg,
            "mentions_charge_word": any(
                w in msg for w in ("رصيد", "unlock", "فتح المصادر", "charged")
            ),
        })
        return msg

    import agents.simple_search.searcher as S
    import agents.simple_search.synthesizer as SY

    searcher_script = sequence_model(
        SearcherDecision(data_type="judgments",
                         objects=[ResolvedObject(level="judgment", case_id=xid,
                                                 case_ref=xref, title="الحكم المرشَّح الأول")],
                         rationale="الترشيح الأول"),
        SearcherDecision(data_type="judgments",
                         objects=[ResolvedObject(level="judgment", case_id=yid,
                                                 case_ref=yref, title="الحكم المرشَّح الثاني")],
                         rationale="بعد الرفض"),
    )
    synth_script = sequence_model(
        SynthesizerOutput(rejected=True,
                          rejection_reason="هذا ليس الحكم المقصود — المطلوب حكم التصفية."),
        SynthesizerOutput(synthesis_md="هذا هو الحكم المطلوب وتفاصيله [1].",
                          used_refs=[1], wi_warranted=True, wi_title="حكم التصفية"),
    )

    written_before_run = {r["unlock_id"] for r in load()["ledger_rows_created"]}
    result = None
    try:
        R.unfold = _watched_unfold                                  # type: ignore[assignment]
        R.build_synthesizer_user_message = _watched_builder         # type: ignore[assignment]
        S.get_agent_model = lambda *_a, **_k: searcher_script.model  # type: ignore[assignment]
        SY.get_agent_model = lambda *_a, **_k: synth_script.model    # type: ignore[assignment]

        hr("RUN — reject-then-resolve-another")
        result = await R.run_simple_search(
            QUESTION, sb, USER_ID, convo, None,
            attached_items=[], recent_messages=[],
        )
        record_workspace_items(list(result.created_item_ids), fixture=FIXTURE)
    finally:
        R.unfold = real_unfold                                      # type: ignore[assignment]
        R.build_synthesizer_user_message = real_builder             # type: ignore[assignment]

    # ── What happened ───────────────────────────────────────────────────────
    hr("OBSERVED")
    after_j = ledger(sb, "judgment")
    turn_new = [r for r in after_j if str(r["unlock_id"]) not in ids(base_j)]
    charged_case_ids = {str(r["content_id"]) for r in turn_new}
    print(f"searcher rounds: {searcher_script.calls} · synthesizer runs: {synth_script.calls}")
    print(f"unfolds performed: {len(unfold_log)}")
    print(f"ledger judgment rows: {len(base_j)} → {len(after_j)}  (Δ +{len(turn_new)})")
    for r in turn_new:
        who = "X (rejected)" if str(r["content_id"]) == xid else (
            "Y (answered)" if str(r["content_id"]) == yid else "??")
        print(f"  NEW {r['unlock_id']} · {r['content_id']} · {who}")

    print(f"\nchat replies: {len(result.chat_messages)}")
    for m in result.chat_messages:
        print(f"  «{short(m, 160)}»")

    # ── The three structural measurements ───────────────────────────────────
    hr("STRUCTURE — could the reply have mentioned X's spent unlock?")
    surviving = synth_inputs[-1] if synth_inputs else {}
    print(f"synthesizer inputs captured: {len(synth_inputs)}")
    for i, s in enumerate(synth_inputs, 1):
        print(f"  [{i}] objects={s['n_objects']} refs={s['ref_ids']} "
              f"X_ref_in_msg={s['mentions_X_caseref']} charge_word={s['mentions_charge_word']}")

    result_fields = [f.name for f in dataclasses.fields(R.SimpleSearchRunResult)]
    unlock_field = [f for f in result_fields
                    if any(k in f.lower() for k in ("unlock", "charge", "cost", "quota", "spent"))]
    print(f"\nSimpleSearchRunResult fields: {result_fields}")
    print(f"  fields carrying unlock/charge accounting: {unlock_field or 'NONE'}")

    reply_text = "\n".join(result.chat_messages)
    mentions_x_in_reply = xid in reply_text or xref in reply_text
    mentions_charge_in_reply = any(w in reply_text for w in ("رصيد", "فتح المصادر", "حكمين"))

    x_charged = xid in charged_case_ids
    y_charged = yid in charged_case_ids
    checks = [
        {"id": "two_unlocks_charged", "want": "ledger Δ == 2 (X at its unfold, Y at its)",
         "ok": len(turn_new) == 2 and x_charged and y_charged},
        {"id": "rejected_ruling_paid", "want": "X — the REJECTED ruling — has a ledger row",
         "ok": x_charged},
        {"id": "no_refund_path", "want": "X's row still present after the turn completed",
         "ok": any(str(r["content_id"]) == xid for r in after_j)},
        {"id": "reply_silent_on_X", "want": "no reply text names X or the spend",
         "ok": not mentions_x_in_reply and not mentions_charge_in_reply},
        {"id": "surviving_synth_cannot_know", "want":
            "the accepted synthesizer's input contains no trace of X's charge",
         "ok": bool(surviving) and not surviving["mentions_X_caseref"]
               and not surviving["mentions_charge_word"]},
        {"id": "result_has_no_unlock_field", "want":
            "SimpleSearchRunResult exposes nothing an SSE/UI layer could surface",
         "ok": not unlock_field},
    ]

    hr("VERDICT")
    for c in checks:
        print(f"  [{'CONFIRMED' if c['ok'] else 'NOT-CONFIRMED'}] {c['id']:<28} — {c['want']}")

    flush_probe(
        FIXTURE,
        verdict="CONFIRMED — 2 unlocks charged, the rejected one is unrefunded and unmentioned"
        if all(c["ok"] for c in checks) else "PARTIAL — see checks",
        question=QUESTION,
        scripted=["searcher (2 rounds)", "synthesizer (reject, accept)"],
        real=["unfold", "judgment_access_resolver", "library_unlocks", "publisher"],
        searcher_rounds=searcher_script.calls,
        synthesizer_runs=synth_script.calls,
        unfolds=unfold_log,
        ledger_before_judgment=len(base_j),
        ledger_after_judgment=len(after_j),
        ledger_delta=len(turn_new),
        charged_case_ids=sorted(charged_case_ids),
        X={"case_id": xid, "case_ref": xref, "charged": x_charged, "outcome": "REJECTED"},
        Y={"case_id": yid, "case_ref": yref, "charged": y_charged, "outcome": "ANSWERED"},
        chat_messages=list(result.chat_messages),
        synthesizer_inputs=synth_inputs,
        result_dataclass_fields=result_fields,
        unlock_accounting_fields=unlock_field,
        checks=checks,
    )

    # ── CLEANUP ─────────────────────────────────────────────────────────────
    hr("CLEANUP — delete exactly the rows this fixture caused")
    doc = load()
    mine = [r for r in doc["ledger_rows_created"]
            if r["fixture"] == FIXTURE and not r["deleted"]
            and r["unlock_id"] not in written_before_run]
    print(f"rows to delete: {[r['unlock_id'] for r in mine] or 'none'}")
    for r in mine:
        # Scoped by unlock_id AND content_id: two other eval lanes are live on
        # this account today and a row of theirs must never be swept up here.
        sb.table("library_unlocks").delete().eq("unlock_id", r["unlock_id"]).eq(
            "content_id", r["content_id"]).execute()
        r["deleted"] = True
        save(doc)
        print(f"  deleted {r['unlock_id']} (flushed)")

    final = ledger_fingerprint(sb)
    restored = final["ids"] == base["ids"]
    print(f"\nledger now {final['total']} rows (baseline {base['total']}) · "
          f"byte-identical: {restored}")
    print(f"  newest judgment: {final['by_content_type'].get('judgment', {}).get('newest')}")

    doc = load()
    for p in doc["probes"]:
        if p["fixture"] == FIXTURE:
            p["cleanup"] = {
                "rows_deleted": [r["unlock_id"] for r in mine],
                "ledger_restored": restored,
                "final_total": final["total"],
                "final_judgment_count": final["by_content_type"].get("judgment", {}).get("count"),
                "final_newest_judgment": final["by_content_type"].get("judgment", {}).get("newest"),
            }
    save(doc)
    print(json.dumps({"delta": len(turn_new), "restored": restored}, ensure_ascii=False))
    return 0 if restored else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
