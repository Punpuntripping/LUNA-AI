"""What the library guardrails did, in Logfire (library_guardrails_hardening.md Phase 5).

Three events, read by the Logfire alerts and by ``scripts/library_attack_review.sql``:

    rate_limit.refused      a limiter answered 429
    library.unlock_written  a reader spent an unlock (the charged path only)
    library.carrier_call    a reader asked to carry a library page into a chat

Backend stdlib logging does not reach Logfire, so these use the logfire API. Every
function here is safe to call from a request path: a telemetry failure costs one
stdlib line and never reaches the caller.

⚠ ``limiter`` IS THE ATTRIBUTE THAT MATTERS. Four limiters can refuse a library
reader and their 429s are byte-identical on purpose (``rate_limit.py``, one
contract), so a status code cannot say which one fired. That hid the real cause of
the 2026-10-20 search complaint: the review blamed the 60/min bucket, but all 23
refusals were the hourly item budget, after five requests on that path. Never merge
these three labels.

⚠ EVERY LABEL IS A CLOSED VOCABULARY. Path and query are attacker-controlled, so
nothing raw reaches an attribute or the fold table, which keeps the table bounded
by len(LIMITERS) × len(ROUTES) × len(KEY_KINDS) × len(TIERS) whatever is sent.

⚠ THE WORDS MUST SURVIVE THE SCRUBBER, WHICH ALSO MATCHES VALUES. Logfire's default
patterns include ``auth`` and ``session``, which is why the auth routes are the
``account`` wing. ``test_vocabulary_survives_the_scrubber`` pins this.

⚠ ``library_user_id``, NOT ``user_id``. The phone pattern in shared/observability.py
matches a digit run inside about 1 UUID in 6, which would leave that user's id
unreadable and un-groupable in the per-user alerts. The dedicated key is on the
scrubber's keep list, so no other event's scrubbing changes.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from backend.app.middleware.origin_lock import PATH_WINGS, classify_path
from shared.observability import get_logfire

logger = logging.getLogger(__name__)
_logfire = get_logfire()

REFUSED_EVENT = "rate_limit.refused"
UNLOCK_EVENT = "library.unlock_written"
CARRIER_EVENT = "library.carrier_call"

LIMITER_GLOBAL = "global"  # RateLimitMiddleware: per path, fails open
LIMITER_ROUTE = "route"  # route_limits: verified identity, fails closed
LIMITER_ITEM_BUDGET = "item_budget"  # library_budget_service: the hourly reach meter
LIMITER_DAILY_CAP = "daily_cap"  # library_budget_service: per account, per Riyadh day
LIMITERS = (LIMITER_GLOBAL, LIMITER_ROUTE, LIMITER_ITEM_BUDGET, LIMITER_DAILY_CAP)

KEY_ANON = "anon"
KEY_USER = "user"  # the global limiter's is the UNVERIFIED sub: a label, not proof
KEY_KINDS = (KEY_ANON, KEY_USER)

TIER_FREE = "free"
TIER_PAID = "paid"
TIER_NONE = "none"  # only the item budget knows the tier
TIERS = (TIER_FREE, TIER_PAID, TIER_NONE)

ROUTE_HUB_SEARCH = "hub_search"
ROUTE_HUB_BROWSE = "hub_browse"
ROUTE_LIBRARY_ITEM = "library_item"
ROUTE_REFERENCE_SOURCE = "reference_source"
ROUTE_OTHER = "other"  # also a member of PATH_WINGS
ROUTES = tuple(
    dict.fromkeys(
        (
            ROUTE_HUB_SEARCH,
            ROUTE_HUB_BROWSE,
            ROUTE_LIBRARY_ITEM,
            ROUTE_REFERENCE_SOURCE,
            *PATH_WINGS,
        )
    )
)

_PUBLIC_LIBRARY_PREFIX = "/api/v1/public/library/"

# A scraper past its limit can send thousands of 429s a minute, so refusals fold:
# one line the first time a key is seen, then one per interval carrying ``count``.
# Alerts must SUM(count), not count lines.
_FLUSH_SECONDS = 60.0


def classify_route(path: str, *, searching: bool = False) -> str:
    """The request path as one of ``ROUTES``. Never returns the input."""
    if path.startswith(_PUBLIC_LIBRARY_PREFIX):
        section, _, rest = path[len(_PUBLIC_LIBRARY_PREFIX):].partition("/")
        if section and rest:
            return ROUTE_LIBRARY_ITEM
        return ROUTE_HUB_SEARCH if searching else ROUTE_HUB_BROWSE
    if path.startswith("/api/v1/workspace/") and path.endswith("/source"):
        return ROUTE_REFERENCE_SOURCE
    return classify_path(path)


def _tier_label(tier: Optional[str]) -> str:
    """``_hub_caller`` says ``free``; any other non-empty tier is a paying one."""
    if not tier:
        return TIER_NONE
    return TIER_FREE if tier == TIER_FREE else TIER_PAID


class _RefusalFold:
    """Refusals per key since that key's last line. Bounded by the vocabularies."""

    def __init__(self) -> None:
        self._pending: dict[tuple[str, str, str, str], int] = {}
        self._last_flush_at = time.monotonic()

    def refused(self, key: tuple[str, str, str, str]) -> None:
        if key in self._pending:
            self._pending[key] += 1
            return
        self._pending[key] = 0
        self._emit(key, 1)

    def flush_due(self) -> None:
        """Driven by every request, not only by refusals: otherwise a burst that
        stops would sit here until the next 429, and the alert would read 1."""
        now = time.monotonic()
        if now - self._last_flush_at < _FLUSH_SECONDS:
            return
        self._last_flush_at = now
        for key, count in list(self._pending.items()):
            if count:
                self._pending[key] = 0
                self._emit(key, count)

    @staticmethod
    def _emit(key: tuple[str, str, str, str], count: int) -> None:
        limiter, route, key_kind, tier = key
        _emit_safely(
            REFUSED_EVENT,
            limiter=limiter,
            route=route,
            key_kind=key_kind,
            tier=tier,
            count=count,
        )


