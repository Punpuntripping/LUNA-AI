"""Reranker agent factory and multi-run loop for reg_search (v2).

Architecture: classification-only LLM (no tools).
The RerankerNode calls run_reranker_for_query() which:
1. Sends search results to the LLM for classification (keep/drop/unfold)
2. Programmatically unfolds requested items via DB calls
3. Repeats with trimmed context (up to 3 rounds)
4. Assembles final RerankerQueryResult from kept results + unfolded data
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model
from supabase import Client as SupabaseClient

from .models import (
    RerankedResult,
    RerankerClassification,
    RerankerQueryResult,
)
from .prompts import build_reranker_user_message, get_reranker_prompt

logger = logging.getLogger(__name__)

RERANKER_LIMITS = UsageLimits(
    response_tokens_limit=70_000,
    request_limit=3,
)

MAX_RERANKER_ROUNDS = 3


# -- Agent factory -------------------------------------------------------------


def create_reranker_agent(
    prompt_key: str = "prompt_1",
    model_override: str | None = None,
) -> Agent[None, RerankerClassification]:
    """Create a classification-only reranker agent. No tools, no deps.

    If ``model_override`` is provided it is used in place of the registry
    default (``reg_search_reranker``). Pass ``None`` (default) to use the
    configured default model.
    """
    from agents.model_registry import create_model

    system_prompt = get_reranker_prompt(prompt_key)
    model = create_model(model_override) if model_override else get_agent_model("reg_search_reranker")

    return Agent(
        model,
        name="reg_search_reranker",
        output_type=RerankerClassification,
        instructions=system_prompt,
        retries=2,
    )


# -- Markdown parser -----------------------------------------------------------


_RESULT_HEADER_RE = re.compile(
    r"^### \[(\d+)\]\s+(مادة|باب/فصل|نظام):\s+(.+?)(?:\s+\[id:([^\]]+)\])?\s*$",
    re.MULTILINE,
)

_SOURCE_TYPE_MAP = {
    "مادة": "article",
    "باب/فصل": "section",
    "نظام": "regulation",
}


_RRF_RE = re.compile(r"RRF:\s*([\d.]+)")


def _parse_result_blocks(markdown: str) -> list[dict[str, Any]]:
    """Parse search results markdown into individual result blocks.

    Each block: {position, source_type, title, id, rrf, markdown}
    """
    matches = list(_RESULT_HEADER_RE.finditer(markdown))
    if not matches:
        return []

    blocks: list[dict[str, Any]] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        block_md = markdown[start:end].rstrip()

        # Extract RRF score from block
        rrf_match = _RRF_RE.search(block_md)
        rrf = float(rrf_match.group(1)) if rrf_match else 0.0

        blocks.append({
            "position": int(m.group(1)),
            "source_type": _SOURCE_TYPE_MAP.get(m.group(2), "unknown"),
            "title": m.group(3).strip(),
            "id": m.group(4) or "",
            "rrf": rrf,
            "markdown": block_md,
        })

    return blocks


def _assemble_markdown(blocks: list[dict[str, Any]]) -> str:
    """Re-assemble result blocks into markdown with renumbered positions."""
    lines: list[str] = [f"## نتائج البحث — {len(blocks)} نتيجة\n"]
    for i, block in enumerate(blocks, 1):
        # Renumber the position in the header
        md = block["markdown"]
        old_header_match = _RESULT_HEADER_RE.match(md)
        if old_header_match:
            old_pos = old_header_match.group(1)
            md = md.replace(f"### [{old_pos}]", f"### [{i}]", 1)
        lines.append(md)
        lines.append("")
    return "\n".join(lines)


# -- Programmatic unfold -------------------------------------------------------


async def _programmatic_unfold(
    supabase: SupabaseClient,
    target_id: str,
    mode: str,
    parent_rrf: float = 0.0,
) -> list[dict[str, Any]]:
    """Unfold a result programmatically and return new result blocks.

    Returns a list of parsed blocks from the unfolded content,
    ready for the next classification round.

    ``parent_rrf`` is the RRF score of the block being unfolded; it is
    inherited (attenuated for siblings/children) so the merger's downstream
    sort still has signal for unfolded items.
    """
    from .unfold_reranker import (
        format_unfolded_result,
        format_unfolded_result_precise,
        unfold_article_with_siblings,
        unfold_regulation,
        unfold_section,
    )

    if mode == "article_precise":
        data = await asyncio.to_thread(
            unfold_article_with_siblings, supabase, target_id,
        )
        # Format as markdown blocks the LLM can classify
        return _article_siblings_to_blocks(data, parent_rrf=parent_rrf)

    elif mode == "section_detailed":
        row = await _fetch_section_row(supabase, target_id)
        if not row:
            return []
        data = await asyncio.to_thread(unfold_section, supabase, row)
        # Section unfold returns child articles — convert to blocks
        return _section_unfold_to_blocks(data, parent_rrf=parent_rrf)

    elif mode == "regulation_detailed":
        row = await _fetch_regulation_row(supabase, target_id)
        if not row:
            return []
        data = await asyncio.to_thread(unfold_regulation, supabase, row)
        # Regulation unfold returns child sections — convert to blocks
        return _regulation_unfold_to_blocks(data, parent_rrf=parent_rrf)

    return []


def _article_siblings_to_blocks(
    data: dict, parent_rrf: float = 0.0
) -> list[dict[str, Any]]:
    """Convert unfold_article_with_siblings output to result blocks.

    The target article inherits the parent's full rrf (it's the same hit,
    just re-rendered with sibling context). Siblings inherit ``parent_rrf
    * 0.5`` so they rank below the original hit but still carry signal.
    """
    from .unfold_reranker import format_unfolded_result_precise

    blocks: list[dict[str, Any]] = []
    sibling_rrf = parent_rrf * 0.5
    target = data.get("target_article")
    if target:
        # Build a result dict compatible with format functions
        result = {
            "source_type": "article",
            "id": target.get("id", ""),
            "title": target.get("title", ""),
            "article_num": target.get("article_num"),
            "content": target.get("content", ""),
            "article_context": target.get("article_context", ""),
            "references_content": target.get("references_content", ""),
            "regulation_title": data.get("regulation_title", ""),
            "section_title": (data.get("parent_section") or {}).get("title", ""),
        }
        md = format_unfolded_result_precise(result, position=1)
        blocks.append({
            "position": 1,
            "source_type": "article",
            "title": target.get("title", ""),
            "id": target.get("id", ""),
            "rrf": parent_rrf,
            "markdown": md,
            "_data": result,
        })

    # Sibling articles
    for sib in data.get("sibling_articles", []):
        result = {
            "source_type": "article",
            "id": sib.get("id", ""),
            "title": sib.get("title", ""),
            "article_num": sib.get("article_num"),
            "content": sib.get("content", ""),
            "references_content": sib.get("references_content", ""),
            "regulation_title": data.get("regulation_title", ""),
            "section_title": (data.get("parent_section") or {}).get("title", ""),
        }
        pos = len(blocks) + 1
        md = format_unfolded_result_precise(result, position=pos)
        blocks.append({
            "position": pos,
            "source_type": "article",
            "title": sib.get("title", ""),
            "id": sib.get("id", ""),
            "rrf": sibling_rrf,
            "markdown": md,
            "_data": result,
        })

    return blocks


def _section_unfold_to_blocks(
    data: dict, parent_rrf: float = 0.0
) -> list[dict[str, Any]]:
    """Convert unfold_section output (child articles) to result blocks.

    All children inherit ``parent_rrf * 0.5``; the section itself (if
    surfaced) inherits the full ``parent_rrf`` since it is the unfolded
    target.
    """
    from .unfold_reranker import format_unfolded_result_precise

    blocks: list[dict[str, Any]] = []
    reg_title = data.get("regulation_title", "")
    section_title = data.get("title", "")
    child_rrf = parent_rrf * 0.5

    for child in data.get("child_articles", []):
        result = {
            "source_type": "article",
            "id": child.get("id", ""),
            "title": child.get("title", ""),
            "article_num": child.get("article_num"),
            "content": child.get("content", ""),
            "article_context": child.get("article_context", ""),
            "references_content": child.get("references_content", ""),
            "regulation_title": reg_title,
            "section_title": section_title,
        }
        pos = len(blocks) + 1
        md = format_unfolded_result_precise(result, position=pos)
        blocks.append({
            "position": pos,
            "source_type": "article",
            "title": child.get("title", ""),
            "id": child.get("id", ""),
            "rrf": child_rrf,
            "markdown": md,
            "_data": result,
        })

    # Also include the section itself as a block (LLM can keep it)
    if data.get("section_summary") or data.get("section_context"):
        sec_result = {
            "source_type": "section",
            "id": data.get("id", ""),
            "title": section_title,
            "section_summary": data.get("section_summary", ""),
            "section_context": data.get("section_context", ""),
            "regulation_title": reg_title,
        }
        pos = len(blocks) + 1
        md = format_unfolded_result_precise(sec_result, position=pos)
        blocks.insert(0, {  # section first, then articles
            "position": 1,
            "source_type": "section",
            "title": section_title,
            "id": data.get("id", ""),
            "rrf": parent_rrf,
            "markdown": md,
            "_data": sec_result,
        })
        # Renumber articles
        for j, b in enumerate(blocks[1:], 2):
            b["position"] = j

    return blocks


def _regulation_unfold_to_blocks(
    data: dict, parent_rrf: float = 0.0
) -> list[dict[str, Any]]:
    """Convert unfold_regulation output (child sections) to result blocks.

    Children sections inherit ``parent_rrf * 0.5``.
    """
    from .unfold_reranker import format_unfolded_result_precise

    blocks: list[dict[str, Any]] = []
    reg_title = data.get("title", "")
    child_rrf = parent_rrf * 0.5

    for sec in data.get("child_sections", []):
        result = {
            "source_type": "section",
            "id": sec.get("id", ""),
            "title": sec.get("title", ""),
            "section_summary": sec.get("section_summary", ""),
            "section_context": sec.get("section_context", ""),
            "regulation_title": reg_title,
        }
        pos = len(blocks) + 1
        md = format_unfolded_result_precise(result, position=pos)
        blocks.append({
            "position": pos,
            "source_type": "section",
            "title": sec.get("title", ""),
            "id": sec.get("id", ""),
            "rrf": child_rrf,
            "markdown": md,
            "_data": result,
        })

    return blocks


# -- Row fetchers (reused from v1) ---------------------------------------------


async def _fetch_section_row(supabase: Any, section_id: str) -> dict | None:
    """Fetch a section row with fields needed by unfold_section()."""
    try:
        resp = await asyncio.to_thread(
            lambda: supabase.table("sections")
            .select(
                "id, title, section_summary, section_keyword, chunk_ref, "
                "regulation_id"
            )
            .eq("id", section_id)
            .maybe_single()
            .execute()
        )
        if not resp or not resp.data:
            return None
        row = resp.data
        reg_id = row.get("regulation_id")
        if reg_id:
            reg_resp = await asyncio.to_thread(
                lambda: supabase.table("regulations")
                .select("title, regulation_ref")
                .eq("id", reg_id)
                .maybe_single()
                .execute()
            )
            if reg_resp and reg_resp.data:
                row["regulation_title"] = reg_resp.data.get("title", "")
                row["regulation_ref"] = reg_resp.data.get("regulation_ref", "")
        return row
    except Exception as e:
        logger.warning("_fetch_section_row(%s): %s", section_id, e)
        return None


async def _fetch_regulation_row(supabase: Any, regulation_id: str) -> dict | None:
    """Fetch a regulation row with fields needed by unfold_regulation()."""
    try:
        resp = await asyncio.to_thread(
            lambda: supabase.table("regulations")
            .select(
                "id, title, type, main_category, sub_category, "
                "regulation_summary, regulation_ref, authority_level, authority_score"
            )
            .eq("id", regulation_id)
            .maybe_single()
            .execute()
        )
        if resp and resp.data:
            return resp.data
        return None
    except Exception as e:
        logger.warning("_fetch_regulation_row(%s): %s", regulation_id, e)
        return None


# -- Result assembler ----------------------------------------------------------


def _assemble_result(block: dict, decision: dict) -> RerankedResult:
    """Assemble a RerankedResult from a block + its LLM decision.

    Extracts content from the block's _data (if available from unfold)
    or parses it from the markdown.
    """
    data = block.get("_data", {})
    source_type = block.get("source_type", "article")
    relevance = decision.get("relevance") or "medium"
    if relevance not in ("high", "medium"):
        relevance = "medium"
    reasoning = decision.get("reasoning", "")
    db_id = block.get("id", "") or ""
    rrf_val = float(block.get("rrf", 0.0) or 0.0)

    if source_type == "section":
        return RerankedResult(
            source_type="section",
            title=data.get("title") or block.get("title", ""),
            content=data.get("section_summary") or _extract_content_from_markdown(block.get("markdown", "")),
            section_title=data.get("title") or block.get("title", ""),
            section_summary=data.get("section_summary", ""),
            article_context=data.get("section_context", ""),
            regulation_title=data.get("regulation_title", ""),
            relevance=relevance,
            reasoning=reasoning,
            db_id=db_id,
            rrf=rrf_val,
        )

    raw_num = data.get("article_num")
    article_num = str(raw_num) if raw_num is not None else None

    return RerankedResult(
        source_type="article",
        title=data.get("title") or block.get("title", ""),
        content=data.get("content") or _extract_content_from_markdown(block.get("markdown", "")),
        article_num=article_num,
        article_context=data.get("article_context", ""),
        references_content=data.get("references_content", ""),
        regulation_title=data.get("regulation_title", ""),
        section_title=data.get("section_title", ""),
        section_summary=data.get("section_summary", ""),
        relevance=relevance,
        reasoning=reasoning,
        db_id=db_id,
        rrf=rrf_val,
    )


def _extract_content_from_markdown(md: str) -> str:
    """Extract blockquote content from a result markdown block as fallback."""
    lines = []
    for line in md.split("\n"):
        if line.startswith("> "):
            lines.append(line[2:])
    return "\n".join(lines) if lines else ""


async def _enrich_kept_blocks(
    all_kept: list[tuple[dict, dict]],
    supabase: SupabaseClient,
) -> None:
    """Fetch full content from DB for kept blocks that lack _data.

    Round-1 "keep" blocks only have markdown (from search results).
    This fetches article content/context/references or section summary
    so the aggregator gets full data.
    """
    for block, _dec in all_kept:
        if block.get("_data"):
            continue  # already has full data from unfold

        target_id = block.get("id", "")
        if not target_id:
            continue

        source_type = block.get("source_type", "article")

        try:
            if source_type == "article":
                resp = await asyncio.to_thread(
                    lambda tid=target_id: supabase.table("articles")
                    .select(
                        "id, title, content, article_num, identifier_number, "
                        "article_context, references_content, section_id, regulation_id"
                    )
                    .eq("id", tid)
                    .maybe_single()
                    .execute()
                )
                if resp and resp.data:
                    art = resp.data
                    # Fetch parent names
                    reg_title = ""
                    sec_title = ""
                    if art.get("regulation_id"):
                        reg_resp = await asyncio.to_thread(
                            lambda rid=art["regulation_id"]: supabase.table("regulations")
                            .select("title")
                            .eq("id", rid)
                            .maybe_single()
                            .execute()
                        )
                        if reg_resp and reg_resp.data:
                            reg_title = reg_resp.data.get("title", "")
                    if art.get("section_id"):
                        sec_resp = await asyncio.to_thread(
                            lambda sid=art["section_id"]: supabase.table("sections")
                            .select("title, section_summary")
                            .eq("id", sid)
                            .maybe_single()
                            .execute()
                        )
                        if sec_resp and sec_resp.data:
                            sec_title = sec_resp.data.get("title", "")

                    block["_data"] = {
                        "title": art.get("title", ""),
                        "content": art.get("content", ""),
                        "article_num": art.get("article_num"),
                        "article_context": art.get("article_context", "") or "",
                        "references_content": art.get("references_content", "") or "",
                        "regulation_title": reg_title,
                        "section_title": sec_title,
                    }

            elif source_type == "section":
                row = await _fetch_section_row(supabase, target_id)
                if row:
                    block["_data"] = {
                        "title": row.get("title", ""),
                        "section_summary": row.get("section_summary", ""),
                        "section_context": row.get("section_context", ""),
                        "regulation_title": row.get("regulation_title", ""),
                    }

        except Exception as e:
            logger.warning("_enrich_kept_blocks(%s %s): %s", source_type, target_id[:12], e)


# -- Main entry point ----------------------------------------------------------


async def run_reranker_for_query(
    query: str,
    rationale: str,
    raw_markdown: str,
    supabase: SupabaseClient,
    *,
    max_high: int = 8,
    max_medium: int = 4,
    model_override: str | None = None,
    round_trace: list[dict] | None = None,
) -> tuple[RerankerQueryResult, list[dict], list[dict]]:
    """Run up to 3 classification rounds with programmatic unfolding between.

    Args:
        query: The sub-query string.
        rationale: Why this query was generated by the expander.
        raw_markdown: Search results markdown (from search_regulations_pipeline).
        supabase: Supabase client for programmatic unfold calls.
        max_high: Max high-relevance results to keep per sub-query.
        max_medium: Max medium-relevance results to keep per sub-query.
        model_override: Optional model registry key (overrides reg_search_reranker default).
        round_trace: Optional list; if provided, a dict is appended per classification
            round with keys ``round_num``, ``user_msg``, ``classification``,
            ``unfolds``, ``usage``. Used by the monitor harness to log every LLM
            I/O exchange, not just the final result.

    Returns:
        (RerankerQueryResult, usage_entries, decision_log)
    """
    agent = create_reranker_agent(model_override=model_override)

    # Parse initial results
    active_blocks = _parse_result_blocks(raw_markdown)
    if not active_blocks:
        return RerankerQueryResult(
            query=query, rationale=rationale, sufficient=False,
            results=[], dropped_count=0,
            summary_note="لم يتم العثور على نتائج قابلة للتحليل",
        ), [], []

    all_kept: list[tuple[dict, dict]] = []  # (block, decision_dict) pairs
    total_dropped = 0
    total_unfolds = 0
    unfold_failed = False
    usage_entries: list[dict] = []
    decision_log: list[dict] = []  # {position, rrf, action, relevance} per decision
    final_summary = ""
    last_sufficient = False

    for round_num in range(1, MAX_RERANKER_ROUNDS + 1):
        # Build trimmed markdown from active blocks only
        trimmed_md = _assemble_markdown(active_blocks)
        user_msg = build_reranker_user_message(
            query, rationale, trimmed_md, round_num,
            max_high=max_high, max_medium=max_medium,
        )

        logger.info(
            "Reranker round %d: %d active blocks, %d chars",
            round_num, len(active_blocks), len(trimmed_md),
        )

        result = None
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                result = await agent.run(user_msg, usage_limits=RERANKER_LIMITS)
                break
            except Exception as e:
                last_err = e
                logger.warning(
                    "Reranker round %d attempt %d/3 failed: %s",
                    round_num, attempt + 1, e,
                )
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))

        if result is None:
            logger.error("Reranker round %d gave up after 3 attempts: %s", round_num, last_err)
            break

        classification = result.output

        # Track usage
        ru = result.usage()
        usage_entries.append({
            "agent": "reranker",
            "reranker_round": round_num,
            "requests": ru.requests,
            "input_tokens": ru.input_tokens,
            "output_tokens": ru.output_tokens,
            "total_tokens": ru.total_tokens,
            "details": dict(ru.details) if ru.details else {},
        })

        # Process decisions
        to_unfold: list[tuple[dict, dict]] = []

        for dec in classification.decisions:
            pos = dec.position
            if pos < 1 or pos > len(active_blocks):
                logger.warning("Reranker: invalid position %d (max %d)", pos, len(active_blocks))
                continue

            block = active_blocks[pos - 1]
            dec_dict = {
                "action": dec.action,
                "relevance": dec.relevance,
                "reasoning": dec.reasoning,
            }

            log_entry = {
                "position": pos,
                "rrf": block.get("rrf", 0.0),
                "action": dec.action,
            }

            if dec.action == "keep":
                all_kept.append((block, dec_dict))
                log_entry["relevance"] = dec.relevance or "medium"
            elif dec.action == "unfold":
                to_unfold.append((block, dec_dict))
            else:
                total_dropped += 1

            decision_log.append(log_entry)

        final_summary = classification.summary_note
        last_sufficient = classification.sufficient

        logger.info(
            "Reranker round %d: %d kept, %d unfold, %d dropped, sufficient=%s",
            round_num, len([d for d in classification.decisions if d.action == "keep"]),
            len(to_unfold), total_dropped, classification.sufficient,
        )

        # 80% rule or nothing to unfold → done
        if classification.sufficient or not to_unfold:
            # Capture trace before breaking (no unfolds this round)
            if round_trace is not None:
                round_trace.append({
                    "round_num": round_num,
                    "user_msg": user_msg,
                    "classification": classification.model_dump(),
                    "unfolds": [],
                    "usage": usage_entries[-1] if usage_entries else {},
                })
            break

        _MODE_BY_SOURCE_TYPE = {
            "regulation": "regulation_detailed",
            "section": "section_detailed",
            "article": "article_precise",
        }

        # Programmatic unfold — parallel across blocks
        async def _do_unfold(block: dict, dec_dict: dict) -> tuple[list[dict], str, bool]:
            target_id = block.get("id", "")
            source_type = block.get("source_type", "")
            mode = _MODE_BY_SOURCE_TYPE.get(source_type)
            if not target_id or not mode:
                logger.warning(
                    "Reranker unfold: unknown source_type %r for id=%s — skipping",
                    source_type, target_id[:16],
                )
                return [], mode or "", False
            parent_rrf = float(block.get("rrf", 0.0) or 0.0)
            for attempt in range(3):
                try:
                    new_blocks = await _programmatic_unfold(
                        supabase, target_id, mode, parent_rrf=parent_rrf,
                    )
                    logger.info(
                        "Reranker unfold: %s %s → %d blocks",
                        mode, target_id[:12], len(new_blocks),
                    )
                    return new_blocks, mode, True
                except Exception as e:
                    logger.warning(
                        "Reranker unfold attempt %d/3 (%s %s): %s",
                        attempt + 1, mode, target_id[:12], e,
                    )
                    if attempt < 2:
                        await asyncio.sleep(0.5 * (attempt + 1))
            return [], mode, False

        unfold_results = await asyncio.gather(
            *[_do_unfold(block, dec_dict) for block, dec_dict in to_unfold]
        )
        unfolded_blocks: list[dict[str, Any]] = []
        unfold_summary: list[dict] = []
        # H8: dedup unfolded blocks across parents within this round and
        # against ids already kept earlier — prevents the same article from
        # entering the next round multiple times (which produced the
        # "article 25 ×6" artifact on q10/q7).
        already_seen_ids: set[str] = {
            (b.get("id") or "") for b, _ in all_kept if b.get("id")
        }
        for (block, dec_dict), (new_blocks, mode, ok) in zip(to_unfold, unfold_results):
            if ok:
                unique_new: list[dict[str, Any]] = []
                for nb in new_blocks:
                    nb_id = nb.get("id", "")
                    if nb_id and nb_id in already_seen_ids:
                        continue
                    if nb_id:
                        already_seen_ids.add(nb_id)
                    unique_new.append(nb)
                unfolded_blocks.extend(unique_new)
                total_unfolds += 1
                unfold_summary.append({
                    "block_id": block.get("id", ""),
                    "mode": mode,
                    "resulting_blocks": len(unique_new),
                    "deduped_from": len(new_blocks),
                    "titles": [b.get("title", "") for b in unique_new[:6]],
                })
            else:
                total_dropped += 1
                unfold_summary.append({
                    "block_id": block.get("id", ""),
                    "mode": mode,
                    "resulting_blocks": 0,
                    "error": "unfold_failed",
                })

        # Capture trace for this round
        if round_trace is not None:
            round_trace.append({
                "round_num": round_num,
                "user_msg": user_msg,
                "classification": classification.model_dump(),
                "unfolds": unfold_summary,
                "usage": usage_entries[-1] if usage_entries else {},
            })

        if not unfolded_blocks:
            # All unfolds returned 0 blocks — treat as a hard failure for this sub-query.
            # Mark final result sufficient=False so upstream knows coverage is incomplete.
            unfold_failed = True
            final_summary = (
                f"[unfold_failed] {final_summary}".strip()
                if final_summary
                else "فشل توسيع النتائج: لم تُعثر على سجلات فرعية"
            )
            break

        # Next round: only the newly unfolded blocks
        active_blocks = unfolded_blocks

    # Enrich kept blocks that lack _data (round-1 keeps from raw search)
    await _enrich_kept_blocks(all_kept, supabase)

    # H8: dedup by DB id within this sub-query (same article/section kept
    # across multiple rounds gets collapsed). When duplicates exist, keep
    # the copy with the highest rrf and merge reasoning strings so no
    # justification is lost.
    by_id: dict[str, tuple[dict, dict]] = {}
    no_id: list[tuple[dict, dict]] = []
    drop_count_before = total_dropped
    for block, dec in all_kept:
        block_id = block.get("id", "") or ""
        if not block_id:
            no_id.append((block, dec))
            continue
        existing = by_id.get(block_id)
        if existing is None:
            by_id[block_id] = (block, dec)
            continue
        # Duplicate — keep the higher-rrf copy, merge reasoning.
        ex_block, ex_dec = existing
        cur_rrf = float(block.get("rrf", 0.0) or 0.0)
        ex_rrf = float(ex_block.get("rrf", 0.0) or 0.0)
        winner_block, winner_dec = (
            (block, dec) if cur_rrf > ex_rrf else (ex_block, ex_dec)
        )
        loser_dec = ex_dec if winner_dec is dec else dec
        merged_dec = dict(winner_dec)
        win_reason = (winner_dec.get("reasoning") or "").strip()
        lose_reason = (loser_dec.get("reasoning") or "").strip()
        if lose_reason and lose_reason != win_reason:
            merged_dec["reasoning"] = (
                f"{win_reason} | {lose_reason}" if win_reason else lose_reason
            )
        by_id[block_id] = (winner_block, merged_dec)
        total_dropped += 1
    deduped: list[tuple[dict, dict]] = list(by_id.values()) + no_id
    if len(deduped) < len(all_kept):
        logger.info(
            "Reranker [%s]: H8 dedup removed %d duplicate DB ids",
            query[:40], len(all_kept) - len(deduped),
        )
    all_kept = deduped

    # Apply per-sub-query keep caps (sort by rrf desc within each tier)
    def _get_rrf(pair: tuple) -> float:
        block, _dec = pair
        return block.get("rrf", 0.0)

    high_kept = [(b, d) for b, d in all_kept if (d.get("relevance") or "medium") == "high"]
    med_kept  = [(b, d) for b, d in all_kept if (d.get("relevance") or "medium") != "high"]
    high_kept.sort(key=_get_rrf, reverse=True)
    med_kept.sort(key=_get_rrf, reverse=True)
    truncated = max(0, len(high_kept) - max_high) + max(0, len(med_kept) - max_medium)
    high_kept = high_kept[:max_high]
    med_kept  = med_kept[:max_medium]
    all_kept_capped = high_kept + med_kept
    total_dropped += truncated

    if truncated > 0:
        logger.info(
            "Reranker [%s]: cap truncated %d results (max_high=%d max_medium=%d)",
            query[:40], truncated, max_high, max_medium,
        )

    # Assemble final results
    results = [_assemble_result(block, dec) for block, dec in all_kept_capped]

    # sufficient: respect the LLM's own assessment; force False on unfold failure
    final_sufficient = False if unfold_failed else last_sufficient

    return RerankerQueryResult(
        query=query,
        rationale=rationale,
        sufficient=final_sufficient,
        results=results,
        dropped_count=total_dropped,
        summary_note=final_summary,
        unfold_rounds=min(round_num, MAX_RERANKER_ROUNDS),
        total_unfolds=total_unfolds,
        caps_applied={"max_high": max_high, "max_medium": max_medium, "truncated_by_cap": truncated},
    ), usage_entries, decision_log
