"""Package-assembly helpers used by the writer_planner runner.

Two top-level entrypoints corresponding to the two paths from
``.claude/plans/writer_planner.md § Two skippable phases``:

- :func:`build_analyzed_items_from_verdicts` — **verdict-walk path**. Called
  when the planner invoked ``analyze_items`` during its run. Walks
  ``deps.last_analyzer_output.items`` and translates each non-`none`
  verdict into an ``AnalyzedItem`` for the WriterPackage.
- :func:`build_analyzed_items_direct` — **bypass path**. Called when the
  planner did NOT invoke ``analyze_items`` because the items were already
  unambiguous (turn-attached or explicitly named). Fetches ``content_md``
  for each selected id and builds ``need='full'`` items directly.

Both helpers expect ``deps.user_id`` / ``deps.conversation_id`` to be set
so the workspace_items lookup is scoped correctly (RLS is the second line
of defense; the explicit user_id + conversation_id filter is the first).
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from agents.writer.models import AnalyzedItem
from backend.app.services.references_service import fetch_item_references

from .models import PlannerRole

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agents.deep_search_v4.aggregator.models import Reference
    from agents.memory.item_analyzer import AnalyzeOutput, WIVerdict

    from .deps import WriterPlannerDeps


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers — fetch + rendering
# ---------------------------------------------------------------------------


def _word_count(text: str) -> int:
    """Best-effort word count; mirrors migration 048's compute_word_count semantics."""
    if not text:
        return 0
    return len([w for w in text.strip().split() if w])


def _fetch_workspace_items_sync(
    supabase: Any, item_ids: list[str], user_id: str, conversation_id: str
) -> dict[str, dict]:
    """Batch-fetch workspace_items by id, scoped to (user_id, conversation_id).

    Returns ``{item_id: row}`` for items found. Missing ids are silently
    omitted — the caller checks for them and logs a warning if any drop out.

    Sync helper (matches the codebase pattern of sync Supabase in async
    contexts). Wrap in ``asyncio.to_thread`` from async code.
    """
    if not item_ids:
        return {}
    try:
        result = (
            supabase
            .table("workspace_items")
            .select(
                "item_id, wi_seq, kind, title, content_md, word_count, summary, "
                "conversation_id, user_id"
            )
            .in_("item_id", item_ids)
            .eq("conversation_id", conversation_id)
            .is_("deleted_at", None)
            .execute()
        )
    except Exception as exc:
        logger.warning(
            "walkers._fetch_workspace_items: DB error for %d ids (%s) — {}",
            len(item_ids), exc,
        )
        return {}
    rows = getattr(result, "data", None) or []
    out: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        # Defense-in-depth: confirm the user_id matches before keeping the row.
        if user_id and str(row.get("user_id") or "") != user_id:
            logger.warning(
                "walkers: skipping item_id=%s — user_id mismatch (RLS bypass attempted?)",
                row.get("item_id"),
            )
            continue
        out[str(row["item_id"])] = row
    return out


async def _fetch_workspace_items(
    deps: "WriterPlannerDeps", item_ids: list[str]
) -> dict[str, dict]:
    """Async wrapper around the sync Supabase fetch."""
    return await asyncio.to_thread(
        _fetch_workspace_items_sync,
        deps.supabase,
        list(item_ids),
        deps.user_id,
        deps.conversation_id,
    )


def _render_refs_md(
    references: list["Reference"],
    wanted: set[int] | None = None,
) -> str:
    """Render Reference rows as a plain-text Arabic citation block.

    Format::

        [n] {title} — {snippet}
        [m] {title} — {snippet}

    When ``wanted`` is provided, only references whose ``n`` is in the set
    are rendered (used by the partial-refs path with its ``refs_needed``
    filter). When ``wanted`` is None, render every reference (used by the
    full-refs path — see :func:`_fetch_used_refs_for_items`).
    """
    if not references:
        return ""
    parts: list[str] = []
    for ref in sorted(references, key=lambda r: int(getattr(r, "n", 0) or 0)):
        n = int(getattr(ref, "n", 0) or 0)
        if wanted is not None and n not in wanted:
            continue
        title = (getattr(ref, "title", "") or "").strip()
        snippet = (getattr(ref, "snippet", "") or "").strip()
        if snippet:
            parts.append(f"[{n}] {title} — {snippet}")
        else:
            parts.append(f"[{n}] {title}")
    return "\n".join(parts)