_fold = _RefusalFold()
_failure_noted = False


def _note_failure(exc: Exception) -> None:
    """One stdlib line per process. The class only: a message could echo a value."""
    global _failure_noted
    if not _failure_noted:
        _failure_noted = True
        logger.warning("Guard signal failed, continuing without it: %s", type(exc).__name__)


def _emit_safely(event: str, **attributes: Any) -> None:
    try:
        _logfire.info(event, **attributes)
    except Exception as exc:  # noqa: BLE001 — telemetry must never fail a request
        _note_failure(exc)


def report_refused(
    limiter: str,
    request: Any,
    *,
    key_kind: str,
    tier: Optional[str] = None,
) -> None:
    """Record one 429. ``request`` may be None: the item budget accepts that."""
    try:
        route = ROUTE_OTHER
        if request is not None:
            searching = bool(request.query_params.get("q", "").strip())
            route = classify_route(request.url.path, searching=searching)
        _fold.refused((limiter, route, key_kind, _tier_label(tier)))
    except Exception as exc:  # noqa: BLE001 — the 429 itself must still go out
        _note_failure(exc)


def flush_due() -> None:
    """Emit folded refusals whose interval has passed. One float compare if not."""
    try:
        _fold.flush_due()
    except Exception as exc:  # noqa: BLE001
        _note_failure(exc)


def report_unlock_written(user_id: str, content_type: str, cost: int) -> None:
    _emit_safely(
        UNLOCK_EVENT,
        library_user_id=str(user_id),
        content_type=content_type,
        cost=cost,
    )


def report_carrier_call(user_id: str, page_type: str) -> None:
    _emit_safely(CARRIER_EVENT, library_user_id=str(user_id), page_type=page_type)


def reset_for_tests() -> None:
    """Drop all state."""
    global _fold, _failure_noted
    _fold = _RefusalFold()
    _failure_noted = False


__all__ = [
    "CARRIER_EVENT",
    "KEY_ANON",
    "KEY_KINDS",
    "KEY_USER",
    "LIMITERS",
    "LIMITER_GLOBAL",
    "LIMITER_ITEM_BUDGET",
    "LIMITER_ROUTE",
    "REFUSED_EVENT",
    "ROUTES",
    "TIERS",
    "UNLOCK_EVENT",
    "classify_route",
    "flush_due",
    "report_carrier_call",
    "report_refused",
    "report_unlock_written",
    "reset_for_tests",
]
