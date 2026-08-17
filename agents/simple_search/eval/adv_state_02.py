"""state-02 — the REDIRECT resume. The user changes the request mid-pause.

Fixture: searcher paused on «أي حكم تقصد؟»; the user replies «خلاص، افتح نظام
العمل». *"Does the resume serve the regulation, or force-match against the stale
candidate list?"*

The answer measured here is neither, and it is more interesting than both:
**a `simple_search` pause cannot be resumed at all.**
``orchestrator._resume_major_agent_inner`` gates resume on
``agent_family not in ("deep_search", "writing")`` — so the pause row is
DELETED and the user's reply is re-routed through the router as an ordinary new
turn (``orchestrator.py:764-781``).

So the fixture's trap (force-matching against a stale candidate list) is
structurally unreachable — the candidate list, the searcher's message history
and its ask_user tool call are all discarded before the reply is read. For THIS
query that is accidentally the right outcome; the report explains why the same
mechanism is damaging for a cooperative reply.

Both halves are exercised for real:

1. a scripted searcher calls ``ask_user`` on a clean conversation, so a REAL
   ``paused_runs`` row is written (flushed to the results JSON immediately);
2. ``_resume_major_agent`` is then driven with the redirect reply, with the
   **real router and real simple_search on the resume leg** — no scripting.

Money safety: «نظام العمل» is a regulation, and D12 does not meter regulations,
so the resume leg cannot write to ``library_unlocks``. Asserted, not assumed.

    .venv/Scripts/python.exe agents/simple_search/eval/adv_state_02.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from adv_ms_common import (  # noqa: E402
    PROTECTED_PAUSE_RUN_ID, USER_ID, flush_probe, hr, ledger_fingerprint, load,
    pause_rows, record_pause_row, record_workspace_items, save, scratch_id,
    service_client, short,
)

from agents import orchestrator as O  # noqa: E402
from agents.simple_search import runner as R  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from _fmodels import ask_user_model  # noqa: E402

FIXTURE = "state-02"
FIRST_QUESTION = "اعطيني تفاصيل الحكم"
SEARCHER_ASKS = "أي حكم تقصد؟ لديك أكثر من حكم في المحادثة."
USER_REPLY = "خلاص، افتح نظام العمل"


async def main() -> int:
    sb = service_client()
    convo = scratch_id()
    hr(f"{FIXTURE} — the redirect resume")
    print(f"scratch conversation: {convo}")

    base_ledger = ledger_fingerprint(sb)
    if pause_rows(sb, convo):
        raise SystemExit("scratch conversation already has a pause — aborting")

    # ── 1. A REAL searcher pause ────────────────────────────────────────────
    hr("1 · the searcher pauses on «أي حكم تقصد؟» (real paused_runs row)")
    searcher_script = ask_user_model(SEARCHER_ASKS)
    import agents.simple_search.searcher as S
    real_model = S.get_agent_model
    try:
        S.get_agent_model = lambda *_a, **_k: searcher_script.model  # type: ignore[assignment]
        paused = await R.run_simple_search(
            FIRST_QUESTION, sb, USER_ID, convo, None,
            attached_items=[], recent_messages=[],
        )
    finally:
        S.get_agent_model = real_model  # type: ignore[assignment]

    rows = pause_rows(sb, convo)
    print(f"run_simple_search: paused={paused.paused}")
    print(f"pause rows on the scratch convo: {len(rows)}")
    if not rows:
        raise SystemExit("expected a real pause row; none written")
    pending_run_id = str(rows[0]["run_id"])
    record_pause_row(pending_run_id, fixture=FIXTURE, conversation_id=convo,
                     agent_family=str(rows[0]["agent_family"]),
                     note="REAL searcher ask_user pause, written by run_simple_search")
    print(f"  run_id={pending_run_id} family={rows[0]['agent_family']}")
    print(f"  question_text stored: {short(str(rows[0]['question_text']), 130)}")

    # The full row (find_open_pause shape) is what the orchestrator resumes on.
    pending = O._find_awaiting_user(sb, convo, USER_ID)
    stored_q = str((pending or {}).get("question_text") or "")
    # THE JSON-BLOB DEFECT: with a real provider, ToolCallPart.args is a JSON
    # STRING, and runner._deferred_question returns it whole. Scripted args are
    # dicts, so this probe stores clean text — the blob is a production-only
    # shape, observed live twice during unlock-02.
    stored_is_json_blob = stored_q.strip().startswith("{")
    has_tool_call_id = bool((pending or {}).get("deferred_payload"))
    print(f"  deferred_payload present (tool_call_id): {has_tool_call_id}")

    # ── 2. The user replies with a REDIRECT ─────────────────────────────────
    hr(f"2 · the user replies «{USER_REPLY}» — driving the REAL resume path")
    events: list[dict] = []
    tokens: list[str] = []
    try:
        async for ev in O._resume_major_agent(
            pending=pending,
            user_reply=USER_REPLY,
            supabase=sb,
            user_id=USER_ID,
            conversation_id=convo,
            case_id=None,
            user_message_id=None,
        ):
            events.append(ev)
            if ev.get("type") == "token":
                tokens.append(str(ev.get("text") or ""))
            else:
                print(f"  event: {ev.get('type')} "
                      f"{short(json.dumps({k: v for k, v in ev.items() if k != 'type'}, ensure_ascii=False, default=str), 150)}")
    except Exception as exc:  # noqa: BLE001
        print(f"  RESUME RAISED: {type(exc).__name__}: {exc}")
        events.append({"type": "_exception", "error": f"{type(exc).__name__}: {exc}"})

    answer = "".join(tokens)
    kinds = [e.get("type") for e in events]
    print(f"\nevent kinds: {kinds}")
    print(f"\n--- answer ({len(answer)} chars) ---")
    print(short(answer, 1400))

    # ── 3. What became of the pause and the run ─────────────────────────────
    hr("3 · aftermath")
    rows_after = pause_rows(sb, convo)
    row_gone = not any(str(r["run_id"]) == pending_run_id for r in rows_after)
    resumed_ev = next((e for e in events if e.get("type") == "agent_resumed"), None)
    ran_family = [e.get("agent_family") for e in events
                  if e.get("type") == "agent_run_started"]
    wi_events = [e for e in events if e.get("type") == "workspace_item_created"]
    record_workspace_items([str(e.get("item_id")) for e in wi_events], fixture=FIXTURE)

    print(f"pause row deleted (abandoned): {row_gone}")
    print(f"agent_resumed announced family: {(resumed_ev or {}).get('agent_family')}")
    print(f"families that actually ran on the reply: {ran_family or 'none (router answered)'}")
    print(f"workspace items created: {len(wi_events)}")

    final_ledger = ledger_fingerprint(sb)
    ledger_untouched = final_ledger["ids"] == base_ledger["ids"]
    print(f"ledger untouched (regulations unmetered): {ledger_untouched}")

    # Did the redirect get served? Look for the regulation, not the ruling.
    serves_regulation = "نظام العمل" in answer
    forced_ruling_match = any(
        p in answer for p in ("لم أجد حكم", "لم أعثر على حكم", "أي حكم تقصد",
                              "الحكم المطلوب")
    )
    print(f"\nanswer mentions نظام العمل      : {serves_regulation}")
    print(f"answer force-matches the ruling : {forced_ruling_match}")

    checks = [
        {"id": "real_pause_written", "want":
            "a real simple_search pause row exists before the reply",
         "ok": bool(pending) and str((pending or {}).get("agent_family")) == "simple_search"},
        {"id": "no_resume_support", "want":
            "simple_search is NOT in the resume allow-list — the run is abandoned",
         "ok": row_gone},
        {"id": "reply_rerouted_fresh", "want":
            "the reply is re-routed through the router as a new turn",
         "ok": bool(answer) or bool(ran_family)},
        {"id": "redirect_served", "want":
            "the user's new request (نظام العمل) is answered",
         "ok": serves_regulation},
        {"id": "no_forced_candidate_match", "want":
            "no «لم أجد حكماً بهذا الاسم» — the stale candidate list is gone",
         "ok": not forced_ruling_match},
        {"id": "no_ledger_writes", "want": "regulations are unmetered (D12)",
         "ok": ledger_untouched},
    ]
    hr("VERDICT")
    for c in checks:
        print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['id']:<26} — {c['want']}")

    flush_probe(
        FIXTURE,
        verdict=("PASS on the fixture's own question — the redirect is served — "
                 "but ONLY because simple_search pauses cannot resume at all: "
                 "the row is deleted and the reply re-routed fresh"),
        first_question=FIRST_QUESTION,
        searcher_question=SEARCHER_ASKS,
        user_reply=USER_REPLY,
        pause_run_id=pending_run_id,
        pause_agent_family=str((pending or {}).get("agent_family") or ""),
        pause_question_text_stored=stored_q,
        pause_question_text_is_json_blob=stored_is_json_blob,
        pause_has_deferred_payload=has_tool_call_id,
        resume_event_kinds=kinds,
        agent_resumed_family=(resumed_ev or {}).get("agent_family"),
        families_run_on_reply=ran_family,
        pause_row_deleted=row_gone,
        answer=answer,
        answer_chars=len(answer),
        serves_regulation=serves_regulation,
        forced_ruling_match=forced_ruling_match,
        workspace_items_created=len(wi_events),
        ledger_untouched=ledger_untouched,
        mechanism=(
            "orchestrator._resume_major_agent_inner:764-781 — "
            "`if agent_family not in ('deep_search','writing')` → log, "
            "_resolve_pause_loud(DELETE), then re-route the reply through _route. "
            "A SECOND, independent blocker sits behind it: "
            "runner._record_searcher_pause never writes `deferred_payload`, so "
            "even if the family were allow-listed the rehydrate would raise "
            "ValueError('deferred_payload missing tool_call_id') and abandon anyway."
        ),
        why_it_matters=(
            "This query redirects, so discarding the pause happens to be right. "
            "A COOPERATIVE reply is the damaging case: «الحكم الأول» or «الثاني» "
            "is re-routed with the candidate list destroyed, and the router must "
            "reconstruct the referent from recent_messages alone. The searcher's "
            "ask_user is therefore a question the system cannot act on."
        ),
        checks=checks,
    )

    # ── CLEANUP ─────────────────────────────────────────────────────────────
    hr("CLEANUP")
    doc = load()
    for r in doc["pause_rows_created"]:
        if r["run_id"] == pending_run_id:
            r["deleted"] = row_gone
    save(doc)
    leftover = [r for r in pause_rows(sb, convo)]
    for r in leftover:
        rid = str(r["run_id"])
        assert rid != PROTECTED_PAUSE_RUN_ID
        sb.table("paused_runs").delete().eq("run_id", rid).eq(
            "conversation_id", convo).execute()
        print(f"  removed leftover pause {rid}")
    protected_ok = any(p["run_id"] == PROTECTED_PAUSE_RUN_ID for p in pause_rows(sb))
    print(f"scratch pauses now: {len(pause_rows(sb, convo))} · "
          f"protected intact: {protected_ok}")

    doc = load()
    for p in doc["probes"]:
        if p["fixture"] == FIXTURE:
            p["cleanup"] = {"pause_row_deleted_by_orchestrator": row_gone,
                            "leftovers_removed": [str(r["run_id"]) for r in leftover],
                            "scratch_pauses_remaining": len(pause_rows(sb, convo)),
                            "protected_row_intact": protected_ok,
                            "ledger_untouched": ledger_untouched}
    save(doc)
    print(json.dumps({"row_gone": row_gone, "serves_regulation": serves_regulation,
                      "ledger_untouched": ledger_untouched}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
