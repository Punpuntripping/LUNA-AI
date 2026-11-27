"""The whole run with scripted models: reads, the one quote retry, masking,
timeouts, the never-raises contract and the ledger."""
from __future__ import annotations

import asyncio
import random

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from agents.attachment_agent import agent as agent_mod
from agents.attachment_agent import runner as runner_mod
from agents.attachment_agent.models import AttachmentAgentCall
from agents.attachment_agent.render import render_for_router
from agents.attachment_agent.verify import DEFAULT_CLARIFY_EDIT, NO_VERIFIED_QUOTE
from agents.utils import usage_sink
from agents.utils.usage_sink import collect_llm_calls
from backend.app.services import masking_service
from shared.privacy.codec import PrivacyCodec

from .fakes import (
    CONVO,
    LEASE,
    PAGE_STARTS,
    USER,
    attachment,
    big_text,
    call,
    final,
    lease_sections,
    make_db,
    point,
    retry_prompts,
    scripted,
    segment_map,
    tool_returns,
    user_prompt,
)

KEY_LINE = "وقد أقر المدعى عليه باستلام المبلغ كاملًا."


def _call(message: str = "متى يبدأ العقد؟", **kw) -> AttachmentAgentCall:
    return AttachmentAgentCall(user_id=USER, conversation_id=CONVO, message_id="m9", user_message=message, **kw)


def _use(monkeypatch, model) -> None:
    monkeypatch.setattr(agent_mod, "get_agent_model", lambda slot: model)


