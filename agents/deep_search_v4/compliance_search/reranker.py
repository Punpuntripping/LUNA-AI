"""ServiceReranker agent for the compliance_search loop.

Receives ONE sub-query's service results as a flat markdown list and classifies
each as keep/drop. No unfold action — services are flat records.

Invocation shape (v2 — mirrors reg_search / case_search): the loop runs
``run_reranker_for_query`` ONCE PER EXPANDER SUB-QUERY, in parallel via
``asyncio.gather`` (see ``loop.RerankerNode``). Each call sees only that
sub-query's retrieved pool and judges relevance against that sub-query.
Cross-query dedup + the global keep cap are applied after the gather, in the
node. (The previous design fused all sub-queries into one pool and issued a
single reranker call — replaced 2026-06-07.)

Architecture:
- create_reranker_agent(): factory, returns Agent[None, ServiceRerankerOutput]
- run_reranker_for_query(): one classification call over a single sub-query's rows
- Model: compliance_search_reranker (qwen3.5-flash) — fast, cheap, Arabic classification
- No tools, no deps type parameter
- UsageLimits: output_tokens_limit=25_000, request_limit=3
"""
from __future__ import annotations

import logging

from pydantic_ai import Agent, TextOutput
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model
from agents.utils.structured_output import make_json_salvager

from .models import RerankedServiceResult, ServiceDecision, ServiceRerankerOutput
from .prompts import RERANKER_SYSTEM_PROMPT
from .unfold_reranker import build_reranker_user_message

logger = logging.getLogger(__name__)

RERANKER_LIMITS = UsageLimits(
    # 25k = 15k thinking budget + ~10k text output headroom.
    # Same shape as reg_search / case_search rerankers.
    # (`response_tokens_limit` was the deprecated alias — switched.)
    output_tokens_limit=25_000,
    request_limit=3,
)


# qwen3.5-flash with enable_thinking sometimes finalises as text
# (``<thinking>…</thinking>{json}``) instead of calling the output tool. The
# salvager rescues a schema-complete JSON without a (large) retry; a genuine
# omission still raises ModelRetry. See agents/utils/structured_output.py.
_COMPLIANCE_RERANKER_RETRY_MSG = (
    "أعد المخرَج ككائن JSON صالح وفق المخطط (sufficient, query_axes, "
    "decisions[position, action, relevance, reasoning, satisfies_axes], "
    "weak_axes, summary_note) فقط — دون أي نص أو وسم <thinking> خارج JSON."
)


def _compliance_text_output() -> TextOutput:
    """``TextOutput`` salvage member for the reranker's ``output_type`` union."""
    return TextOutput(
        make_json_salvager(ServiceRerankerOutput, retry_msg=_COMPLIANCE_RERANKER_RETRY_MSG)
    )


def create_reranker_agent(
    model_override: str | None = None,
) -> Agent[None, ServiceRerankerOutput]:
    """Create ServiceReranker agent — structured output, no tools.

    ``model_override`` is a tier override token (``qwen``/``deepseek``/
    ``alibaba``/``openrouter``) applied to the slot's policy; tier stays fixed.

    Returns:
        Configured Agent with ServiceRerankerOutput output type.
    """
    model = get_agent_model("compliance_search_reranker", model_override)
    return Agent(
        model,
        name="compliance_search_reranker",
        output_type=[ServiceRerankerOutput, _compliance_text_output()],
        instructions=RERANKER_SYSTEM_PROMPT,
        retries=2,
        # Cap reasoning at 15k — same rationale as reg_search reranker.
        model_settings={
            "extra_body": {
                "enable_thinking": True,
                "thinking_budget": 15_000,
            },
        },
    )


# -- Result assembly -----------------------------------------------------------


