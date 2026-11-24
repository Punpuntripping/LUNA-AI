"""The extended planner run: validator (budget limits, ids, no budget), masking (encode
in, decode out, fakes persisted first), the ledger row, failures. No real model:
pydantic-ai ``FunctionModel`` (no pytest-asyncio in CI — ``asyncio.run``)."""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from pydantic_ai.messages import ModelResponse, RetryPromptPart, TextPart, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agents.deep_search_v4.extended import budget as B
from agents.deep_search_v4.extended import planner as P
from agents.deep_search_v4.extended.models import AngleCard, DocView, ExtendedPlan, PlannerView, SectionView
from agents.utils import usage_sink

SECRET = "0551234567"  # a phone number: the codec masks it in any context


def view(n_docs: int = 2) -> PlannerView:
    docs = [DocView(id=f"D{i}", item_id=f"i{i}", title=f"مستند {i}", doc_type="judgment", summary="",
                    content_chars=10) for i in range(1, n_docs + 1)]
    angles = [AngleCard(id=f"A{i}", kind="strengthen", strength="strong", for_side="respondent",
                        for_party="المدعى عليه", title=f"وجه {i}", argument="حجة", legal_question="سؤال؟",
                        weakness="", sections=[SectionView(ref="D1.S1", label="الموكّل", owner="x")])
              for i in range(1, 4)]
    refs = {f"D{d}.S{s}" for d in range(1, n_docs + 1) for s in range(1, 6)}
    return PlannerView(map_id="m1", message_id="msg", documents=docs, angles=angles, section_refs=frozenset(refs))


def req(title: str = "بحث", mode: str = "reg_compliance_led", cap: int | None = 6, angles=("A1",),
        sections=("D1.S2",), focus: str = "تركيز") -> dict:
    return {"title": title, "angles": list(angles), "sections": list(sections), "mode": mode,
            "max_sub_queries": cap, "focus": focus, "why": "لأن"}


def plan(*reqs: dict, cheaper: list[dict] | None = None, **kw) -> dict:
    out = {"understanding": "يريد المستخدم…", "client_side": "respondent", "requests": list(reqs) or [req(), req("ب2")],
           "cheaper": {"note": "أقدر أدمج البحثين", "requests": cheaper} if cheaper else None,
           "left_out": [{"angle": "a3", "why": "ضعيف"}], "rationale": "لأن"}
    out.update(kw)
    return out


class Script:
    """A FunctionModel that answers with the given plans in order (the last repeats) and
    records what it was sent."""

    def __init__(self, *answers: dict, text: bool = False) -> None:
        self.answers = list(answers)
        self.calls: list[list[Any]] = []
        self.text = text

    def __call__(self, messages: list[Any], info: AgentInfo) -> ModelResponse:
        self.calls.append(messages)
        answer = self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]
        if self.text:
            return ModelResponse(parts=[TextPart("<thinking>x</thinking>" + json.dumps(answer, ensure_ascii=False))])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, answer)])

    def user_prompt(self) -> str:
        for part in self.calls[0][0].parts:
            if isinstance(part, UserPromptPart):
                return str(part.content)
        return ""

    def retries(self) -> list[str]:
        out = []
        for messages in self.calls:
            for part in messages[-1].parts:
                if isinstance(part, RetryPromptPart):
                    out.append(str(part.content))
        return out


@pytest.fixture
def model(monkeypatch):
    def _use(script: Script) -> Script:
        monkeypatch.setattr(P, "get_agent_model", lambda slot: FunctionModel(script.__call__))
        return script
    return _use


@pytest.fixture
def ledger(monkeypatch):
    rows: list[dict] = []
    monkeypatch.setattr(usage_sink, "record_call", lambda **kw: rows.append(kw))
    return rows


def run(v: PlannerView | None = None, mode=B.MODES[20], **kw) -> P.PlannerRun:
    return asyncio.run(P.run_extended_planner(
        v or view(), query=kw.pop("query", "ما موقفي؟"), recent_messages=kw.pop("recent", []), mode=mode,
        user_id="u1", **kw,
    ))


# ── a valid plan ─────────────────────────────────────────────────────────────


def test_a_valid_plan_is_returned_normalised_and_billed(model, ledger, monkeypatch):
    s = model(Script(plan(req(), req("ب2", "full", 8, angles=("A2",)), cheaper=[req("مدمج", cap=8)])))
    monkeypatch.setattr(P, "_priced", lambda result, usage: ("wafer-deepseek-v4.1-flash-1017", 0.0042))
    out = run()
    assert isinstance(out.plan, ExtendedPlan) and [r.title for r in out.plan.requests] == ["بحث", "ب2"]
    assert out.plan.left_out[0].angle == "A3"            # normalised
    assert out.points == pytest.approx(0.42) and out.model == "wafer-deepseek-v4.1-flash-1017"
    assert len(s.calls) == 1 and s.retries() == []
    assert "<budget>\nThe most this extended search may use:\n- at most 3 searches;" in s.user_prompt()
    assert len(ledger) == 1
    row = ledger[0]
    assert (row["agent"], row["agent_family"], row["outcome"], row["cost_usd"]) == (
        "deep_search.extended_planner", "deep_search", "ok", 0.0042)
    assert row["tokens_in"] > 0 and row["tokens_out"] > 0 and row["requests"] == 1


