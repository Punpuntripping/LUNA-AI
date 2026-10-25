"""Per-query reranker for case_search.

Runs one LLM classification call per sub-query (concurrent, not pooled).
Cases are flat documents — only keep/drop (no unfold unlike reg_search).

Since Wave 3 of the case-topics plan (§7 / decision D6) the reranker is no
longer a zero-context surface: ``run_reranker_for_query`` accepts the planner's
``planner_brief`` and renders it at the HEAD of the *user* message. The system
prompt (``create_reranker_agent``'s ``instructions=``) stays per-run constant —
it is the primary prompt-cache prefix and must not vary per turn.

Decider engine (``.claude/plans/reranker_decider_engine.md``, phase B): with
``CASE_RERANKER_ENGINE=decider`` each sub-query first asks the OpenRouter
decision models (``shared/decider.py``, rules in ``decider_rules.py``); the
qwen agent below runs only when no decider answers (D1/D2), so one turn may
mix engines.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from pydantic_ai import Agent, TextOutput
from pydantic_ai.usage import UsageLimits

from agents.deep_search_v4.shared import decider as _decider
from agents.deep_search_v4.shared.court_levels import (
    COURT_LEVEL_FROM_AR,
    normalize_court_level,
)
from agents.utils.agent_models import get_agent_model, primary_model_of_slot
from agents.utils.usage_limits import OUTPUT_TOKENS_CAP
from agents.utils.structured_output import make_json_salvager

from . import decider_rules
from .models import (
    CaseRerankerClassification,
    RerankedCaseResult,
    RerankerQueryResult,
)
from .prompts import (
    DEFAULT_RERANKER_PROMPT,
    build_reranker_user_message,
    get_reranker_prompt,
)

logger = logging.getLogger(__name__)

RERANKER_LIMITS = UsageLimits(
    output_tokens_limit=OUTPUT_TOKENS_CAP,  # shared runaway net — see agents/utils/usage_limits.py
    request_limit=3,
)

# The qwen flash family with enable_thinking sometimes finalises as text
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
    prompt_key: str = DEFAULT_RERANKER_PROMPT,
    model_override: str | None = None,
) -> Agent[None, CaseRerankerClassification]:
    """Create a per-query classification reranker agent. No tools, no deps.

    ``model_override`` is a tier override token (``qwen``/``deepseek``/
    ``alibaba``/``openrouter``) applied to the slot's policy; tier stays fixed.

    Takes **no per-turn input on purpose.** ``instructions=`` is the primary
    prompt-cache prefix; a turn-scoped argument here (a ``planner_brief``, the
    user's question, …) would invalidate the cache on every single call. Per-turn
    context belongs in the user message — see ``build_reranker_user_message``.
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

# Court level, parsed out of the header parenthetical. Built from the CANONICAL
# Arabic vocabulary (`shared/court_levels.py`) rather than an inline
# `(ابتدائي|استئناف)` alternation: that two-value regex — plus the two-branch
# ternary behind it — could not match `(عليا)` and relabelled all 125
# supreme-court rulings as `first_instance`. This was the THIRD copy of that bug
# (unfold_ura.py and search.py were the other two). Never re-derive the map here.
_COURT_LEVEL_RE = re.compile(
    r"\((" + "|".join(re.escape(ar) for ar in COURT_LEVEL_FROM_AR) + r")\)"
)

# The sectioned formatter's court line: `المحكمة: {court} — {city} ({level})`
# (unfold_reranker.format_candidate_for_reranker). The legacy `### [N] حكم: …`
# header regexes below no longer match that shape at all, so without this the
# fallback path would assemble a court-less, level-less result.
_SECTIONED_COURT_RE = r"^المحكمة:\s*(.+?)(?:\s+—|\s+\(|$)"


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
    """Assemble a RerankedCaseResult from a block + its LLM decision.

    Reads back the markdown the LLM was shown. Two shapes reach it:

    1. the **legacy** hybrid-search block (`### [N] حكم: {court} — {city}
       ({level})` + `**رقم القضية:**` … ), still emitted by
       ``search._format_case_result`` on the non-sectioned CLI path;
    2. the **sectioned** `case_topics` block (`المحكمة: {court} — {city}
       ({level})` + matched topics + `الملخص:`), where this is only the
       *fallback* — ``assemble_kept_cases`` normally replaces these results with
       rows re-fetched from the DB, and falls through to here only when it
       returns nothing.

    The per-field metadata regexes stay legacy-only (the sectioned payload
    carries no `رقم القضية` / `المجالات القانونية` — they were deliberately
    dropped from the reranker view), but court / city / level are parsed for
    both, so the fallback still names a court instead of an empty string.
    """
    relevance = decision.get("relevance") or "medium"
    if relevance not in ("high", "medium"):
        relevance = "medium"

    md = block.get("markdown", "")

    court = (
        _extract_field(md, r"حكم:\s+(.+?)(?:\s+—|$)")
        or _extract_field(md, _SECTIONED_COURT_RE)
    )
    city = _extract_field(md, r"—\s+(.+?)\s+\(")
    # Three-value passthrough via the canonical map — `عليا` must survive.
    level_match = _COURT_LEVEL_RE.search(md)
    court_level = normalize_court_level(
        COURT_LEVEL_FROM_AR.get(level_match.group(1)) if level_match else None
    )
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


# -- Decider engine (plan reranker_decider_engine.md, phase B) -----------------


def _decider_models(engine: str | None, model_override: str | None) -> list[str]:
    """The ``CASE_RERANKER_DECIDERS`` chain when the decider engine should run, else ``[]``.

    ``[]`` (→ today's qwen path) when a ``model_override`` is set, the engine
    (``engine`` or, when None, ``CASE_RERANKER_ENGINE``) is not ``"decider"``,
    the dedicated key is missing (D6), or settings can't load.
    """
    if model_override is not None:
        return []
    if engine is not None and engine.strip().lower() != "decider":
        return []
    try:
        from shared.config import get_settings

        settings = get_settings()
    except Exception:  # noqa: BLE001 — settings unavailable (bare test env)
        return []
    if (engine or settings.CASE_RERANKER_ENGINE or "").strip().lower() != "decider":
        return []
    if not settings.OPENROUTER_RERANKER_API_KEY:
        return []
    return _decider.parse_models(settings.CASE_RERANKER_DECIDERS)


async def _run_decider(
    *,
    query: str,
    rationale: str,
    raw_markdown: str,
    planner_brief: str | None,
    positions: list[int],
    max_keep: int,
    models: list[str],
    failures: list[str],
) -> tuple[CaseRerankerClassification | None, dict | None, dict | None]:
    """One decider request for this sub-query → ``(classification, usage_entry, trace)``.

    ``classification`` is None when the caller must run qwen; the reason goes to
    ``failures``. ``usage_entry`` is set whenever a decider answered (it was
    billed), even if its answer could not be used. Never raises.
    """
    state = decider_rules.build_state(query, rationale, raw_markdown, planner_brief=planner_brief)
    questions, required = decider_rules.build_questions(positions, decider_rules.channel_of(rationale))
    try:
        reply = await _decider.decide(state, questions, required=required, models=models)
    except Exception as e:  # noqa: BLE001 — the engine must never break the reranker
        logger.warning("Reranker [%s]: decider raised %s — qwen path", query[:40], e, exc_info=True)
        failures.append(f"decider error: {type(e).__name__}")
        return None, None, None
    if reply is None:   # decide() logged each model's failure
        failures.append("decider: no answer")
        return None, None, None

    # The ledger prices by ``model`` and buckets by ``agent`` (orchestrator
    # ``_emit_executor_ledger`` → deep_search.reranker.case), so both are pinned.
    usage_entry = {**reply.usage_entry, "agent": "reranker", "model": reply.model, "query": query[:80]}
    try:
        scores = decider_rules.scores_from_answers(reply.answers, positions)
        classification = decider_rules.classify(scores, model=reply.model, max_keep=max_keep)
    except Exception as e:  # noqa: BLE001 — unusable answer → qwen, like any decider failure
        logger.warning(
            "Reranker [%s]: decider %s answer unusable (%s) — qwen path",
            query[:40], reply.model, e,
        )
        failures.extend(reply.fallback_from)
        failures.append(f"{reply.model}: rules failed ({type(e).__name__})")
        return None, usage_entry, None

    logger.info(
        "Reranker [%s]: decider %s (%s) kept %d of %d%s",
        query[:40], reply.model, reply.model_served, len(classification.keeps),
        len(positions), f", after {reply.fallback_from}" if reply.fallback_from else "",
    )

    trace = {
        "round_num": 1,
        "engine": "decider",
        "model": reply.model,
        "model_served": reply.model_served,
        "fallback_from": list(reply.fallback_from),
        "user_msg": state,
        "scores": {p: s for p, (s, _m) in scores.items()},
        "mech": {p: m for p, (_s, m) in scores.items()},
        "classification": classification.model_dump(),
        "usage": usage_entry,
    }
    return classification, usage_entry, trace


# -- Main entry point ----------------------------------------------------------


async def run_reranker_for_query(
    query: str,
    rationale: str,
    raw_markdown: str,
    model_override: str | None = None,
    prompt_key: str = DEFAULT_RERANKER_PROMPT,
    *,
    max_keep: int = 10,
    round_trace: list[dict] | None = None,
    planner_brief: str | None = None,
    engine: str | None = None,
) -> tuple[RerankerQueryResult, list[dict], list[dict]]:
    """Run per-query LLM reranker classification for a single sub-query.

    Args:
        query: The sub-query string.
        rationale: Why this query was generated by the expander.
        raw_markdown: Search results in markdown format (from search_pipeline).
        model_override: Optional model registry key.
        prompt_key: Which reranker prompt variant to use.
        max_keep: Max results to keep per sub-query (single flat cap).
        round_trace: Optional list; one trace dict appended per pass.
        planner_brief: Optional planner-distilled background (the
            ``planner_brief`` ``ContextBlock.body`` only — never ``case_brief``
            / ``prior_search_lessons``). Rendered at the HEAD of the user
            message; empty/None renders nothing.
        engine: ``"llm"`` | ``"decider"``; None reads ``CASE_RERANKER_ENGINE``.
            The decider path needs ``model_override is None`` and the
            dedicated key; if no decider answers, the qwen path runs as before.

    Returns:
        (RerankerQueryResult, usage_entries, decision_log)

    Prompt-cache discipline: the brief goes into the USER message only. The
    system prompt (``create_reranker_agent``'s ``instructions=``) stays per-run
    constant — it is the primary cache prefix, and varying it per turn would
    invalidate the cache on every call. The brief is placed ahead of the
    sub-query so the N concurrent calls of one turn share the longest possible
    prefix. See ``build_reranker_user_message``.
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

    user_msg = build_reranker_user_message(
        query, rationale, raw_markdown,
        max_keep=max_keep,
        planner_brief=planner_brief,
    )

    logger.info(
        "Reranker [%s]: %d blocks, %d chars, planner_brief=%s",
        query[:40],
        len(blocks),
        len(raw_markdown),
        bool((planner_brief or "").strip()),
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
            # Explicit per-slot model — the "reranker" ROLE is shared with
            # reg_compliance, which heads on a different model.
            "model": primary_model_of_slot("case_search_reranker"),
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

    # Decider engine first (plan reranker_decider_engine.md, phase B). Its keeps
    # come back already capped and ranked, and zero keeps is a valid answer, so
    # there is no reconsider nudge on this path (D4). No answer → qwen below.
    classification: CaseRerankerClassification | None = None
    decider_failures: list[str] = []
    models = _decider_models(engine, model_override)
    # Only the sectioned loop's channel-prefixed sub-queries were tested; the
    # legacy RerankerNode (no ``[channel]`` prefix) stays on qwen.
    if models and decider_rules.channel_of(rationale) in decider_rules.RUBRIC_V2_CHANNEL:
        classification, decider_usage, decider_trace = await _run_decider(
            query=query, rationale=rationale, raw_markdown=raw_markdown,
            planner_brief=planner_brief, positions=list(pos_to_block),
            max_keep=max_keep, models=models, failures=decider_failures,
        )
        if decider_usage is not None:   # billed even when its answer was unusable
            usage_entries.append(decider_usage)
        if classification is not None:
            if round_trace is not None:
                round_trace.append(decider_trace)
            kept, dropped, decision_log = _build_from_classification(classification)

    if classification is None:
        agent = create_reranker_agent(prompt_key=prompt_key, model_override=model_override)
        try:
            classification, usage_entry = await _run_once()
            usage_entries.append(usage_entry)
            if round_trace is not None:
                round_trace.append({
                    "round_num": 1,
                    "engine": "llm",
                    "fallback_from": list(decider_failures),
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
                        "engine": "llm",
                        "fallback_from": list(decider_failures),
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