def assemble_service_result(
    row: dict, dec: ServiceDecision
) -> RerankedServiceResult:
    """Build a typed ``RerankedServiceResult`` from a kept row + its decision.

    The fields come straight off the raw service DB row; only ``relevance`` and
    ``reasoning`` come from the LLM decision. Mirrors the inline assembly that
    used to live in ``loop.RerankerNode`` before the per-query refactor.
    """
    relevance = dec.relevance or "medium"
    if relevance not in ("high", "medium"):
        relevance = "medium"
    return RerankedServiceResult(
        service_ref=row.get("service_ref", "") or "",
        title=row.get("service_name_ar", "") or "",
        content=row.get("service_context", "") or "",
        provider_name=row.get("provider_name", "") or "",
        service_url=(row.get("service_url") or row.get("url", "") or ""),
        sectors=row.get("sectors") or [],
        is_proactive=bool(row.get("is_proactive", False)),
        score=float(row.get("score", 0.0) or 0.0),
        relevance=relevance,
        reasoning=dec.reasoning or "",
    )


# -- Per-sub-query entry point -------------------------------------------------


async def run_reranker_for_query(
    query: str,
    rationale: str,
    rows: list[dict],
    *,
    max_keep: int,
    round_count: int = 1,
    model_override: str | None = None,
) -> tuple[ServiceRerankerOutput, list[RerankedServiceResult], dict, str, bytes | None]:
    """Run ONE reranker invocation over a single sub-query's service rows.

    Government services are flat records, so — unlike the reg_search reranker —
    there is no classify→unfold→reclassify loop here: a single classification
    call per sub-query, then a per-query keep cap (high-relevance ahead of
    medium, ties by RRF score). Cross-query dedup and the global cap are the
    caller's job (``loop.RerankerNode``).

    Args:
        query: The expander sub-query string (becomes the reranker's focus).
        rationale: Why the expander generated this sub-query (logs only).
        rows: This sub-query's retrieved service rows (deduped within the query).
        max_keep: Per-sub-query keep cap.
        round_count: Retrieval round (1=initial). Drives the prompt round wrapper.
        model_override: Optional tier override token for the reranker model.

    Returns:
        (output, kept, usage, user_message, messages_json)
          output        — raw ServiceRerankerOutput (sufficient/decisions/weak_axes)
          kept          — typed RerankedServiceResult list, already per-query capped
          usage         — token usage dict for this single call
          user_message  — the rendered reranker prompt body (for MD logging)
          messages_json — all_messages_json() bytes (reasoning extraction), or None
    """
    if not rows:
        empty_usage = {
            "agent": "reranker",
            "requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
        }
        return (
            ServiceRerankerOutput(
                sufficient=False,
                decisions=[],
                weak_axes=[],
                summary_note="لا توجد نتائج بحث لهذا الاستعلام",
            ),
            [],
            empty_usage,
            "",
            None,
        )

    agent = create_reranker_agent(model_override=model_override)

    # focus_instruction = the sub-query: the reranker judges each service's
    # relevance against THIS sub-query (n_queries=1 — single pool view).
    user_message = build_reranker_user_message(
        query,
        rows,
        round_count,
        1,
        max_keep=max_keep,
    )

    result = await agent.run(user_message, usage_limits=RERANKER_LIMITS)
    output: ServiceRerankerOutput = result.output

    ru = result.usage()
    usage = {
        "agent": "reranker",
        "requests": ru.requests,
        "input_tokens": ru.input_tokens,
        "output_tokens": ru.output_tokens,
        "total_tokens": ru.total_tokens,
        "cached_tokens": int(getattr(ru, "cache_read_tokens", 0) or 0),
    }

    # Assemble kept results, dedup by service_ref within this sub-query.
    kept: list[RerankedServiceResult] = []
    kept_refs: set[str] = set()
    for dec in output.decisions:
        if dec.action != "keep":
            continue
        idx = dec.position - 1
        if not (0 <= idx < len(rows)):
            continue
        row = rows[idx]
        ref = row.get("service_ref", "") or ""
        if not ref or ref in kept_refs:
            continue
        kept.append(assemble_service_result(row, dec))
        kept_refs.add(ref)

    # Per-query cap: high-relevance ahead of medium, ties broken by score desc.
    high = sorted([r for r in kept if r.relevance == "high"], key=lambda r: -r.score)
    med = sorted([r for r in kept if r.relevance != "high"], key=lambda r: -r.score)
    kept = (high + med)[:max_keep]

    logger.info(
        "compliance reranker q=%s: %d rows -> %d kept (max_keep=%d), sufficient=%s",
        query[:40], len(rows), len(kept), max_keep, output.sufficient,
    )

    return output, kept, usage, user_message, result.all_messages_json()