def test_the_instructions_follow_the_mode(model, ledger):
    s = model(Script(plan()))
    run(mode=B.MODES[20])
    from agents.deep_search_v4.extended.prompts import SYSTEM_PROMPT_BUDGET, SYSTEM_PROMPT_NO_BUDGET

    assert s.calls[0][0].instructions == SYSTEM_PROMPT_BUDGET.strip()
    s2 = model(Script(plan(req(cap=None), req("ب2", cap=None))))
    run(mode=B.NO_BUDGET)
    assert s2.calls[0][0].instructions == SYSTEM_PROMPT_NO_BUDGET.strip()


def test_a_plan_answered_as_text_is_salvaged(model, ledger):
    model(Script(plan(), text=True))
    assert len(run().plan.requests) == 2


def test_bare_section_numbers_are_qualified_on_a_one_document_map(model, ledger):
    model(Script(plan(req(sections=("S2", "d1·s3")), req("ب2", sections=()))))
    out = run(view(n_docs=1))
    assert out.plan.requests[0].sections == ["D1.S2", "D1.S3"]


# ── the validator ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad, words", [
    (plan(req(), req("2"), req("3"), req("4")), "plan: 4 searches > the budget's 3"),
    (plan(req(cap=9), req("2")), "max_sub_queries above the budget's 8 in request(s) [1]"),
    (plan(req(cap=None), req("2")), "max_sub_queries is required (a number) in request(s) [1]"),
    (plan(req(angles=("A9",)), req("2", sections=("D3.S1",))), "unknown angle ids ['A9'] / section refs ['D3.S1']"),
    (plan(req(), req("2"), cheaper=[req("c", cap=12)]), "cheaper option: max_sub_queries above the budget's 8"),
])
def test_outside_the_mode_goes_back_to_the_model(model, ledger, bad, words):
    s = model(Script(bad, plan()))
    out = run(mode=B.MODES[20])
    assert len(out.plan.requests) == 2
    assert len(s.calls) == 2 and words in s.retries()[0]
    assert s.retries()[0].startswith("Outside the budget or unknown ids — fix and answer again: ")
    assert ledger[0]["requests"] == 2


def test_too_many_full_searches_in_the_fifteen_mode(model, ledger):
    s = model(Script(plan(req(mode="full"), req("2", mode="full"), req("3", mode="full")), plan()))
    run(mode=B.MODES[15])
    assert "plan: 3 `full` searches > the budget's 2" in s.retries()[0]


def test_no_budget_checks_only_the_ids(model, ledger):
    big = plan(*(req(str(i), mode="full", cap=None) for i in range(8)), cheaper=[req("c", cap=40)])
    s = model(Script(big))
    out = run(mode=B.NO_BUDGET)
    assert len(out.plan.requests) == 8 and s.retries() == []
    s2 = model(Script(plan(req(angles=("A7",)), req("2")), plan()))
    run(mode=B.NO_BUDGET)
    assert "unknown angle ids ['A7']" in s2.retries()[0]


def test_a_plan_that_never_fits_raises_and_is_still_billed(model, ledger):
    s = model(Script(plan(req(angles=("A9",)), req("2"))))
    with pytest.raises(Exception):
        run()
    assert len(s.calls) == 1 + P.OUTPUT_RETRIES
    assert len(ledger) == 1 and ledger[0]["outcome"] == "error" and ledger[0]["tokens_in"] > 0


def test_a_slow_model_times_out(model, ledger, monkeypatch):
    async def slow(messages, info):
        await asyncio.sleep(1)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, plan())])

    monkeypatch.setattr(P, "get_agent_model", lambda slot: FunctionModel(slow))
    monkeypatch.setattr(P, "PLANNER_TIMEOUT_S", 0.05)
    with pytest.raises((asyncio.TimeoutError, TimeoutError)):
        run()


# ── masking (وضع السرية) ─────────────────────────────────────────────────────


def test_masked_in_decoded_out_fakes_persisted_first(model, ledger, monkeypatch):
    from backend.app.services import masking_service
    from shared.privacy import PrivacyCodec

    codec = PrivacyCodec(enabled=True)
    monkeypatch.setattr(P, "_active_codec", lambda: codec)
    events: list[str] = []
    monkeypatch.setattr(masking_service, "persist_new_mappings",
                        lambda sb, uid, c: events.append(f"persist:{uid}:{len(c.new_mappings)}"))

    v = view()
    v.angles[0].argument = f"رقم الجوال {SECRET} للتواصل"

    def answer(messages, info):
        events.append("model")
        prompt = next(str(p.content) for p in messages[0].parts if isinstance(p, UserPromptPart))
        assert SECRET not in prompt
        fake = codec.encode(SECRET)
        assert fake != SECRET and fake in prompt
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,
                                                 plan(req(focus=f"التواصل على {fake}"), req("2")))])

    monkeypatch.setattr(P, "get_agent_model", lambda slot: FunctionModel(answer))
    out = run(v, supabase=object())
    assert events == ["persist:u1:1", "model"]
    assert out.plan.requests[0].focus == f"التواصل على {SECRET}"


def test_no_codec_is_a_passthrough(model, ledger, monkeypatch):
    monkeypatch.setattr(P, "_active_codec", lambda: None)
    s = model(Script(plan()))
    v = view()
    v.angles[0].argument = f"رقم الجوال {SECRET}"
    run(v)
    assert SECRET in s.user_prompt()
