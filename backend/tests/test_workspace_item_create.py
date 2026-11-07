"""create_workspace_item — the one insert every agent card goes through.

Regression for convo a6f68263 (2026-11-06): a deep search resumed after a
clarify question stored the pasted pleading + question + reply as
describe_query (6,329 chars). The CHECK on that column (migration 038) is 4000,
the insert failed with SQLSTATE 23514, the report was lost, and the user was
told the conversation had hit the 15-item cap (it held 6).

No live DB: the Supabase client is a small fake whose insert either records the
payload or raises the same APIError PostgREST returns.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from postgrest.exceptions import APIError

from backend.app.errors import LunaHTTPException
from backend.app.services.workspace_service import (
    DESCRIBE_QUERY_MAX_CHARS,
    create_workspace_item,
)

_CAP_DETAIL = "وصلت المحادثة إلى الحد الأقصى للعناصر — احذف عنصرًا قبل الإضافة"


class _FakeTable:
    def __init__(self, sb: "_FakeSupabase") -> None:
        self._sb = sb

    def insert(self, payload: dict) -> "_FakeTable":
        self._sb.payload = payload
        return self

    def execute(self):
        if self._sb.error is not None:
            raise self._sb.error
        return type("R", (), {"data": [{"item_id": "wi-1", **self._sb.payload}]})()


class _FakeSupabase:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.payload: dict = {}

    def table(self, name: str) -> _FakeTable:
        assert name == "workspace_items"
        return _FakeTable(self)


def _create(sb: _FakeSupabase, describe_query: str | None) -> dict:
    return create_workspace_item(
        sb,
        "user-1",
        kind="agent_search",
        created_by="agent",
        title="عنوان",
        conversation_id="conv-1",
        content_md="# تقرير",
        describe_query=describe_query,
    )


def test_limit_matches_the_db_check():
    sql = (
        Path(__file__).resolve().parents[2]
        / "shared/db/migrations/038_workspace_items_describe_query.sql"
    ).read_text(encoding="utf-8")
    assert re.search(rf"char_length\(describe_query\)\s*<=\s*{DESCRIBE_QUERY_MAX_CHARS}\b", sql)


def test_short_describe_query_is_stored_untouched():
    sb = _FakeSupabase()
    _create(sb, "نص السؤال كما كُتب")
    assert sb.payload["describe_query"] == "نص السؤال كما كُتب"


def test_resumed_pleading_is_clipped_keeping_message_start_and_reply():
    original = "فضيلة رئيس وأعضاء الدائرة " + "و" * 5678
    exchange = "سؤال التوضيح: هل تريد إثبات الملكية؟\nردّ المستخدم: لا أرغب حالياً في إثبات الملكية"
    query = original + "\n\n" + exchange
    assert len(query) > DESCRIBE_QUERY_MAX_CHARS

    sb = _FakeSupabase()
    _create(sb, query)

    stored = sb.payload["describe_query"]
    assert len(stored) == DESCRIBE_QUERY_MAX_CHARS
    assert stored.startswith("فضيلة رئيس وأعضاء الدائرة")
    assert stored.endswith(exchange)


def test_exactly_at_limit_is_not_clipped():
    query = "ق" * DESCRIBE_QUERY_MAX_CHARS
    sb = _FakeSupabase()
    _create(sb, query)
    assert sb.payload["describe_query"] == query


def test_cap_trigger_maps_to_cap_message():
    sb = _FakeSupabase(APIError({
        "message": "workspace_items_cap_exceeded",
        "code": "23514",
        "hint": "Conversation has reached the 15-item limit.",
        "details": None,
    }))
    with pytest.raises(LunaHTTPException) as exc:
        _create(sb, "سؤال")
    assert exc.value.status_code == 400
    assert exc.value.detail == _CAP_DETAIL


def test_other_check_violation_is_not_reported_as_the_cap():
    sb = _FakeSupabase(APIError({
        "message": 'new row for relation "workspace_items" violates check constraint "workspace_content_shape"',
        "code": "23514",
        "hint": None,
        "details": None,
    }))
    with pytest.raises(LunaHTTPException) as exc:
        _create(sb, "سؤال")
    assert exc.value.status_code == 500
    assert exc.value.detail != _CAP_DETAIL
