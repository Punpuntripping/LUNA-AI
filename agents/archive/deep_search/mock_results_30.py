"""Combined mock executor results for all 30 test_queries.json queries.

Merges mock_results_1_15.py and mock_results_16_30.py into one dict and
exposes a get_mock_result() helper for use in integration tests.

Usage in tests::

    from agents.deep_search.mock_results_30 import get_mock_result

    # Patch all three executors with query-specific mocks:
    result = get_mock_result(query_id=13, tool="regulations")

Quality matrix summary
----------------------
Diverse by design — the planner LLM should exercise:
  • Strong results  → straight-through reporting
  • Medium results  → one extra round or cross-feed
  • Weak results    → ask_user() or partial-report path
  • ALL-WEAK (18)   → produce report with "لم نعثر" disclaimer
  • OUT-OF-SCOPE (4, 12) → TaskEnd reason="out_of_scope"

Cross-feed signals embedded
-----------------------------
  Q1  regs mention "وزارة الداخلية / أمان" → compliance cross-feed
  Q6  cases mention "المادة 98" → regulations cross-feed
  Q9  regs mention "وزارة الداخلية" → compliance cross-feed
  Q14 regulations weak → compliance (إيجار) cross-feed
  Q20 compliance=EMPTY after regs/cases → ask_user() path
  Q25 regulations=WEAK → compliance carries the answer (نافذ العقاري)
  Q26 regulations mention "الإفراغ" → compliance cross-feed
  Q28 cases=STRONG → regulations cross-feed for asset-hiding articles
"""
from __future__ import annotations

from agents.deep_search.mock_results_1_15 import MOCK_RESULTS_1_15
from agents.deep_search.mock_results_16_30 import MOCK_RESULTS_16_30

# Single merged lookup dict  {query_id: {tool: result_str}}
MOCK_RESULTS_30: dict[int, dict[str, str]] = {
    **MOCK_RESULTS_1_15,
    **MOCK_RESULTS_16_30,
}


def get_mock_result(query_id: int, tool: str) -> str:
    """Return the mock result string for *tool* at *query_id*.

    Args:
        query_id: 1-based query number matching test_queries.json.
        tool: one of "regulations", "cases", "compliance".

    Raises:
        KeyError: if query_id or tool is not found.
    """
    entry = MOCK_RESULTS_30[query_id]
    return entry[tool]


def all_query_ids() -> list[int]:
    """Return sorted list of all available query IDs."""
    return sorted(MOCK_RESULTS_30.keys())
