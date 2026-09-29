"""Shared fixtures for case_search tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.deep_search_v4.case_search.models import (
    CaseSearchDeps,
    SearchResult,
)


@pytest.fixture
def sample_case_row() -> dict:
    """A single case row matching the hybrid_search_cases RPC schema."""
    return {
        "court": "المحكمة التجارية بالرياض",
        "city": "الرياض",
        "court_level": "first_instance",
        "case_number": "1445/3/2001",
        "judgment_number": "JDG-2001",
        "date_hijri": "1445/03/15",
        "content": (
            "الوقائع: تعاقد الطرفان على توريد بضاعة بقيمة 500,000 ريال. "
            "المطالبات: يطالب المدعي بإلزام المدعى عليه بسداد المبلغ المتبقي. "
            "تسبيب الحكم: ثبت للمحكمة وجود عقد موقع بين الطرفين."
        ),
        "legal_domains": ["منازعات البيع والشراء", "عقود تجارية"],
        "referenced_regulations": [
            {"النظام": "نظام المحكمة التجارية", "الرقم": "77"},
            {"النظام": "نظام المعاملات المدنية", "الرقم": "418"},
        ],
        "appeal_result": "تأييد",
        "appeal_court": "محكمة الاستئناف التجارية",
        "appeal_date_hijri": "1445/06/20",
        "details_url": "https://najiz.sa/cases/1445-3-2001",
        "score": 0.85,
        "case_ref": "CASE-1445-3-2001",
    }


@pytest.fixture
def mock_supabase(sample_case_row) -> MagicMock:
    """Mock Supabase client where .rpc().execute() returns sample case data."""
    client = MagicMock()
    rpc_response = MagicMock()
    rpc_response.data = [sample_case_row]
    execute_mock = MagicMock(return_value=rpc_response)
    rpc_chain = MagicMock()
    rpc_chain.execute = execute_mock
    client.rpc.return_value = rpc_chain
    return client


@pytest.fixture
def mock_embedding_fn() -> AsyncMock:
    """Async function returning a 4096-dim zero vector."""
    fn = AsyncMock()
    fn.return_value = [0.0] * 4096
    return fn


@pytest.fixture
def case_search_deps(mock_supabase, mock_embedding_fn) -> CaseSearchDeps:
    """CaseSearchDeps fixture with mock supabase and mock embedding_fn."""
    return CaseSearchDeps(
        supabase=mock_supabase,
        embedding_fn=mock_embedding_fn,
        score_threshold=0.005,
        mock_results=None,
        _events=[],
        _search_log=[],
    )


@pytest.fixture
def sample_search_results() -> list[SearchResult]:
    """List of SearchResult dataclasses for reranker tests."""
    return [
        SearchResult(
            query="سوابق قضائية في الفصل التعسفي",
            raw_markdown=(
                "## نتائج البحث في السوابق القضائية -- 3 نتيجة\n\n"
                "### [1] حكم: المحكمة العمالية بالرياض (ابتدائي)\n"
                "**رقم القضية:** 1445/3/2001\n"
                "**التاريخ:** 1445/03/15\n\n"
                "الوقائع: فصل العامل تعسفياً بدون إنذار مسبق...\n\n"
                "**المجالات القانونية:** منازعات عمالية\n"
            ),
            result_count=3,
        ),
        SearchResult(
            query="تعويض العامل المفصول عن أجور متأخرة",
            raw_markdown=(
                "## نتائج البحث في السوابق القضائية -- 2 نتيجة\n\n"
                "### [1] حكم: المحكمة العمالية بجدة (ابتدائي)\n"
                "**رقم القضية:** 1446/1/500\n"
                "**التاريخ:** 1446/01/10\n\n"
                "الوقائع: طالب العامل بأجور متأخرة لمدة 3 أشهر...\n\n"
                "**المجالات القانونية:** منازعات عمالية\n"
            ),
            result_count=2,
        ),
    ]
