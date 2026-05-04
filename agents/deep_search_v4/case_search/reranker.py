"""Per-query reranker for case_search.

Runs one LLM classification call per sub-query (concurrent, not pooled).
Cases are flat documents — only keep/drop (no unfold unlike reg_search).
"""
from __future__ import annotations

import logging
import re
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model

from .models import (
    RerankedCaseResult,
    RerankerClassification,
    RerankerQueryResult,
)
from .prompts import build_reranker_user_message, get_reranker_prompt

logger = logging.getLogger(__name__)

RERANKER_LIMITS = UsageLimits(
    response_tokens_limit=70_000,
    request_limit=3,
)


def create_reranker_agent(
    prompt_key: str = "prompt_1",
    model_override: str | None = None,
) -> Agent[None, RerankerClassification]:
    """Create a per-query classification reranker agent. No tools, no deps."""
    system_prompt = get_reranker_prompt(prompt_key)

    from agents.model_registry import create_model

    model = (
        create_model(model_override)
        if model_override
        else get_agent_model("case_search_reranker")
    )

    return Agent(
        model,
        name="case_search_reranker",
        output_type=RerankerClassification,
        instructions=system_prompt,
        retries=2,
    )


# -- Markdown parser -----------------------------------------------------------

# Matches both the new minimal `### [N]` header and the legacy
# `### [N] حكم: <court> — <city> (<level>)` header (still used by the
# non-sectioned search.py path).
_RESULT_HEADER_RE = re.compile(
    r"^### \[(\d+)\](?:\s+حكم:\s*(.+?))?\s*$",
    re.MULTILINE,
)

_RRF_RE = re.compile(r"RRF:\s*([\d.]+)")


def _parse_case_blocks(markdown: str) -> list[dict[str, Any]]:
    """Parse case search results markdown into individual result blocks."""
    matches = list(_RESULT_HEADER_RE.finditer(markdown))
    if not matches:
        return []

    blocks: list[dict[str, Any]] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        block_md = markdown[start:end].rstrip()

        rrf_match = _RRF_RE.search(block_md)
        rrf = float(rrf_match.group(1)) if rrf_match else 0.0

        blocks.append({
            "position": int(m.group(1)),
            "title": (m.group(2) or "").strip(),
            "rrf": rrf,
            "markdown": block_md,
        })

    return blocks


# -- Result assembler ----------------------------------------------------------


def _assemble_case_result(block: dict, decision: dict) -> RerankedCaseResult:
    """Assemble a RerankedCaseResult from a block + its LLM decision."""
    relevance = decision.get("relevance") or "medium"
    if relevance not in ("high", "medium"):
        relevance = "medium"

    md = block.get("markdown", "")

    court = _extract_field(md, r"حكم:\s+(.+?)(?:\s+—|$)")
    city = _extract_field(md, r"—\s+(.+?)\s+\(")
    court_level_raw = _extract_field(md, r"\((ابتدائي|استئناف)\)")
    court_level = "appeal" if court_level_raw == "استئناف" else "first_instance"
    case_number = _extract_field(md, r"\*\*رقم القضية:\*\*\s+(.+?)(?:\s+\||$)")
    judgment_number = _extract_field(md, r"\*\*رقم الحكم:\*\*\s+(.+?)(?:\s+\||$)")
    date_hijri = _extract_field(md, r"\*\*التاريخ:\*\*\s+(.+?)$")

    domains_raw = _extract_field(md, r"\*\*المجالات القانونية:\*\*\s+(.+?)$")
    legal_domains = [d.strip() for d in domains_raw.split("·")] if domains_raw else []

    content = _extract_content(md)

    court_str = court or ""
    case_num_str = case_number or ""
    date_str = date_hijri or ""
    title = " | ".join(filter(None, [court_str, case_num_str, date_str]))

    return RerankedCaseResult(
        title=title,
        court=court or None,
        city=city or None,
        court_level=court_level,
        case_number=case_number or None,
        judgment_number=judgment_number or None,
        date_hijri=date_hijri or None,
        content=content,
        legal_domains=legal_domains,
        score=block.get("rrf", 0.0),
        relevance=relevance,
        reasoning=decision.get("reasoning", ""),
    )


