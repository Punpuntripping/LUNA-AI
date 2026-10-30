"""WI preview lock — the ONE lock decision and the ONE truncation.

Plan: ``.claude/plans/wi_preview_lock.md`` (owner decisions 2026-10-30, §0/§9).

Free users read ~40% of every NEW agent workspace item (a deep-search report, a
writer draft) and an Arabic upgrade wall where the rest would be. The chat
answer stays whole; only the item is previewed. Two consumers, so the web/app
and the router can never see two different previews:

* ``backend/app/services/wi_preview_service.py`` — every workspace response
  (the ``_to_response`` choke point), the PATCH / share refusals and the
  references filter.
* ``agents/tool_repository/unfold_workspace_item.py`` — the router's unfold
  (``RouterDeps`` only), wrapped in the deliberate-lock markers (§6). Planner
  and writer deps carry no policy, so they keep the full content.

THE DECISION (§3) — at READ time, never stored::

    locked = flag on AND kind lockable AND created_at >= WI_PREVIEW_LOCK_FROM
             AND NOT created_while_paid AND NOT user_is_paid_now AND NOT demo

The three per-user / per-config clauses are folded into :class:`LockPolicy`,
which exists only when the lock can bite (flag on, FROM set, user free). Every
caller holding ``None`` behaves exactly as before the feature existed — that is
the flag-off guarantee, and it costs no paid lookup.

``created_while_paid`` is stamped by migration 198's BEFORE INSERT trigger from
``user_subscriptions``; a row without the column (198 not applied) reads as
False. ``user_is_paid_now`` is ``shared.quota.library_state(...).is_paid`` — the
library's own predicate (effective plan, after the expired→free fallback, not
free), so a store-billed plan (migration 194: source apple / google /
test_store) is paid here exactly when the quota gate says it is.

THE TRUNCATION (§4) — budget = 40% of the WHOLE body by characters, never
exceeded, never per section (``gate_exposure_budget.md`` §3.1 — the same
``GateBudget`` / ``free_budget``; NOT ``gate_decision``, whose withheld-floor
rule opens short documents, and every lockable WI locks, the first one too).
The cut lands on the last top-level markdown block inside the budget; when that
leaves under 30% it cuts inside the next block instead — a paragraph at its last
sentence end (. ؟ ! ؛), a list at an item boundary, a table at a row boundary
(header + separator kept). A ``[n]`` marker is never split and a trailing
heading is dropped. The preview is always a PREFIX of the stored body, which is
what lets the PATCH guard refuse any edit of it outright.
"""
from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from supabase import Client as SupabaseClient

from shared.config import get_settings
from shared.library.gate_budget import GateBudget, free_budget

logger = logging.getLogger(__name__)


# ── Policy dials ────────────────────────────────────────────────────────────

# Floor 0 / no ceiling on purpose: a floor would let a short item through above
# 40%, and a ceiling would show a long one less than the owner decided.
WI_PREVIEW_BUDGET = GateBudget(ratio=0.40, floor=0, ceiling=10**9)
# The cut never lands below this share of the body when the block structure
# allows it (§4) — a 6% preview is not a preview.
MIN_SHOWN_RATIO = 0.30

LOCKABLE_KINDS = frozenset({"agent_search", "agent_writing"})
# Never locked, whatever the kind (§2, §9.1): verbatim law text from
# articles_v2, and the memory scaffold.
EXEMPT_SUBTYPES = frozenset({"statute_package", "memory_file"})
# The writer's «ملخّص» is distilled from the user's own file or note (writer
# prompt "summary": "An explicit reference to the file or note it was distilled
# from") — exempt by owner decision §9.1.
EXEMPT_WRITING_SUBTYPES = frozenset({"summary"})

# Old app / web builds render content_md and know nothing of ``preview``: they
# get this one fixed line after the visible part, so the cut never reads as a
# broken document. New clients strip it (``WI_PREVIEW_LEGACY_SUFFIX`` in
# frontend/lib/contracts/wi-preview.ts — the SAME string; a test pins it). No
# URL and no purchase wording: an old iOS build must not carry a call to buy
# outside the store.
LEGACY_WALL_LINE = "> **بقية هذا المستند متاحة في الباقات المدفوعة.**"
LEGACY_WALL_SUFFIX = "\n\n" + LEGACY_WALL_LINE


# ── Who is locked ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LockPolicy:
    """The lock as it applies to ONE free user, for one request or router turn.

    Exists only when the lock can bite; ``None`` means nothing locks. Carries
    no per-item state — :func:`is_locked` decides per row.
    """

    lock_from: datetime


