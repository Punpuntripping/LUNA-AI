"""«بحث موسّع» at the API edge (build spec C): ``SendMessageRequest.plan_choice``, the
route handing it to ``send_message_stream``, ``message_service`` handing it to the
orchestrator ONLY when set (a typed message calls ``handle_message`` exactly as
before), and the SSE relay: ``agent_question.pause_reason`` and
``agent_progress.data.searches``. Offline; ``asyncio.run`` (CI has no pytest-asyncio).
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

import backend.app.api.messages as messages_api
import backend.app.services.message_service as ms
from backend.app.models.requests import PlanChoice, SendMessageRequest
from shared import quota

USER = "user-x"
CONV = "conv-x"


# ── the request model ───────────────────────────────────────────────────────

def test_plan_choice_is_optional_and_typed():
    assert SendMessageRequest(content="سؤال").plan_choice is None
    body = SendMessageRequest(content="نفّذ الخطة", plan_choice={"run_id": "r1", "option": "cheaper"})
    assert body.plan_choice == PlanChoice(run_id="r1", option="cheaper")
    assert SendMessageRequest(content="x", plan_choice=None).plan_choice is None


@pytest.mark.parametrize("bad", [
    {"run_id": "r1", "option": "all"},
    {"run_id": "", "option": "plan"},
    {"option": "plan"},
    {"run_id": "r" * 65, "option": "plan"},
])
def test_a_malformed_plan_choice_is_refused(bad):
    with pytest.raises(ValidationError):
        SendMessageRequest(content="x", plan_choice=bad)


# ── the route ───────────────────────────────────────────────────────────────

def test_the_route_hands_plan_choice_to_the_stream(monkeypatch):
    seen: dict[str, Any] = {}

    async def fake_run_db(fn, *a, **k):
        return "uid" if fn is messages_api.get_user_id else {"conversation_id": CONV}

    def fake_stream(_supabase, **kw):
        seen.update(kw)

        async def gen():
            yield ""
        return gen()

    monkeypatch.setattr(messages_api, "run_db", fake_run_db)
    monkeypatch.setattr(messages_api.message_service, "send_message_stream", fake_stream)
    body = SendMessageRequest(content="بحث عادي", plan_choice={"run_id": "r9", "option": "ordinary"})
    asyncio.run(messages_api.send_message(
        CONV, body, SimpleNamespace(), user=SimpleNamespace(auth_id="a"), supabase=object(),
    ))
    assert seen["plan_choice"] == PlanChoice(run_id="r9", option="ordinary")
    assert seen["content"] == "بحث عادي"


# ── message_service ─────────────────────────────────────────────────────────

class _Chain:
    def __init__(self, fake: "_Supabase", table: str) -> None:
        self._fake, self._table, self._update = fake, table, None

    def update(self, payload: dict) -> "_Chain":
        self._update = payload
        return self

    def __getattr__(self, _name: str):
        return lambda *a, **k: self

    def execute(self) -> Any:
        if self._update is not None:
            self._fake.updates.append((self._table, dict(self._update)))
        return SimpleNamespace(data=[])


class _Supabase:
    def __init__(self) -> None:
        self.updates: list[tuple[str, dict]] = []

    def table(self, name: str) -> _Chain:
        return _Chain(self, name)


def _stream(monkeypatch, script: list[dict], **send: Any) -> tuple[list[tuple[str, dict]], dict]:
    captured: dict[str, Any] = {}

    async def _check(*_a, **_k):
        return None

    async def _handle(**kw):
        captured.update(kw)
        for ev in script:
            yield ev

    from shared.privacy import PrivacyCodec

    monkeypatch.setattr(quota, "check", _check)
    monkeypatch.setattr(ms, "handle_message", _handle)
    monkeypatch.setattr(ms, "_insert_turn_rows", lambda *a, **k: None)
    monkeypatch.setattr(ms, "write_audit_log", lambda *a, **k: None)
    monkeypatch.setattr(ms, "build_turn_codec",
                        lambda *a, **k: PrivacyCodec(real_to_fake={}, fake_to_real={}, enabled=False))
    ms._active_runs.pop(CONV, None)

    async def _connected() -> bool:
        return False

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None)),
                              is_disconnected=_connected)

    async def _drain() -> list[str]:
        return [chunk async for chunk in ms.send_message_stream(
            _Supabase(), user_id=USER, conversation_id=CONV,
            conv={"conversation_id": CONV, "case_id": None, "message_count": 2},
            content="نفّذ الخطة", request=request, attachment_ids=None, **send,
        )]

    events: list[tuple[str, dict]] = []
    for chunk in asyncio.run(_drain()):
        lines = dict(ln.split(": ", 1) for ln in chunk.strip().splitlines() if ": " in ln)
        events.append((lines.get("event", ""), json.loads(lines.get("data", "{}"))))
    return events, captured


def test_a_typed_message_calls_the_orchestrator_as_before(monkeypatch):
    _, captured = _stream(monkeypatch, [{"type": "done", "usage": {}}])
    assert "plan_choice" not in captured


def test_a_button_hands_plan_choice_as_plain_data(monkeypatch):
    _, captured = _stream(
        monkeypatch, [{"type": "done", "usage": {}}],
        plan_choice=PlanChoice(run_id="r1", option="plan"),
    )
    assert captured["plan_choice"] == {"run_id": "r1", "option": "plan"}


def _decode_spy(monkeypatch) -> list[str]:
    seen: list[str] = []
    real = ms.decode_text

    def spy(codec, text, *, emit=True):
        seen.append(text)
        return real(codec, text, emit=emit)

    monkeypatch.setattr(ms, "decode_text", spy)
    return seen


def test_the_card_question_relays_its_pause_reason(monkeypatch):
    decoded = _decode_spy(monkeypatch)
    card_text = "بحث موسّع — الخطة المقترحة"
    events, _ = _stream(monkeypatch, [
        {"type": "agent_question", "run_id": "r1", "question": card_text,
         "pause_reason": "extended_plan", "suggestions": []},
        {"type": "done", "usage": {}},
    ])
    (q,) = [d for n, d in events if n == "agent_question"]
    assert q == {"run_id": "r1", "question": card_text, "suggestions": [], "pause_reason": "extended_plan"}
    # Stored and returned real by record_extended_pause — never decoded a second time.
    assert card_text not in decoded


@pytest.mark.parametrize("extra", [{}, {"pause_reason": "clarify"}, {"pause_reason": "approve_plan"}])
def test_every_other_pause_relays_todays_payload(monkeypatch, extra):
    """Flag off (or any non-card pause): the agent_question payload is byte-for-byte
    today's — no pause_reason key — and the question is decoded as today."""
    decoded = _decode_spy(monkeypatch)
    events, _ = _stream(monkeypatch, [
        {"type": "agent_question", "run_id": "r1", "question": "؟", "suggestions": ["أ"], **extra},
        {"type": "done", "usage": {}},
    ])
    (q,) = [d for n, d in events if n == "agent_question"]
    assert q == {"run_id": "r1", "question": "؟", "suggestions": ["أ"]}
    assert list(q) == ["run_id", "question", "suggestions"]
    assert "؟" in decoded


