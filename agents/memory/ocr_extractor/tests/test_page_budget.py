"""The OCR page budget (shared/quota/ocr_budget.py) — the owner's rules D2–D6,
checked against the worked examples in .claude/plans/ocr_page_budget.md §2."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from shared.quota.ocr_budget import (
    OcrBudget,
    budget_from_state,
    format_ranges,
    page_ranges,
    per_file_cap,
    pick_pages,
    plan_files,
)

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
FAR = (NOW + timedelta(days=30)).isoformat()


def _state(total, used=0, expires_at=FAR, is_expired=False):
    return {"ocr_pages_monthly": total, "ocr_pages": used, "expires_at": expires_at, "is_expired": is_expired}


def _one(total, used, pages, **kw):
    budget = budget_from_state(_state(total, used, **kw), now=NOW)
    return plan_files([("f", pages)], budget)[0]


@pytest.mark.parametrize(
    "total, used, file_pages, expires_at, expected",
    [
        # Khalid: Pro, fresh window, 606 pages
        (150, 0, 606, FAR, "1–60، 314–333، 587–606"),
        # same, plan ends in 5 days → no per-file cap (D3)
        (150, 0, 606, (NOW + timedelta(days=5)).isoformat(), "1–90، 319–348، 577–606"),
        # Pro with 30 left
        (150, 120, 606, FAR, "1–18، 307–312، 601–606"),
        # lawyer trial (50) → cap 33
        (50, 0, 606, FAR, "1–21، 308–313، 601–606"),
        # free (3), 10-page file → 2 pages, both from the start
        (3, 0, 10, FAR, "1–2"),
        # Max reads a 606-page file in full
        (1000, 0, 606, FAR, "1–606"),
    ],
)
def test_worked_examples(total, used, file_pages, expires_at, expected):
    plan = _one(total, used, file_pages, expires_at=expires_at)
    assert format_ranges(plan.pages) == expected


def test_nothing_left_reads_nothing():
    plan = _one(150, 150, 606)
    assert plan.pages == () and not plan.complete


def test_plan_not_in_the_plan_reads_nothing():
    assert _one(0, 0, 5).pages == ()


def test_one_page_left_still_reads_one():
    assert _one(150, 149, 606).pages == (1,)


def test_several_files_smallest_first_in_input_order():
    budget = budget_from_state(_state(150), now=NOW)
    plans = plan_files([("big", 606), ("img", 1), ("doc", 20)], budget)
    assert [p.item_id for p in plans] == ["big", "img", "doc"]
    assert [len(p.pages) for p in plans] == [100, 1, 20]
    assert plans[1].complete and plans[2].complete and not plans[0].complete


def test_cap_is_on_the_plan_total_not_on_what_is_left():
    # 129 left after two small files; the big one still gets the full 100 (D2 = option a)
    budget = OcrBudget(total=150, used=21, per_file_cap=100)
    plan = plan_files([("big", 606)], budget)[0]
    assert len(plan.pages) == 100


def test_second_big_file_takes_what_is_left():
    budget = budget_from_state(_state(150), now=NOW)
    a, b = plan_files([("a", 606), ("b", 700)], budget)
    assert len(a.pages) == 100 and len(b.pages) == 50


def test_unknown_page_count_reads_the_opening_pages_last():
    budget = budget_from_state(_state(150), now=NOW)
    unknown, known = plan_files([("u", None), ("k", 30)], budget)
    assert known.pages == tuple(range(1, 31))
    assert unknown.pages == tuple(range(1, 101)) and unknown.total_pages is None
    assert not unknown.complete


def test_unlimited_plan():
    budget = budget_from_state(_state(None), now=NOW)
    full, unknown = plan_files([("f", 606), ("u", None)], budget)
    assert full.pages == tuple(range(1, 607)) and full.complete
    assert unknown.pages is None and unknown.complete


@pytest.mark.parametrize(
    "delta, expired, expected",
    [
        (timedelta(days=7), False, None),            # exactly 7 days → lifted
        (timedelta(days=7, seconds=1), False, 100),  # just over → capped
        (timedelta(days=2), True, 100),              # already expired → capped
        (-timedelta(days=1), False, 100),            # in the past → capped
    ],
)
def test_seven_day_exception(delta, expired, expected):
    assert per_file_cap(150, expires_at=(NOW + delta).isoformat(), is_expired=expired, now=NOW) == expected


def test_no_expiry_is_capped():
    assert per_file_cap(150, expires_at=None, now=NOW) == 100
    assert per_file_cap(3, expires_at=None, now=NOW) == 2
    assert per_file_cap(1, expires_at=None, now=NOW) == 1  # D4 floor


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 9, 10, 33, 99, 100, 150])
@pytest.mark.parametrize("total", [1, 2, 7, 11, 101, 606, 1183])
def test_pick_pages_shape(total, n):
    pages = pick_pages(total, n)
    assert len(pages) == min(n, total)
    assert list(pages) == sorted(set(pages))
    assert all(1 <= p <= total for p in pages)
    if n < total:
        edge = n // 5
        assert pages[: n - 2 * edge] == tuple(range(1, n - 2 * edge + 1))
        if edge:
            assert pages[-edge:] == tuple(range(total - edge + 1, total + 1))


def test_ranges():
    assert page_ranges([1, 2, 3, 7, 9, 10]) == [(1, 3), (7, 7), (9, 10)]
    assert format_ranges([5]) == "5"
    assert format_ranges([]) == ""