# Refs-family kinds — these are the WI kinds that carry [n] citation
# markers and have rows in ``workspace_item_references``. Meta-family kinds
# (attachment / notes) don't, so we never call fetch_item_references on them.
_REFS_FAMILY_KINDS: frozenset[str] = frozenset({"agent_search", "agent_writer"})


async def _fetch_used_refs_for_items(
    supabase: Any, item_ids: list[str]
) -> dict[str, str]:
    """Fetch the **used** references for each WI in parallel + render them.

    Used by both the verdict-walk's ``need='full'`` refs-family branch and
    the bypass path's refs-family branch. The ``used_only=True`` filter on
    ``fetch_item_references`` returns only rows the publisher marked as
    cited in the WI's body — so we get every reference the LLM might want
    to ground a citation on, with no extra noise.

    Concurrency: one ``asyncio.gather`` fans the per-WI fetches in parallel.
    Each ``fetch_item_references`` already does a batched SELECT + per-domain
    source enrichment, so the per-call cost is steady (~50–100ms typical).
    A turn with 5 full refs-family WIs runs ~one fetch's worth of latency
    instead of five.

    Returns:
        ``{item_id: resolved_refs_md}`` for items that had at least one
        used ref. Items with zero used refs (or whose fetch raises) are
        omitted from the dict — callers check ``in`` to decide whether to
        attach a ``<refs>`` block.
    """
    if not item_ids:
        return {}

    async def _fetch_one(iid: str) -> tuple[str, str]:
        try:
            refs = await fetch_item_references(supabase, iid, used_only=True)
        except Exception as exc:
            logger.warning(
                "walkers: fetch_used_refs failed for item_id=%s (%s) — skipping refs",
                iid, exc,
            )
            return iid, ""
        return iid, _render_refs_md(refs, wanted=None)

    results = await asyncio.gather(*[_fetch_one(iid) for iid in item_ids])
    return {iid: md for iid, md in results if md}


def _role_for(item_id: str, role_assignments: dict[str, PlannerRole]) -> PlannerRole:
    """Get the role for an item, defaulting to 'source' with a warning."""
    role = role_assignments.get(item_id)
    if role is None:
        logger.warning(
            "walkers: no role for item_id=%s; defaulting to 'source'", item_id
        )
        return "source"
    return role


# ---------------------------------------------------------------------------
# Public — verdict-walk path
# ---------------------------------------------------------------------------


