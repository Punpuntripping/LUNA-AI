"""OCR page budget — how many pages of each attachment OCR may read, and which.

Owner rules, 2026-11-16 (``.claude/plans/ocr_page_budget.md`` §2):

* D2  One file reads at most ⌊⅔ × the plan's TOTAL monthly OCR pages⌋. Measured
      against the plan total, never against what is left: a user can reach the
      limit, just never in a single request.
* D3  …unless the plan ends within 7 days (and has not ended yet). Then the cap
      is lifted and one file may use everything left — every paid purchase,
      renewals included, zeroes the OCR meter (migration 137), so those pages
      would be lost anyway.
* D4  With at least one page left, at least one page is read.
* D5  Several files in one message: smallest first, each drawing on what the
      ones before it left.
* D6  A file bigger than its budget N reads its first N − 2⌊N/5⌋ pages, a solid
      run of ⌊N/5⌋ pages centred in the pages between the first and last blocks
      (so it can never repeat either), and its last ⌊N/5⌋ pages. Small N
      degenerates to "the first N pages".

Pure — no I/O. The quota gate, the OCR runner and the composer preview
(``frontend/lib/ocr-budget.ts``, a mirror) all follow this one rule. Change the
mirror together with this file.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

#: D2 — a file's share of the plan's monthly total.
PER_FILE_NUM, PER_FILE_DEN = 2, 3
#: D3 — a plan ending within this window lifts the per-file cap.
EXPIRY_GRACE = timedelta(days=7)
#: D6 — the middle and last blocks are each ⌊N / EDGE_DIVISOR⌋ pages (20%).
EDGE_DIVISOR = 5


@dataclass(frozen=True)
class OcrBudget:
    """The OCR allowance as one quota-state read sees it.

    ``total`` None = unlimited (``dev``); 0 = OCR not in the plan.
    ``per_file_cap`` None = no per-file cap (unlimited plan, or D3).
    """

    total: int | None
    used: int
    per_file_cap: int | None

    @property
    def remaining(self) -> int | None:
        """Pages left this window. None = unlimited."""
        if self.total is None:
            return None
        return max(0, self.total - self.used)


@dataclass(frozen=True)
class FilePlan:
    """What OCR may read of one attachment.

    ``pages``: 1-based page numbers, ascending. ``()`` = nothing (no budget
    left). ``None`` = the whole document — only when both the allowance is
    unlimited and the page count is unknown.
    ``total_pages``: None when the page count is unknown.
    """

    item_id: str
    total_pages: int | None
    pages: tuple[int, ...] | None

    @property
    def complete(self) -> bool:
        """Every page of the document is planned."""
        if self.pages is None:
            return True
        return self.total_pages is not None and len(self.pages) >= self.total_pages

    @property
    def page_count(self) -> int | None:
        """Pages planned. None = the whole document, length unknown."""
        return None if self.pages is None else len(self.pages)


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def per_file_cap(
    total: int | None,
    *,
    expires_at: Any = None,
    is_expired: bool = False,
    now: datetime | None = None,
) -> int | None:
    """D2 + D3 + D4. None = no per-file cap."""
    if total is None:
        return None
    now = now or datetime.now(timezone.utc)
    ends = _parse_ts(expires_at)
    if ends is not None and not is_expired and now < ends <= now + EXPIRY_GRACE:
        return None
    return max(1, total * PER_FILE_NUM // PER_FILE_DEN)


def budget_from_state(state: Mapping[str, Any], *, now: datetime | None = None) -> OcrBudget:
    """Build the budget from a ``get_user_quota_state`` row."""
    raw_total = state.get("ocr_pages_monthly")
    total = None if raw_total is None else max(0, int(raw_total))
    used = max(0, int(state.get("ocr_pages") or 0))
    cap = per_file_cap(
        total,
        expires_at=state.get("expires_at"),
        is_expired=bool(state.get("is_expired")),
        now=now,
    )
    return OcrBudget(total=total, used=used, per_file_cap=cap)


def pick_pages(total_pages: int | None, n: int) -> tuple[int, ...]:
    """D6 — which ``n`` pages of a ``total_pages``-page document to read.

    Unknown length: the opening ``n`` pages (there is no known end to take a
    last block from).
    """
    if n <= 0:
        return ()
    if total_pages is None:
        return tuple(range(1, n + 1))
    if n >= total_pages:
        return tuple(range(1, total_pages + 1))
    edge = n // EDGE_DIVISOR
    first = n - 2 * edge
    # Pages strictly between the first and last blocks. total_pages > n, so
    # gap_len >= edge + 1 and the middle run never touches either block.
    gap_len = total_pages - edge - first
    mid_start = first + 1 + (gap_len - edge) // 2
    return (
        *range(1, first + 1),
        *range(mid_start, mid_start + edge),
        *range(total_pages - edge + 1, total_pages + 1),
    )


def _clean_total(value: Any) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def plan_files(files: Sequence[tuple[str, Any]], budget: OcrBudget) -> list[FilePlan]:
    """D4 + D5 + D6 for one message's files, returned in input order.

    ``files``: ``(item_id, page_count)``; page_count None/0 = unknown. Files are
    served smallest first; unknown-length files go last, since they may be large.
    """
    totals = [_clean_total(count) for _, count in files]
    order = sorted(
        range(len(files)),
        key=lambda i: (totals[i] is None, totals[i] or 0, i),
    )
    remaining = budget.remaining
    plans: list[FilePlan | None] = [None] * len(files)
    for i in order:
        item_id, total = files[i][0], totals[i]
        limit = _min_or_none(budget.per_file_cap, remaining)
        if limit is None and total is None:
            plans[i] = FilePlan(item_id, None, None)
            continue
        if limit is None:
            n = total
        elif total is None:
            n = limit
        else:
            n = min(total, limit)
        pages = pick_pages(total, max(0, n))
        plans[i] = FilePlan(item_id, total, pages)
        if remaining is not None:
            remaining -= len(pages)
    return [p for p in plans if p is not None]


def _min_or_none(a: int | None, b: int | None) -> int | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def page_ranges(pages: Sequence[int]) -> list[tuple[int, int]]:
    """Collapse ascending page numbers into inclusive runs: [(1, 60), (314, 333)]."""
    runs: list[tuple[int, int]] = []
    for p in pages:
        if runs and p == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], p)
        else:
            runs.append((p, p))
    return runs


def format_ranges(pages: Sequence[int]) -> str:
    """«1–60، 314–333، 587–606» — Latin digits, en dash, Arabic comma."""
    return "، ".join(
        str(a) if a == b else f"{a}–{b}" for a, b in page_ranges(pages)
    )
