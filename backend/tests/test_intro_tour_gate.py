"""What «اتعرف على ريحان» calls a purchase — `resolve_paid_activated_at`.

The 4-step intro tour is supposed to arrive right after someone buys (A2,
`.claude/plans/edu_series.md` §8). Until 2026-08-18 the frontend decided that
on `plan_id !== "free"`, which is not the same question at all: `dev` is not
`free`, so every internal grant opened the tour, and the test the owner ran on
a "new account" was really a dev-plan account being told it had just paid.
There was also no time in the condition, so a subscriber of three months who
had never had `onboarding_seen` written got a welcome tour out of nowhere.

These are the walls that replaced it. No DB — the function takes the embedded
`user_subscriptions` row exactly as PostgREST hands it to `GET /auth/me`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.app.services.subscription_service import (
    NON_CUSTOMER_PLAN_IDS,
    PAID_SOURCE,
    resolve_paid_activated_at,
)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


NOW = datetime.now(timezone.utc)
LIVE_TERM = _iso(NOW + timedelta(days=25))
DEAD_TERM = _iso(NOW - timedelta(days=1))


def _row(**overrides) -> dict:
    """A freshly purchased, still-running `pro` subscription."""
    row = {
        "plan_id": "pro",
        "source": PAID_SOURCE,
        "started_at": _iso(NOW - timedelta(minutes=2)),
        "expires_at": LIVE_TERM,
        "usage_reset_at": None,
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# The one case that says yes
# ---------------------------------------------------------------------------


def test_a_real_purchase_reports_when_it_happened() -> None:
    bought_at = NOW - timedelta(minutes=2)
    at = resolve_paid_activated_at(_row(started_at=_iso(bought_at)))
    assert at is not None
    assert abs((at - bought_at).total_seconds()) < 1


def test_the_answer_is_the_LATEST_money_moment_not_the_first() -> None:
    """`grant_plan` preserves `started_at` when it extends a live same-plan term,
    so a renewal moves only `usage_reset_at` (stamped with the payment's
    `paid_at`). Reading `started_at` alone would date a renewing customer's
    subscription to the day they first joined."""
    renewed_at = NOW - timedelta(hours=1)
    at = resolve_paid_activated_at(
        _row(
            started_at=_iso(NOW - timedelta(days=90)),
            usage_reset_at=_iso(renewed_at),
        )
    )
    assert at is not None
    assert abs((at - renewed_at).total_seconds()) < 1


def test_an_older_usage_reset_never_drags_the_answer_backwards() -> None:
    """GREATEST, not "whichever is set" — an upgrade moves `started_at` forward
    while `usage_reset_at` may still hold the previous purchase."""
    upgraded_at = NOW - timedelta(minutes=5)
    at = resolve_paid_activated_at(
        _row(
            started_at=_iso(upgraded_at),
            usage_reset_at=_iso(NOW - timedelta(days=30)),
        )
    )
    assert at is not None
    assert abs((at - upgraded_at).total_seconds()) < 1


# ---------------------------------------------------------------------------
# Wall 1 — money actually moved
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", ["manual", "signup", "code", "", None])
def test_a_grant_is_not_a_purchase(source) -> None:
    assert resolve_paid_activated_at(_row(source=source)) is None


# ---------------------------------------------------------------------------
# Wall 2 — the plan is a customer plan
#
# THE regression this file exists for: `dev` is not `free`, and the owner runs
# real test checkouts against it, so a dev row can carry source='payment' and a
# live term and still be nobody's first purchase.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("plan_id", sorted(NON_CUSTOMER_PLAN_IDS))
def test_internal_plans_never_read_as_bought(plan_id: str) -> None:
    assert resolve_paid_activated_at(_row(plan_id=plan_id)) is None


def test_dev_stays_excluded_even_when_it_was_genuinely_paid_for() -> None:
    assert (
        resolve_paid_activated_at(
            _row(plan_id="dev", source=PAID_SOURCE, expires_at=LIVE_TERM)
        )
        is None
    )


# ---------------------------------------------------------------------------
# Wall 3 — the term is still running
# ---------------------------------------------------------------------------


def test_an_expired_paid_plan_is_not_paid() -> None:
    """It has already fallen back to free everywhere quota is enforced
    (`get_user_quota_state`); it must not read as paid here either."""
    assert resolve_paid_activated_at(_row(expires_at=DEAD_TERM)) is None


def test_a_never_expiring_grant_is_not_paid() -> None:
    """`expires_at IS NULL` is the shape of a manual/dev grant. `_term_is_running`
    already refuses it, which is the second wall behind the source check."""
    assert resolve_paid_activated_at(_row(expires_at=None)) is None


# ---------------------------------------------------------------------------
# Degenerate rows — /me answers for every account, including broken ones
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("row", [None, {}, {"plan_id": None, "source": PAID_SOURCE}])
def test_missing_or_empty_rows_answer_none(row) -> None:
    assert resolve_paid_activated_at(row) is None


def test_a_purchase_with_no_timestamps_at_all_answers_none() -> None:
    """Nothing to compare a window against — better silent than "just now"."""
    assert (
        resolve_paid_activated_at(_row(started_at=None, usage_reset_at=None)) is None
    )


def test_an_unparseable_timestamp_does_not_raise() -> None:
    assert resolve_paid_activated_at(_row(started_at="not-a-date")) is None


def test_a_free_signup_row_answers_none() -> None:
    """The shape of every brand-new account: source='signup', plan='free'."""
    assert (
        resolve_paid_activated_at(
            {
                "plan_id": "free",
                "source": "signup",
                "started_at": _iso(NOW),
                "expires_at": None,
                "usage_reset_at": None,
            }
        )
        is None
    )