async def build_analyzed_items_from_verdicts(
    analyzer_output: "AnalyzeOutput | None",
    selected_ids: list[str],
    role_assignments: dict[str, PlannerRole],
    deps: "WriterPlannerDeps",
) -> list[AnalyzedItem]:
    """Verdict-walk: convert analyzer verdicts into AnalyzedItems.

    Steps:
      1. Filter verdicts to those whose item_id is in ``selected_ids`` AND
         whose ``need`` is not ``'none'`` (none-verdicts are dropped).
      2. Batch-fetch the matching workspace_items rows so we have
         ``title`` + ``content_md`` (for full verdicts) + ``word_count``.
      3. For each kept verdict:
           - need='full'    → body_md = wi.content_md, no extras.
           - need='partial' refs-family → body_md = verdict.distilled, resolve
             refs_needed via references_service.fetch_item_references and
             filter to the wanted [n] set.
           - need='partial' meta-family → body_md = verdict.distilled or "",
             extras carry extracted_metadata.

    Returns:
        List of AnalyzedItem in the order ``selected_ids`` specifies (planner
        intent preserved); verdicts whose ids fall outside selected_ids are
        skipped silently.
    """
    if analyzer_output is None or not analyzer_output.items:
        logger.warning(
            "walkers: verdict-walk requested but analyzer_output empty/None — []"
        )
        return []

    # 1. Filter to non-none verdicts whose id is in selected_ids.
    selected_set = set(selected_ids)
    verdicts_by_id: dict[str, "WIVerdict"] = {}
    for v in analyzer_output.items:
        if getattr(v, "item_id", None) in selected_set and getattr(v, "need", None) != "none":
            verdicts_by_id[v.item_id] = v

    kept_ids = [iid for iid in selected_ids if iid in verdicts_by_id]
    if not kept_ids:
        return []

    # 2. Batch-fetch workspace_items rows.
    wi_index = await _fetch_workspace_items(deps, kept_ids)

    # 2b. Identify full refs-family items that need their used-references
    #     unfolded (the writer expects every cited [n] to be resolved when
    #     it gets the body in full). Fetch them in one parallel pass.
    full_refs_ids: list[str] = []
    for item_id in kept_ids:
        verdict = verdicts_by_id[item_id]
        wi = wi_index.get(item_id)
        if wi is None:
            continue
        kind_for_check = str(wi.get("kind") or getattr(verdict, "kind", "") or "")
        need_for_check = str(getattr(verdict, "need", "full"))
        if need_for_check == "full" and kind_for_check in _REFS_FAMILY_KINDS:
            full_refs_ids.append(item_id)
    full_refs_md_by_id = await _fetch_used_refs_for_items(
        deps.supabase, full_refs_ids
    )

    # 3. Walk verdicts and build AnalyzedItems.
    analyzed: list[AnalyzedItem] = []
    for item_id in kept_ids:
        verdict = verdicts_by_id[item_id]
        wi = wi_index.get(item_id)
        if wi is None:
            logger.warning(
                "walkers: verdict for item_id=%s but WI row missing — skipping",
                item_id,
            )
            continue

        kind = str(wi.get("kind") or getattr(verdict, "kind", "") or "")
        title = str(wi.get("title") or "")
        word_count_before = int(wi.get("word_count") or 0)
        wi_seq = int(wi.get("wi_seq")) if wi.get("wi_seq") is not None else None
        role = _role_for(item_id, role_assignments)
        need = str(getattr(verdict, "need", "full"))

        if need == "full":
            body_md = str(wi.get("content_md") or "")
            # Full refs-family: attach the used-refs block so [n] citations
            # in body_md are resolvable. Meta-family items skip this (no refs).
            resolved_refs_md = (
                full_refs_md_by_id.get(item_id) if kind in _REFS_FAMILY_KINDS else None
            )
            analyzed.append(
                AnalyzedItem(
                    item_id=item_id,
                    wi_seq=wi_seq,
                    title=title,
                    kind=kind,
                    role=role,
                    need="full",
                    body_md=body_md,
                    word_count_before=word_count_before,
                    word_count_after=word_count_before,
                    resolved_refs_md=resolved_refs_md,
                )
            )
            continue

        # need == 'partial' from here on.
        is_refs_family = kind in _REFS_FAMILY_KINDS

        if is_refs_family:
            body_md = str(getattr(verdict, "distilled", "") or "")
            refs_needed_raw = getattr(verdict, "refs_needed", None) or []
            refs_needed = [int(n) for n in refs_needed_raw]
            resolved_refs_md: str | None = None
            if refs_needed:
                # Partial path: use the analyzer's explicit ``refs_needed``
                # subset as the filter. We fetch the full (unfiltered) list
                # because the analyzer's subset may include refs not flagged
                # as ``used`` in the publisher's row (the analyzer reasons over
                # the distilled slice's text, which is a different signal).
                try:
                    all_refs = await fetch_item_references(deps.supabase, item_id)
                    rendered = _render_refs_md(all_refs, wanted=set(refs_needed))
                    resolved_refs_md = rendered or None
                except Exception as exc:
                    logger.warning(
                        "walkers: fetch_item_references failed for item_id=%s (%s)",
                        item_id, exc,
                    )
            analyzed.append(
                AnalyzedItem(
                    item_id=item_id,
                    wi_seq=wi_seq,
                    title=title,
                    kind=kind,
                    role=role,
                    need="partial",
                    body_md=body_md,
                    word_count_before=word_count_before,
                    word_count_after=_word_count(body_md),
                    refs_needed=refs_needed,
                    resolved_refs_md=resolved_refs_md,
                )
            )
        else:
            # meta-family partial: attachment / notes.
            distilled = getattr(verdict, "distilled", None)
            body_md = str(distilled or "")
            extracted_md = dict(
                getattr(verdict, "extracted_metadata", None) or {}
            )
            analyzed.append(
                AnalyzedItem(
                    item_id=item_id,
                    wi_seq=wi_seq,
                    title=title,
                    kind=kind,
                    role=role,
                    need="partial",
                    body_md=body_md,
                    word_count_before=word_count_before,
                    word_count_after=_word_count(body_md),
                    extracted_metadata=extracted_md,
                )
            )

    return analyzed