def configured_lock_from() -> Optional[datetime]:
    """``WI_PREVIEW_LOCK_FROM`` when the flag is on, else ``None``.

    Flag on with FROM unset is a misconfiguration and locks nothing (logged):
    the alternative — every item a free user ever had locking at once — breaks
    decision §0.4 ("everything older stays open").
    """
    settings = get_settings()
    if not settings.WI_PREVIEW_LOCK_ENABLED:
        return None
    lock_from = settings.WI_PREVIEW_LOCK_FROM
    if lock_from is None:
        logger.warning(
            "WI_PREVIEW_LOCK_ENABLED is on but WI_PREVIEW_LOCK_FROM is unset — nothing locks"
        )
        return None
    if lock_from.tzinfo is None:
        lock_from = lock_from.replace(tzinfo=timezone.utc)
    return lock_from


# The paid lookup runs on every list / get / unfold of a free user, so it is
# cached in-process. A "paid" answer is held longer than a "free" one: a stale
# paid answer only shows a fresh free user their full item for a minute, while
# a stale free answer would keep someone who just upgraded behind the wall
# (§3: upgrading unlocks at once). Failures are never cached.
_PAID_TTL_S = 60.0
_FREE_TTL_S = 15.0
_PAID_CACHE_MAX = 4096
_paid_cache: dict[str, tuple[float, bool]] = {}


async def user_is_paid(supabase: SupabaseClient, user_id: str) -> bool:
    """The library's paid predicate for ``user_id`` (a users.user_id), cached.

    A failed lookup answers False — treated as free, i.e. locked — like the
    library gate (§3); it is not cached, so the next read retries.
    """
    now = time.monotonic()
    hit = _paid_cache.get(user_id)
    if hit is not None and hit[0] > now:
        return hit[1]
    try:
        from shared.quota import library_state

        paid = bool((await library_state(supabase, user_id)).is_paid)
    except Exception as exc:  # noqa: BLE001
        logger.warning("wi_preview_lock: paid lookup failed (treated as free): %s", exc)
        return False
    if len(_paid_cache) >= _PAID_CACHE_MAX:
        _paid_cache.clear()
    _paid_cache[user_id] = (now + (_PAID_TTL_S if paid else _FREE_TTL_S), paid)
    return paid


async def resolve_lock_policy(
    supabase: SupabaseClient, user_id: Optional[str]
) -> Optional[LockPolicy]:
    """The :class:`LockPolicy` for this user, or ``None`` when nothing locks.

    Flag off (or FROM unset) returns before any DB read.
    """
    lock_from = configured_lock_from()
    if lock_from is None or not user_id:
        return None
    if await user_is_paid(supabase, user_id):
        return None
    return LockPolicy(lock_from=lock_from)


def _reset_paid_cache() -> None:
    """Test hook — drop the in-process paid cache."""
    _paid_cache.clear()


# ── Which rows lock ─────────────────────────────────────────────────────────


def _parse_ts(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _subtype(row: Mapping[str, Any]) -> Optional[str]:
    metadata = row.get("metadata")
    if isinstance(metadata, Mapping):
        value = metadata.get("subtype")
        return str(value) if value is not None else None
    return None


def is_lockable(row: Mapping[str, Any]) -> bool:
    """Whether this KIND of item ever locks (§2) — independent of user and date.

    Agent search reports and writer drafts only. Never: user notes, memos and
    statute packages (``note``), attachments, ``references`` (library pages,
    gated by the library), ``convo_context``, an ``agent_search`` the USER
    created (blog imports — public anyway), the writer's ``memory_file`` and
    ``summary``.
    """
    kind = str(row.get("kind") or "")
    if kind not in LOCKABLE_KINDS:
        return False
    if str(row.get("created_by") or "") != "agent":
        return False
    subtype = _subtype(row)
    if subtype in EXEMPT_SUBTYPES:
        return False
    if kind == "agent_writing" and subtype in EXEMPT_WRITING_SUBTYPES:
        return False
    return True


def is_locked(
    row: Mapping[str, Any], policy: Optional[LockPolicy], *, is_demo: bool = False
) -> bool:
    """The §3 decision for one row under one user's policy."""
    if policy is None or is_demo or not is_lockable(row):
        return False
    if row.get("created_while_paid"):
        return False
    created = _parse_ts(row.get("created_at"))
    if created is None:
        # Age unknown — the safe reading of "only items created after launch".
        return False
    return created >= policy.lock_from


# ── The truncation ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PreviewCut:
    """What a locked item shows. ``visible`` is always a prefix of the body."""

    visible: str
    shown_chars: int
    total_chars: int

    @property
    def truncated(self) -> bool:
        return self.shown_chars < self.total_chars

    @property
    def pct(self) -> int:
        """Shown share of the body, whole percent (the router marker's {pct})."""
        if self.total_chars <= 0:
            return 100
        return int(round(100 * self.shown_chars / self.total_chars))


_HEADING_RE = re.compile(r"^ {0,3}#{1,6}(?:\s|$)")
_HR_RE = re.compile(r"^ {0,3}([-*_])(?:\s*\1){2,}\s*$")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s+")
_TABLE_LINE_RE = re.compile(r"^\s*\|")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}")
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
# A citation marker, ASCII digits (what every publisher writes).
_MARKER_RE = re.compile(r"\[(\d{1,4})\]")
_TRAILING_MARKERS_RE = re.compile(r"(?:[ \t]*\[\d{1,4}\])+")
_SENTENCE_END = frozenset(".؟?!؛")
# Closers a sentence end may carry before its whitespace: quotes, brackets and
# the end of a bold run («…».** / (…).).
_SENTENCE_CLOSERS = frozenset("»)]\"”’'*")


