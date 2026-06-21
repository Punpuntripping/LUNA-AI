"""Offline sanity check for reranker_runs forensic dicts (no DB, no network).

Builds fake per-domain reranker outputs, runs them through the URA adapters and
the service-layer _build_row, and asserts the persisted shape for ALL three
domains: bare-UUID ref_id, source_table, title, and dropped_results (llm + cap).

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/verify_reranker_forensics.py
"""
from __future__ import annotations

from agents.deep_search_v4.reg_search.models import (
    RerankedResult,
    RerankerQueryResult as RegRQR,
)
from agents.deep_search_v4.case_search.models import (
    RerankedCaseResult,
    RerankerQueryResult as CaseRQR,
)
from agents.deep_search_v4.compliance_search.models import (
    ComplianceSearchResult,
    RerankedServiceResult,
)
from agents.deep_search_v4.ura.reg_adapter import reg_to_rqr
from agents.deep_search_v4.ura.case_adapter import case_to_rqr
from agents.deep_search_v4.ura.compliance_adapter import compliance_to_rqr
from backend.app.services.retrieval_artifacts_service import _build_row

UUID_A = "b9b463fd-e358-52b3-beb9-dfbd62bcc3d7"


def _check_row(row: dict, *, source_table: str, expect_drops: int) -> None:
    k = row["kept_results"][0]
    assert k["source_table"] == source_table, k
    assert k["ref_id"] and ":" not in k["ref_id"], f"ref_id not bare uuid: {k}"
    assert k["title"], f"title empty: {k}"
    assert len(row["dropped_results"]) == expect_drops, row["dropped_results"]
    for d in row["dropped_results"]:
        assert d["source_table"] == source_table, d
        assert ":" not in d["ref_id"], d
        assert d["drop_reason"] in ("llm", "cap"), d


def check_reg() -> dict:
    rqr = RegRQR(
        query="q", rationale="r", sufficient=True,
        results=[RerankedResult(
            source_type="chunk", title="المادة (٥)", relevance="high",
            reasoning="ينطبق", db_id=UUID_A, rrf=0.9,
        )],
        dropped_count=2, summary_note="n",
        dropped_results=[
            {"db_id": "11111111-1111-1111-1111-111111111111", "title": "x",
             "reasoning": "خارج النطاق", "drop_reason": "llm", "source_type": "chunk"},
            {"db_id": "22222222-2222-2222-2222-222222222222", "title": "y",
             "reasoning": "", "drop_reason": "cap", "source_type": "chunk"},
        ],
    )
    shared = reg_to_rqr([rqr])[0]
    row = _build_row(ura_id="u", agent_family="reg_search", sub_query_index=0, rqr=shared)
    _check_row(row, source_table="chunks", expect_drops=2)
    return row


def check_case() -> dict:
    rqr = CaseRQR(
        query="q", rationale="r", sufficient=True,
        results=[RerankedCaseResult(
            title="محكمة | 123 | 1445", relevance="high", reasoning="مطابق",
            db_id="case-ref-abc", db_uuid=UUID_A, score=0.8, source_type="case",
        )],
        dropped_count=1, summary_note="n",
        dropped_results=[
            {"source_table": "cases", "ref_id": "33333333-3333-3333-3333-333333333333",
             "title": "z", "reasoning": "غير ذي صلة", "drop_reason": "llm",
             "source_type": "case"},
        ],
    )
    shared = case_to_rqr([rqr])[0]
    row = _build_row(ura_id="u", agent_family="case_search", sub_query_index=0, rqr=shared)
    _check_row(row, source_table="cases", expect_drops=1)
    assert row["kept_results"][0]["ref_id"] == UUID_A  # db_uuid preferred
    return row


def check_compliance() -> dict:
    svc = RerankedServiceResult(
        service_ref="SR-1", service_id=UUID_A, title="خدمة حكومية",
        content="...", provider_name="جهة", relevance="high", reasoning="مطابق",
        score=0.7,
    )
    result = ComplianceSearchResult(
        kept_results=[svc], quality="strong", queries_used=["q1"], rounds_used=1,
    )
    shared = compliance_to_rqr(
        result,
        per_query_service_refs={"q1": ["SR-1"]},
        per_query_dropped={"q1": [
            {"service_id": "44444444-4444-4444-4444-444444444444", "title": "d",
             "reasoning": "خارج النطاق", "drop_reason": "llm"},
            {"service_id": "55555555-5555-5555-5555-555555555555", "title": "e",
             "reasoning": "", "drop_reason": "cap"},
        ]},
        original_focus_instruction="q1",
    )[0]
    row = _build_row(ura_id="u", agent_family="compliance_search", sub_query_index=0, rqr=shared)
    _check_row(row, source_table="services", expect_drops=2)
    assert row["kept_results"][0]["ref_id"] == UUID_A  # real services.id, not the hash
    return row


def main() -> None:
    reg, case, comp = check_reg(), check_case(), check_compliance()
    print("OK — all 3 domains: bare uuid + source_table + title + llm/cap drops")
    print("reg  kept :", reg["kept_results"][0])
    print("case kept :", case["kept_results"][0])
    print("comp kept :", comp["kept_results"][0])
    print("comp drops:", comp["dropped_results"])


if __name__ == "__main__":
    main()
