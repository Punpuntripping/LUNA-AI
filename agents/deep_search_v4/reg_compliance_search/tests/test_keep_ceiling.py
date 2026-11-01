"""Regulation search keeps 10 per sub-query up to 10 queries, 8 at 11-12, 7 from 13."""
from __future__ import annotations

import dataclasses

import pytest

from agents.deep_search_v4.orchestrator import FullLoopDeps
from agents.deep_search_v4.reg_compliance_search.loop import (
    MAX_KEEP_PER_SUBQUERY,
    keep_for_query_count,
)
from agents.deep_search_v4.reg_compliance_search.models import LoopState


def _default(cls, name: str):
    return {f.name: f.default for f in dataclasses.fields(cls)}[name]


@pytest.mark.parametrize(
    ("queries", "keep"),
    [(1, 10), (4, 10), (10, 10), (11, 8), (12, 8), (13, 7), (14, 7), (20, 7)],
)
def test_keep_by_query_count(queries, keep):
    assert keep_for_query_count(queries) == keep


def test_defaults_do_not_lower_the_steps():
    # A default below 10 would quietly cap every turn under the steps above.
    assert _default(LoopState, "reranker_max_keep") == MAX_KEEP_PER_SUBQUERY == 10
    assert _default(FullLoopDeps, "reg_max_keep") == MAX_KEEP_PER_SUBQUERY


def test_loop_state_has_no_result_budget():
    # A budget here would bring back the N-based keep this file exists to rule out.
    assert "result_budget" not in {f.name for f in dataclasses.fields(LoopState)}
