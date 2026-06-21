"""Per-query reranker for case_search.

Runs one LLM classification call per sub-query (concurrent, not pooled).
Cases are flat documents — only keep/drop (no unfold unlike reg_search).
"""
from __future__ import annotations

import logging
import re
from typing import Any

from pydantic_ai import Agent, TextOutput
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model
from agents.utils.structured_output import make_json_salvager

from .models import (
    CaseRerankerClassification,
    RerankedCaseResult,
    RerankerQueryResult,
)
from .prompts import build_reranker_user_message, get_reranker_prompt

logger = logging.getLogger(__name__)

RERANKER_LIMITS = UsageLimits(
    # 25k = 15k thinking budget + ~10k per-chunk text output headroom.
    # Same shape as reg_search reranker — uncapped thinking on qwen3.5-flash
    # is otherwise the wall-clock tail risk.
    # (`response_tokens_limit` was the deprecated alias — switched.)
    output_tokens_limit=25_000,
    request_limit=3,
)

# qwen3.5-flash with enable_thinking sometimes finalises as text
# (``<thinking>…</thinking>{json}``) instead of calling the output tool. The
# salvager rescues a schema-complete JSON without a (large) retry; a genuine
# omission still raises ModelRetry. See agents/utils/structured_output.py.
_CASE_RERANKER_RETRY_MSG = (
    "Return the output as a valid JSON object conforming to the schema "
    "(sufficient, query_axes, "
    "keeps[position, relevance, reasoning, satisfies_axes], "
    "summary_note) only — with no text or <thinking> tag outside the JSON. "
    "Emit one entry only for each ruling you KEEP; relevance is required."
)


def _case_text_output() -> TextOutput:
    """``TextOutput`` salvage member for the reranker's ``output_type`` union."""
    return TextOutput(
        make_json_salvager(CaseRerankerClassification, retry_msg=_CASE_RERANKER_RETRY_MSG)
    )


