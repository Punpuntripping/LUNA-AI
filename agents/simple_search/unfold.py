"""``unfold(always)`` — turn ONE resolved legal object into agent-facing text.

The deterministic, LLM-free input path for the ``simple_search`` synthesizer
(plan ``.claude/plans/simple_search_family.md`` §4 + §5). Given a
:class:`~agents.simple_search.models.ResolvedObject`, this module fetches the
object's **real content** and renders it as Arabic markdown under a measured
token budget.

Unfold here means the REAL content (D8). deep_search compresses because it ranks
across many candidates; simple_search holds exactly one object and can afford
the actual text. What is taken from deep_search is the *pattern* — a per-level
function turning a DB row into agent-facing text with measured caps — not its
payloads.

Six levels (§4)::

    L1 chunk           chunks_v2.content       + title/context, framed by its regulation
    L2 regulation_doc  llm_summary + intro + BODY + APPENDIXES   <- the §5 ladder
    L3 article         articles_v2.content     (+ owns/MADDA fallback)
    L4 judgment        cases.content           — the FULL ruling, not the summary
    L5 circular        circulars.content       + issuing entity + source link
    L6 service         the structured services payload

Structure — the house pure-layer split (mirrors
``agents/tool_repository/fetch_article.py``):

* **Pure layer** — ordering, the ladder, budget splitting, truncation and every
  renderer. No DB, no agent runtime, no ``pydantic_ai``. This is what the test
  suite exercises.
* **Fetch layer** — sync PostgREST calls (``_fetch_*``), each wrapped by the
  async entry points in ``asyncio.to_thread``.
* **Entry points** — ``unfold_chunk`` … ``unfold_service`` and the ``unfold``
  dispatcher.

Operational constraints:

* ``supabase`` must be the **sync service-role** client. The anon key hits RLS
  and silently returns empty ``in_(...)`` / ``eq(...)`` results (§9 trap 11).
* Every read is best-effort: a failed fetch degrades the section rather than
  raising, and ``UnfoldResult.ok`` goes False only when the object itself could
  not be read.
* This module never imports from ``backend/`` — §11a's real rule, which binds
  the retrieval + unfold core and not the publishers. Where a backend
  implementation is the reference, it is COPIED with a comment naming the source
  (see ``sort_document_order``). The one thing that genuinely lives in
  ``backend/`` — the D12 ruling unlock — is INJECTED as
  :data:`JudgmentAccessResolver` rather than imported, so the charge is
  unavoidable here without dragging the dependency in.
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from agents.deep_search_v4.reg_compliance_search.unfold_reranker import (
    format_chunk,
    unfold_chunk_precise,
)
from agents.deep_search_v4.shared.case_summary import strip_pipeline_sections
from agents.deep_search_v4.shared.court_levels import court_level_ar
# §9 trap 9 — ``MAX_SERVICE_CONTEXT_CHARS`` is defined TWICE in the tree with
# different values: 600 in ``reg_compliance_search/unfold_reranker.py:73`` (the
# reranker's compact block) and 2,000 in ``ura/services_unfold.py:30`` (the
# user/reference view). Importing both unaliased into one module silently binds
# whichever import ran last. Only ONE is imported here, and it is aliased.
from agents.deep_search_v4.ura.services_unfold import (
    MAX_SERVICE_CONTEXT_CHARS as _URA_SERVICE_CONTEXT_CHARS,
    build_service_aggregator_content,
    build_service_context,
)
from agents.simple_search.models import (
    RUNG_FULL_CONTENT,
    RUNG_NOT_APPLICABLE,
    RUNG_SUMMARIES,
    RUNG_TRUNCATED_SUMMARIES,
    LadderDecision,
    ResolvedObject,
    SimpleSearchLevel,
    UnfoldResult,
    UnfoldSection,
)

logger = logging.getLogger(__name__)


# =========================================================================== #
# §5 — the budget constants. Every cap carries how it was calibrated (house
# convention; the model is ``MAX_AGGREGATOR_CONTENT_CHARS`` at
# ``case_search/unfold_ura.py:41-44``).
# =========================================================================== #

# ONE named char↔token constant. Never scatter ``// 4`` heuristics: Arabic
# tokenises far denser than English and a 4:1 guess under-counts a 68,750-char
# regulation by ~8k tokens. Locked by plan §5.
ARABIC_CHARS_PER_TOKEN = 2.75

# Above this, real content is replaced by chunk summaries (D9). Measured over
# all 3,951 regulations with chunks (live, 2026-08-15): 3,530 (89.3%) sit under
# it and never leave rung 1. The switch moves ~218 mid-size regulations from
# full text to summaries — the deliberate trade for a leaner common case.
REAL_CONTENT_MAX_TOKENS = 25_000

# Above this, summaries are position-truncated (D9). Measured: only 16 of 3,951
# regulations (0.40%) reach it. Rung-2 median is ~27,310 chars (~10k tokens) of
# summaries — comfortably inside. Corpus max summaries anywhere: 489,678 chars.
SUMMARIES_MAX_TOKENS = 50_000

# The two ceilings in chars — both exact at 2.75 chars/token.
REAL_CONTENT_MAX_CHARS = int(REAL_CONTENT_MAX_TOKENS * ARABIC_CHARS_PER_TOKEN)  # 68,750
SUMMARIES_MAX_CHARS = int(SUMMARIES_MAX_TOKENS * ARABIC_CHARS_PER_TOKEN)        # 137,500

# §5.3 — the appendix reservation at rung 3. Reserved floors WITH spillover:
# whatever a side does not use flows to the other. Verified against the rung-3
# population (live, 2026-08-15): of the 16 rung-3 regulations 8 have appendixes,
# and exactly ONE (65e6caee…, 63,265 chars of ملحق summaries) exceeds the
# reserved 34,375-char slice — spillover then lifts it to 45,624. The other 7
# fit whole (32,056 / 28,160 / 14,969 / 14,108 / 4,360 / 2,173 / 326).
# Spillover is load-bearing in the other direction too: 2,767 of 3,951
# regulations (70%) have NO appendixes at all — كود البناء السعودي العام
# (16a94b17…, 246,135 chars of body summaries, zero ملاحق) must get the whole
# 137,500, not 103,125. 75/25 needs no adjustment on current data.
BODY_SHARE = 0.75
APPENDIX_SHARE = 0.25

# §5.4 — chars per WORD, for deciding the rung from row metadata BEFORE any body
# is fetched. ``chunks_v2.word_count`` is a real column; ``length(content)`` is
# not reachable through PostgREST, so the pre-fetch measurement is
# ``word_count × this``. Measured over all 48,390 chunks (live, 2026-08-15):
# global Σchars/Σwords = 6.28, p50 = 6.27, p90 = 7.09, p99 = 8.64.
ARABIC_CHARS_PER_WORD = 6.28

# How far over the content ceiling the ESTIMATE may sit before we refuse to
# materialise the body at all. Calibrated from the estimator's measured error:
# per-regulation true/estimated ranges 0.562 … 2.371 over all 3,951 regulations.
#   * No false skip. A regulation whose real content fits (≤ 68,750) can have an
#     estimate no larger than 68,750 / 0.562 = 122,331 — below the 137,500 gate,
#     so rung 1 is never wrongly denied.
#   * Bounded memory. We fetch only when estimate ≤ 137,500, and true ≤ 2.371 ×
#     estimate, so at most ~326k chars are ever materialised — against a corpus
#     worst case of 1,944,676. That is what stops a 308k-word regulation being
#     pulled into memory (§5.4).
# The estimate NEVER decides rung 1 by itself: content that IS fetched is
# re-measured exactly, and demoted to summaries if it overruns.
CONTENT_FETCH_GUARD_FACTOR = 2.0

# §4 L2 — ``regulations_v2.intro`` is highly variable and is header material, not
# body: it is rendered ABOVE the ladder's body/appendix slices and so is capped
# on its own. Measured over 3,951 regulations (live, 2026-08-15): p50 = 1,122,
# p90 = 3,538, p99 = 13,756, max = 38,638 chars. 8,000 keeps ~97.5% whole
# (97 regulations clip) and bounds the header at ~2.9k tokens.
MAX_REG_INTRO_CHARS = 8_000

# §4 L2 — ``regulations_v2.llm_summary`` is the abstract. Measured: p50 = 1,347,
# p99 = 1,935, max = 2,415 chars — so 3,000 clips nothing today and is purely a
# net against a future re-ingest.
MAX_REG_ABSTRACT_CHARS = 3_000

# §4 L6 — the compact ``service_context`` fallback. A THIRD, local constant
# rather than either colliding import (§9 trap 9). Same value as the ura view
# (2,000) because it renders the same field; asserted equal below so a drift in
# ``services_unfold`` surfaces here instead of silently diverging.
SIMPLE_SEARCH_SERVICE_CONTEXT_CHARS = 2_000
assert SIMPLE_SEARCH_SERVICE_CONTEXT_CHARS == _URA_SERVICE_CONTEXT_CHARS, (
    "services_unfold.MAX_SERVICE_CONTEXT_CHARS changed — re-calibrate "
    "SIMPLE_SEARCH_SERVICE_CONTEXT_CHARS deliberately rather than tracking it."
)

# PostgREST page size for the per-regulation chunk sweep. The largest regulation
# carries 672 chunks (live max), so one page covers the corpus today; paging
# exists so a future re-ingest past PostgREST's 1,000-row default cap cannot
# silently truncate a document.
_CHUNK_PAGE = 1_000

# PostgREST ``in_`` batch size — the URL-length trap. Same value as
# ``ura/enrich._ID_BATCH``.
_ID_BATCH = 150


# =========================================================================== #
# Pure layer — token arithmetic.
# =========================================================================== #


def estimate_tokens(chars: int) -> int:
    """Token estimate for ``chars`` of Arabic text, via :data:`ARABIC_CHARS_PER_TOKEN`."""
    if chars <= 0:
        return 0
    return math.ceil(chars / ARABIC_CHARS_PER_TOKEN)


def chars_for_tokens(tokens: int) -> int:
    """Char budget equivalent to ``tokens``, via :data:`ARABIC_CHARS_PER_TOKEN`."""
    if tokens <= 0:
        return 0
    return int(tokens * ARABIC_CHARS_PER_TOKEN)


def estimate_content_chars(word_counts: list[int] | list[Any]) -> int:
    """Pre-fetch char estimate from ``chunks_v2.word_count`` (§5.4).

    The metadata-only measurement that decides whether a body is fetched at all.
    Never exact — see :data:`CONTENT_FETCH_GUARD_FACTOR` for the calibrated
    error bounds and why an estimate can safely *deny* rung 1 but never *grant* it.
    """
    total = 0
    for wc in word_counts:
        try:
            total += max(0, int(wc or 0))
        except (TypeError, ValueError):
            continue
    return int(total * ARABIC_CHARS_PER_WORD)


# =========================================================================== #
# Pure layer — document order (§4 L2 ordering trap).
# =========================================================================== #
#
# COPIED from ``backend/app/services/library_service._ordered_chunk_query``
# (~:2548-2570, and its rationale note at :2528-2545). It is copied and not
# imported because ``agents/`` must never import from ``backend/`` — and the two
# renderings must agree, since a chunk's position in the agent's unfold should
# match its position on the public library page.
#
# The canonical order is ``corpus DESC, position, chunk_ref``:
#
#   * ``position`` is scoped PER STREAM, not per document. A regulation's
#     appendix chunks (``corpus='appendix'``) restart at position 1 alongside its
#     body chunks. 1,184 regulations carry both streams, so ordering by
#     ``position`` ALONE interleaves ملاحق into the body — and getting it wrong
#     is SILENT: you simply get an appendix in the middle of the نظام.
#   * ``corpus DESC`` is what puts the body first: descending alphabetically,
#     'without_articles' > 'with_articles' > 'appendix'.
#   * ``chunk_ref`` is the stable tiebreaker (without it the pairing order is not
#     even stable between requests).
#
# ⚠ Inherited from the reference: this leans on every body-stream name sorting
# AFTER 'appendix' descending. A new corpus value that doesn't (say 'annex')
# would silently reorder documents. ``body_before_appendix`` below is the guard
# the SQL cannot have, and L2 additionally PARTITIONS on ``corpus`` before
# rendering — so even a rogue corpus value cannot land a ملحق inside the متن.

_CHUNK_BODY_CORPORA = ("with_articles", "without_articles")
_CHUNK_APPENDIX_CORPUS = "appendix"

# ``.order()`` calls that reproduce the SQL, in order. Applied by the fetch layer.
_CHUNK_ORDER: tuple[tuple[str, bool], ...] = (
    ("corpus", True),    # DESC
    ("position", False),
    ("chunk_ref", False),
)


def is_appendix_row(row: dict[str, Any]) -> bool:
    """True when a ``chunks_v2`` row belongs to the appendix stream.

    ``corpus`` is THE body/appendix discriminator (§4). An unknown or missing
    value counts as body — a new statutory stream must never be silently
    demoted into the ملاحق section.
    """
    return (row.get("corpus") or "") == _CHUNK_APPENDIX_CORPUS


def sort_document_order(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort ``chunks_v2`` rows into DOCUMENT reading order.

    Reproduces ``ORDER BY corpus DESC, position, chunk_ref`` exactly. Python's
    sort is stable, so sorting by the secondary keys first and then by ``corpus``
    descending yields the identical permutation — no negated-string hack, and no
    divergence from the SQL when a future corpus value appears.
    """
    ordered = sorted(
        rows,
        key=lambda r: (_as_int(r.get("position")), str(r.get("chunk_ref") or "")),
    )
    # The NULL-corpus leg is load-bearing, and `str(corpus or "")` got it wrong:
    # "" sorts LAST under reverse=True, dropping an unknown-corpus row BEHIND the
    # ملاحق. Postgres `ORDER BY corpus DESC` is NULLS FIRST, so that also diverged
    # from the SQL this function claims to reproduce. An unknown corpus is BODY
    # until proven otherwise — silently demoting real body text into the appendix
    # section is the exact failure this ordering exists to prevent.
    ordered.sort(
        key=lambda r: (
            1 if r.get("corpus") is None else 0,   # NULL first, mirroring NULLS FIRST
            str(r.get("corpus") or ""),
        ),
        reverse=True,
    )
    return ordered


