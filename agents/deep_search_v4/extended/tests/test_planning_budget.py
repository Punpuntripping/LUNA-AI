"""Budget modes (§4.3, D22, D23, D33): the mode pick, the budget block, the draft estimate."""
from __future__ import annotations

import pytest

from agents.deep_search_v4.extended import budget as B
from agents.deep_search_v4.extended.models import ExtendedRequest


def req(mode: str = "reg_compliance_led", cap: int | None = 8) -> ExtendedRequest:
    return ExtendedRequest(title="t", mode=mode, max_sub_queries=cap, focus="f")


# ── the table ────────────────────────────────────────────────────────────────


def test_the_mode_table_is_the_q12_draft():
    assert {m: (x.max_searches, x.max_per_side, x.max_full) for m, x in B.MODES.items()} == {
        15: (3, 8, 2), 20: (3, 8, 3), 30: (4, 10, 4), 40: (5, 10, 5), 50: (6, 12, 6),
    }
    assert all(x.points == m and not x.no_budget for m, x in B.MODES.items())
    assert B.NO_BUDGET.no_budget and B.NO_BUDGET.label == "none"
    assert (B.NO_BUDGET.max_searches, B.NO_BUDGET.max_per_side, B.NO_BUDGET.max_full) == (None, None, None)
    assert B.MARGINS == {"pro": 5, "pro_plus": 5, "max": 10, "dev": 10}  # dev = internal test plan
    assert (B.MIN_POINTS, B.NO_BUDGET_ABOVE, B.NO_BUDGET_MAX_SEARCHES) == (10, 50, None)


# ── the pick (D23, D33) ──────────────────────────────────────────────────────


@pytest.mark.parametrize("left, plan, mode", [
    (15, "pro", 20),         # owner's example: 15 left → the 20 mode
    (10, "pro", 15),         # 10 left → the 15 mode
    (20, "pro", 20),
    (24.9, "pro", 20),
    (25, "pro", 30),
    (15, "max", 20),
    (20, "max", 30),
    (30, "max", 40),
    (45, "max", 50),
    (50, "max", 50),
    (50, "pro_plus", 50),
    (10, "max", 20),
])
def test_the_biggest_mode_within_left_plus_margin(left, plan, mode):
    assert B.pick_mode(left, plan) is B.MODES[mode]


@pytest.mark.parametrize("left", [50.01, 51, 120, 500])
def test_over_fifty_left_is_no_budget(left):
    assert B.pick_mode(left, "max") is B.NO_BUDGET


def test_unlimited_is_no_budget():
    assert B.pick_mode(None, "pro") is B.NO_BUDGET


@pytest.mark.parametrize("left", [9.99, 5, 0, -3])
def test_under_ten_left_is_no_extended_search(left):
    assert B.pick_mode(left, "max") is None


@pytest.mark.parametrize("plan", ["free", "basic", None, "", "enterprise"])
def test_other_plans_never_get_a_mode(plan):
    assert B.pick_mode(30, plan) is None
    assert B.pick_mode(None, plan) is None


def test_mode_by_label_round_trips():
    for m in (*B.MODES.values(), B.NO_BUDGET):
        assert B.mode_by_label(m.label) is m
    assert B.mode_by_label("70") is None and B.mode_by_label("x") is None and B.mode_by_label(None) is None


# ── the block (§4.3) ─────────────────────────────────────────────────────────


def test_the_fifteen_mode_block_is_the_plan_text():
    assert B.budget_block(B.MODES[15]) == (
        "<budget>\n"
        "The most this extended search may use:\n"
        "- at most 3 searches;\n"
        "- per search, at most 8 sub-queries on each side it runs (regulations, judgments) — a `full` search may run 8 + 8;\n"
        "- at most 2 searches may be `full`; the others search regulations only or judgments only.\n"
        "These are limits, not targets: use less when the question needs less.\n"
        "</budget>"
    )


def test_a_mode_where_every_search_may_be_full_says_so():
    block = B.budget_block(B.MODES[30])
    assert "- at most 4 searches;" in block
    assert "at most 10 sub-queries on each side" in block and "may run 10 + 10" in block
    assert "- any of the 4 may be `full`;" in block
    assert "points" not in block.lower() and "نقط" not in block


def test_the_no_budget_block_is_the_d33_text():
    assert B.budget_block(B.NO_BUDGET) == (
        "<budget>\n"
        "No budget limit for this extended search: plan the searches, sides and caps the question needs.\n"
        "Fewer is still better — every search adds minutes and a report the user must read.\n"
        "</budget>"
    )


# ── the estimate (§4.3, §2.8 draft) ──────────────────────────────────────────


def test_one_search_has_no_merger():
    assert B.estimate_points([req(cap=8)]) == pytest.approx(1.95 + 8 * 0.285)


def test_several_searches_add_the_merger_and_the_planner():
    reqs = [req("full", 8), req("reg_compliance_led", 6), req("case_led", 5)]
    expected = 3 * 1.95 + (16 + 6 + 5) * 0.285 + 2.0 + 0.4
    assert B.estimate_points(reqs, planner_points=0.4) == pytest.approx(expected)


def test_an_uncapped_side_is_priced_at_todays_average():
    assert B.sub_queries_of(req("reg_compliance_led", None)) == 10
    assert B.sub_queries_of(req("case_led", None)) == 7
    assert B.sub_queries_of(req("full", None)) == 17
    assert B.sub_queries_of({"mode": "full", "max_sub_queries": 4}) == 8


def test_the_fifteen_mode_used_to_the_full_is_about_twenty():
    # §2.8: 3 searches, 40 sub-queries (2 full at 8+8, 1 one-sided at 8) + merger + planner ≈ 20.
    reqs = [req("full", 8), req("full", 8), req("reg_compliance_led", 8)]
    assert round(B.estimate_points(reqs, planner_points=0.4)) == 20


def test_the_ordinary_estimate():
    assert B.ordinary_estimate_points() == pytest.approx(1.95 + 12 * 0.285)
    assert B.round_points(B.ordinary_estimate_points()) == 5


@pytest.mark.parametrize("x, n", [(14.5, 15), (14.49, 14), (0.5, 1), (5.37, 5), (13.0, 13)])
def test_round_half_up(x, n):
    assert B.round_points(x) == n
