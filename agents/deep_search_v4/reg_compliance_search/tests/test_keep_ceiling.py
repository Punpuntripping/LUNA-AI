"""Regulation search keeps a flat 7 per sub-query, whatever the query count."""
from __future__ import annotations

import dataclasses

from agents.deep_search_v4.orchestrator import FullLoopDeps
from agents.deep_search_v4.reg_compliance_search.loop import MAX_KEEP_PER_SUBQUERY
from agents.deep_search_v4.reg_compliance_search.models import LoopState


def _default(cls, name: str):
    return {f.name: f.default for f in dataclasses.fields(cls)}[name]


def test_keep_is_7():
    assert MAX_KEEP_PER_SUBQUERY == 7


def test_defaults_match_the_keep():
    assert _default(LoopState, "reranker_max_keep") == MAX_KEEP_PER_SUBQUERY
    assert _default(FullLoopDeps, "reg_max_keep") == MAX_KEEP_PER_SUBQUERY


def test_loop_state_has_no_result_budget():
    # A budget here would bring back the N-based keep this file exists to rule out.
    assert "result_budget" not in {f.name for f in dataclasses.fields(LoopState)}