@pytest.fixture
def persisted(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(runner_mod, "persist_new_mappings", lambda sb, uid, codec: calls.append((uid, codec)) or 0)
    return calls


def _lease_db():
    return make_db(
        [attachment("i1", "عقد الإيجار", LEASE, wi_seq=1)],
        maps=[segment_map("i1", LEASE, lease_sections())],
        pages={"i1": PAGE_STARTS},
    )


def _memo_db():
    text = big_text()
    cut = text.index(KEY_LINE)
    secs = [
        {"seq": 1, "char_start": 0, "char_end": cut, "label": "facts", "value": "supporting", "what": "الوقائع"},
        {"seq": 2, "char_start": cut, "char_end": len(text), "label": "admission", "value": "core",
         "what": "إقرار المدعى عليه", "owner": "المدعي"},
    ]
    return make_db([attachment("i1", "مذكرة الدعوى", text, wi_seq=2, doc_type="memo")],
                   maps=[segment_map("i1", text, secs)])


def _run(db, c=None):
    return asyncio.run(runner_mod.run_attachment_agent(c or _call(), supabase=db))


# -- reading -----------------------------------------------------------------------


def test_outline_then_read_sections_then_a_checked_quote(monkeypatch, persisted):
    seen: list = []
    _use(monkeypatch, scripted([
        call("read_sections", refs=["F1·S2"]),
        final(points=[point("أقر المدعى عليه بالاستلام", ("F1", "أقر المدعى عليه باستلام المبلغ كاملًا"))]),
    ], seen))
    result = _run(_memo_db(), _call("هل أقر الخصم بالاستلام؟"))
    assert result.outcome == "found"
    q = result.points[0].quotes[0]
    assert (q.text, q.section_ref, q.section_what, q.owner, q.wi) == (
        "أقر المدعى عليه باستلام المبلغ كاملًا", "S2", "إقرار المدعى عليه", "المدعي", "WI-2")
    prompt = user_prompt(seen[0])
    assert "<current_message>هل أقر الخصم بالاستلام؟</current_message>" in prompt
    assert "F1·S2 · admission · core · إقرار المدعى عليه · المدعي" in prompt
    assert KEY_LINE not in prompt  # a long file is not given whole
    assert persisted == [(USER, None)]
    block = render_for_router(result)
    assert "«مذكرة الدعوى» (WI-2) — القسم: إقرار المدعى عليه — صاحب الكلام: المدعي" in block
    assert "«أقر المدعى عليه باستلام المبلغ كاملًا»" in block


def test_bad_quote_gets_one_retry_then_is_dropped(monkeypatch, persisted):
    seen: list = []
    bad = final(points=[point("الأجرة شهرية", ("F1", "يدفع المستأجر الأجرة كل شهر"))])
    _use(monkeypatch, scripted([bad, bad], seen))
    result = _run(_lease_db())
    assert len(seen) == 2  # one retry, no more
    retries = retry_prompts(seen[1])
    assert len(retries) == 1 and "not word for word" in retries[0] and "يدفع المستأجر الأجرة كل شهر" in retries[0]
    assert result.outcome == "not_found" and result.points == []
    assert result.note.startswith(NO_VERIFIED_QUOTE)


def test_retry_fixes_the_quote(monkeypatch, persisted):
    _use(monkeypatch, scripted([
        final(points=[point("الأجرة مقدمًا", ("F1", "يدفع المستأجر الأجرة كل شهر"))]),
        final(points=[point("الأجرة مقدمًا", ("F1", "يدفع المستأجر الأجرة مقدما كل ستة أشهر"))]),
    ]))
    result = _run(_lease_db())
    assert result.outcome == "found"
    q = result.points[0].quotes[0]
    assert q.text == "يَدْفَعُ المستأجرُ الأجرةَ مقدمًا كل ستة أشهر" and q.page == 2 and q.section_ref == "S3"


def test_quote_of_unread_text_sends_the_model_to_read_it(monkeypatch, persisted):
    seen: list = []
    quote = ("F1", "أقر المدعى عليه باستلام المبلغ")
    _use(monkeypatch, scripted([
        final(points=[point("أقر", quote)]),
        call("find", phrase="باستلام المبلغ"),
        final(points=[point("أقر", quote)]),
    ], seen))
    result = _run(_memo_db())
    assert "not in anything you have read" in retry_prompts(seen[1])[0]
    assert "باستلام المبلغ" in tool_returns(seen[2])[0]
    assert result.outcome == "found" and result.points[0].quotes[0].section_ref == "S2"


def test_unclear_change_request_gets_one_question_and_changes_nothing(monkeypatch, persisted):
    _use(monkeypatch, scripted([final("needs_clarification", request="change")]))
    db = _lease_db()
    result = _run(db, _call("عدّل القسم"))
    assert result.outcome == "needs_clarification" and result.ask_user == DEFAULT_CLARIFY_EDIT
    assert result.edits == [] and db.writes() == []


# -- masking ---------------------------------------------------------------------------


def test_masked_text_is_read_quoted_and_located(monkeypatch, persisted):
    codec = PrivacyCodec(rng=random.Random(7))
    token = masking_service.set_active_codec(codec)
    seen: list = []
    try:
        def answer(messages):
            # persisted before the model saw a single fake
            assert persisted and persisted[0][1] is codec
            fake_id = codec.real_to_fake["1010234567"]
            return final(points=[
                point("السجل", ("F1", f"سجل تجاري رقم {fake_id}")),
                point("التوقيع", ("F1", "التوقيع: المؤجر")),
            ])
        _use(monkeypatch, scripted([answer], seen))
        result = _run(_lease_db())
    finally:
        masking_service.reset_active_codec(token)
    prompt = user_prompt(seen[0])
    assert "1010234567" not in prompt and "lessor.office@mail.com" not in prompt
    assert codec.real_to_fake["lessor.office@mail.com"] in prompt
    assert result.outcome == "found"
    first, second = (p.quotes[0] for p in result.points)
    assert codec.real_to_fake["1010234567"] in first.text and first.page == 1
    # after the e-mail line (whose length changed when masked) the page is still right
    assert second.page == 3 and second.section_ref == "S6"


def test_no_codec_is_a_passthrough(monkeypatch, persisted):
    seen: list = []
    _use(monkeypatch, scripted([final(points=[point("السجل", ("F1", "سجل تجاري رقم 1010234567"))])], seen))
    result = _run(_lease_db())
    assert "سجل تجاري رقم 1010234567" in user_prompt(seen[0])
    assert result.points[0].quotes[0].text == "سجل تجاري رقم 1010234567"


# -- failures ------------------------------------------------------------------------------


def test_timeout_is_an_error_and_still_bills_what_ran(monkeypatch, persisted):
    monkeypatch.setattr(runner_mod, "AGENT_TIMEOUT_S", 0.3)
    flushed: list = []
    monkeypatch.setattr(usage_sink, "_flush", lambda sb, buf: flushed.extend(buf or []) or 0)
    state = {"n": 0}

    async def slow(messages, info):
        state["n"] += 1
        if state["n"] == 1:
            return ModelResponse(parts=[call("find", phrase="الأجرة")])
        await asyncio.sleep(5)
        return ModelResponse(parts=[TextPart("{}")])

    _use(monkeypatch, FunctionModel(slow))
    db = _lease_db()

    async def go():
        with collect_llm_calls(db, conversation_id=CONVO, user_id=USER, message_id="m9"):
            return await runner_mod.run_attachment_agent(_call(), supabase=db)

    result = asyncio.run(go())
    assert result.outcome == "error" and result.note == runner_mod.NOTE_TIMEOUT
    assert "timeout" in result.error
    assert [r["agent"] for r in flushed] == [runner_mod.STAGE]
    assert flushed[0]["tokens_in"] > 0


def test_cost_lands_on_the_turn_ledger(monkeypatch, persisted):
    flushed: list = []
    monkeypatch.setattr(usage_sink, "_flush", lambda sb, buf: flushed.extend(buf or []) or 0)
    _use(monkeypatch, scripted([final("not_found")]))
    db = _lease_db()

    async def go():
        with collect_llm_calls(db, conversation_id=CONVO, user_id=USER, message_id="m9"):
            return await runner_mod.run_attachment_agent(_call(), supabase=db)

    result = asyncio.run(go())
    assert result.outcome == "not_found"
    assert len(flushed) == 1 and flushed[0]["agent"] == runner_mod.STAGE


def test_never_raises_on_a_failed_read():
    result = _run(make_db([attachment("i1", "عقد", LEASE)], fail={"workspace_items"}))
    assert result.outcome == "error" and result.note == runner_mod.NOTE_ERROR


def test_never_raises_when_the_agent_cannot_be_built(monkeypatch, persisted):
    def boom():
        raise RuntimeError("no provider")
    monkeypatch.setattr(runner_mod, "create_attachment_agent", boom)
    result = _run(_lease_db())
    assert result.outcome == "error" and "no provider" in result.error


def test_never_raises_on_a_model_error(monkeypatch, persisted):
    def broken(messages, info):
        raise ValueError("provider 500")
    _use(monkeypatch, FunctionModel(broken))
    result = _run(_lease_db())
    assert result.outcome == "error" and "provider 500" in result.error


def test_never_raises_past_the_usage_limits(monkeypatch, persisted):
    _use(monkeypatch, scripted([call("find", phrase="الأجرة")] * 20))
    result = _run(_lease_db())
    assert result.outcome == "error"


# -- nothing to read --------------------------------------------------------------------


def test_no_attachments():
    result = _run(make_db([attachment("i1", "تقرير", "نص", kind="agent_search")]))
    assert result.outcome == "not_found" and result.note == runner_mod.NOTE_NO_FILES


def test_picked_files_missing():
    result = _run(_lease_db(), _call(item_ids=["gone"]))
    assert result.outcome == "not_found" and result.note == runner_mod.NOTE_PICKED_MISSING


def test_files_without_text():
    result = _run(make_db([attachment("i1", "صورة العقد", "")]))
    assert result.outcome == "not_found" and "«صورة العقد»" in result.note


def test_all_attachments_when_none_picked(monkeypatch, persisted):
    seen: list = []
    db = make_db([
        attachment("i1", "عقد", LEASE, created_at="2026-11-01"),
        attachment("i2", "مذكرة", "نص المذكرة", created_at="2026-11-02"),
        attachment("i3", "تقرير", "نص", kind="agent_search"),
    ])
    _use(monkeypatch, scripted([final("which_file")], seen))
    result = _run(db, _call("ماذا في الملف؟"))
    prompt = user_prompt(seen[0])
    assert "## F1 — «عقد»" in prompt and "## F2 — «مذكرة»" in prompt and "تقرير" not in prompt
    assert result.outcome == "which_file" and result.ask_user == "أي الملفين تقصد: «عقد» أم «مذكرة»؟"
