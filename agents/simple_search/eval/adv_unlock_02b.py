"""unlock-02b — the DISCARD. Answers already paid for are thrown away on a late pause.

Not one of §13h's 24 fixtures. It is the mechanism the live ``unlock-02`` run
walked into and the reason that run's money finding is worse than the fixture
predicted: the three unlocks were charged, **and then the turn returned
``paused=True`` with zero replies and zero cards.** The user paid for three
rulings and received «لم يظهر لي أي تقرير مرفق مع الرسالة».

``runner.run_simple_search``'s pause branch returns ``_empty(paused=True, …)``,
which builds a result with empty ``chat_messages`` / ``created_item_ids``. Any
answer already accumulated in ``answers`` during an earlier cycle is discarded
with it. The charge happened in that earlier cycle and is permanent.

**This probe costs nothing.** It reproduces the discard on ``chunk`` objects,
which D12 does not meter, so the ledger is provably untouched while the control
flow is identical: round 1 resolves two documents → one synthesizer accepts and
one rejects (so the loop continues) → round 2 the searcher calls ``ask_user``.

    .venv/Scripts/python.exe agents/simple_search/eval/adv_unlock_02b.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from adv_ms_common import (  # noqa: E402
    USER_ID, flush_probe, hr, ledger_fingerprint, pause_rows, scratch_id,
    service_client, short,
)

from agents.simple_search import runner as R  # noqa: E402
from agents.simple_search.models import ResolvedObject, UnfoldResult  # noqa: E402
from agents.simple_search.publisher import SimpleSearchPublishResult  # noqa: E402
from agents.simple_search.searcher import SearcherDecision  # noqa: E402
from agents.simple_search.synthesizer import SynthesizerOutput  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from _fmodels import ScriptedModel, sequence_model  # noqa: E402
from pydantic_ai.messages import ModelResponse, ToolCallPart  # noqa: E402

FIXTURE = "unlock-02b"
QUESTION = "اعطيني تفاصيل النظامين المذكورين"


def searcher_then_ask() -> ScriptedModel:
    """Round 1: resolve two documents. Round 2: call ask_user (the pause)."""
    state = {"n": 0}

    def fn(messages, info):
        state["n"] += 1
        if state["n"] == 1:
            out = SearcherDecision(
                data_type="regs",
                objects=[
                    ResolvedObject(level="chunk", chunk_id="chunk-A",
                                   regulation_id="reg-A", title="النظام الأول"),
                    ResolvedObject(level="chunk", chunk_id="chunk-B",
                                   regulation_id="reg-B", title="النظام الثاني"),
                ],
            )
            tool = info.output_tools[0]
            return ModelResponse(parts=[ToolCallPart(tool_name=tool.name,
                                                     args=out.model_dump())])
        return ModelResponse(parts=[ToolCallPart(
            tool_name="ask_user",
            args={"question": "أي نظام تقصد بالضبط؟"})])

    return ScriptedModel(fn)


async def main() -> int:
    sb = service_client()
    convo = scratch_id()
    hr(f"{FIXTURE} — is an accepted answer discarded when a LATER cycle pauses?")
    print(f"scratch conversation: {convo}")
    print("objects: chunks (D12 — NOT metered). Ledger must not move at all.")

    base = ledger_fingerprint(sb)
    pauses_before = pause_rows(sb, convo)
    print(f"ledger before: {base['total']} rows · scratch pauses: {len(pauses_before)}")

    # Stub the two I/O edges so this probe touches nothing real.
    unfolded: list[str] = []
    published: list[dict] = []

    async def _unfold(supabase, obj, *, judgment_access=None):
        unfolded.append(obj.chunk_id or obj.primary_id())
        return UnfoldResult(level=obj.level, text=f"محتوى {obj.chunk_id}", chars=20)

    async def _publish(supabase, **kw):
        published.append(kw)
        return SimpleSearchPublishResult(item_id=f"stub-wi-{len(published)}",
                                         sse_events=[])

    import agents.simple_search.searcher as S
    import agents.simple_search.synthesizer as SY

    searcher_script = searcher_then_ask()
    # Document order is preserved, so reg-A's synthesizer runs first: ACCEPT,
    # then reg-B REJECTS — which is what forces cycle 2.
    synth_script = sequence_model(
        SynthesizerOutput(synthesis_md="جواب النظام الأول الكامل [1].",
                          used_refs=[1], wi_warranted=True, wi_title="النظام الأول"),
        SynthesizerOutput(rejected=True, rejection_reason="ليس النظام المطلوب."),
    )

    real_unfold, real_publish = R.unfold, R.publish_simple_search_result
    real_record = R._record_searcher_pause
    recorded: list[dict] = []

    def _no_write_pause(supabase, result, **kw):
        """Capture the pause WITHOUT writing a row — state-01/02 own that."""
        recorded.append({"question": kw.get("question", "")})
        return None

    try:
        R.unfold = _unfold                                   # type: ignore[assignment]
        R.publish_simple_search_result = _publish            # type: ignore[assignment]
        R._record_searcher_pause = _no_write_pause           # type: ignore[assignment]
        S.get_agent_model = lambda *_a, **_k: searcher_script.model   # type: ignore[assignment]
        SY.get_agent_model = lambda *_a, **_k: synth_script.model     # type: ignore[assignment]

        hr("RUN — accept + reject on cycle 1, ask_user on cycle 2")
        result = await R.run_simple_search(
            QUESTION, sb, USER_ID, convo, None,
            attached_items=[], recent_messages=[],
        )
    finally:
        R.unfold = real_unfold                               # type: ignore[assignment]
        R.publish_simple_search_result = real_publish        # type: ignore[assignment]
        R._record_searcher_pause = real_record               # type: ignore[assignment]

    hr("OBSERVED")
    print(f"searcher rounds: {searcher_script.calls} · synthesizer runs: {synth_script.calls}")
    print(f"objects unfolded (i.e. PAID FOR, had they been rulings): {unfolded}")
    print(f"publishes attempted: {len(published)}")
    print(f"\nresult.paused          = {result.paused}")
    print(f"result.question_text   = {short(str(result.question_text), 120)}")
    print(f"result.chat_messages   = {result.chat_messages}")
    print(f"result.created_item_ids= {result.created_item_ids}")

    final = ledger_fingerprint(sb)
    ledger_untouched = final["ids"] == base["ids"]
    print(f"\nledger untouched: {ledger_untouched} ({final['total']} rows)")

    accepted_answer_lost = (result.paused and not result.chat_messages
                            and synth_script.calls >= 1)
    checks = [
        {"id": "cycle2_pause", "want": "the turn ends paused from a LATER cycle",
         "ok": result.paused and searcher_script.calls == 2},
        {"id": "work_was_done", "want":
            "objects were unfolded before the pause (the charge point)",
         "ok": len(unfolded) == 2},
        {"id": "accepted_answer_discarded", "want":
            "an ACCEPTED synthesis is dropped — chat_messages is empty",
         "ok": accepted_answer_lost},
        {"id": "no_card_published", "want":
            "the accepted answer's card is never published either",
         "ok": not published and not result.created_item_ids},
        {"id": "zero_cost_probe", "want": "ledger byte-identical (chunks unmetered)",
         "ok": ledger_untouched},
    ]
    hr("VERDICT")
    for c in checks:
        print(f"  [{'CONFIRMED' if c['ok'] else 'NOT-CONFIRMED'}] {c['id']:<26} — {c['want']}")

    flush_probe(
        FIXTURE,
        verdict=("CONFIRMED — a late ask_user discards answers already produced "
                 "(and, for rulings, already paid for)")
        if all(c["ok"] for c in checks) else "PARTIAL — see checks",
        origin="discovered by the live unlock-02 run, isolated here at zero cost",
        question=QUESTION,
        objects="chunk level — D12 unmetered, so the ledger cannot move",
        searcher_rounds=searcher_script.calls,
        synthesizer_runs=synth_script.calls,
        objects_unfolded=unfolded,
        publishes_attempted=len(published),
        result={"paused": result.paused, "question_text": result.question_text,
                "chat_messages": list(result.chat_messages),
                "created_item_ids": list(result.created_item_ids)},
        mechanism=("runner.run_simple_search's pause branch returns _empty(paused=True), "
                   "which discards the `answers` dict accumulated by earlier cycles. "
                   "The unlock charged during those cycles is permanent."),
        live_consequence=("unlock-02 live: 3 rulings charged, then paused with 0 replies "
                          "and 0 cards — «لم يظهر لي أي تقرير مرفق مع الرسالة»"),
        ledger_untouched=ledger_untouched,
        checks=checks,
    )
    print(json.dumps({"paused": result.paused, "answers_lost": accepted_answer_lost,
                      "ledger_untouched": ledger_untouched}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