# ---------------------------------------------------------------------------
# Public — bypass path
# ---------------------------------------------------------------------------


async def build_analyzed_items_direct(
    selected_ids: list[str],
    role_assignments: dict[str, PlannerRole],
    deps: "WriterPlannerDeps",
) -> list[AnalyzedItem]:
    """Bypass path: fetch raw content_md for each id, build need='full' items.

    Used when the planner emitted ``PlannerDecision(analyzer_invoked=False)``
    because the relevant items were unambiguous (turn-attached, or named
    explicitly). The planner LLM itself never saw ``content_md`` — this
    function is the post-LLM unfolding point. Per § Core invariant.

    For refs-family items (``agent_search`` / ``agent_writer``) the function
    ALSO fetches the WI's used references via ``fetch_item_references(...,
    used_only=True)`` and renders them into ``resolved_refs_md`` so the
    writer can ground every ``[n]`` citation that appears inline in
    ``body_md``. Meta-family items skip the refs fetch (no references).
    """
    if not selected_ids:
        return []

    wi_index = await _fetch_workspace_items(deps, selected_ids)

    # Identify refs-family items present in the fetched index — these get
    # their used refs unfolded in parallel.
    refs_family_ids = [
        iid for iid in selected_ids
        if iid in wi_index and str(wi_index[iid].get("kind") or "") in _REFS_FAMILY_KINDS
    ]
    refs_md_by_id = await _fetch_used_refs_for_items(
        deps.supabase, refs_family_ids
    )

    analyzed: list[AnalyzedItem] = []
    for item_id in selected_ids:
        wi = wi_index.get(item_id)
        if wi is None:
            logger.warning(
                "walkers (bypass): WI row not found for item_id=%s — skipping",
                item_id,
            )
            continue

        kind = str(wi.get("kind") or "")
        title = str(wi.get("title") or "")
        word_count = int(wi.get("word_count") or 0)
        wi_seq = int(wi.get("wi_seq")) if wi.get("wi_seq") is not None else None
        body_md = str(wi.get("content_md") or "")
        role = _role_for(item_id, role_assignments)
        resolved_refs_md = (
            refs_md_by_id.get(item_id) if kind in _REFS_FAMILY_KINDS else None
        )

        analyzed.append(
            AnalyzedItem(
                item_id=item_id,
                wi_seq=wi_seq,
                title=title,
                kind=kind,
                role=role,
                need="full",
                body_md=body_md,
                word_count_before=word_count,
                word_count_after=word_count,
                resolved_refs_md=resolved_refs_md,
            )
        )

    return analyzed


__all__ = [
    "build_analyzed_items_from_verdicts",
    "build_analyzed_items_direct",
]
