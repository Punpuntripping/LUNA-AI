"""state-01 — the PAUSE-SLOT COLLISION. §9 trap 10, documented and never tested.

``find_open_pause`` reads THE single open pause per conversation. The searcher
and the deep_search planner share that one slot. This drives the collision for
real: a planner-shaped pause row is written on the scratch conversation first,
then ``run_simple_search`` runs with a scripted searcher whose only move is
``ask_user``.

The fixture asks for "defined behaviour — either the searcher declines to pause
and answers/asks inline, or the old pause is resolved first. NOT two pause rows,
NOT a clobbered planner pause." Both of those disasters are in fact avoided —
``_record_searcher_pause`` checks ``find_open_pause`` and refuses to write. What
this probe measures is what happens *instead*, and the mirror the fixture asks
for: **whose resume wins.**

The planner row is synthetic but planner-SHAPED — written through
``agents.paused_runs.record_pause`` with the same fields
``orchestrator._record_deferred`` writes (``agent_family='deep_search'``,
``pause_reason='clarify'``, ``deferred_payload`` carrying a ``tool_call_id``,
``message_history`` bytes). It lives on the scratch conversation ONLY. The
account's one real planner pause (``6e1d3707…`` on ``fcb965fb…``) is never read,
written, or counted here — every query is scoped by ``conversation_id``.

    .venv/Scripts/python.exe agents/simple_search/eval/adv_state_01.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from adv_ms_common import (  # noqa: E402
    PROTECTED_PAUSE_CONVO, PROTECTED_PAUSE_RUN_ID, USER_ID, flush_probe, hr,
    ledger_fingerprint, load, pause_rows, record_pause_row, save, scratch_id,
    service_client, short,
)

from agents.paused_runs import PauseRecord, find_open_pause, record_pause  # noqa: E402
from agents.simple_search import runner as R  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from _fmodels import ask_user_model  # noqa: E402

FIXTURE = "state-01"
QUESTION = "اش هي المادة 5؟"
SEARCHER_ASKS = "أي نظام تقصد بالمادة 5؟ نظام العمل أم نظام التنفيذ؟"
PLANNER_ASKED = "هل أنتِ المنفَذ ضدها في هذا الطلب؟ ومتى قُدّم طلب التنفيذ؟"


def planner_shaped_pause(sb, convo: str) -> str | None:
    """Write a deep_search planner pause exactly as ``_record_deferred`` does."""
    now = datetime.now(timezone.utc)
    return record_pause(
        sb,
        PauseRecord(
            conversation_id=convo,
            user_id=USER_ID,
            case_id=None,
            agent_family="deep_search",
            task_label="بحث معمّق",
            # A stub: this probe never actually resumes the planner, and a real
            # pydantic-ai history is not needed to occupy the slot.
            message_history=b'[{"parts":[],"kind":"request"}]',
            deferred_payload={
                "tool_call_id": "call_state01_planner",
                "tool_name": "ask_user",
                "args": {"question": PLANNER_ASKED},
                "partial_output": None,
            },
            question_text=PLANNER_ASKED,
            pause_reason="clarify",
            asked_at=now,
            expires_at=now + timedelta(hours=24),
        ),
    )


async def main() -> int:
    sb = service_client()
    convo = scratch_id()
    hr(f"{FIXTURE} — a searcher pause on top of an open planner pause")
    print(f"scratch conversation: {convo}")
    print(f"protected real pause  : {PROTECTED_PAUSE_RUN_ID} on {PROTECTED_PAUSE_CONVO}")
    print("  (all counts below are scoped by conversation_id — never global)")

    base_ledger = ledger_fingerprint(sb)
    before = pause_rows(sb, convo)
    print(f"\nscratch pauses before: {len(before)} (must be 0)")
    if before:
        raise SystemExit("scratch conversation is not clean — aborting")

    # ── 1. Occupy the slot with a planner-shaped pause ───────────────────────
    hr("1 · the deep_search planner pauses first")
    planner_run_id = planner_shaped_pause(sb, convo)
    if not planner_run_id:
        raise SystemExit("could not write the planner pause")
    record_pause_row(planner_run_id, fixture=FIXTURE, conversation_id=convo,
                     agent_family="deep_search",
                     note="SYNTHETIC planner-shaped pause written by this probe")
    planner_row_before = next(
        (p for p in pause_rows(sb, convo) if p["run_id"] == planner_run_id), None)
    print(f"planner run_id: {planner_run_id}")
    print(f"question_text : «{short(PLANNER_ASKED, 90)}»")

    # ── 2. Now the searcher wants to pause too ───────────────────────────────
    hr("2 · the simple_search searcher calls ask_user on the SAME conversation")
    searcher_script = ask_user_model(SEARCHER_ASKS)
    import agents.simple_search.searcher as S
    real_model = S.get_agent_model
    try:
        S.get_agent_model = lambda *_a, **_k: searcher_script.model  # type: ignore[assignment]
        result = await R.run_simple_search(
            QUESTION, sb, USER_ID, convo, None,
            attached_items=[], recent_messages=[],
        )
    finally:
        S.get_agent_model = real_model  # type: ignore[assignment]

    after = pause_rows(sb, convo)
    print(f"result.paused        = {result.paused}")
    print(f"result.question_text = «{short(str(result.question_text), 90)}»")
    print(f"scratch pause rows   : {len(before)} → {len(after)}")
    for p in after:
        print(f"  {p['run_id']} · {p['agent_family']} · «{short(p['question_text'], 70)}»")

    planner_row_after = next(
        (p for p in after if p["run_id"] == planner_run_id), None)
    planner_intact = planner_row_after == planner_row_before
    searcher_row = [p for p in after if p["agent_family"] == "simple_search"]

    # ── 3. THE MIRROR — whose resume wins if the user replies? ───────────────
    hr("3 · THE MIRROR — the user now replies. Whose run resumes?")
    open_pause = find_open_pause(sb, convo, USER_ID)
    winner_family = str((open_pause or {}).get("agent_family") or "")
    winner_run = str((open_pause or {}).get("run_id") or "")
    shown_question = str((open_pause or {}).get("question_text") or "")
    print(f"find_open_pause returns : {winner_run} · family={winner_family}")
    print(f"question the ORCHESTRATOR would show the user:")
    print(f"  «{short(shown_question, 140)}»")
    print(f"question the SEARCHER actually asked:")
    print(f"  «{short(SEARCHER_ASKS, 140)}»")
    swapped = shown_question.strip() != SEARCHER_ASKS.strip()
    resumable = winner_family in ("deep_search", "writing")
    print(f"\nquestion silently swapped for another agent's: {swapped}")
    print(f"a reply would resume family «{winner_family}» (resumable: {resumable})")
    print("  → the searcher's run is unrecoverable; the reply is fed to the "
          "planner's ask_user as if it answered THAT question.")

    checks = [
        {"id": "no_second_row", "want": "the searcher does NOT write a 2nd pause row",
         "ok": len(after) == 1 and not searcher_row},
        {"id": "planner_not_clobbered", "want": "the planner row is byte-identical",
         "ok": planner_intact},
        {"id": "turn_still_reports_paused", "want":
            "run_simple_search returns paused=True with the searcher's question",
         "ok": result.paused and str(result.question_text or "").strip() == SEARCHER_ASKS},
        {"id": "question_swapped", "want":
            "BUT the orchestrator shows the PLANNER's question instead (the finding)",
         "ok": swapped},
        {"id": "resume_goes_to_planner", "want":
            "a user reply resumes deep_search, not the searcher",
         "ok": winner_family == "deep_search"},
    ]
    hr("VERDICT")
    for c in checks:
        print(f"  [{'CONFIRMED' if c['ok'] else 'NOT-CONFIRMED'}] {c['id']:<26} — {c['want']}")

    flush_probe(
        FIXTURE,
        verdict=("CONFIRMED — no second row and no clobber, but the searcher's "
                 "question is silently replaced by the planner's and the reply "
                 "resumes the wrong agent"),
        question=QUESTION,
        searcher_question=SEARCHER_ASKS,
        planner_question=PLANNER_ASKED,
        planner_run_id=planner_run_id,
        pause_rows_before=len(before), pause_rows_after=len(after),
        planner_row_intact=planner_intact,
        searcher_row_written=bool(searcher_row),
        runner_result={"paused": result.paused,
                       "question_text": result.question_text,
                       "chat_messages": list(result.chat_messages)},
        find_open_pause_returns={"run_id": winner_run, "agent_family": winner_family,
                                 "question_text": shown_question},
        question_shown_to_user=shown_question,
        question_actually_asked=SEARCHER_ASKS,
        question_swapped=swapped,
        mechanism=(
            "runner._record_searcher_pause refuses to write when find_open_pause "
            "is non-empty (§9 trap 10) and returns None — but run_simple_search "
            "still returns paused=True. orchestrator's _SkipRunRecord handler "
            "then RE-QUERIES _find_awaiting_user for the question to emit "
            "(orchestrator.py:2269-2276) and never reads ss_outcome.question_text, "
            "so the row it finds — the planner's — supplies the question the user "
            "sees."
        ),
        consequence=(
            "The user is asked a question no agent asked this turn, and their "
            "answer is delivered to a different agent's ask_user. The searcher's "
            "run is dropped with no row and no trace."
        ),
        checks=checks,
    )

    # ── CLEANUP ─────────────────────────────────────────────────────────────
    hr("CLEANUP — remove ONLY this probe's synthetic planner row")
    assert planner_run_id != PROTECTED_PAUSE_RUN_ID
    sb.table("paused_runs").delete().eq("run_id", planner_run_id).eq(
        "conversation_id", convo).execute()
    doc = load()
    for r in doc["pause_rows_created"]:
        if r["run_id"] == planner_run_id:
            r["deleted"] = True
    save(doc)
    left = pause_rows(sb, convo)
    protected_ok = any(p["run_id"] == PROTECTED_PAUSE_RUN_ID for p in pause_rows(sb))
    final_ledger = ledger_fingerprint(sb)
    print(f"scratch pauses now: {len(left)} · protected row intact: {protected_ok}")
    print(f"ledger untouched  : {final_ledger['ids'] == base_ledger['ids']}")

    doc = load()
    for p in doc["probes"]:
        if p["fixture"] == FIXTURE:
            p["cleanup"] = {"planner_row_deleted": planner_run_id,
                            "scratch_pauses_remaining": len(left),
                            "protected_row_intact": protected_ok,
                            "ledger_untouched": final_ledger["ids"] == base_ledger["ids"]}
    save(doc)
    print(json.dumps({"rows": len(after), "swapped": swapped,
                      "winner": winner_family}, ensure_ascii=False))
    return 0 if not left and protected_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
