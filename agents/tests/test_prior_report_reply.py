"""The reply for a sufficient or partial verdict (plan artifact_analyzer.md §4.6).

Code prints the quotes, so the model cannot retype legal text, and a failed
responder still leaves the user with the verified quotes.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agents import orchestrator
from agents.memory.artifact_analyzer.models import AnalyzerResult, VerifiedQuote
from agents.models import NextStep
from agents.responder import prior_report
from agents.responder.models import (
    OUTPUT_TYPES,
    RESPONDER_MODES,
    PriorReportArtifact,
    PriorReportResponse,
    ResponderInput,
)
from agents.responder.prior_report import (
    FALLBACK_LINE,
    FALLBACK_OFFER,
    FOUND_RELATED,
    MAX_SHOWN_QUOTES,
    NO_SEARCH,
    YES_SEARCH,
    assemble,
    cite_quotes,
    compose_message,
)
from agents.responder.prompts import MODE_BLOCKS, SYSTEM_PROMPTS, build_turn_message
from agents.utils.reply_guard import NO_INTERNALS_RULE

QUOTE = "كان المستثنى من الحجز ثلثي الراتب (نحو 67%)"


def _quote(**over: Any) -> VerifiedQuote:
    return VerifiedQuote(**{
        "text": QUOTE, "source_name": "نظام التنفيذ — المادة 26", "item_id": "wi-2",
        "report_title": "حجز الراتب", "report_id": "wi-2", **over,
    })


def _result(verdict: str = "sufficient", **over: Any) -> AnalyzerResult:
    return AnalyzerResult(**{
        "verdict": verdict, "restatement": "ما النسبة؟", "quotes": [_quote()], **over,
    })


def _reply(**over: Any) -> PriorReportResponse:
    return PriorReportResponse(**{"chat_summary_md": "دينك ليس نفقة، فالثلث فقط يُحجز.", **over})


# ── Composition ───────────────────────────────────────────────────────────────


def _refs(result: Any) -> list[dict]:
    events = [e for e in result.sse_events if e["type"] == "reply_metadata"]
    assert len(events) == 1
    return events[0]["metadata"]["quote_refs"]


def test_quotes_sit_behind_numbers_word_for_word_with_their_source() -> None:
    result = assemble(_result(), _reply())
    # The bubble is the voice with its number; the quote is one click away.
    assert result.chat_summary == "دينك ليس نفقة، فالثلث فقط يُحجز. [1]"
    assert QUOTE not in result.chat_summary
    # The report's own words, attributed to the report and the law it rests on.
    assert _refs(result) == [{
        "n": 1, "text": QUOTE,
        "source": "من بحثنا السابق «حجز الراتب»، استنادًا إلى: نظام التنفيذ — المادة 26",
    }]


def test_the_responder_numbers_are_kept_where_it_put_them() -> None:
    quotes = [_quote(text="نص 1"), _quote(text="نص 2"), _quote(text="نص 3")]
    text, refs = cite_quotes("الأولى.[1] [2]\n\nالثانية.[3]", quotes)
    assert text == "الأولى.[1] [2]\n\nالثانية.[3]"
    assert [r["text"] for r in refs] == ["نص 1", "نص 2", "نص 3"]


def test_numbers_follow_the_order_the_text_cites_them() -> None:
    quotes = [_quote(text="نص 1"), _quote(text="نص 2")]
    text, refs = cite_quotes("أولًا [2]. ثم [1].", quotes)
    assert text == "أولًا [1]. ثم [2]."
    assert [(r["n"], r["text"]) for r in refs] == [(1, "نص 2"), (2, "نص 1")]


def test_a_number_that_points_at_no_passage_is_dropped() -> None:
    text, refs = cite_quotes("الجملة [7]. والأخرى [1، 9].", [_quote()])
    assert text == "الجملة. والأخرى [1]."
    assert len(refs) == 1


def test_arabic_digits_and_grouped_numbers_are_read() -> None:
    quotes = [_quote(text="نص 1"), _quote(text="نص 2")]
    text, _ = cite_quotes("الجملة [١، ٢].", quotes)
    assert text == "الجملة [1] [2]."


def test_an_uncited_passage_gets_a_number_at_the_end_of_the_answer() -> None:
    quotes = [_quote(text="نص 1"), _quote(text="نص 2"), _quote(text="نص 3")]
    text, refs = cite_quotes("الجواب [2].\n\nما لا نملكه.", quotes)
    assert text == "الجواب [1]. [2] [3]\n\nما لا نملكه."
    assert [r["text"] for r in refs] == ["نص 2", "نص 1", "نص 3"]


def test_a_text_with_no_numbers_still_opens_every_quote() -> None:
    text, refs = cite_quotes("الجواب.", [_quote(text="نص 1"), _quote(text="نص 2")])
    assert text == "الجواب. [1] [2]"
    assert len(refs) == 2


def test_quotes_printed_word_for_word_when_the_voice_failed() -> None:
    text = compose_message("سطر", [_quote()])
    assert f"> «{QUOTE}»" in text
    assert "— من بحثنا السابق «حجز الراتب»، استنادًا إلى: نظام التنفيذ — المادة 26" in text


def test_text_opened_from_a_law_is_attributed_to_the_law_alone() -> None:
    chunk = _quote(item_id=None, report_title="", ref_id="reg:c1")
    assert compose_message("سطر", [chunk]).endswith("— نظام التنفيذ — المادة 26")


def test_a_report_sentence_with_no_citation_names_only_the_report() -> None:
    text = compose_message("سطر", [_quote(source_name="")])
    assert text.endswith("— من بحثنا السابق «حجز الراتب»")


def test_a_source_no_report_cited_reads_as_a_related_source() -> None:
    text = compose_message("سطر", [_quote(uncited=True, item_id=None)])
    assert FOUND_RELATED in text
    _, refs = cite_quotes("سطر [1]", [_quote(uncited=True, item_id=None)])
    assert refs[0]["note"] == FOUND_RELATED.rstrip(":")


def test_repeal_status_stays_with_the_quote() -> None:
    text = compose_message("سطر", [_quote(status="ملغي — لم يعد سارياً")])
    assert "(ملغي — لم يعد سارياً)" in text
    _, refs = cite_quotes("سطر [1]", [_quote(status="ملغي — لم يعد سارياً")])
    assert refs[0]["source"].endswith("(ملغي — لم يعد سارياً)")


def test_law_change_notice_is_carried() -> None:
    result = _result(transition_line="بدأ نفاذ «نظام التنفيذ» في 2026/10/28م")
    assert "بدأ نفاذ" in assemble(result, _reply()).chat_summary


def test_a_reply_shows_a_handful_of_quotes_not_all() -> None:
    quotes = [_quote(text=f"نص {i}") for i in range(MAX_SHOWN_QUOTES + 3)]
    text = compose_message("سطر", quotes)
    assert text.count("> «") == MAX_SHOWN_QUOTES
    _, refs = cite_quotes(f"سطر [{MAX_SHOWN_QUOTES + 1}]", quotes)
    assert len(refs) == MAX_SHOWN_QUOTES


# ── Verdicts ──────────────────────────────────────────────────────────────────


def test_sufficient_keeps_the_voice_chips_and_question() -> None:
    chip = NextStep(kind="draft", label="خطاب للبنك", prompt="اكتب خطابًا للبنك")
    result = assemble(_result(), _reply(
        next_steps=[chip], follow_up_question="هل أُودع الراتب بعد الأمر؟", question_basis="ask_user",
    ))
    assert result.next_steps == [chip]
    assert result.follow_up_question == "هل أُودع الراتب بعد الأمر؟"


def test_partial_offers_the_search_with_yes_and_no_chips() -> None:
    result = assemble(_result("partial"), _reply(search_offer="تبغاني أبحث عن مدة التظلم؟"))
    assert result.follow_up_question == "تبغاني أبحث عن مدة التظلم؟"
    assert [s.label for s in result.next_steps] == [YES_SEARCH, NO_SEARCH]


def test_partial_ignores_a_model_answer_chip_and_keeps_an_action() -> None:
    wrong = NextStep(kind="answer", label="نعم", prompt="نعم")
    action = NextStep(kind="draft", label="خطاب", prompt="اكتب خطابًا")
    result = assemble(_result("partial"), _reply(next_steps=[wrong, action]))
    assert [s.label for s in result.next_steps] == [YES_SEARCH, NO_SEARCH, "خطاب"]


def test_partial_without_an_offer_still_asks() -> None:
    assert assemble(_result("partial"), _reply()).follow_up_question == FALLBACK_OFFER


def test_quoted_report_gets_the_existing_card_chip() -> None:
    assert assemble(_result(), _reply()).sse_events[0] == {
        "type": "referenced_existing_item", "item_id": "wi-2",
    }


def test_a_law_quote_points_at_the_report_whose_search_found_it() -> None:
    law = _quote(item_id=None, report_title="", ref_id="reg:c1", report_id="wi-4")
    result = _result(quotes=[law], report_ids=["wi-5", "wi-4"])
    assert assemble(result, _reply()).sse_events[0] == {
        "type": "referenced_existing_item", "item_id": "wi-4",
    }
    assert _refs(assemble(result, _reply()))[0]["source"] == "نظام التنفيذ — المادة 26"


def test_the_failed_voice_carries_no_refs() -> None:
    events = assemble(_result(), None).sse_events
    assert all(e["type"] != "reply_metadata" for e in events)


def test_every_reply_gets_the_chip_even_with_no_mapped_quote() -> None:
    law = _quote(item_id=None, report_title="", report_id=None)
    result = _result("partial", quotes=[law], report_ids=["wi-5"])
    assert assemble(result, None).sse_events == [
        {"type": "referenced_existing_item", "item_id": "wi-5"},
    ]


# ── Responder failure ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("verdict", ["sufficient", "partial"])
def test_failure_still_delivers_the_verified_quotes(verdict: str) -> None:
    result = assemble(_result(verdict), None)
    assert result.chat_summary.startswith(FALLBACK_LINE)
    assert f"> «{QUOTE}»" in result.chat_summary


def test_failure_on_partial_still_offers_the_search() -> None:
    result = assemble(_result("partial"), None)
    assert result.follow_up_question == FALLBACK_OFFER
    assert [s.label for s in result.next_steps] == [YES_SEARCH, NO_SEARCH]


def test_failure_on_sufficient_invents_no_question() -> None:
    assert assemble(_result(), None).follow_up_question is None


def test_the_reply_goes_through_the_responder_in_its_own_mode(monkeypatch) -> None:
    seen: list[ResponderInput] = []
    policies: list[Any] = []

    async def fake(inp: ResponderInput, *, model_override: Any = None) -> PriorReportResponse:
        seen.append(inp)
        policies.append(model_override)
        return _reply()

    monkeypatch.setattr(prior_report, "run_responder", fake)
    monkeypatch.setattr(prior_report, "load_profession_group", lambda *_: None)
    monkeypatch.setattr(prior_report, "load_style_profile", lambda *_: None)
    many = [_quote(text=f"نص {i}") for i in range(MAX_SHOWN_QUOTES + 2)]
    result = asyncio.run(prior_report.write_prior_report_reply(
        _result(quotes=many, transition_line="سطر"),
        supabase=None, user_id="u", conversation_id="c",
    ))
    assert seen[0].mode == "prior_report"
    # Lighter reasoning than the responder slot: one sentence over printed quotes.
    from agents.utils.agent_models import AGENT_MODELS

    assert policies[0] is AGENT_MODELS["artifact_analyzer"]
    assert len(seen[0].artifact.quotes) == MAX_SHOWN_QUOTES
    assert seen[0].artifact.version_rule
    assert result.chat_summary.startswith("دينك")


# ── Mode wiring ───────────────────────────────────────────────────────────────


def test_mode_is_wired_everywhere() -> None:
    assert "prior_report" in RESPONDER_MODES
    assert OUTPUT_TYPES["prior_report"] is PriorReportResponse
    assert "prior_report" in MODE_BLOCKS
    assert NO_INTERNALS_RULE in SYSTEM_PROMPTS["prior_report"]


def test_turn_message_gives_the_model_the_verdict_and_the_passages() -> None:
    text = build_turn_message(ResponderInput(
        mode="prior_report",
        artifact=PriorReportArtifact(
            question="ما النسبة؟", verdict="partial", quotes=[_quote()],
            missing=["مدة التظلم"], ask_user=["تاريخ الإيداع"],
        ),
    ))
    assert "partial" in text
    assert f'<passage n="1" source="نظام التنفيذ — المادة 26">{QUOTE}</passage>' in text
    assert "مدة التظلم" in text
    assert "تاريخ الإيداع" in text
    assert "`search_offer`" in text


def test_the_prompt_forbids_promising_a_result() -> None:
    assert "Never say or imply what a search would find" in MODE_BLOCKS["prior_report"]


def test_reply_flows_through_the_normal_bubble() -> None:
    result = assemble(_result("partial"), _reply(search_offer="تبغاني أبحث؟"))
    tokens = orchestrator._bubble_tokens(result)
    assert "".join(tokens).endswith("تبغاني أبحث؟")
    assert orchestrator._follow_up_event(result)["question"] == "تبغاني أبحث؟"
