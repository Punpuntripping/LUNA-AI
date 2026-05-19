"""Standalone mock test for the compliance loop.

DEAD INFRASTRUCTURE WARNING: this module imports ``PartialURA`` and
``compliance_search.ura_runner.run_compliance_from_partial_ura`` -- both were
removed when the two-stage merger was replaced by ``ura/merger.build_ura_from_phases``
(see ``ura/__init__.py``). The file therefore cannot import-clean and the
``run_mock_test`` / ``run_live_test`` entry points are non-functional.

It is kept only as a reference fixture. ``_build_mock_reg_results`` has been
updated to the URA v3.0 two-view schema (chunk-shaped ``RegURAResult``) so the
result-construction code byte-compiles and matches the current contract; the
surrounding harness still needs a rewrite against ``build_ura_from_phases``
before it can run.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")

QUERIES_PATH = Path(__file__).resolve().parent.parent.parent / "test_queries.json"

MOCK_COMPLIANCE_MD = """
## نتائج البحث في الخدمات الحكومية -- 2 نتيجة

### [1] خدمة: تقديم بلاغ عمالي -- وزارة الموارد البشرية والتنمية الاجتماعية
**درجة الصلة:** RRF: 0.0128
**المنصة:** مسار | **الرابط:** https://www.hrsd.gov.sa/

<references>
- SVC-HRSD-001 | تقديم بلاغ عمالي | وزارة الموارد البشرية
- SVC-HRSD-002 | حساب مكافأة نهاية الخدمة | وزارة الموارد البشرية
</references>
"""


def _load_queries() -> dict[int, dict]:
    data = json.loads(QUERIES_PATH.read_text(encoding="utf-8"))
    return {q["id"]: q for q in data["queries"] if q.get("text")}


def _build_mock_reg_results():
    """Build mock URA v3.0 ``RegURAResult`` shells (chunk-shaped, two-view)."""
    from agents.deep_search_v4.ura.schema import RegURAResult

    return [
        RegURAResult(
            ref_id="reg:550e8400-e29b-41d4-a716-446655440000",
            source_type="reg_chunk",
            relevance="high",
            reasoning="نص صريح في مكافأة نهاية الخدمة",
            reg_title="نظام العمل",
            reg_scope="علاقات العمل",
            chunk_content=(
                "يستحق العامل عند انتهاء عقده مكافأة نهاية الخدمة عن كل سنة..."
            ),
        ),
        RegURAResult(
            ref_id="reg:550e8400-e29b-41d4-a716-446655440001",
            source_type="reg_chunk",
            relevance="medium",
            reasoning="متعلق باحتساب بدل الإجازات",
            reg_title="نظام العمل",
            reg_scope="علاقات العمل",
            chunk_content="للعامل الحق في إجازة سنوية مدفوعة الأجر...",
        ),
    ]


async def run_mock_test(query_id: int) -> None:
    queries = _load_queries()
    if query_id not in queries:
        print(f"Query #{query_id} not found")
        return

    query_text = queries[query_id]["text"]
    print(f"\n[Query #{query_id}] {query_text[:100]}...")
    print(f"Building mock PartialURA...")

    partial = _build_mock_partial_ura(query_id, query_text)
    print(f"  reg results: {len(partial.results)}")
    print(f"  sector_filter: {partial.sector_filter}")

    from agents.deep_search_v4.compliance_search.models import ComplianceSearchDeps
    from agents.deep_search_v4.compliance_search.ura_runner import run_compliance_from_partial_ura

    # Mock deps -- no real supabase/embedding needed
    deps = ComplianceSearchDeps(
        supabase=None,  # type: ignore[arg-type]
        embedding_fn=None,  # type: ignore[arg-type]
        mock_results={"compliance": MOCK_COMPLIANCE_MD},
    )

    print("Running compliance search (mock)...")
    result = await run_compliance_from_partial_ura(partial=partial, deps=deps)

    print(f"\n--- Compliance URA Slice ---")
    print(f"Queries used ({len(result.queries_used)}):")
    for q in result.queries_used:
        print(f"  - {q}")
    print(f"\nService results ({len(result.results)}):")
    for r in result.results:
        print(f"  [{r['domain']}] {r['ref_id']} -- {r['title']}")
        if r.get("triggered_by_ref_ids"):
            print(f"    triggered_by: {r['triggered_by_ref_ids']}")


async def run_live_test(query_id: int) -> None:
    """Real DB run -- requires SUPABASE_URL/KEY env vars."""
    from agents.utils.supabase_client import get_supabase_client
    from agents.utils.embeddings import get_embedding

    queries = _load_queries()
    if query_id not in queries:
        print(f"Query #{query_id} not found")
        return

    query_text = queries[query_id]["text"]
    print(f"\n[Live] Query #{query_id}: {query_text[:100]}...")

    partial = _build_mock_partial_ura(query_id, query_text)
    # Use real embedding for the query
    partial.original_query = query_text

    from agents.deep_search_v4.compliance_search.models import ComplianceSearchDeps
    from agents.deep_search_v4.compliance_search.ura_runner import run_compliance_from_partial_ura

    deps = ComplianceSearchDeps(
        supabase=get_supabase_client(),
        embedding_fn=get_embedding,
    )

    result = await run_compliance_from_partial_ura(partial=partial, deps=deps)
    print(f"Queries used: {len(result.queries_used)}")
    print(f"Services found: {len(result.results)}")
    for r in result.results:
        print(f"  {r['title']} ({r['source_type']}) triggers={r['triggered_by_ref_ids']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query-id", type=int, default=10)
    parser.add_argument("--live", action="store_true", help="Run with real DB (no mock)")
    args = parser.parse_args()

    if args.live:
        asyncio.run(run_live_test(args.query_id))
    else:
        asyncio.run(run_mock_test(args.query_id))


if __name__ == "__main__":
    main()