def _extract_field(md: str, pattern: str) -> str:
    match = re.search(pattern, md, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _extract_content(md: str) -> str:
    lines = md.split("\n")
    content_lines = []
    in_content = False
    for line in lines:
        if line.startswith("###") or line.startswith("**") or line.startswith("---"):
            if in_content:
                break
            continue
        if line.strip() and not line.startswith("  -"):
            in_content = True
            content_lines.append(line)
        elif in_content and not line.strip():
            content_lines.append(line)
    return "\n".join(content_lines).strip()[:3000]


# -- Main entry point ----------------------------------------------------------


async def run_reranker_for_query(
    query: str,
    rationale: str,
    raw_markdown: str,
    model_override: str | None = None,
    prompt_key: str = "prompt_1",
    *,
    max_high: int = 6,
    max_medium: int = 4,
    round_trace: list[dict] | None = None,
) -> tuple[RerankerQueryResult, list[dict], list[dict]]:
    """Run per-query LLM reranker classification for a single sub-query.

    Args:
        query: The sub-query string.
        rationale: Why this query was generated by the expander.
        raw_markdown: Search results in markdown format (from search_pipeline).
        model_override: Optional model registry key.
        prompt_key: Which reranker prompt variant to use.
        max_high: Max high-relevance results to keep per sub-query.
        max_medium: Max medium-relevance results to keep per sub-query.

    Returns:
        (RerankerQueryResult, usage_entries, decision_log)
    """
    blocks = _parse_case_blocks(raw_markdown)

    if not blocks:
        return RerankerQueryResult(
            query=query,
            rationale=rationale,
            sufficient=False,
            results=[],
            dropped_count=0,
            summary_note="لم يتم العثور على نتائج قابلة للتصنيف",
        ), [], []

    agent = create_reranker_agent(prompt_key=prompt_key, model_override=model_override)
    user_msg = build_reranker_user_message(
        query, rationale, raw_markdown,
        max_high=max_high, max_medium=max_medium,
    )

    logger.info(
        "Reranker [%s]: %d blocks, %d chars",
        query[:40],
        len(blocks),
        len(raw_markdown),
    )

    usage_entries: list[dict] = []
    decision_log: list[dict] = []

    try:
        result = await agent.run(user_msg, usage_limits=RERANKER_LIMITS)
        classification = result.output

        ru = result.usage()
        usage_entry = {
            "agent": "reranker",
            "query": query[:80],
            "requests": ru.requests,
            "input_tokens": ru.input_tokens,
            "output_tokens": ru.output_tokens,
            "total_tokens": ru.total_tokens,
            "details": dict(ru.details) if ru.details else {},
        }
        usage_entries.append(usage_entry)

        if round_trace is not None:
            round_trace.append({
                "round_num": 1,
                "user_msg": user_msg,
                "classification": classification.model_dump(),
                "usage": usage_entry,
            })

    except Exception as e:
        logger.error("Reranker error [%s]: %s", query[:40], e, exc_info=True)
        return RerankerQueryResult(
            query=query,
            rationale=rationale,
            sufficient=False,
            results=[],
            dropped_count=len(blocks),
            summary_note=f"خطأ في التصنيف: {str(e)[:100]}",
        ), [], []

    # Map position → block
    pos_to_block: dict[int, dict] = {b["position"]: b for b in blocks}

    kept: list[RerankedCaseResult] = []
    dropped = 0
    decided_positions: set[int] = set()

    for dec in classification.decisions:
        pos = dec.position
        if pos not in pos_to_block:
            logger.warning("Reranker: invalid position %d (max %d)", pos, len(blocks))
            continue

        decided_positions.add(pos)
        block = pos_to_block[pos]

        # M4 — coerce any non-keep/drop action (e.g. an "unfold" leak from
        # the shared schema, or a hypothetical model rebellion) to "drop"
        # so the case reranker's terminal pass is strictly binary.
        action = dec.action if dec.action in ("keep", "drop") else "drop"
        if action != dec.action:
            logger.info(
                "Reranker: coerced action %r → 'drop' at position %d",
                dec.action, pos,
            )

        dec_dict = {
            "action": action,
            "relevance": dec.relevance,
            "reasoning": dec.reasoning,
        }

        log_entry = {
            "position": pos,
            "rrf": block.get("rrf", 0.0),
            "action": action,
            "relevance": dec.relevance or ("medium" if action == "keep" else None),
            "reasoning": dec.reasoning or "",
        }

        if action == "keep":
            kept.append(_assemble_case_result(block, dec_dict))
        else:
            dropped += 1

        decision_log.append(log_entry)

    # Positions with no explicit decision → treat as dropped
    all_positions = set(pos_to_block.keys())
    undecided_positions = sorted(all_positions - decided_positions)
    undecided = len(undecided_positions)
    if undecided > 0:
        logger.warning(
            "Reranker: %d positions had no decision (treated as dropped): %s",
            undecided,
            ", ".join(str(p) for p in undecided_positions),
        )
        for pos in undecided_positions:
            block = pos_to_block[pos]
            decision_log.append({
                "position": pos,
                "rrf": block.get("rrf", 0.0),
                "action": "undecided",
                "relevance": None,
                "reasoning": "لم يصنّف المُصنّف هذا الموضع",
            })
        dropped += undecided

    # Apply per-sub-query keep caps (sort by score desc within each tier)
    high_results = sorted(
        [r for r in kept if r.relevance == "high"],
        key=lambda r: -r.score,
    )
    med_results = sorted(
        [r for r in kept if r.relevance != "high"],
        key=lambda r: -r.score,
    )
    truncated = max(0, len(high_results) - max_high) + max(0, len(med_results) - max_medium)
    kept = high_results[:max_high] + med_results[:max_medium]
    dropped += truncated

    if truncated > 0:
        logger.info(
            "Reranker [%s]: cap truncated %d results (max_high=%d max_medium=%d)",
            query[:40], truncated, max_high, max_medium,
        )

    logger.info(
        "Reranker [%s]: %d kept, %d dropped, %d undecided, sufficient=%s",
        query[:40],
        len(kept),
        dropped,
        undecided,
        classification.sufficient,
    )

    return RerankerQueryResult(
        query=query,
        rationale=rationale,
        sufficient=classification.sufficient,
        results=kept,
        dropped_count=dropped,
        summary_note=classification.summary_note,
        caps_applied={"max_high": max_high, "max_medium": max_medium, "truncated_by_cap": truncated},
    ), usage_entries, decision_log
