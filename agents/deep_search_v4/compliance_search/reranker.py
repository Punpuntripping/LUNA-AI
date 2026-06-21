"""ServiceReranker agent for the compliance_search loop.

Receives ONE sub-query's service results as a flat markdown list. Keep-only
contract: the LLM emits an entry ONLY for each service it keeps; the drop set
is derived by difference over the rows it did not list. No unfold action —
services are flat records.

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

from .models import RerankedServiceResult, ServiceKeep, ServiceRerankerOutput
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
    "Return the output as a valid JSON object conforming to the schema "
    "(sufficient, query_axes, "
    "keeps[position, relevance, reasoning, satisfies_axes], "
    "weak_axes, summary_note) only — with no text or <thinking> tag outside the JSON. "
    "List ONLY services you keep; services you omit are dropped."
)

# Appended to the user message on the single kept==0 retry. The model
# classified every candidate as a drop; nudge it to reconsider before
# accepting an empty keep list as final.
_KEPT_ZERO_RETRY_NOTE = (
    "\n\n---\n"
    "**ملاحظة:** صنّفت {n} من أصل {n} خدمة كغير ذات صلة (لم تُبقِ أي خدمة). "
    "إن لم تكن أي خدمة منطبقة فعلًا، أعد قائمة `keeps` فارغة — وإلا فأعد النظر "
    "وأبقِ الخدمات المنطبقة.\n"
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
    row: dict, dec: ServiceKeep
) -> RerankedServiceResult:
    """Build a typed ``RerankedServiceResult`` from a kept row + its keep entry.

    The fields come straight off the raw service DB row; only ``relevance`` and
    ``reasoning`` come from the LLM keep entry. Mirrors the inline assembly that
    used to live in ``loop.RerankerNode`` before the per-query refactor.
    """
    relevance = dec.relevance
    if relevance not in ("high", "medium"):
        relevance = "medium"
    return RerankedServiceResult(
        service_ref=row.get("service_ref", "") or "",
        service_id=row.get("id", "") or "",
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


def _assemble_keeps(
    output: ServiceRerankerOutput, rows: list[dict]
) -> tuple[list[RerankedServiceResult], set[str], set[int]]:
    """Assemble kept results from a keep-only reranker output.

    Integrity gate over the (small) keep set:
      - out-of-range ``position`` → skip (hallucinated keep; safe to drop).
      - empty/duplicate ``service_ref`` → skip (existing within-query dedup).

    Returns ``(kept, kept_refs, kept_idx)`` where ``kept_idx`` is the set of
    0-based row indices actually retained — the caller derives the drop set as
    every other row index (drop-by-difference).
    """
    kept: list[RerankedServiceResult] = []
    kept_refs: set[str] = set()
    kept_idx: set[int] = set()
    for dec in output.keeps:
        idx = dec.position - 1
        if not (0 <= idx < len(rows)):
            continue
        row = rows[idx]
        ref = row.get("service_ref", "") or ""
        if not ref or ref in kept_refs:
            continue
        kept.append(assemble_service_result(row, dec))
        kept_refs.add(ref)
        kept_idx.add(idx)
    return kept, kept_refs, kept_idx


# -- Per-sub-query entry point -------------------------------------------------


async def run_reranker_for_query(
    query: str,
    rationale: str,
    rows: list[dict],
    *,
    max_keep: int,
    round_count: int = 1,
    model_override: str | None = None,
) -> tuple[
    ServiceRerankerOutput,
    list[RerankedServiceResult],
    dict,
    str,
    bytes | None,
    list[dict],
]:
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
        (output, kept, usage, user_message, messages_json, dropped)
          output        — raw ServiceRerankerOutput (sufficient/keeps/weak_axes)
          kept          — typed RerankedServiceResult list, already per-query capped
          usage         — token usage dict for this single call
          user_message  — the rendered reranker prompt body (for MD logging)
          messages_json — all_messages_json() bytes (reasoning extraction), or None
          dropped       — forensic dicts for services this sub-query did NOT keep:
                          drop-by-difference (drop_reason="llm", reasoning="" — the
                          keep-only contract has no per-drop reasoning) and per-query
                          cap truncations (drop_reason="cap", reasoning="").
                          Each: ``{"service_id", "title", "reasoning", "drop_reason"}``.
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
                keeps=[],
                weak_axes=[],
                summary_note="لا توجد نتائج بحث لهذا الاستعلام",
            ),
            [],
            empty_usage,
            "",
            None,
            [],
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

    # Keep-only contract: the model emits one entry PER KEPT service; everything
    # it does not list is a drop. Assemble keeps (integrity gate + dedup), then
    # derive the drop set by difference over the row indices it did not keep.
    kept, kept_refs, kept_idx = _assemble_keeps(output, rows)

    # kept==0 of N>0 → ONE retry. The model may have classified everything as a
    # drop; nudge it to reconsider before accepting an empty keep list. Accept
    # whatever the 2nd pass returns (empty included). Only retained retry signal.
    if not kept and rows:
        retry_message = user_message + _KEPT_ZERO_RETRY_NOTE.format(n=len(rows))
        retry_result = await agent.run(retry_message, usage_limits=RERANKER_LIMITS)
        # Combine usage across both passes so billing/telemetry stays accurate.
        result = retry_result
        output = retry_result.output
        kept, kept_refs, kept_idx = _assemble_keeps(output, rows)

    ru = result.usage()
    usage = {
        "agent": "reranker",
        "requests": ru.requests,
        "input_tokens": ru.input_tokens,
        "output_tokens": ru.output_tokens,
        "total_tokens": ru.total_tokens,
        "cached_tokens": int(getattr(ru, "cache_read_tokens", 0) or 0),
    }

    # Derive drops by difference: every row the reranker did NOT keep is a drop.
    # The keep-only contract carries no per-drop reasoning, so reasoning="".
    dropped: list[dict] = []
    for idx, row in enumerate(rows):
        if idx in kept_idx:
            continue
        dropped.append({
            "service_id": row.get("id", "") or "",
            "title": row.get("service_name_ar", "") or "",
            "reasoning": "",
            "drop_reason": "llm",
        })

    # Per-query cap: high-relevance ahead of medium, ties broken by score desc.
    high = sorted([r for r in kept if r.relevance == "high"], key=lambda r: -r.score)
    med = sorted([r for r in kept if r.relevance != "high"], key=lambda r: -r.score)
    ordered = high + med
    kept = ordered[:max_keep]
    # Forensic: kept rows pushed out by the per-query cap (reason "cap").
    for r in ordered[max_keep:]:
        dropped.append({
            "service_id": r.service_id or "",
            "title": r.title or "",
            "reasoning": "",
            "drop_reason": "cap",
        })

    logger.info(
        "compliance reranker q=%s: %d rows -> %d kept (max_keep=%d), sufficient=%s",
        query[:40], len(rows), len(kept), max_keep, output.sufficient,
    )

    return output, kept, usage, user_message, result.all_messages_json(), dropped