def test_artifact_ids_put_the_primary_item_first(monkeypatch):
    """messages.artifact_ids (echoed on ``done``) follows workspace_item_created order,
    except an item marked ``primary`` (an extended search's merged answer, emitted LAST so
    the clients auto-open it) goes FIRST — the message's citation target."""
    events, _ = _stream(monkeypatch, [
        {"type": "workspace_item_created", "item_id": "wi-1", "kind": "agent_search", "title": "1"},
        {"type": "workspace_item_created", "item_id": "wi-2", "kind": "agent_search", "title": "2"},
        {"type": "workspace_item_created", "item_id": "wi-merged", "kind": "agent_search", "title": "م",
         "primary": True},
        {"type": "token", "text": "الخلاصة"},
        {"type": "done", "usage": {}},
    ])
    (done,) = [d for n, d in events if n == "done"]
    assert done["artifact_ids"] == ["wi-merged", "wi-1", "wi-2"]


def test_progress_relays_the_parallel_searches(monkeypatch):
    events, _ = _stream(monkeypatch, [
        {"type": "agent_progress", "stage": "searching", "text": "بحث",
         "data": {"sources": 7, "searches": {"total": 3, "done": 1}, "phase": "case"}},
        {"type": "agent_progress", "stage": "searching", "text": "بحث", "data": {"sources": 2}},
        {"type": "done", "usage": {}},
    ])
    progress = [d["data"] for n, d in events if n == "agent_progress"]
    assert progress == [{"sources": 7, "searches": {"total": 3, "done": 1}}, {"sources": 2}]
