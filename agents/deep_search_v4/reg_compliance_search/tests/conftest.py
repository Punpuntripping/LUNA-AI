"""Shared fixtures + a sync Supabase fake for reg_search package tests.

The fake mirrors the tiny slice of the supabase-py surface the search + unfold
layers touch — ``rpc(name, params).execute()`` and
``table(name).select(cols).in_(col, ids)/.eq(col, val)/.maybe_single().execute()``
— returning ``.data`` from in-memory table stores. Every call is logged so
tests can assert the RPC params (``p_sectors`` presence) and the per-type
content-fetch dispatch (which table, which column list, which ids).
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agents.deep_search_v4.reg_compliance_search.models import RegComplianceSearchDeps


# -- Sync Supabase fake -------------------------------------------------------


class _Resp:
    def __init__(self, data) -> None:
        self.data = data


class _RpcQuery:
    def __init__(self, data) -> None:
        self._data = data

    def execute(self) -> _Resp:
        return _Resp(self._data)


class _TableQuery:
    def __init__(self, name: str, rows: list[dict], log: list[dict]) -> None:
        self.name = name
        self.rows = rows
        self._cols = None
        self._in = None
        self._eq = None
        self._single = False
        self._log = log

    def select(self, cols):
        self._cols = cols
        return self

    def in_(self, col, ids):
        self._in = (col, list(ids))
        return self

    def eq(self, col, val):
        self._eq = (col, val)
        return self

    def maybe_single(self):
        self._single = True
        return self

    def execute(self) -> _Resp:
        rows = list(self.rows)
        if self._in is not None:
            col, ids = self._in
            idset = set(ids)
            rows = [r for r in rows if r.get(col) in idset]
        if self._eq is not None:
            col, val = self._eq
            rows = [r for r in rows if r.get(col) == val]
        self._log.append({
            "table": self.name,
            "cols": self._cols,
            "in": self._in,
            "eq": self._eq,
            "single": self._single,
        })
        if self._single:
            return _Resp(rows[0] if rows else None)
        return _Resp(rows)


class MockSupabase:
    """Records rpc + table calls; returns configured in-memory data."""

    def __init__(self, rpc_handler=None, tables: dict[str, list[dict]] | None = None) -> None:
        self.rpc_calls: list[tuple[str, dict]] = []
        self.table_log: list[dict] = []
        self._rpc_handler = rpc_handler or (lambda name, params: [])
        self._tables = tables or {}

    def rpc(self, name: str, params: dict) -> _RpcQuery:
        self.rpc_calls.append((name, dict(params)))
        return _RpcQuery(self._rpc_handler(name, dict(params)))

    def table(self, name: str) -> _TableQuery:
        return _TableQuery(name, self._tables.get(name, []), self.table_log)


# -- Row builders -------------------------------------------------------------


def rpc_row(source_type: str, source_id: str, score: float, **over) -> dict:
    """A ``search_topics`` RPC output row (the 11-column TABLE shape)."""
    base = {
        "source_type": source_type,
        "topic_id": f"topic-{source_id}",
        "topic_text": f"موضوع {source_id}",
        "score": score,
        "source_ref": f"sref-{source_id}",
        "source_id": source_id,
        "doc_ref": f"docref-{source_id}",
        "doc_id": f"doc-{source_id}",
        "title": f"عنوان {source_id}",
        "sectors": ["الصحة"],
        "entity_ref": "17405",
    }
    base.update(over)
    return base


def chunk_content(source_id: str, corpus: str = "with_articles", **over) -> dict:
    """A ``chunks_v2`` content row (CHUNK_SELECT_TOPICS columns)."""
    base = {
        "id": source_id,
        "chunk_ref": f"chref-{source_id}",
        "regulation_id": f"reg-{source_id}",
        "position": 1,
        "prev_chunk_id": None,
        "next_chunk_id": None,
        "title": f"مقطع {source_id}",
        "summary": f"ملخص المقطع {source_id}",
        "context": "سياق المقطع الحالي",
        "corpus": corpus,
    }
    base.update(over)
    return base


def circular_content(source_id: str, **over) -> dict:
    """A ``circulars`` content row (CIRCULAR_SELECT columns + embedded entity)."""
    base = {
        "id": source_id,
        "circ_ref": f"circ-{source_id}",
        "title": f"تعميم {source_id}",
        "content": "محتوى التعميم بشأن إلزام المنشآت. " * 5,
        "entity_ref": "17405",
        "source": "https://example.gov.sa/circ",
        "entities": {"entity_name": "الهيئة العامة للغذاء والدواء"},
    }
    base.update(over)
    return base


def service_content(source_id: str, **over) -> dict:
    """A ``services`` content row (SERVICE_SELECT columns)."""
    base = {
        "id": source_id,
        "service_ref": f"svc-{source_id}",
        "service_name_ar": f"خدمة {source_id}",
        "provider_name": "وزارة العدل",
        "service_context": "وصف مختصر للخدمة الحكومية.",
        "intro_title": f"مقدمة خدمة {source_id}",
        "intro_description": "شرح تفصيلي",
        "steps": ["الخطوة الأولى", "الخطوة الثانية"],
        "requirements": ["متطلب"],
        "required_documents": ["مستند"],
        "service_url": "https://example.gov.sa/svc",
        "url": "",
    }
    base.update(over)
    return base


def regulation_meta_row(reg_id: str, name: str = "نظام العمل", scope: str = "نطاق النظام") -> dict:
    """A ``regulations_v2`` meta row (clean_title/title/scope for the chunk unfold)."""
    return {"id": reg_id, "clean_title": name, "title": name, "scope": scope}


# -- Deps fixtures ------------------------------------------------------------


@pytest.fixture
def mock_embedding_fn() -> AsyncMock:
    fn = AsyncMock()
    fn.return_value = [0.0] * 1024
    return fn


def make_deps(supabase, mock_embedding_fn) -> RegComplianceSearchDeps:
    return RegComplianceSearchDeps(
        supabase=supabase,
        embedding_fn=mock_embedding_fn,
        _events=[],
    )