@dataclass
class _Block:
    kind: str                       # para | list | table | heading | fence
    lines: list[tuple[int, int]]    # (start, end) offsets, end exclusive

    @property
    def start(self) -> int:
        return self.lines[0][0]

    @property
    def end(self) -> int:
        return self.lines[-1][1]


def _blocks(text: str) -> list[_Block]:
    """Top-level markdown blocks: runs of non-blank lines (a fence spans blanks)."""
    lines: list[tuple[int, int]] = []
    pos = 0
    for raw in text.split("\n"):
        lines.append((pos, pos + len(raw)))
        pos += len(raw) + 1

    blocks: list[_Block] = []
    current: list[tuple[int, int]] = []
    in_fence = False
    for start, end in lines:
        line = text[start:end]
        if in_fence:
            current.append((start, end))
            if _FENCE_RE.match(line):
                in_fence = False
                blocks.append(_Block("fence", current))
                current = []
            continue
        if not line.strip():
            if current:
                blocks.append(_Block(_classify(text, current), current))
                current = []
            continue
        if _FENCE_RE.match(line):
            if current:
                blocks.append(_Block(_classify(text, current), current))
            current = [(start, end)]
            in_fence = True
            continue
        current.append((start, end))
    if current:
        blocks.append(_Block("fence" if in_fence else _classify(text, current), current))
    return blocks


def _classify(text: str, lines: list[tuple[int, int]]) -> str:
    rows = [text[s:e] for s, e in lines]
    if all(_HEADING_RE.match(r) for r in rows):
        return "heading"
    if all(_TABLE_LINE_RE.match(r) for r in rows):
        return "table"
    if _LIST_ITEM_RE.match(rows[0]):
        return "list"
    return "para"


def _bold_balanced(text: str, start: int, cut: int) -> bool:
    """No unclosed ``**`` run inside the cut part of one block."""
    return text.count("**", start, cut) % 2 == 0


def _sentence_ends(text: str, start: int, end: int) -> list[int]:
    """Cut positions right after a sentence end inside ``text[start:end]``.

    The cut swallows the closers and any ``[n]`` markers that follow the
    terminator («… التعويض. [3]»), so a citation always stays with its
    sentence and is never split. «10.5» is not a sentence end.
    """
    out: list[int] = []
    for i in range(start, end):
        ch = text[i]
        if ch not in _SENTENCE_END:
            continue
        if ch == "." and i + 1 < end and text[i + 1].isdigit():
            continue
        pos = i + 1
        while pos < end and text[pos] in _SENTENCE_CLOSERS:
            pos += 1
        markers = _TRAILING_MARKERS_RE.match(text, pos, end)
        if markers:
            pos = markers.end()
            if pos < end and text[pos] in _SENTENCE_END:
                pos += 1
        if pos < end and not text[pos].isspace():
            continue
        out.append(pos)
    return out


def _inner_cuts(text: str, block: _Block) -> list[int]:
    """Candidate cut positions INSIDE one block (never its own end)."""
    end = block.end
    if block.kind == "table":
        # Header + separator always; then whole rows only.
        if len(block.lines) < 3 or not _TABLE_SEP_RE.match(text[slice(*block.lines[1])]):
            return []
        return [e for _s, e in block.lines[2:-1]]
    if block.kind == "list":
        items = [
            block.lines[i - 1][1]
            for i in range(1, len(block.lines))
            if _LIST_ITEM_RE.match(text[slice(*block.lines[i])])
        ]
        return items + _sentence_ends(text, block.start, end)
    if block.kind == "para":
        line_ends = [e for _s, e in block.lines[:-1]]
        return line_ends + _sentence_ends(text, block.start, end)
    return []          # a heading or a fenced block is never cut inside


