"""``runner._load_user_words`` — the user's own words up to the upload (owner, 2026-11-12)."""
from __future__ import annotations

from types import SimpleNamespace

from agents.memory.attachment_segmenter import runner as R


class _Q:
    """A tiny PostgREST-like query over in-memory rows."""

    def __init__(self, rows):
        self._rows = list(rows)

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._rows = [r for r in self._rows if str(r.get(col)) == str(val)]
        return self

    def lt(self, col, val):
        self._rows = [r for r in self._rows if str(r.get(col)) < str(val)]
        return self

    def order(self, col, desc=False):
        self._rows.sort(key=lambda r: str(r.get(col)), reverse=desc)
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def execute(self):
        return SimpleNamespace(data=self._rows)


class _SB:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        if name not in self.tables:
            raise RuntimeError(f"no table {name}")
        return _Q(self.tables[name])


ROW = {"item_id": "doc-1", "conversation_id": "c1", "created_at": "2026-11-01T10:00:05"}


def _messages(*items):
    return [
        {"message_id": mid, "conversation_id": "c1", "role": role, "content": text, "created_at": at}
        for mid, role, text, at in items
    ]


def test_attached_message_in_full_then_earlier_user_messages_newest_first():
    sb = _SB({
        "message_attachments": [{"message_id": "m3", "document_id": "doc-1"}],
        "messages": _messages(
            ("m1", "user", "سؤالي الأول عن العقد", "2026-11-01T09:00:00"),
            ("m2", "assistant", "رد المساعد لا يدخل", "2026-11-01T09:01:00"),
            ("m3", "user", "انظر جدول المبالغ في الصفحة الثالثة", "2026-11-01T10:00:00"),
            ("m4", "user", "رسالة بعد الرفع لا تدخل", "2026-11-01T11:00:00"),
        ),
    })
    words = R._load_user_words(sb, ROW)
    assert words.startswith(R._ATTACHED_HEADING + "\nانظر جدول المبالغ في الصفحة الثالثة")
    assert R._EARLIER_HEADING in words and "- سؤالي الأول عن العقد" in words
    assert "رد المساعد" not in words  # never the assistant
    assert "بعد الرفع" not in words  # nothing after the upload


def test_earlier_messages_stop_at_the_budget(monkeypatch):
    monkeypatch.setattr(R, "USER_WORDS_EARLIER_BUDGET", 30)
    sb = _SB({
        "message_attachments": [{"message_id": "m9", "document_id": "doc-1"}],
        "messages": _messages(
            ("m1", "user", "أ" * 50, "2026-11-01T08:00:00"),
            ("m2", "user", "ب" * 20, "2026-11-01T09:00:00"),
            ("m9", "user", "مع الملف", "2026-11-01T10:00:00"),
        ),
    })
    words = R._load_user_words(sb, ROW)
    assert "- " + "ب" * 20 in words  # newest first, fits
    assert "- " + "أ" * 10 + " …" in words  # the next one is cut at the 10 chars left
    assert "أ" * 50 not in words


def test_without_an_attachment_link_anchors_on_the_item_time():
    sb = _SB({
        "message_attachments": [],
        "messages": _messages(
            ("m1", "user", "قبل الرفع", "2026-11-01T09:00:00"),
            ("m2", "user", "بعد الرفع", "2026-11-01T12:00:00"),
        ),
    })
    words = R._load_user_words(sb, ROW)
    assert R._ATTACHED_HEADING not in words
    assert "قبل الرفع" in words and "بعد الرفع" not in words


def test_failures_return_empty_words():
    assert R._load_user_words(_SB({}), ROW) == ""
    assert R._load_user_words(_SB({}), {"item_id": "", "conversation_id": ""}) == ""