def body_before_appendix(rows: list[dict[str, Any]]) -> bool:
    """True when no appendix chunk sits between two body chunks.

    The invariant ``sort_document_order`` exists to hold. Cheap enough to assert
    at runtime, and the guard the SQL ordering cannot carry.
    """
    seen_appendix = False
    for row in rows:
        if is_appendix_row(row):
            seen_appendix = True
        elif seen_appendix:
            return False
    return True


def partition_body_appendix(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split ordered rows into ``(body, appendixes)``, preserving order within each.

    Partitioning on ``corpus`` — rather than trusting the sort — is what makes an
    interleave structurally impossible in the rendered L2 document, whatever a
    future corpus value does to the ordering.
    """
    body = [r for r in rows if not is_appendix_row(r)]
    appendixes = [r for r in rows if is_appendix_row(r)]
    return body, appendixes


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# =========================================================================== #
# Pure layer — the §5 ladder.
# =========================================================================== #


def measure_chars(rows: list[dict[str, Any]], field: str) -> tuple[int, int]:
    """``(body_chars, appendix_chars)`` for ``field`` over ``chunks_v2`` rows."""
    body = 0
    appendix = 0
    for row in rows:
        n = len(row.get(field) or "")
        if is_appendix_row(row):
            appendix += n
        else:
            body += n
    return body, appendix


def choose_rung(
    *,
    body_content_chars: int,
    appendix_content_chars: int,
    body_summary_chars: int,
    appendix_summary_chars: int,
    content_estimated: bool = False,
) -> LadderDecision:
    """Pick the rung for one regulation and size the rung-3 slices (§5.1, §5.3).

    The four sides are separate parameters **on purpose**. Both ladder tests must
    measure body + appendixes TOGETHER, and the measured failure mode is scoring
    the body alone: 92 real regulations have a body under the 25k switch and a
    body+ملاحق over it, so scoring the body alone serves full text past the very
    ceiling the switch exists to enforce. A single ``content_chars`` parameter
    would make that bug expressible; this signature does not.

    Args:
        body_content_chars: ``Σ length(content)`` over non-appendix chunks.
        appendix_content_chars: ``Σ length(content)`` over appendix chunks.
        body_summary_chars: ``Σ length(summary)`` over non-appendix chunks.
        appendix_summary_chars: ``Σ length(summary)`` over appendix chunks.
        content_estimated: the content figures came from ``word_count`` rather
            than from a materialised body (§5.4). Recorded on the decision; it
            never changes the arithmetic.

    Returns:
        A :class:`LadderDecision`. ``body_budget_chars`` / ``appendix_budget_chars``
        are the §5.3 slices at rung 3, and the measured sizes below it.
    """
    content_total = max(0, body_content_chars) + max(0, appendix_content_chars)
    summary_total = max(0, body_summary_chars) + max(0, appendix_summary_chars)

    common = {
        "body_content_chars": body_content_chars,
        "appendix_content_chars": appendix_content_chars,
        "body_summary_chars": body_summary_chars,
        "appendix_summary_chars": appendix_summary_chars,
        "content_estimated": content_estimated,
    }

    if content_total <= REAL_CONTENT_MAX_CHARS:
        return LadderDecision(
            rung=RUNG_FULL_CONTENT,
            payload="content",
            body_budget_chars=body_content_chars,
            appendix_budget_chars=appendix_content_chars,
            reason="content_within_ceiling",
            **common,
        )

    if summary_total <= SUMMARIES_MAX_CHARS:
        return LadderDecision(
            rung=RUNG_SUMMARIES,
            payload="summary",
            body_budget_chars=body_summary_chars,
            appendix_budget_chars=appendix_summary_chars,
            reason="content_over_ceiling_summaries_fit",
            **common,
        )

    body_budget, appendix_budget = split_budget(
        body_chars=body_summary_chars,
        appendix_chars=appendix_summary_chars,
        total_budget=SUMMARIES_MAX_CHARS,
    )
    return LadderDecision(
        rung=RUNG_TRUNCATED_SUMMARIES,
        payload="summary",
        body_budget_chars=body_budget,
        appendix_budget_chars=appendix_budget,
        reason="summaries_over_ceiling",
        **common,
    )


def split_budget(
    *, body_chars: int, appendix_chars: int, total_budget: int
) -> tuple[int, int]:
    """Divide a char budget between body and ملاحق — reserved floors with spillover (§5.3).

    Greedy filling starves one side or the other, and the corpus proves it in
    both directions: 70% of regulations have no appendixes at all (body-first
    greed would be fine, appendix reservation wasted), while one regulation
    carries 1,161,719 chars of ملاحق against a 15,780-char body (appendix-first
    greed would starve the متن).

    So each side gets a reserved floor — :data:`BODY_SHARE` / :data:`APPENDIX_SHARE`
    — and **whatever a side does not use flows to the other**. The spillover is
    the load-bearing half: without it كود البناء السعودي العام (zero ملاحق) would
    forfeit a quarter of its budget to a section that does not exist.

    Body claims spillover first: the متن is the statute, the ملاحق are annexes to
    it, so on a document that overruns in both directions the body is the side
    to keep whole.

    Returns:
        ``(body_budget, appendix_budget)``. Their sum never exceeds
        ``total_budget``, and neither exceeds what its side actually needs.
    """
    body_need = max(0, body_chars)
    appendix_need = max(0, appendix_chars)
    total = max(0, total_budget)

    body_reserve = int(total * BODY_SHARE)
    appendix_reserve = total - body_reserve  # exact complement — no rounding loss

    body_alloc = min(body_need, body_reserve)
    appendix_alloc = min(appendix_need, appendix_reserve)

    spare = total - body_alloc - appendix_alloc
    if spare > 0:
        extra = min(spare, body_need - body_alloc)
        body_alloc += extra
        spare -= extra
    if spare > 0:
        extra = min(spare, appendix_need - appendix_alloc)
        appendix_alloc += extra

    return body_alloc, appendix_alloc


# =========================================================================== #
# Pure layer — position truncation.
# =========================================================================== #

# Appended to a slice that lost units, and to a body clipped mid-unit.
_TRUNCATION_MARK = "…"


def truncate_by_position(
    units: list[dict[str, Any]], field: str, budget_chars: int
) -> tuple[list[dict[str, Any]], UnfoldSection, str]:
    """Fill from the FIRST unit forward until the slice is spent; drop the rest (§5.1).

    Budget-derived, not a fixed count — the whole point of §5.3. ``units`` must
    already be in document order; this walks them in the order given.

    Whole units are the granularity, with one exception: when not even the first
    unit fits, it is kept and hard-clipped to the budget rather than yielding an
    empty section. A section that renders nothing is worse than one that renders
    a clipped opening — the synthesizer can see the truncation mark and say so.

    Returns:
        ``(kept_units, accounting, clipped_text)``. ``clipped_text`` is the
        hard-clipped body of the partially-kept FIRST unit, or ``""`` when every
        kept unit is whole. The caller renders ``kept_units`` and substitutes
        ``clipped_text`` for the last of them when it is non-empty.
    """
    total_chars = sum(len(u.get(field) or "") for u in units)
    section = UnfoldSection(
        name="",
        units_total=len(units),
        units_kept=0,
        chars_total=total_chars,
        chars_kept=0,
    )

    if budget_chars <= 0 or not units:
        return [], section, ""

    kept: list[dict[str, Any]] = []
    spent = 0
    for unit in units:
        size = len(unit.get(field) or "")
        if spent + size > budget_chars:
            break
        kept.append(unit)
        spent += size

    if kept:
        section.units_kept = len(kept)
        section.chars_kept = spent
        return kept, section, ""

    # Not even the first unit fits — keep it, clipped.
    first = units[0]
    clipped = (first.get(field) or "")[:budget_chars].rstrip() + _TRUNCATION_MARK
    section.units_kept = 1
    section.chars_kept = len(clipped)
    return [first], section, clipped


def _clip(text: str | None, limit: int) -> str:
    """Hard char clip with a truncation mark. ``None``/blank → ``""``."""
    value = (text or "").strip()
    if limit <= 0 or len(value) <= limit:
        return value
    return value[:limit].rstrip() + _TRUNCATION_MARK


# =========================================================================== #
# Pure layer — renderers. Every one takes plain dicts and returns an
# UnfoldResult; none of them touches a DB or an agent runtime.
# =========================================================================== #

_TRUNCATION_NOTE_AR = (
    "> ⚠ اقتُطع جزء من هذا المحتوى لتجاوزه الحد المسموح؛ "
    "ما يظهر هنا هو أول ما يرد في الوثيقة بترتيبها الأصلي."
)

_SUMMARY_MODE_NOTE_AR = (
    "> ℹ هذا النظام أطول من أن يُعرض نصّه الكامل، "
    "فيما يلي ملخصات أقسامه بترتيب الوثيقة."
)


def _finalise(
    *,
    level: SimpleSearchLevel,
    lines: list[str],
    sections: list[UnfoldSection],
    rung: int = RUNG_NOT_APPLICABLE,
    payload: str = "content",
    notes: list[str] | None = None,
    ok: bool = True,
) -> UnfoldResult:
    """Join rendered lines and stamp the measured size onto an UnfoldResult."""
    text = "\n".join(line for line in lines if line is not None).strip()
    return UnfoldResult(
        level=level,
        text=text,
        rung=rung,
        payload=payload,  # type: ignore[arg-type]
        chars=len(text),
        estimated_tokens=estimate_tokens(len(text)),
        sections=sections,
        notes=list(notes or []),
        ok=ok,
    )


def _not_found(level: SimpleSearchLevel, what: str, note: str) -> UnfoldResult:
    """A failed read, rendered as a short Arabic line the synthesizer can relay."""
    return UnfoldResult(
        level=level,
        text=f"تعذّر العثور على {what} المطلوب في قاعدة البيانات.",
        rung=RUNG_NOT_APPLICABLE,
        payload="none",
        chars=0,
        estimated_tokens=0,
        notes=[note],
        ok=False,
    )


def _reg_display_name(reg_row: dict[str, Any]) -> str:
    """``clean_title`` falling back to ``title`` — the house display rule."""
    return (reg_row.get("clean_title") or reg_row.get("title") or "").strip()


# --- L1 · Regulation chunk --------------------------------------------------


def render_chunk(
    chunk_row: dict[str, Any], framing: str = "", *, regulation_name: str = ""
) -> UnfoldResult:
    """Render ONE ``chunks_v2`` row: the reranker's framing + the REAL body (§4 L1).

    ``framing`` is the markdown block produced by ``format_chunk`` over an
    ``unfold_chunk_precise`` dict — regulation name, scope, and the three-chunk
    context window. It is reused verbatim (§7.2) and then the thing it
    deliberately omits is appended.

    > The single biggest gap in the existing helpers: ``CHUNK_SELECT``
    > (``unfold_reranker.py:79-82``) omits ``content`` on purpose — deep_search's
    > reranker never sees a chunk body. A fetch-one-object agent must select it
    > itself, which :data:`SIMPLE_CHUNK_SELECT` does.

    A chunk body needs no ladder: the largest ``chunks_v2.content`` in the corpus
    is 32,052 chars (~11.7k tokens), well under the 68,750-char ceiling. The cap
    is still applied as a net against a future re-ingest.
    """
    body = (chunk_row.get("content") or "").strip()
    title = (chunk_row.get("title") or "").strip() or "بدون عنوان"
    kept = _clip(body, REAL_CONTENT_MAX_CHARS)

    lines: list[str] = []
    if framing.strip():
        lines.append(framing.strip())
    else:
        # No framing available (reg meta fetch failed) — render a minimal header
        # so the synthesizer still knows which نظام the section belongs to.
        lines.append(f"### {title}")
        if regulation_name:
            lines.append(f"**النظام:** {regulation_name}")
    lines.append("")
    lines.append("**النص الكامل للمقطع:**")
    lines.append("")
    lines.append(kept if kept else "(لا يوجد نص لهذا المقطع)")
    if body and len(kept) < len(body):
        lines.append("")
        lines.append(_TRUNCATION_NOTE_AR)

    section = UnfoldSection(
        name="content",
        units_total=1,
        units_kept=1 if kept else 0,
        chars_total=len(body),
        chars_kept=len(kept),
    )
    notes = ["content_over_ceiling"] if body and len(kept) < len(body) else []
    return _finalise(level="chunk", lines=lines, sections=[section], notes=notes)


# --- L2 · Full regulation ---------------------------------------------------


def render_regulation_doc(
    reg_row: dict[str, Any],
    chunk_rows: list[dict[str, Any]],
    decision: LadderDecision,
) -> UnfoldResult:
    """Render a whole regulation as a document (§4 L2), under the §5 ladder.

    Composite order, fixed by §4::

        regulations_v2.llm_summary   the abstract
        regulations_v2.intro         highly variable — capped on its own
        BODY        corpus <> 'appendix'   all content OR all summaries
        APPENDIXES  corpus  = 'appendix'   all content OR all summaries

    ``chunk_rows`` must carry the field ``decision.payload`` selects
    (``content`` at rung 1, ``summary`` at rungs 2–3). Rows arrive in any order;
    they are sorted into document order and then PARTITIONED on ``corpus``, so a
    ملحق can never land between two body sections whatever the ordering does.
    """
    ordered = sort_document_order(chunk_rows)
    body_rows, appendix_rows = partition_body_appendix(ordered)
    field = "summary" if decision.payload == "summary" else "content"

    name = _reg_display_name(reg_row) or "نظام غير مُسمّى"
    lines: list[str] = [f"# {name}", ""]

    doc_type = (reg_row.get("doc_type_raw") or "").strip()
    if doc_type and doc_type != "غير محدد":
        lines.append(f"**نوع الوثيقة:** {doc_type}")
    landing = (reg_row.get("landing_url") or "").strip()
    if landing:
        lines.append(f"**الرابط:** {landing}")
    if lines[-1] != "":
        lines.append("")

    sections: list[UnfoldSection] = []
    notes: list[str] = [decision.reason]
    if decision.content_estimated:
        notes.append("content_size_estimated_from_word_count")

    # -- abstract -----------------------------------------------------------
    abstract_raw = (reg_row.get("llm_summary") or reg_row.get("summary") or "").strip()
    abstract = _clip(abstract_raw, MAX_REG_ABSTRACT_CHARS)
    if abstract:
        lines += ["## ملخص النظام", "", abstract, ""]
        sections.append(
            UnfoldSection(
                name="abstract",
                units_total=1,
                units_kept=1,
                chars_total=len(abstract_raw),
                chars_kept=len(abstract),
            )
        )

    # -- intro --------------------------------------------------------------
    intro_raw = (reg_row.get("intro") or "").strip()
    intro = _clip(intro_raw, MAX_REG_INTRO_CHARS)
    if intro:
        lines += ["## مقدمة النظام", "", intro, ""]
        sections.append(
            UnfoldSection(
                name="intro",
                units_total=1,
                units_kept=1,
                chars_total=len(intro_raw),
                chars_kept=len(intro),
            )
        )

    if decision.payload == "summary":
        lines += [_SUMMARY_MODE_NOTE_AR, ""]

    # -- body ---------------------------------------------------------------
    body_block, body_section = _render_chunk_run(
        body_rows, field, decision.body_budget_chars, heading="## متن النظام"
    )
    body_section.name = "body"
    sections.append(body_section)
    lines += body_block

    # -- appendixes ---------------------------------------------------------
    if appendix_rows:
        apx_block, apx_section = _render_chunk_run(
            appendix_rows, field, decision.appendix_budget_chars, heading="## الملاحق"
        )
        apx_section.name = "appendixes"
        sections.append(apx_section)
        lines += apx_block
    else:
        sections.append(
            UnfoldSection(name="appendixes", units_total=0, units_kept=0,
                          chars_total=0, chars_kept=0)
        )

    return _finalise(
        level="regulation_doc",
        lines=lines,
        sections=sections,
        rung=decision.rung,
        payload=decision.payload,
        notes=notes,
    )


def _render_chunk_run(
    rows: list[dict[str, Any]], field: str, budget_chars: int, *, heading: str
) -> tuple[list[str], UnfoldSection]:
    """Render one ordered run of chunks under a char budget. Returns (lines, accounting)."""
    if not rows:
        return [], UnfoldSection(name="", units_total=0, units_kept=0,
                                 chars_total=0, chars_kept=0)

    kept, section, clipped_last = truncate_by_position(rows, field, budget_chars)
    lines: list[str] = [heading, ""]
    for i, row in enumerate(kept):
        title = (row.get("title") or "").strip() or "بدون عنوان"
        lines.append(f"### {title}")
        lines.append("")
        is_clipped_tail = bool(clipped_last) and i == len(kept) - 1
        text = clipped_last if is_clipped_tail else (row.get(field) or "").strip()
        lines.append(text if text else "(لا يوجد نص)")
        lines.append("")
    if section.truncated:
        lines.append(_TRUNCATION_NOTE_AR)
        lines.append("")
    return lines, section


# --- L3 · Article -----------------------------------------------------------


def render_article(
    article_row: dict[str, Any],
    *,
    regulation_name: str = "",
    regulation_url: str = "",
    from_owns_fallback: bool = False,
) -> UnfoldResult:
    """Render ONE ``articles_v2`` row as the full article body (§4 L3).

    In deep_search a fetched article is text only, never a citation
    (``fetch_article_tool.md`` §7). Here it becomes a first-class reference —
    ``article_full`` (§6.1). That is the intended difference, not an oversight;
    this renderer only produces the text, the reference is the publisher's job.

    ``articles_v2.content`` is normally small (p50 = 325, p99 = 5,117 chars) but
    21 of 51,792 rows exceed the 68,750-char content ceiling — one reaches
    244,419 — so the cap is real, not theoretical.
    """
    body = (article_row.get("content") or "").strip()
    number = str(article_row.get("article_number") or "").strip()
    kept = _clip(body, REAL_CONTENT_MAX_CHARS)

    header = f"## نص المادة {number}" if number else "## نص المادة"
    if regulation_name:
        header += f" من {regulation_name}"

    lines: list[str] = [header, ""]
    if regulation_url:
        lines += [f"**الرابط:** {regulation_url}", ""]
    if from_owns_fallback:
        # §4 L3: articles_v2 SUPERSEDED the chunks_v2.owns map; we reinstate owns
        # as a SECOND layer, not a replacement. The chunk carries a RUN of مواد,
        # so the text is wider than the single article asked for — say so.
        lines += [
            "> ℹ لم تتوفّر هذه المادة مفردةً في فهرس المواد، "
            "وهذا هو المقطع النظامي الذي يحتوي عليها ضمن مجموعة مواد.",
            "",
        ]
    lines.append(kept if kept else "(لا يوجد نص لهذه المادة)")
    if body and len(kept) < len(body):
        lines += ["", _TRUNCATION_NOTE_AR]

    section = UnfoldSection(
        name="content",
        units_total=1,
        units_kept=1 if kept else 0,
        chars_total=len(body),
        chars_kept=len(kept),
    )
    notes: list[str] = []
    if from_owns_fallback:
        notes.append("owns_map_fallback")
    if body and len(kept) < len(body):
        notes.append("content_over_ceiling")
    return _finalise(level="article", lines=lines, sections=[section], notes=notes)


# --- L4 · Judgment ----------------------------------------------------------
#
# D12 / §7.3 — THE metered level. Everything below exists because a comment
# claiming the charge happens is exactly how it came not to happen: the eval
# (agents_reports/simple_search_eval_case_c.md §4) ran this path on a ruling the
# user had never unlocked and measured **17 unlocks before, 17 after** while the
# full 3,343-char ``cases.content`` was served. So the entitlement is now a
# VALUE the renderer demands, not a sentence in a docstring.
#
# The charge itself lives in ``backend/`` (``library_service.resolve_access``)
# and must not be imported here — this module is the retrieval core, which §11a
# keeps backend-free. The seam is inversion of control: the caller injects a
# resolver, this module decides WHEN to call it (after the row is built, never
# before) and refuses to render a body without its verdict.


@dataclass(frozen=True)
class JudgmentAccess:
    """The entitlement verdict for ONE ruling, bound to ONE ``cases.id``.

    Built by the runner from ``library_service.AccessDecision``. ``case_id`` is
    load-bearing and not decoration: it is what stops a grant obtained for one
    ruling from unlocking a different one, so the value cannot be hoisted out of
    a loop or reused across a fan-out.

    ``charged`` is True only when this turn wrote a new ledger row. A ruling the
    user already unlocked on ``/judgments`` comes back ``granted=True,
    charged=False`` — the same ``(user, 'judgment', cases.id)`` tuple, which the
    ``library_unlocks`` UNIQUE constraint makes a shared unlock by construction.
    """

    case_id: str
    granted: bool
    charged: bool = False
    reason: str = ""


#: Injected by the runner: ``cases.id`` → the entitlement for it. Async because
#: ``resolve_access`` is, and it must be called with the id of the row that was
#: actually found — never with an id guessed before the fetch.
JudgmentAccessResolver = Callable[[str], Awaitable[JudgmentAccess]]

#: ``AccessDecision.reason`` → the Arabic line the synthesizer relays. Anything
#: unlisted falls to the generic sentence; the reason token itself is NEVER
#: shown (it is English telemetry).
_JUDGMENT_REFUSAL_AR: dict[str, str] = {
    "quota_exhausted": (
        "تعذّر فتح نص هذا الحكم كاملًا: انتهى رصيد فتح المصادر في هذه الفترة. "
        "يتجدّد الرصيد تلقائيًا، أو يمكنك ترقية الخطة لفتحه الآن."
    ),
    "frozen_library": (
        "تعذّر فتح نص هذا الحكم كاملًا: المصادر المفتوحة سابقًا مجمّدة على "
        "الخطة الحالية. ترقية الخطة تعيد فتحها."
    ),
    "locked": "تعذّر فتح نص هذا الحكم كاملًا: لا توجد خطة سارية على الحساب.",
    "anonymous": "تعذّر فتح نص هذا الحكم كاملًا: يلزم تسجيل الدخول أولًا.",
}

_JUDGMENT_REFUSAL_DEFAULT_AR = "تعذّر فتح نص هذا الحكم كاملًا في الوقت الحالي."


def judgment_entitled(access: JudgmentAccess | None, case_id: str) -> bool:
    """Is this verdict a grant FOR THIS ruling? — the single predicate.

    Four ways to be False, and each one is a real failure mode rather than
    defensive padding: no resolver was wired (the D12 bug itself), the resolver
    refused, the row carries no id to bind to, or the grant belongs to a
    DIFFERENT ruling (a hoisted/reused verdict).
    """
    return bool(
        access is not None
        and access.granted
        and case_id
        and access.case_id == case_id
    )


def _judgment_refused(access: JudgmentAccess | None) -> UnfoldResult:
    """The no-body result. Carries an Arabic line and zero ruling text."""
    reason = (access.reason if access else "") or "unresolved"
    return UnfoldResult(
        level="judgment",
        text=_JUDGMENT_REFUSAL_AR.get(reason, _JUDGMENT_REFUSAL_DEFAULT_AR),
        rung=RUNG_NOT_APPLICABLE,
        payload="none",
        chars=0,
        estimated_tokens=0,
        notes=["judgment_access_denied", f"access_reason_{reason}"],
        ok=False,
    )


def render_judgment(
    case_row: dict[str, Any],
    *,
    entity_name: str = "",
    access: JudgmentAccess | None = None,
) -> UnfoldResult:
    """Render ONE ``cases`` row as the FULL ruling (§4 L4) — **if it is paid for**.

    ``access`` has no permissive default. Omit it and this returns the refusal
    result with no ruling text in it, because this function is the only place in
    the tree that writes ``cases.content`` into a prompt: gating it here means
    the body cannot be produced by a caller that forgot to charge, whatever path
    it came in on. That is the whole point — the previous version asserted the
    charge in this very docstring and no charge existed.

    **``cases.content``, not ``cases.summary``.** This is a deliberate widening:
    deep_search never puts raw ruling text in front of an LLM (``case_topics_loop``
    D2 made ``summary`` a hard replacement), and the public source popup shows the
    ملخص because the full text is the PDPL-sensitive payload ``/judgments`` is
    noindexed over. D12 authorises it — the gate protects the preview surface,
    not the agent — and its other half is the unlock this function now demands.

    Anything derived from ``cases.summary`` runs through
    :func:`~agents.deep_search_v4.shared.case_summary.strip_pipeline_sections`
    first: 16,505 of 30,531 rows carry a resolver-telemetry appendix and 252
    carry a Python traceback, and a model happily restates either into the
    visible answer.

    The Arabic court-level label comes from ``shared/court_levels.py``. Four
    independent hand-rolled ternaries previously collapsed the three-value column
    to two and mislabelled all 125 supreme rulings as ابتدائي — hence the shared
    home, and ``strict=True`` here so an unknown value prints nothing rather than
    asserting ابتدائي.
    """
    case_id = str(case_row.get("id") or "")
    if not judgment_entitled(access, case_id):
        if access is None:
            logger.error(
                "simple_search: render_judgment called with NO entitlement for "
                "case %s — refusing to serve cases.content (D12/§7.3)", case_id,
            )
        elif access.granted and access.case_id != case_id:
            logger.error(
                "simple_search: judgment entitlement is for case %s, not %s — "
                "refusing (a grant is bound to ONE ruling)",
                access.case_id, case_id,
            )
        return _judgment_refused(access)

    body = (case_row.get("content") or "").strip()
    kept = _clip(body, REAL_CONTENT_MAX_CHARS)

    court = (case_row.get("court") or "").strip()
    level_ar = court_level_ar(case_row.get("court_level"), strict=True)
    title_bits = [b for b in ("حكم", court) if b]
    lines: list[str] = ["## " + " ".join(title_bits), ""]

    meta: list[str] = []
    if level_ar:
        meta.append(f"**درجة المحكمة:** {level_ar}")
    for label, key in (
        ("المدينة", "city"),
        ("رقم القضية", "case_number"),
        ("رقم الحكم", "judgment_number"),
        ("التاريخ الهجري", "date_hijri"),
    ):
        value = str(case_row.get(key) or "").strip()
        if value:
            meta.append(f"**{label}:** {value}")
    if entity_name:
        meta.append(f"**الجهة:** {entity_name}")
    for label, key in (
        ("محكمة الاستئناف", "appeal_court"),
        ("مدينة الاستئناف", "appeal_city"),
        ("رقم حكم الاستئناف", "appeal_judgment_number"),
        ("تاريخ الاستئناف", "appeal_date_hijri"),
        ("نتيجة الاستئناف", "appeal_result"),
    ):
        value = str(case_row.get(key) or "").strip()
        if value:
            meta.append(f"**{label}:** {value}")
    url = (case_row.get("details_url") or "").strip()
    if url:
        meta.append(f"**الرابط:** {url}")
    if meta:
        lines += meta + [""]

    # short_summary is a ~200-char lead, NOT `summary` — but it is derived from
    # the same pipeline document, so it is stripped on the same rule.
    lead = strip_pipeline_sections((case_row.get("short_summary") or "").strip())
    if lead:
        lines += ["**موضوع الحكم:** " + lead, ""]

    lines += ["### نص الحكم", ""]
    lines.append(kept if kept else "(لا يوجد نص لهذا الحكم)")
    if body and len(kept) < len(body):
        lines += ["", _TRUNCATION_NOTE_AR]

    section = UnfoldSection(
        name="content",
        units_total=1,
        units_kept=1 if kept else 0,
        chars_total=len(body),
        chars_kept=len(kept),
    )
    notes = ["content_over_ceiling"] if body and len(kept) < len(body) else []
    # Telemetry the ledger can be reconciled against: whether THIS turn paid.
    notes.append("judgment_access_charged" if access.charged else "judgment_access_free")
    return _finalise(level="judgment", lines=lines, sections=[section], notes=notes)


# --- L5 · Circular ----------------------------------------------------------


def render_circular(
    circular_row: dict[str, Any], *, entity_name: str = ""
) -> UnfoldResult:
    """Render ONE ``circulars`` row: full content + issuing entity + source link (§4 L5).

    The user-facing popup serves this uncapped (``source_viewer.py:529-553``) and
    the agent side matches it — with the same 68,750-char net every level carries,
    which clips 4 of 1,843 circulars (0.22%; the longest is 168,782 chars).
    """
    body = (circular_row.get("content") or "").strip()
    kept = _clip(body, REAL_CONTENT_MAX_CHARS)
    title = (circular_row.get("title") or "").strip() or "بدون عنوان"

    lines: list[str] = [f"## تعميم: {title}", ""]
    if entity_name:
        lines.append(f"**الجهة:** {entity_name}")
    doc_type = (circular_row.get("doc_type") or "").strip()
    if doc_type:
        lines.append(f"**نوع الوثيقة:** {doc_type}")
    source = (circular_row.get("source") or "").strip()
    if source:
        lines.append(f"**الرابط:** {source}")
    if lines[-1] != "":
        lines.append("")

    lines += ["### نص التعميم", ""]
    lines.append(kept if kept else "(لا يوجد نص لهذا التعميم)")
    if body and len(kept) < len(body):
        lines += ["", _TRUNCATION_NOTE_AR]

    section = UnfoldSection(
        name="content",
        units_total=1,
        units_kept=1 if kept else 0,
        chars_total=len(body),
        chars_kept=len(kept),
    )
    notes = ["content_over_ceiling"] if body and len(kept) < len(body) else []
    return _finalise(level="circular", lines=lines, sections=[section], notes=notes)


# --- L6 · Service -----------------------------------------------------------


def render_service(service_row: dict[str, Any]) -> UnfoldResult:
    """Render ONE ``services`` row as the rich structured payload (§4 L6).

    Reuses ``build_service_aggregator_content`` (``ura/services_unfold.py``)
    wholesale — intro, steps, requirements, required documents, link — and falls
    back to the compact ``service_context`` when the structured fields are absent.

    > **Output constraint, carried forward and not re-litigated (2026-08-03).**
    > The UNFOLD carries the rich payload; the ANSWER must stay a well-framed
    > pointer. We do not restate a procedure's steps under ريحان's chrome — it
    > makes us the apparent authority on a process we do not own, and steps go
    > stale when the issuing entity edits them. That constraint belongs to level
    > 6's synthesizer prompt, which is why it is only a note here: this function
    > must still hand the model the full payload so it can frame accurately.
    """
    fallback = build_service_context(service_row)
    content = build_service_aggregator_content(
        service_name=service_row.get("service_name_ar") or "",
        intro_title=service_row.get("intro_title") or "",
        provider_name=service_row.get("provider_name") or "",
        intro_description=service_row.get("intro_description") or "",
        steps=service_row.get("steps"),
        requirements=service_row.get("requirements"),
        required_documents=service_row.get("required_documents"),
        service_url=service_row.get("service_url") or "",
        url=service_row.get("url") or "",
        fallback_context=fallback,
    )
    kept = _clip(content, REAL_CONTENT_MAX_CHARS)

    lines: list[str] = [kept if kept else "(لا توجد تفاصيل لهذه الخدمة)"]
    if content and len(kept) < len(content):
        lines += ["", _TRUNCATION_NOTE_AR]

    section = UnfoldSection(
        name="content",
        units_total=1,
        units_kept=1 if kept else 0,
        chars_total=len(content),
        chars_kept=len(kept),
    )
    notes = ["content_over_ceiling"] if content and len(kept) < len(content) else []
    return _finalise(level="service", lines=lines, sections=[section], notes=notes)


# =========================================================================== #
# Fetch layer — sync PostgREST. Every one of these is called through
# ``asyncio.to_thread`` and returns a default rather than raising.
# =========================================================================== #

# §4 L1 — the reranker's CHUNK_SELECT deliberately omits ``content``; this adds
# it, plus ``corpus`` (the body/appendix discriminator) and ``word_count`` (the
# §5.4 metadata measurement).
SIMPLE_CHUNK_SELECT = (
    "id, chunk_ref, regulation_id, position, corpus, word_count, "
    "prev_chunk_id, next_chunk_id, title, summary, context, content"
)

# §5.4 — the metadata sweep. NO ``content``, NO ``summary``: the whole point is
# to size the document before either is materialised.
CHUNK_METADATA_SELECT = "id, chunk_ref, regulation_id, position, corpus, word_count, title"

REGULATION_SELECT = (
    "id, clean_title, title, doc_type_raw, landing_url, pdf_url, "
    "scope, intro, summary, llm_summary"
)

ARTICLE_SELECT = "id, regulation_id, article_number, content, chunk_parent_id"

CASE_SELECT = (
    "id, case_ref, entity_id, court, court_level, city, case_number, "
    "judgment_number, date_hijri, date_gregorian, details_url, content, "
    "short_summary, appeal_court, appeal_city, appeal_judgment_number, "
    "appeal_date_hijri, appeal_result"
)

CIRCULAR_SELECT = "id, circ_ref, entity_id, title, doc_type, content, source"

SERVICE_SELECT = (
    "id, service_ref, service_name_ar, provider_name, service_context, "
    "intro_title, intro_description, steps, requirements, required_documents, "
    "service_url, url"
)


def _rows(resp: Any) -> list[dict[str, Any]]:
    """``getattr(resp, "data", None) or []`` — the house response unwrap."""
    data = getattr(resp, "data", None) or []
    if isinstance(data, dict):
        return [data]
    return list(data)


def _one(resp: Any) -> dict[str, Any]:
    rows = _rows(resp)
    return rows[0] if rows else {}


def _fetch_row(supabase, table: str, column: str, value: str, select: str) -> dict[str, Any]:
    """One row by an equality key. ``{}`` on miss or on any failure."""
    if not value:
        return {}
    try:
        resp = (
            supabase.table(table)
            .select(select)
            .eq(column, value)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 — best-effort read
        logger.warning("simple_search unfold: %s.%s=%r failed: %s", table, column, value, exc)
        return {}
    return _one(resp)


def _fetch_chunks_for_regulation(
    supabase, regulation_id: str, select: str
) -> list[dict[str, Any]]:
    """All ``chunks_v2`` rows of one regulation, in DOCUMENT order, paged.

    The ``.order()`` chain is the copied ``_ordered_chunk_query`` ordering (see
    the module note above). Ordering in the QUERY as well as in Python matters
    here: pages are stitched together, so an unordered fetch would interleave
    across page boundaries no matter how the result is sorted afterwards.
    """
    if not regulation_id:
        return []
    out: list[dict[str, Any]] = []
    offset = 0
    while True:
        try:
            query = supabase.table("chunks_v2").select(select).eq(
                "regulation_id", str(regulation_id)
            )
            for col, desc in _CHUNK_ORDER:
                query = query.order(col, desc=desc) if desc else query.order(col)
            resp = query.range(offset, offset + _CHUNK_PAGE - 1).execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "simple_search unfold: chunk page %d for reg %s failed: %s",
                offset, regulation_id, exc,
            )
            break
        page = _rows(resp)
        out.extend(page)
        if len(page) < _CHUNK_PAGE:
            break
        offset += _CHUNK_PAGE
    return out


def _fetch_entity_name(supabase, entity_id: str) -> str:
    """``entities.entity_name`` (Arabic) for one id; ``""`` on miss."""
    row = _fetch_row(supabase, "entities", "id", str(entity_id or ""), "id, entity_name")
    return (row.get("entity_name") or "").strip()


def _fetch_article_by_number(
    supabase, regulation_id: str, article_number: str
) -> dict[str, Any]:
    """``articles_v2`` row for ``(regulation_id, article_number)``.

    ``article_number`` is matched by exact TEXT equality — the corpus stores
    compound values like ``"1-1"`` as strings (same rule as
    ``fetch_article._fetch_article_content``).
    """
    if not (regulation_id and article_number):
        return {}
    try:
        resp = (
            supabase.table("articles_v2")
            .select(ARTICLE_SELECT)
            .eq("regulation_id", str(regulation_id))
            .eq("article_number", str(article_number).strip())
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "simple_search unfold: article (%s, %r) failed: %s",
            regulation_id, article_number, exc,
        )
        return {}
    return _one(resp)


def _fetch_article_by_owns(
    supabase, regulation_id: str, article_number: str
) -> dict[str, Any]:
    """§4 L3 fallback — find the chunk whose ``owns.MADDA`` carries the article.

    ``fetch_article_tool.md`` §2 records that ``articles_v2`` SUPERSEDED the
    ``owns`` approach; this deliberately reinstates it as a SECOND layer, not a
    replacement, for the numbers ``articles_v2`` has no row for.

    ``owns`` is a jsonb map of section kind → integer list, e.g.
    ``{"BAB": [5], "FASL": [6], "MADDA": [14, 15, 16, 17, 18]}``. PostgREST
    cannot index into that array usefully, so the regulation's chunks are swept
    and matched in Python — the sweep is one regulation wide, never corpus wide.

    Returns an ``articles_v2``-SHAPED dict (``content`` + ``article_number``) so
    the renderer needs no second branch. The body is the whole owning chunk,
    which spans a RUN of مواد — the renderer says so.
    """
    try:
        wanted = int(str(article_number).strip())
    except (TypeError, ValueError):
        return {}  # compound numbers ("1-1") have no integer in the owns map
    rows = _fetch_chunks_for_regulation(
        supabase, regulation_id, "id, chunk_ref, corpus, position, title, content, owns"
    )
    for row in sort_document_order(rows):
        owns = row.get("owns")
        if not isinstance(owns, dict):
            continue
        madda = owns.get("MADDA")
        if not isinstance(madda, list):
            continue
        if wanted in {_as_int(m) for m in madda}:
            return {
                "id": "",
                "regulation_id": regulation_id,
                "article_number": str(article_number),
                "content": row.get("content") or "",
                "chunk_parent_id": row.get("id") or "",
            }
    return {}


# =========================================================================== #
# Entry points — one per level (§4). All async; all wrap the sync client.
# =========================================================================== #


async def unfold_chunk(supabase, resolved: ResolvedObject) -> UnfoldResult:
    """L1 — one regulation chunk, with its real body (§4 L1)."""
    chunk = await asyncio.to_thread(
        _fetch_row, supabase, "chunks_v2", "id", resolved.chunk_id, SIMPLE_CHUNK_SELECT
    )
    if not chunk:
        return _not_found("chunk", "المقطع النظامي", "chunk_row_not_found")

    # Reuse (§7.2): the PRECISE unfold + formatter give the regulation name,
    # scope and the three-chunk context window. Both are sync and hit the DB.
    framing = ""
    try:
        unfolded = await asyncio.to_thread(unfold_chunk_precise, supabase, chunk)
        framing = format_chunk(unfolded, resolved.title or "C1")
    except Exception as exc:  # noqa: BLE001 — framing is decoration, body is not
        logger.warning("simple_search unfold: chunk framing failed: %s", exc)

    return render_chunk(chunk, framing, regulation_name=resolved.subtitle)


async def unfold_regulation_doc(supabase, resolved: ResolvedObject) -> UnfoldResult:
    """L2 — a whole regulation, under the §5 ladder.

    The §5.4 sequence, and the reason it has three fetch phases rather than one:

    1. **Metadata sweep** — ``word_count`` only, no bodies. Sizes the document.
    2. **Content**, but only when the estimate clears
       :data:`CONTENT_FETCH_GUARD_FACTOR`. Fetched content is re-measured
       EXACTLY, so the estimate can deny rung 1 but never grant it.
    3. **Summaries**, only when content did not win.

    89.3% of regulations stop after phase 2 with rung 1.
    """
    reg_id = resolved.regulation_id
    reg_row = await asyncio.to_thread(
        _fetch_row, supabase, "regulations_v2", "id", reg_id, REGULATION_SELECT
    )
    if not reg_row:
        return _not_found("regulation_doc", "النظام", "regulation_row_not_found")

    meta = await asyncio.to_thread(
        _fetch_chunks_for_regulation, supabase, reg_id, CHUNK_METADATA_SELECT
    )
    if not meta:
        # A regulation with no chunks still has an abstract and an intro — render
        # the header alone rather than failing the whole lookup.
        decision = choose_rung(
            body_content_chars=0, appendix_content_chars=0,
            body_summary_chars=0, appendix_summary_chars=0,
        )
        result = render_regulation_doc(reg_row, [], decision)
        result.notes.append("regulation_has_no_chunks")
        return result

    body_meta, appendix_meta = partition_body_appendix(meta)
    est_body = estimate_content_chars([r.get("word_count") for r in body_meta])
    est_appendix = estimate_content_chars([r.get("word_count") for r in appendix_meta])
    guard = int(REAL_CONTENT_MAX_CHARS * CONTENT_FETCH_GUARD_FACTOR)

    if est_body + est_appendix <= guard:
        rows = await asyncio.to_thread(
            _fetch_chunks_for_regulation, supabase, reg_id, SIMPLE_CHUNK_SELECT
        )
        body_rows, appendix_rows = partition_body_appendix(rows)
        body_chars, appendix_chars = measure_chars(rows, "content")
        if body_chars + appendix_chars <= REAL_CONTENT_MAX_CHARS:
            decision = choose_rung(
                body_content_chars=body_chars,
                appendix_content_chars=appendix_chars,
                body_summary_chars=0,
                appendix_summary_chars=0,
            )
            return render_regulation_doc(reg_row, rows, decision)
        # Fetched but over the ceiling — the summaries path below re-uses the
        # rows we already hold (SIMPLE_CHUNK_SELECT carries `summary` too), so
        # the overshoot costs no extra round trip.
        summary_body, summary_apx = measure_chars(rows, "summary")
        decision = choose_rung(
            body_content_chars=body_chars,
            appendix_content_chars=appendix_chars,
            body_summary_chars=summary_body,
            appendix_summary_chars=summary_apx,
        )
        return render_regulation_doc(reg_row, rows, decision)

    # Too large to materialise. Summaries only — and the content figures stay as
    # the estimate, which is sound here: the guard means est > 137,500, and the
    # measured floor of true/est (0.562) puts true content above 77,275 — over
    # the 68,750 ceiling either way, so rung 1 was never reachable.
    rows = await asyncio.to_thread(
        _fetch_chunks_for_regulation,
        supabase,
        reg_id,
        "id, chunk_ref, regulation_id, position, corpus, word_count, title, summary",
    )
    summary_body, summary_apx = measure_chars(rows, "summary")
    decision = choose_rung(
        body_content_chars=est_body,
        appendix_content_chars=est_appendix,
        body_summary_chars=summary_body,
        appendix_summary_chars=summary_apx,
        content_estimated=True,
    )
    return render_regulation_doc(reg_row, rows, decision)


async def unfold_article(supabase, resolved: ResolvedObject) -> UnfoldResult:
    """L3 — one مادة, with the ``owns`` fallback (§4 L3).

    Three resolution paths, in order:

    1. ``article_id`` — the row was already identified.
    2. ``(regulation_id, article_number)`` — exact TEXT equality on
       ``article_number``; compound values like ``"1-1"`` exist, so it is never
       cast to int. Same key ``fetch_article`` uses.
    3. The ``chunks_v2.owns`` MADDA map — the revived second layer.
    """
    article: dict[str, Any] = {}
    fallback = False

    if resolved.article_id:
        article = await asyncio.to_thread(
            _fetch_row, supabase, "articles_v2", "id", resolved.article_id, ARTICLE_SELECT
        )
    if not article and resolved.regulation_id and resolved.article_number:
        article = await asyncio.to_thread(
            _fetch_article_by_number,
            supabase,
            resolved.regulation_id,
            resolved.article_number,
        )
    if not article and resolved.regulation_id and resolved.article_number:
        article = await asyncio.to_thread(
            _fetch_article_by_owns,
            supabase,
            resolved.regulation_id,
            resolved.article_number,
        )
        fallback = bool(article)

    if not article:
        return _not_found("article", "المادة", "article_row_not_found")

    reg_id = article.get("regulation_id") or resolved.regulation_id
    reg_row = await asyncio.to_thread(
        _fetch_row, supabase, "regulations_v2", "id", str(reg_id or ""), REGULATION_SELECT
    )
    return render_article(
        article,
        regulation_name=_reg_display_name(reg_row) or resolved.subtitle,
        regulation_url=(reg_row.get("landing_url") or "").strip(),
        from_owns_fallback=fallback,
    )


async def unfold_judgment(
    supabase,
    resolved: ResolvedObject,
    *,
    judgment_access: JudgmentAccessResolver | None = None,
) -> UnfoldResult:
    """L4 — one ruling, FULL ``cases.content`` (§4 L4), for exactly ONE unlock.

    Keyed by ``cases.id`` when present, else by ``case_ref`` — the key
    ``case:<ref>`` references have always carried.

    **Build, then charge** — the order ``workspace.py::get_reference_source``
    settled on 2026-08-15 (its step 4/5 comment). A ruling that is not in the
    corpus 404s here having cost nothing; charging first would spend a permanent
    unlock on a document that was never delivered.

    The charge keys on the id of the row we actually found, which is why the
    resolver is called HERE and not by the caller: a ``case_ref``-only object
    (every case-C attachment is one — ``case:<ref>`` is what the panel stores)
    does not know its ``cases.id`` until this fetch returns, and the ledger,
    ``/judgments`` and the reference panel all key on ``cases.id``.
    """
    if resolved.case_id:
        case = await asyncio.to_thread(
            _fetch_row, supabase, "cases", "id", resolved.case_id, CASE_SELECT
        )
    else:
        case = await asyncio.to_thread(
            _fetch_row, supabase, "cases", "case_ref", resolved.case_ref, CASE_SELECT
        )
    if not case:
        return _not_found("judgment", "الحكم", "case_row_not_found")

    case_id = str(case.get("id") or "")
    access = await judgment_access(case_id) if judgment_access else None
    if not judgment_entitled(access, case_id):
        # Refuse before the entity join: a refused ruling costs one read, not two.
        return render_judgment(case, access=access)

    entity_name = ""
    if case.get("entity_id"):
        entity_name = await asyncio.to_thread(
            _fetch_entity_name, supabase, str(case.get("entity_id"))
        )
    return render_judgment(case, entity_name=entity_name, access=access)


async def unfold_circular(supabase, resolved: ResolvedObject) -> UnfoldResult:
    """L5 — one تعميم, full content + issuing entity + source link (§4 L5)."""
    circular = await asyncio.to_thread(
        _fetch_row, supabase, "circulars", "id", resolved.circular_id, CIRCULAR_SELECT
    )
    if not circular:
        return _not_found("circular", "التعميم", "circular_row_not_found")

    entity_name = ""
    if circular.get("entity_id"):
        entity_name = await asyncio.to_thread(
            _fetch_entity_name, supabase, str(circular.get("entity_id"))
        )
    return render_circular(circular, entity_name=entity_name)


async def unfold_service(supabase, resolved: ResolvedObject) -> UnfoldResult:
    """L6 — one government service, the rich structured payload (§4 L6)."""
    service = await asyncio.to_thread(
        _fetch_row, supabase, "services", "id", resolved.service_id, SERVICE_SELECT
    )
    if not service:
        return _not_found("service", "الخدمة", "service_row_not_found")
    return render_service(service)


# =========================================================================== #
# Dispatch — the level registry (§7.1 pattern: no silent default; a miss raises
# a KeyError that LISTS the available keys).
# =========================================================================== #

UnfoldFn = Callable[[Any, ResolvedObject], Awaitable[UnfoldResult]]

_UNFOLDERS: dict[str, UnfoldFn] = {
    "chunk": unfold_chunk,
    "regulation_doc": unfold_regulation_doc,
    "article": unfold_article,
    "judgment": unfold_judgment,
    "circular": unfold_circular,
    "service": unfold_service,
}


def get_unfolder(level: str) -> UnfoldFn:
    """The unfold function for ``level``.

    Raises:
        KeyError: naming the unknown level AND listing the available ones. No
            silent default — an unregistered level is a wiring bug, and a
            fallback would ship a plausible-looking answer about the wrong
            object (the pattern is ``aggregator/prompts.py:691-718``).
    """
    try:
        return _UNFOLDERS[level]
    except KeyError:
        raise KeyError(
            f"simple_search: unknown level {level!r}. "
            f"Available levels: {sorted(_UNFOLDERS)}"
        ) from None


async def unfold(
    supabase,
    resolved: ResolvedObject,
    *,
    judgment_access: JudgmentAccessResolver | None = None,
) -> UnfoldResult:
    """``unfold(always)`` — the synthesizer's input path (§2.2).

    Dispatches on ``resolved.level``, after checking the object actually carries
    the id its level is opened by. Cases A, B and C all arrive here identically:
    the full unfold never belonged to the searcher.

    Args:
        supabase: the sync **service-role** client. The anon key hits RLS and
            silently returns empty results (§9 trap 11).
        resolved: the searcher's product — ids + level + display fields.
        judgment_access: the D12 entitlement resolver, required to serve a
            ruling's body and IGNORED by the other five levels — **regulations
            are not metered** (D12), so wiring one is not a licence to charge
            for them. Omit it and a judgment unfolds to the refusal line rather
            than to free ruling text.
    """
    missing = resolved.missing_id()
    if missing:
        logger.warning(
            "simple_search unfold: level=%s missing %s", resolved.level, missing
        )
        return _not_found(
            resolved.level, resolved.label_ar(), f"unresolved_{missing}"
        )
    # The metered level takes an argument the other five have no use for. An
    # explicit branch rather than a uniform signature: five unfolders carrying an
    # unused ``judgment_access`` would read as "any of these might charge", and
    # the point of D12 is that exactly one of them does.
    if resolved.level == "judgment":
        return await unfold_judgment(
            supabase, resolved, judgment_access=judgment_access
        )
    return await get_unfolder(resolved.level)(supabase, resolved)


__all__ = [
    # constants
    "ARABIC_CHARS_PER_TOKEN",
    "ARABIC_CHARS_PER_WORD",
    "REAL_CONTENT_MAX_TOKENS",
    "SUMMARIES_MAX_TOKENS",
    "REAL_CONTENT_MAX_CHARS",
    "SUMMARIES_MAX_CHARS",
    "BODY_SHARE",
    "APPENDIX_SHARE",
    "CONTENT_FETCH_GUARD_FACTOR",
    "MAX_REG_INTRO_CHARS",
    "MAX_REG_ABSTRACT_CHARS",
    "SIMPLE_SEARCH_SERVICE_CONTEXT_CHARS",
    "SIMPLE_CHUNK_SELECT",
    "CHUNK_METADATA_SELECT",
    "REGULATION_SELECT",
    "ARTICLE_SELECT",
    "CASE_SELECT",
    "CIRCULAR_SELECT",
    "SERVICE_SELECT",
    # D12 / §7.3 — the ruling entitlement seam
    "JudgmentAccess",
    "JudgmentAccessResolver",
    "judgment_entitled",
    # pure layer
    "estimate_tokens",
    "chars_for_tokens",
    "estimate_content_chars",
    "is_appendix_row",
    "sort_document_order",
    "body_before_appendix",
    "partition_body_appendix",
    "measure_chars",
    "choose_rung",
    "split_budget",
    "truncate_by_position",
    "render_chunk",
    "render_regulation_doc",
    "render_article",
    "render_judgment",
    "render_circular",
    "render_service",
    # entry points
    "unfold",
    "unfold_chunk",
    "unfold_regulation_doc",
    "unfold_article",
    "unfold_judgment",
    "unfold_circular",
    "unfold_service",
    "get_unfolder",
]