def create_reranker_agent(
    prompt_key: str = "prompt_1",
    model_override: str | None = None,
) -> Agent[None, CaseRerankerClassification]:
    """Create a per-query classification reranker agent. No tools, no deps.

    ``model_override`` is a tier override token (``qwen``/``deepseek``/
    ``alibaba``/``openrouter``) applied to the slot's policy; tier stays fixed.
    """
    system_prompt = get_reranker_prompt(prompt_key)
    model = get_agent_model("case_search_reranker", model_override)

    return Agent(
        model,
        name="case_search_reranker",
        output_type=[CaseRerankerClassification, _case_text_output()],
        instructions=system_prompt,
        retries=2,
        # Cap reasoning at 15k — same rationale as reg_search reranker.
        model_settings={
            "extra_body": {
                "enable_thinking": True,
                "thinking_budget": 15_000,
            },
        },
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
    max_keep: int = 10,
    round_trace: list[dict] | None = None,
) -> tuple[RerankerQueryResult, list[dict], list[dict]]:
    """Run per-query LLM reranker classification for a single sub-query.

    Args:
        query: The sub-query string.
        rationale: Why this query was generated by the expander.
        raw_markdown: Search results in markdown format (from search_pipeline).
        model_override: Optional model registry key.
        prompt_key: Which reranker prompt variant to use.
        max_keep: Max results to keep per sub-query (single flat cap).

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
        max_keep=max_keep,
    )

    logger.info(
        "Reranker [%s]: %d blocks, %d chars",
        query[:40],
        len(blocks),
        len(raw_markdown),
    )

    usage_entries: list[dict] = []
    decision_log: list[dict] = []

    # Map position → block
    pos_to_block: dict[int, dict] = {b["position"]: b for b in blocks}

    async def _run_once(extra_note: str | None = None):
        """One agent call. Returns (classification, usage_entry) or raises."""
        msg = user_msg if not extra_note else f"{user_msg}\n\n{extra_note}"
        result = await agent.run(msg, usage_limits=RERANKER_LIMITS)
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
        return result.output, usage_entry

    def _build_from_classification(classification):
        """Keep-only application: iterate keeps (integrity-gated), derive drops
        by set-difference. Returns (kept, dropped_count, log_entries)."""
        kept: list[RerankedCaseResult] = []
        log_entries: list[dict] = []
        kept_positions: set[int] = set()

        for keep in classification.keeps:
            pos = keep.position
            if pos not in pos_to_block:
                logger.warning(
                    "Reranker: invalid keep position %d (max %d) — skipped",
                    pos, len(blocks),
                )
                continue
            if pos in kept_positions:
                logger.warning(
                    "Reranker: duplicate keep position %d — keeping first, skipping",
                    pos,
                )
                continue
            kept_positions.add(pos)
            block = pos_to_block[pos]

            dec_dict = {
                "relevance": keep.relevance,
                "reasoning": keep.reasoning,
            }
            kept.append(_assemble_case_result(block, dec_dict))
            log_entries.append({
                "position": pos,
                "rrf": block.get("rrf", 0.0),
                "action": "keep",
                "relevance": keep.relevance,
                "reasoning": keep.reasoning or "",
            })

        # Derive drops by difference: un-kept candidate positions are dropped.
        dropped_positions = sorted(set(pos_to_block) - kept_positions)
        for pos in dropped_positions:
            block = pos_to_block[pos]
            log_entries.append({
                "position": pos,
                "rrf": block.get("rrf", 0.0),
                "action": "drop",
                "relevance": None,
                "reasoning": "",
            })

        return kept, len(dropped_positions), log_entries

    try:
        classification, usage_entry = await _run_once()
        usage_entries.append(usage_entry)
        if round_trace is not None:
            round_trace.append({
                "round_num": 1,
                "user_msg": user_msg,
                "classification": classification.model_dump(),
                "usage": usage_entry,
            })

        kept, dropped, decision_log = _build_from_classification(classification)

        # kept == 0 of N>0 → ONE retry with a dynamic nudge; accept empty on
        # the 2nd pass. Only retained retry signal (keep-only removed the
        # count-mismatch / undecided failure class).
        if not kept and len(blocks) > 0:
            note = (
                f"You classified 0 of {len(blocks)} rulings as keep. "
                "If truly none apply to the sub-query, return an empty keeps "
                "list — otherwise reconsider and keep the genuinely relevant "
                "rulings."
            )
            logger.info(
                "Reranker [%s]: kept 0 of %d — one retry with nudge",
                query[:40], len(blocks),
            )
            classification2, usage_entry2 = await _run_once(extra_note=note)
            usage_entries.append(usage_entry2)
            if round_trace is not None:
                round_trace.append({
                    "round_num": 2,
                    "user_msg": f"{user_msg}\n\n{note}",
                    "classification": classification2.model_dump(),
                    "usage": usage_entry2,
                })
            classification = classification2
            kept, dropped, decision_log = _build_from_classification(classification)

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

    # Apply a single flat per-sub-query keep cap. High-relevance results are
    # ordered ahead of medium; ties broken by score desc.
    high_results = sorted(
        [r for r in kept if r.relevance == "high"],
        key=lambda r: -r.score,
    )
    med_results = sorted(
        [r for r in kept if r.relevance != "high"],
        key=lambda r: -r.score,
    )
    ordered = high_results + med_results
    truncated = max(0, len(ordered) - max_keep)
    kept = ordered[:max_keep]
    dropped += truncated

    if truncated > 0:
        logger.info(
            "Reranker [%s]: cap truncated %d results (max_keep=%d)",
            query[:40], truncated, max_keep,
        )

    logger.info(
        "Reranker [%s]: %d kept, %d dropped, sufficient=%s",
        query[:40],
        len(kept),
        dropped,
        classification.sufficient,
    )

    return RerankerQueryResult(
        query=query,
        rationale=rationale,
        sufficient=classification.sufficient,
        results=kept,
        dropped_count=dropped,
        summary_note=classification.summary_note,
        caps_applied={"max_keep": max_keep, "truncated_by_cap": truncated},
    ), usage_entries, decision_log
