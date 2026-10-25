"""The deep_search `status` events never reach the wire.

The executor loops narrate onto `status`: reranker internals (keep/drop counts,
thresholds, rounds, channel taxonomy) AND every sub-query the expanders wrote
(«جاري البحث في السوابق القضائية: …», «جاري البحث في الأنظمة واللوائح: …»).
The sub-queries used to be whitelisted into the «بحث معمق» window; they are the
search logic itself, so since 2026-10-26 the relay drops every `status` line —
nothing for the client to read, in the UI or in the network tab. Progress still
reaches the client through `agent_progress` (static stage labels + counts).

Drives the real ``send_message_stream`` with a scripted ``handle_message``
(harness shared with test_next_steps_relay).
"""
from __future__ import annotations

import json

import pytest

from backend.app.services import message_service as ms
from backend.tests.test_next_steps_relay import (  # noqa: F401 - _env is an autouse fixture
    _FakeSupabase,
    _drain,
    _env,
    _script,
)

_STATUS_LINES = [
    # sub-queries — what the expanders wrote
    "جاري البحث في الأنظمة واللوائح: فصل الموظف بسبب التغيب عن العمل...",
    "جاري البحث في السوابق القضائية: تعويض الفصل التعسفي...",
    "بحث [principle]: شروط الإنهاء الفوري من العامل...",
    "بحث [basis]: الدفع بعدم قبول الدعوى لخلو الإخطار السابق...",
    # internals
    "تم توليد 4 استعلامات عبر 3 قنوات (principle:2, facts:1, basis:1)",
    "تم تصفية النتائج: 2 نتيجة محتفظ بها، 145 محذوفة",
    "سطر جديد أضافه مطوّر في حلقة تنفيذية لاحقاً",
]


def test_no_status_line_reaches_the_client(monkeypatch):
    monkeypatch.setattr(ms, "handle_message", _script([
        {"type": "agent_progress", "stage": "searching",
         "text": "البحث في الأنظمة واللوائح", "data": {"queries": 4, "sources": 9}},
        *({"type": "status", "text": line} for line in _STATUS_LINES),
        {"type": "token", "text": "الجواب."},
        {"type": "done", "usage": {}},
    ]))
    events = _drain(_FakeSupabase())
    names = [n for n, _ in events]

    assert "status" not in names
    wire = json.dumps([d for _, d in events], ensure_ascii=False)
    for line in _STATUS_LINES:
        assert line.split(": ", 1)[-1][:20] not in wire

    # Stage progress (labels + counts) still goes out, untouched.
    progress = dict(events)["agent_progress"]
    assert progress["stage"] == "searching"
    assert progress["data"] == {"queries": 4, "sources": 9}
    assert "token" in names and "done" in names


@pytest.mark.parametrize("line", _STATUS_LINES)
def test_each_line_is_dropped_on_its_own(monkeypatch, line):
    monkeypatch.setattr(ms, "handle_message", _script([
        {"type": "status", "text": line},
        {"type": "token", "text": "الجواب."},
        {"type": "done", "usage": {}},
    ]))
    assert "status" not in [n for n, _ in _drain(_FakeSupabase())]