def _word_cut(text: str, start: int, budget: int) -> Optional[int]:
    """Last whitespace at or before ``budget`` that splits no marker or bold run."""
    for pos in range(min(budget, len(text) - 1), start, -1):
        if not text[pos].isspace():
            continue
        rest = text[pos:].lstrip()
        if _MARKER_RE.match(rest):
            continue
        if not _bold_balanced(text, start, pos):
            continue
        return pos
    return None


def _trim_tail(text: str) -> str:
    """Strip trailing whitespace, headings and rules — never end on a heading."""
    out = text.rstrip()
    while out:
        nl = out.rfind("\n")
        last = out[nl + 1:]
        if _HEADING_RE.match(last) or _HR_RE.match(last):
            out = out[:nl].rstrip() if nl >= 0 else ""
            continue
        break
    return out


def preview_budget(total_chars: int) -> int:
    """40% of the body, rounded DOWN so the share is never exceeded."""
    total = max(0, int(total_chars))
    return min(free_budget(total, WI_PREVIEW_BUDGET), int(total * WI_PREVIEW_BUDGET.ratio))


def preview_cut(content_md: Optional[str]) -> PreviewCut:
    """The preview of one body. Pure; byte-identical for every caller."""
    text = content_md or ""
    total = len(text)
    if total == 0:
        return PreviewCut("", 0, 0)
    budget = preview_budget(total)
    floor_keep = math.ceil(total * MIN_SHOWN_RATIO)
    blocks = _blocks(text)

    straddle = next((i for i, b in enumerate(blocks) if b.end > budget), None)
    if straddle is None:
        # Only whitespace lies past the budget — there is nothing to withhold.
        visible = text.rstrip()
        return PreviewCut(visible, len(visible), total)

    block = blocks[straddle]
    prefix_end = blocks[straddle - 1].end if straddle > 0 else 0
    best = _trim_tail(text[:prefix_end])

    if len(best) < floor_keep:
        inner = [
            c for c in _inner_cuts(text, block)
            if block.start < c <= budget and _bold_balanced(text, block.start, c)
        ]
        if block.kind == "list":
            # Item boundaries first; a sentence inside an item only when no
            # whole item fits.
            items = [
                c for c in inner
                if any(c == block.lines[i - 1][1] for i in range(1, len(block.lines)))
            ]
            inner = items or inner
        if inner:
            candidate = _trim_tail(text[:max(inner)])
            if len(candidate) > len(best):
                best = candidate

    if len(best) < floor_keep and block.kind in ("para", "list"):
        cut = _word_cut(text, block.start, budget)
        if cut is not None:
            candidate = _trim_tail(text[:cut])
            if len(candidate) > len(best):
                best = candidate

    return PreviewCut(best, len(best), total)


def cited_numbers(text: Optional[str]) -> frozenset[int]:
    """Every ``[n]`` citation number in ``text``."""
    return frozenset(int(m) for m in _MARKER_RE.findall(text or ""))


def hidden_refs_line_ar(count: int) -> str:
    """«وN مصادر أخرى في الجزء المقفل» with Arabic number agreement, Latin
    digits (§9.3). ``""`` for 0. Mirrors ``wiPreviewHiddenRefsLine`` in
    frontend/lib/contracts/wi-preview.ts, so the router's source list and the
    المراجع panel say the same thing."""
    n = max(0, int(count))
    if n == 0:
        return ""
    if n == 1:
        return "ومصدر آخر في الجزء المقفل"
    if n == 2:
        return "ومصدران آخران في الجزء المقفل"
    if n <= 10:
        return f"و{n} مصادر أخرى في الجزء المقفل"
    return f"و{n} مصدراً آخر في الجزء المقفل"


__all__ = [
    "EXEMPT_SUBTYPES",
    "EXEMPT_WRITING_SUBTYPES",
    "LEGACY_WALL_LINE",
    "LEGACY_WALL_SUFFIX",
    "LOCKABLE_KINDS",
    "LockPolicy",
    "MIN_SHOWN_RATIO",
    "PreviewCut",
    "WI_PREVIEW_BUDGET",
    "cited_numbers",
    "configured_lock_from",
    "hidden_refs_line_ar",
    "is_lockable",
    "is_locked",
    "preview_budget",
    "preview_cut",
    "resolve_lock_policy",
    "user_is_paid",
]
