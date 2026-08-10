"""Preprocessor — deterministic reference assembly for the aggregator.

This module is PURE CODE (no LLM). Under URA 2.0, the URA merger has already
deduplicated results by ``ref_id`` and tier-split them into ``high_results`` /
``medium_results``. The preprocessor here simply:

1. Walks ``ura.high_results`` then ``ura.medium_results`` (so high-relevance
   refs get lower citation numbers).
2. Dispatches on the typed discriminated union (``RegURAResult`` /
   ``ComplianceURAResult`` / ``CircularURAResult`` / ``CaseURAResult``) to build
   a ``Reference`` per URA result — no dict lookups, no cross-domain dedup.
3. Assigns 1-based citation numbers in iteration order.
4. Returns a parallel mapping ``ref.n -> appears_in_sub_queries`` (sorted)
   so the post-validator can do coverage checks.

When ``agg_input.ura is None`` (legacy callers that build ``sub_queries``
manually), the old pre-URA path still runs: iterate ``sub_queries.results``,
dedup by identity tuple, merge relevance / reasoning.

Assigning citation numbers in code (not in the LLM) is the central
anti-hallucination mechanism for the aggregator.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Iterable

from agents.deep_search_v4.reg_compliance_search.models import RerankedResult
from agents.deep_search_v4.shared.case_summary import strip_pipeline_sections
from agents.deep_search_v4.source_viewer import build_source_view
from agents.deep_search_v4.ura.schema import (
    AggregatorItem,
    CaseURAResult,
    CircularURAResult,
    ComplianceURAResult,
    CrossRef,
    RegURAResult,
    ReferenceView,
    URAResultBase,
)
from shared.seo.judgment_naming import judgment_subject

from .models import AggregatorInput, Reference

logger = logging.getLogger(__name__)

__all__ = [
    "preprocess_references",
    "attach_source_views",
    "collect_ordered_ura_results",
    "build_snippet",
    "render_aggregator_content",
    "render_cross_ref",
    "case_ref_to_cross_ref",
    "COURT_LEVEL_AR",
    "_identity_key",
    "_merge_duplicates",
]


# ---------------------------------------------------------------------------
# Identity + merging
# ---------------------------------------------------------------------------


def _norm(s: str | None) -> str:
    """Normalize for identity comparisons (trim + collapse whitespace)."""
    if not s:
        return ""
    return " ".join(s.split()).strip()


def _identity_key(result: RerankedResult) -> tuple[str, str, str]:
    """Return the stable identity tuple for a legacy reranker result.

    Only used on the backward-compat path when ``agg_input.ura is None``.
    """
    reg = _norm(result.regulation_title)
    if result.source_type == "article":
        return ("article", reg, _norm(result.article_num))
    # section (only other option per the Literal)
    return ("section", reg, _norm(result.section_title))


def _is_citable(result: RerankedResult) -> bool:
    """Drop malformed legacy reranker results that cannot produce a citation."""
    reg = _norm(result.regulation_title)
    if not reg:
        title = _norm(result.title)
        has_regulation_in_title = any(
            marker in title
            for marker in ("نظام ", "لائحة ", "الأدلة ", "اللوائح ")
        )
        if not has_regulation_in_title:
            return False
        return True
    if result.source_type == "article":
        return bool(_norm(result.article_num))
    if result.source_type == "section":
        return bool(_norm(result.section_title))
    return True


_RELEVANCE_RANK = {"high": 2, "medium": 1}


def _relevance_max(a: str, b: str) -> str:
    """Return the stronger of two relevance tags ("high" > "medium")."""
    return a if _RELEVANCE_RANK.get(a, 0) >= _RELEVANCE_RANK.get(b, 0) else b


def _merge_reasoning(parts: Iterable[str]) -> str:
    """Semicolon-join non-empty reasoning strings, preserving first-seen order,
    deduped case-sensitively after whitespace normalization."""
    seen: set[str] = set()
    ordered: list[str] = []
    for p in parts:
        norm = _norm(p)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        ordered.append(norm)
    return "؛ ".join(ordered)


def _merge_duplicates(results: list[RerankedResult]) -> RerankedResult:
    """Collapse a list of duplicate reranker results into one (legacy path)."""
    if not results:
        raise ValueError("_merge_duplicates requires at least one result")
    base = results[0]
    if len(results) == 1:
        return base

    best_relevance = base.relevance
    for r in results[1:]:
        best_relevance = _relevance_max(best_relevance, r.relevance)

    merged_reasoning = _merge_reasoning(r.reasoning for r in results)

    content = base.content
    section_summary = base.section_summary
    for r in results[1:]:
        if not content and r.content:
            content = r.content
        if not section_summary and r.section_summary:
            section_summary = r.section_summary

    return base.model_copy(
        update={
            "relevance": best_relevance,
            "reasoning": merged_reasoning,
            "content": content,
            "section_summary": section_summary,
        }
    )


# ---------------------------------------------------------------------------
# Snippet extraction
# ---------------------------------------------------------------------------


_SENTENCE_TERMINATORS = ".!؟?\n"


def _build_snippet_text(text: str, max_chars: int = 500) -> str:
    """Snippet helper for arbitrary text."""
    source = (text or "").strip()
    if not source:
        return ""
    window = source[: max_chars + 1]
    best_cut = -1
    for term in _SENTENCE_TERMINATORS:
        idx = window.rfind(term)
        if idx > best_cut:
            best_cut = idx
    if best_cut != -1 and best_cut <= max_chars:
        snippet = source[: best_cut + 1].strip()
        if snippet:
            return snippet
    if len(source) <= max_chars:
        return source
    cut = source.rfind(" ", 0, max_chars + 1)
    if cut <= 0:
        return source[:max_chars].rstrip()
    return source[:cut].rstrip()


# ---------------------------------------------------------------------------
# Aggregator-view content rendering (URA v3.0 two-view)
# ---------------------------------------------------------------------------
#
# The aggregator view is the citable / grounding source of truth. The synthesis
# prompt block and the grounding validator both read the text produced here;
# ``Reference.snippet`` is a truncated derivative for UI hover only.


# Arabic labels for `cases.court_level` — canonical map lives in
# `shared/court_levels.py`, re-exported here for existing importers. The three
# DB values are first_instance (23,932 rows), appeal (6,474) and supreme (125).
# An unrecognised or empty level renders no parenthetical rather than leaking an
# English token into the Arabic prompt (see the `.get(..., "")` at the call site
# below — deliberately NOT a default label).
from agents.deep_search_v4.shared.court_levels import COURT_LEVEL_AR  # noqa: E402


def render_cross_ref(cr: CrossRef) -> str:
    """Render one resolved cross-reference.

    Shape (identical in both projections, per the URA reframe plan)::

        {target_reg_title}, {target_type}:{target_number}
        content: {content}
    """
    number = "" if cr.target_number is None else str(cr.target_number)
    head = f"{cr.target_reg_title or ''}, {cr.target_type or ''}:{number}".strip()
    body = (cr.content or "").strip()
    if body:
        return f"{head}\ncontent: {body}"
    return head


def render_aggregator_content(item: AggregatorItem) -> str:
    """Render an ``AggregatorItem`` into the full synthesis-input text body.

    This is the canonical content the LLM synthesizes from AND the text the
    grounding validator grounds against. Per-domain shape:

    - regulations: ``chunk_content`` followed by rendered ``cross_refs``.
    - compliance:  ``service_context``.
    - circulars:   ``تعميم: {title}`` / ``الجهة: {entity}`` / body (capped 4k).
    - cases:       ``المحكمة: {court} ({court_level_ar})`` header, then
      ``case_content``, then ``referenced_regulations``. ``case_content`` is
      `cases.summary` — a DERIVED structured summary, not the ruling text (D2).
      The header line is omitted when ``court`` is empty; the parenthetical is
      omitted when ``court_level`` is empty or unrecognised.
    """
    parts: list[str] = []
    if item.domain == "regulations":
        if item.chunk_content:
            parts.append(item.chunk_content.strip())
        for cr in item.cross_refs or []:
            rendered = render_cross_ref(cr)
            if rendered:
                parts.append(rendered)
    elif item.domain == "compliance":
        if item.service_context:
            parts.append(item.service_context.strip())
    elif item.domain == "circulars":
        header: list[str] = []
        if item.circular_title:
            header.append(f"تعميم: {item.circular_title.strip()}")
        if item.entity_name:
            header.append(f"الجهة: {item.entity_name.strip()}")
        if header:
            parts.append("\n".join(header))
        if item.circular_content:
            parts.append(item.circular_content.strip())
    elif item.domain == "cases":
        court = (item.court or "").strip()
        if court:
            level_ar = COURT_LEVEL_AR.get((item.court_level or "").strip(), "")
            parts.append(
                f"المحكمة: {court} ({level_ar})" if level_ar else f"المحكمة: {court}"
            )
        if item.case_content:
            # Old persisted URAs carry `case_content` from before unfold_ura
            # stripped the pipeline's internal sections — strip here too so a
            # resumed artifact can't feed the LLM internal resolver ids or the
            # classifier's `ConnectError:` crash dump (252 live rows).
            parts.append(strip_pipeline_sections(item.case_content.strip()))
        for rr in item.referenced_regulations or []:
            text = _render_case_referenced_regulation(rr)
            if text:
                parts.append(text)
    return "\n\n".join(p for p in parts if p)


# ``cases.referenced_regulations`` entries are RESOLVER OUTPUT, not clean
# citations: alongside the نظام name / مادة number / article text they carry
# internal corpus ids and match telemetry (``regulation_id``,
# ``target_chunk_ids``, ``confidence``, ``bm25_score``, ``vector_score``,
# ``combined_score``, ``match_method``). Dumping every dict value into the
# synthesis text taught the LLM those ids — it restated them into visible
# answers — so rendering is now allow-listed to the citation-relevant fields.
# Key aliases per producer: pipeline (النظام/الرقم/reference_content), legacy
# search formatter (regulation_name/article_number), URA gate-test shape
# (target_reg_title/content), plus generic title/article.
_CASE_REF_NAME_KEYS = ("النظام", "title", "regulation_name", "target_reg_title", "matched_title")
_CASE_REF_ARTICLE_KEYS = ("الرقم", "article", "article_number", "target_number")
_CASE_REF_BODY_KEYS = ("reference_content", "content")


def _first_str(d: dict, keys: tuple[str, ...]) -> str:
    for k in keys:
        v = d.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _render_case_referenced_regulation(rr) -> str:
    """Render one case referenced-regulation entry for the synthesis text.

    Name + مادة number + resolved article text only — never the resolver's
    internal ids or scores. A non-dict entry renders as its string form
    (legacy artifacts stored plain strings).
    """
    if not isinstance(rr, dict):
        return str(rr).strip()
    name = _first_str(rr, _CASE_REF_NAME_KEYS)
    number = _first_str(rr, _CASE_REF_ARTICLE_KEYS)
    body = _first_str(rr, _CASE_REF_BODY_KEYS)
    head = name
    if number:
        head = f"{name} — المادة {number}" if name else f"المادة {number}"
    if head and body:
        return f"{head}\n{body}"
    return head or body


def case_ref_to_cross_ref(rr) -> CrossRef | None:
    """Project one case ``referenced_regulations`` entry onto a ``CrossRef``.

    The citation panel renders إحالات for regulations AND cases now, and the two
    domains store the same idea in two different shapes: the reg domain ships
    typed ``CrossRef`` models out of ``cross_references_v2``, while a case's
    citations are resolver-written dicts keyed in Arabic (``النظام`` / ``الرقم``
    / ``reference_content``). Normalising here — rather than teaching the panel a
    second shape — keeps ONE wire contract and ONE renderer for both.

    Input MUST be an already-gated entry (``ReferenceView.referenced_regulations``,
    which ran through ``gate_cross_refs_for_reference``): this function reads the
    citation fields and ignores everything else, but it is not itself the place
    that drops the resolver's internal ids — see ``_CASE_REF_INTERNAL_KEYS``.

    ``target_type`` is set to the corpus's own ``"madda"`` token so a case إحالة
    and a reg إحالة render identically. Returns ``None`` for an entry with
    neither a name nor a number — an unlabelled body is not a citation.
    """
    if not isinstance(rr, dict):
        text = str(rr).strip()
        return CrossRef(target_reg_title=text) if text else None
    name = _first_str(rr, _CASE_REF_NAME_KEYS)
    number = _first_str(rr, _CASE_REF_ARTICLE_KEYS)
    if not name and not number:
        return None
    try:
        target_number = int(number) if number else None
    except ValueError:
        # Non-numeric مادة labels exist («الثانية عشرة»); keep them in the title
        # rather than dropping the إحالة over an int() that was never load-bearing.
        target_number = None
        name = f"{name} — المادة {number}" if name else f"المادة {number}"
    return CrossRef(
        target_type="madda" if target_number is not None else "",
        target_reg_title=name,
        target_number=target_number,
        content=_first_str(rr, _CASE_REF_BODY_KEYS),
    )


def build_snippet(result, max_chars: int = 500) -> str:
    """Extract a short excerpt suitable for hover tooltips.

    Accepts either a legacy ``RerankedResult`` or a URA result (any of the
    discriminated union members).

    URA v3.0: the snippet is derived from the **aggregator-view** content
    (``result.for_aggregator()`` -> ``AggregatorItem``), truncated. It is
    UI-hover-only metadata, NOT the grounding source of truth.

    Legacy ``RerankedResult``: prefers ``content`` -> ``section_summary`` ->
    ``title`` (pre-URA path, unchanged).

    NOTE: government-service (compliance) snippets are dropped for the UI at
    the panel's read boundary (``references_service._load_references``), NOT
    here — this text is still the aggregator prompt's fallback ``<content>``
    body.
    """
    if isinstance(result, (RegURAResult, ComplianceURAResult, CaseURAResult)):
        source = render_aggregator_content(result.for_aggregator())
    elif isinstance(result, CircularURAResult):
        # Circular hover snippet = the raw circular content (D11 "snippet from
        # circular_content"), NOT the rendered ``تعميم:``/``الجهة:`` header block
        # — routing through render_aggregator_content would let the header's
        # trailing newline truncate the snippet before the body.
        source = getattr(result, "content", "") or ""
    else:
        # Legacy RerankedResult
        source = (
            getattr(result, "content", "")
            or getattr(result, "section_summary", "")
            or getattr(result, "title", "")
            or ""
        )
    return _build_snippet_text(source, max_chars=max_chars)


# ---------------------------------------------------------------------------
# Legacy path -- RerankedResult -> Reference
# ---------------------------------------------------------------------------


def _reference_from_result(n: int, result: RerankedResult) -> Reference:
    """Build a Reference from a merged RerankedResult, assigning citation number `n`."""
    ref_id = f"reg:{result.db_id}" if getattr(result, "db_id", "") else ""
    return Reference(
        n=n,
        source_type=result.source_type,  # type: ignore[arg-type]
        regulation_title=result.regulation_title,
        article_num=result.article_num if result.source_type == "article" else None,
        section_title=result.section_title if result.source_type == "section" else None,
        title=result.title,
        snippet=build_snippet(result),
        relevance=result.relevance,
        ref_id=ref_id,
        domain="regulations",
    )


# ---------------------------------------------------------------------------
# URA v3.0 path -- typed URA result -> Reference (via the two-view projections)
# ---------------------------------------------------------------------------
#
# This is the load-bearing ``ReferenceView -> Reference`` mapping. It lives
# here (not in ura/schema.py) so schema.py never imports the aggregator --
# preprocessor.py already depends on both modules.


def _reference_from_ura(n: int, r: URAResultBase) -> Reference:
    """Build a numbered ``Reference`` from a URA result via ``for_reference()``.

    The URA result exposes a typed ``ReferenceView`` (display metadata) and a
    typed ``AggregatorItem`` (citable content). The snippet is derived from the
    aggregator view, truncated -- UI hover only.
    """
    view: ReferenceView = r.for_reference()  # type: ignore[attr-defined]
    snippet = build_snippet(r)

    if view.domain == "regulations":
        return Reference(
            n=n,
            source_type="chunk",
            regulation_title=view.reg_title or "",
            title=view.reg_title or "",
            snippet=snippet,
            relevance=view.relevance,
            ref_id=view.ref_id,
            domain="regulations",
            landing_url=view.landing_url or "",
            doc_type=view.doc_type or "",
            cross_refs=list(view.cross_refs or []),
        )

    if view.domain == "compliance":
        return Reference(
            n=n,
            source_type="gov_service",
            regulation_title=view.provider_name or "",
            title=view.service_name or "",
            snippet=snippet,
            relevance=view.relevance,
            ref_id=view.ref_id,
            domain="compliance",
            service_url=view.service_url or "",
            url=view.url or "",
        )

    if view.domain == "circulars":
        # Mirrors the compliance mapping: the issuing entity is the "parent"
        # label (regulation_title slot, like provider_name for services), the
        # circular's own title is the specific title, and the circular's source
        # link rides on the generic ``url`` field. ``entity_name`` is carried
        # explicitly for the citation panel.
        return Reference(
            n=n,
            source_type="circular",
            regulation_title=view.entity_name or "",
            title=view.circular_title or "تعميم",
            snippet=snippet,
            relevance=view.relevance,
            ref_id=view.ref_id,
            domain="circulars",
            entity_name=view.entity_name or "",
            url=view.source_url or "",
        )

    if view.domain == "cases":
        # A ruling's إحالات are the أنظمة/مواد it cites. They ride the SAME
        # ``cross_refs`` field the reg domain uses (see case_ref_to_cross_ref) so
        # the panel has one shape to render; ``view.referenced_regulations`` is
        # already gated + telemetry-stripped by ``for_reference()``.
        case_refs = [
            cr
            for cr in (
                case_ref_to_cross_ref(rr)
                for rr in (view.referenced_regulations or [])
            )
            if cr is not None
        ]
        return Reference(
            n=n,
            source_type="case",
            regulation_title=view.entity_name or view.court or "",
            # A card's title says what the ruling is ABOUT — never its case
            # number, which is not a title and is absent on most rows.
            #
            # ``judgment_subject`` is the SINGLE SOURCE OF TRUTH for judgment
            # naming (``shared/seo/judgment_naming.py``): the 10,000 published
            # /judgments slugs were cut from it and it renders that page's H1,
            # so importing it — never re-deriving — is what keeps this label and
            # the page the card's «فتح الحكم في ريحان» button opens identical.
            # URLs are permanent; a forked derivation could never be reconciled.
            #
            # ``judgment_subject`` deliberately, NOT ``judgment_display_title``:
            # no « — المحكمة … 1445هـ» tail on a chat card (user decision).
            # It never returns empty (it bottoms out at «حكم قضائي»), so the
            # ``or "قضية"`` is belt-and-braces.
            title=judgment_subject(
                {
                    "short_summary": view.short_summary,
                    "summary": view.summary,
                    "court": view.court,
                    "case_number": view.case_number,
                    "judgment_number": view.judgment_number,
                }
            )
            or "قضية",
            snippet=snippet,
            relevance=view.relevance,
            ref_id=view.ref_id,
            domain="cases",
            details_url=view.details_url or "",
            entity_name=view.entity_name or "",
            cross_refs=case_refs,
        )

    # Defensive: unknown URA domain. Build a minimal Reference so callers keep
    # working; upstream type checkers catch new domains.
    return Reference(
        n=n,
        source_type="regulation",
        regulation_title="",
        title="",
        snippet=snippet,
        relevance=getattr(view, "relevance", "medium"),
        ref_id=getattr(view, "ref_id", "") or "",
        domain="regulations",
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def preprocess_references(
    agg_input: AggregatorInput,
) -> tuple[list[Reference], dict[int, list[int]]]:
    """Assign 1-based citation numbers to URA results (or legacy reranker output).

    Preferred path (URA 2.0): when ``agg_input.ura`` is set, iterate
    ``ura.high_results`` then ``ura.medium_results``. URA merger already
    deduplicates by ``ref_id`` and bins by relevance tier, so this function
    does NOT re-dedupe. Each URA result yields exactly one ``Reference``.

    Fallback (legacy): when ``ura is None`` — used by older CLIs / replay
    tests — walks ``agg_input.sub_queries`` and merges duplicates by
    identity tuple.

    Returns:
        references: ordered list, each with a unique .n starting at 1.
        ref_to_sub_queries: mapping reference.n -> sorted list of 0-based
            sub_query indices that produced it.
    """
    ura = agg_input.ura
    if ura is not None:
        return _preprocess_from_ura(ura)
    return _preprocess_from_legacy(agg_input)


def _preprocess_from_ura(
    ura,
) -> tuple[list[Reference], dict[int, list[int]]]:
    """URA 2.0 path: high tier first, then medium tier, no dedup."""
    references: list[Reference] = []
    ref_to_sub_queries: dict[int, list[int]] = {}

    # High tier first so high-relevance refs get lower citation numbers.
    ordered_results: list[URAResultBase] = []
    ordered_results.extend(ura.high_results or [])
    ordered_results.extend(ura.medium_results or [])

    for n, r in enumerate(ordered_results, start=1):
        ref = _reference_from_ura(n, r)
        references.append(ref)
        ref_to_sub_queries[n] = sorted(list(r.appears_in_sub_queries or []))

    return references, ref_to_sub_queries


async def attach_source_views(
    supabase: Any,
    references: list[Reference],
    ura_results: list[URAResultBase],
) -> None:
    """Resolve `SourceView` for each reference in parallel and attach in place.

    `references[i]` must correspond 1:1 with `ura_results[i]` (same iteration
    order used by ``_preprocess_from_ura``). Each source-view lookup runs as
    its own ``asyncio.gather`` task so total wall-time is bounded by the
    slowest single Supabase round-trip rather than N sequential round-trips.

    Failure handling: if ``build_source_view`` raises (DB unavailable, missing
    row, malformed ref_id) for any reference, the warning is logged and that
    reference's ``source_view`` stays ``None``. The aggregator NEVER crashes
    because of source resolution -- this is purely additive UX metadata.
    """
    if supabase is None:
        return
    if not references or not ura_results:
        return
    if len(references) != len(ura_results):
        logger.warning(
            "attach_source_views: reference/ura length mismatch (%d vs %d) -- skipping",
            len(references),
            len(ura_results),
        )
        return

    async def _resolve_one(ura: URAResultBase) -> Any:
        try:
            return await build_source_view(supabase, ura)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "attach_source_views: build_source_view(%s) failed: %s",
                getattr(ura, "ref_id", "<no ref_id>"),
                exc,
            )
            return None

    views = await asyncio.gather(
        *(_resolve_one(ura) for ura in ura_results),
        return_exceptions=False,
    )

    for ref, view in zip(references, views):
        if view is not None:
            ref.source_view = view


def collect_ordered_ura_results(ura) -> list[URAResultBase]:
    """Return URA results in the same order ``_preprocess_from_ura`` consumes them.

    High-tier first, then medium-tier. Exposed so callers (the runner) can
    pair references with their originating URA result for ``attach_source_views``
    without duplicating tier-ordering logic.
    """
    ordered: list[URAResultBase] = []
    ordered.extend(ura.high_results or [])
    ordered.extend(ura.medium_results or [])
    return ordered


def _preprocess_from_legacy(
    agg_input: AggregatorInput,
) -> tuple[list[Reference], dict[int, list[int]]]:
    """Legacy path: reranker-driven dedup + merge (pre-URA)."""
    grouped: dict[tuple[str, str, str], list[RerankedResult]] = {}
    first_seen_order: dict[tuple[str, str, str], int] = {}
    sub_query_hits: dict[tuple[str, str, str], list[int]] = {}

    appearance = 0
    for sq_idx, sub_query in enumerate(agg_input.sub_queries or []):
        for result in getattr(sub_query, "results", []) or []:
            # Only legacy RerankedResult instances are dedup-able by identity
            # tuple. URA results (if any slipped in) are skipped here -- this
            # path exists solely for pre-URA callers.
            if not isinstance(result, RerankedResult):
                continue
            if not _is_citable(result):
                continue
            key = _identity_key(result)
            if key not in grouped:
                grouped[key] = []
                first_seen_order[key] = appearance
                appearance += 1
                sub_query_hits[key] = []
            grouped[key].append(result)
            if sq_idx not in sub_query_hits[key]:
                sub_query_hits[key].append(sq_idx)

    references: list[Reference] = []
    ref_to_sub_queries: dict[int, list[int]] = {}

    if not grouped:
        return references, ref_to_sub_queries

    merged: dict[tuple[str, str, str], RerankedResult] = {
        key: _merge_duplicates(items) for key, items in grouped.items()
    }

    sorted_keys = sorted(
        merged.keys(),
        key=lambda k: (
            -_RELEVANCE_RANK.get(merged[k].relevance, 0),
            first_seen_order[k],
        ),
    )

    for new_n, key in enumerate(sorted_keys, start=1):
        ref = _reference_from_result(new_n, merged[key])
        references.append(ref)
        ref_to_sub_queries[new_n] = sorted(sub_query_hits[key])

    return references, ref_to_sub_queries
