"""No reply exposes how we work (plan artifact_analyzer.md §4.10)."""
from __future__ import annotations

import pytest

from agents.responder.prompts import SYSTEM_PROMPTS
from agents.router.router import SYSTEM_PROMPT as ROUTER_PROMPT
from agents.utils import reply_guard
from agents.utils.reply_guard import NO_INTERNALS_RULE, ReplyGuard, scrub_reply


def test_identifiers_are_removed() -> None:
    assert scrub_reply("أنهى deep_search البحث.") == "أنهى البحث."
    assert scrub_reply("استبعد المصدر (reranker) من النتائج") == "استبعد المصدر من النتائج"
    assert scrub_reply("المرجع [S3] في التقرير [R1]") == "المرجع في التقرير"


def test_uuids_are_removed() -> None:
    assert scrub_reply("البطاقة dcb87f62-8039-4ddf-96d8-2da5e566a116 جاهزة") == "البطاقة جاهزة"


@pytest.mark.parametrize(
    "text",
    [
        # WI-N is printed on the card button, so a reply may name it.
        "وقد عُولج هذا السؤال في بطاقة البحث السابقة (WI-2).",
        # Ordinary Arabic and legal text must survive byte for byte.
        "صلى في المسجد الجامع، وفق المخطط المعتمد.",
        "النموذج S3 والمادة [5] من النظام  بمسافتين.",
        "",
    ],
)
def test_ordinary_text_is_untouched(text: str) -> None:
    assert scrub_reply(text) == text


def test_token_spacing_survives_for_word_streams() -> None:
    assert scrub_reply(" كلمة") == " كلمة"


def test_tokens_are_cleaned_in_the_stream() -> None:
    event = ReplyGuard("c").check({"type": "token", "text": "من aggregator هنا"})
    assert event == {"type": "token", "text": "من هنا"}


def test_clean_event_is_passed_through_unchanged() -> None:
    event = {"type": "token", "text": "نص عادي"}
    assert ReplyGuard("c").check(event) is event


@pytest.mark.parametrize("kind", ["agent_question", "follow_up"])
def test_questions_are_cleaned(kind: str) -> None:
    event = ReplyGuard("c").check({"type": kind, "question": "هل deep_search لازم؟", "run_id": "r"})
    assert event["question"] == "هل لازم؟"
    assert event["run_id"] == "r"


def test_chips_are_cleaned() -> None:
    event = ReplyGuard("c").check({"type": "next_steps", "items": [
        {"kind": "draft", "label": "صياغة deep_search", "prompt": "اكتب [S1] خطاباً"},
    ]})
    assert event["items"] == [{"kind": "draft", "label": "صياغة", "prompt": "اكتب خطاباً"}]


def test_other_events_are_not_touched() -> None:
    event = {"type": "agent_selected", "agent_family": "deep_search"}
    assert ReplyGuard("c").check(event) is event


def test_a_broken_event_cannot_cost_the_answer() -> None:
    event = {"type": "next_steps", "items": ["not a dict"]}
    assert ReplyGuard("c").check(event) is event


def test_arabic_process_words_are_logged_not_changed(monkeypatch) -> None:
    logged: list[dict] = []
    monkeypatch.setattr(
        reply_guard._logfire, "info",
        lambda name, **attrs: logged.append({"name": name, **attrs}),
    )
    guard = ReplyGuard("c")
    event = guard.check({"type": "token", "text": "الجامع لم يستخدم هذا المصدر"})
    guard.check({"type": "done"})
    assert event["text"] == "الجامع لم يستخدم هذا المصدر"
    assert logged[0]["name"] == "reply_guard.process_words"
    assert "الجامع" in logged[0]["words"]


def test_removals_are_logged_at_the_end_of_the_turn(monkeypatch) -> None:
    logged: list[dict] = []
    monkeypatch.setattr(
        reply_guard._logfire, "warn",
        lambda name, **attrs: logged.append({"name": name, **attrs}),
    )
    guard = ReplyGuard("c")
    guard.check({"type": "token", "text": "deep_search"})
    assert logged == []
    guard.check({"type": "done"})
    assert logged[0]["terms"] == ["deep_search"]


def test_voices_carry_the_rule() -> None:
    assert NO_INTERNALS_RULE in ROUTER_PROMPT
    assert "{NO_INTERNALS_RULE}" not in ROUTER_PROMPT
    for prompt in SYSTEM_PROMPTS.values():
        assert NO_INTERNALS_RULE in prompt
