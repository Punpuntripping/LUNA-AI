"""Central tier-based model assignment + provider fallback for all agents.

Every agent resolves its model through ``get_agent_model(slot)``, which returns
a Pydantic AI ``FallbackModel``. The chain walks both model families on the
primary provider, then both families on the fallback provider::

    (primary provider, primary family)
    (primary provider, fallback family)
    (fallback provider, primary family)
    (fallback provider, fallback family)

``FallbackModel`` advances on ``ModelAPIError`` only (4xx/5xx API errors) -
output-validation and tool-retry failures do NOT trigger provider fallback.

There are two providers (Alibaba primary, OpenRouter fallback) and two model
families per tier (Qwen primary, DeepSeek fallback). Each agent declares an
intent via ``ModelPolicy``; ``AGENT_MODELS`` below is the single editable
control surface.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Literal

from pydantic_ai.models.fallback import FallbackModel

from agents.model_registry import create_model, get_model_config

Provider = Literal["alibaba", "openrouter"]
Tier = Literal["tier_1", "tier_2"]
Family = Literal["qwen", "deepseek"]

# tier -> family -> provider -> model_registry key
TIERS: dict[str, dict[str, dict[str, str]]] = {
    "tier_1": {
        "qwen":     {"alibaba": "qwen3.6-plus",    "openrouter": "or-qwen3.6-plus"},
        "deepseek": {"alibaba": "deepseek-v4-pro", "openrouter": "or-deepseek-v4-pro"},
    },
    "tier_2": {
        "qwen":     {"alibaba": "qwen3.5-flash",     "openrouter": "or-qwen3.5-flash"},
        "deepseek": {"alibaba": "deepseek-v4-flash", "openrouter": "or-deepseek-v4-flash"},
    },
}

_OTHER_PROVIDER: dict[str, Provider] = {"alibaba": "openrouter", "openrouter": "alibaba"}
_OTHER_FAMILY: dict[str, Family] = {"qwen": "deepseek", "deepseek": "qwen"}

# Valid CLI ``--model`` override tokens (see :func:`apply_override`). Two name a
# model family, two name a provider; all keep the slot's tier fixed.
OVERRIDE_TOKENS: tuple[str, ...] = ("qwen", "deepseek", "alibaba", "openrouter")


@dataclass(frozen=True)
class ModelPolicy:
    """An agent's model intent.

    ``provider`` and ``primary`` name the head of the fallback chain; the
    fallback provider and family are derived automatically.
    """

    tier: Tier
    provider: Provider = "alibaba"
    primary: Family = "qwen"


def resolve_chain(policy: ModelPolicy) -> list[str]:
    """Return the ordered ``model_registry`` keys for a policy's 4-step chain."""
    fb_provider = _OTHER_PROVIDER[policy.provider]
    fb_family = _OTHER_FAMILY[policy.primary]
    cells = TIERS[policy.tier]
    return [
        cells[policy.primary][policy.provider],
        cells[fb_family][policy.provider],
        cells[policy.primary][fb_provider],
        cells[fb_family][fb_provider],
    ]


def build_fallback_model(policy: ModelPolicy) -> FallbackModel:
    """Build a Pydantic AI ``FallbackModel`` for a policy's 4-step chain."""
    models = [create_model(key) for key in resolve_chain(policy)]
    return FallbackModel(models[0], *models[1:])


# slot -> policy -- THE per-agent control surface. Edit tiers/providers here.
# For now only the three rerankers use tier_2; everything else is tier_1.
AGENT_MODELS: dict[str, ModelPolicy] = {
    "planner_decider":            ModelPolicy("tier_1"),
    "planner_responder":          ModelPolicy("tier_1"),
    "aggregator":                 ModelPolicy("tier_1"),
    "agent_writer":               ModelPolicy("tier_1"),
    # Tier_1 — Layer-2 Major planner that sits in front of writing_executor.
    # Talks to the user (ask_user, present_plan_for_approval), calls
    # item_analyzer for context distillation when prior-WI scope is wide, and
    # hands a WriterPackage to the writing executor at the end. Multi-turn loop
    # per user turn (capped at 3 present_plan_for_approval cycles). Output is a
    # discriminated list[PlannerDecision | DeferredToolRequests]; same shape as
    # the deep_search planner. See .claude/plans/writer_planner.md.
    "writer_planner_decider":     ModelPolicy("tier_1"),
    "router":                     ModelPolicy("tier_1"),
    "reg_search_expander":        ModelPolicy("tier_1"),
    "reg_search_reranker":        ModelPolicy("tier_2"),
    "reg_search_aggregator":      ModelPolicy("tier_1"),
    "case_search_expander":       ModelPolicy("tier_1"),
    "case_search_reranker":       ModelPolicy("tier_2"),
    "case_search_aggregator":     ModelPolicy("tier_1"),
    "compliance_search_expander": ModelPolicy("tier_1"),
    "compliance_search_reranker": ModelPolicy("tier_2"),
    # Tier_2 DeepSeek-primary with reasoning enabled — runs once per published
    # workspace item to produce an agent-facing coverage summary.
    "artifact_summarizer":        ModelPolicy("tier_2", primary="deepseek"),
    # Tier_2 DeepSeek-primary — Layer-4 librarian that verdicts workspace_items
    # against a caller's query. Two LLM calls max per analyze() invocation
    # (one per family: refs vs meta). Short structured outputs — reasoning
    # mode is OFF (see .claude/plans/item_analyzer_v2.md §6).
    "item_analyzer":              ModelPolicy("tier_2", primary="deepseek"),
    # Tier_2 DeepSeek-primary — runs once per deep_search invocation, in parallel
    # with the expanders, to pick the 2-5 sector AND-filter. Replaces the old
    # planner_decider.sectors output (decider had no visibility into per-sector
    # corpus contents — diagnosed in conv faa3b71e). DeepSeek-flash is fast and
    # cheap; the call is short (two-field structured output).
    "sector_picker":              ModelPolicy("tier_2", primary="deepseek"),
}


def apply_override(slot: str, token: str | None) -> ModelPolicy:
    """Apply a CLI override token to a slot's base policy, staying within tier.

    ``token`` may name a family (``qwen``/``deepseek``) or a provider
    (``alibaba``/``openrouter``); it tweaks the head of the chain only. The tier
    is fixed by the slot - an agent can only use models within its tier.
    """
    base = AGENT_MODELS[slot]
    if not token:
        return base
    token = token.strip().lower()
    if token in ("qwen", "deepseek"):
        return replace(base, primary=token)  # type: ignore[arg-type]
    if token in ("alibaba", "openrouter"):
        return replace(base, provider=token)  # type: ignore[arg-type]
    raise ValueError(
        f"Invalid model override '{token}'. Expected one of: "
        f"qwen, deepseek, alibaba, openrouter (the agent is locked to its tier)."
    )


def get_agent_model(
    slot: str, override: ModelPolicy | str | None = None
) -> FallbackModel:
    """Resolve a slot to a ``FallbackModel``.

    ``override`` may be a :class:`ModelPolicy` or a CLI token string. A
    ``ModelPolicy`` whose tier differs from the slot's declared tier is rejected
    - agents are locked to their tier.
    """
    base = AGENT_MODELS[slot]
    if override is None:
        policy = base
    elif isinstance(override, str):
        policy = apply_override(slot, override)
    else:
        if override.tier != base.tier:
            raise ValueError(
                f"Override tier '{override.tier}' does not match slot '{slot}' "
                f"tier '{base.tier}' - agents are locked to their tier."
            )
        policy = override
    return build_fallback_model(policy)


# =============================================================================
# COST ACCOUNTING
# =============================================================================
# Cost is tracked per *tier*, not per resolved model. Within a tier the qwen
# and deepseek families (and Alibaba vs OpenRouter) price out roughly equal,
# and ``FallbackModel`` only swaps off the qwen/Alibaba primary on a 4xx/5xx
# API error. Billing every call at the tier's qwen/Alibaba primary rate is
# therefore a correct *conservative ceiling* — the deepseek fallback and
# OpenRouter are both cheaper. The registry is the single source of pricing
# truth; tier rates are derived from it (no duplicated price tables).

# A deep_search sub-agent's role name (the ``agent`` field on inner_usage
# entries) → its tier. Mirrors AGENT_MODELS: expanders/aggregators are tier_1,
# rerankers tier_2. Unknown roles default to tier_1.
_SUBAGENT_TIER: dict[str, Tier] = {
    "expander": "tier_1",
    "reranker": "tier_2",
    "aggregator": "tier_1",
    "sector_picker": "tier_2",
}


def tier_of_subagent(agent: str) -> Tier:
    """Return the tier a deep_search sub-agent role runs on."""
    return _SUBAGENT_TIER.get(agent, "tier_1")


@lru_cache(maxsize=None)
def tier_rate(tier: Tier) -> tuple[float, float]:
    """``(input_price, output_price)`` per 1M tokens for a tier.

    Read from the registry entry for the tier's qwen/Alibaba primary model —
    keeping ``model_registry`` the sole source of pricing truth.
    """
    key = TIERS[tier]["qwen"]["alibaba"]
    cfg = get_model_config(key)
    return (cfg.input_price or 0.0, cfg.output_price or 0.0)


def cost_usd(
    tier: str,
    input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int = 0,
    cached_tokens: int = 0,
) -> float:
    """USD cost of a single LLM call billed at ``tier`` rates.

    Reasoning tokens bill at the output rate — providers count them as
    completion tokens, and pydantic_ai's ``output_tokens`` does NOT include
    them (they live in ``usage.details['reasoning_tokens']``). ``cached_tokens``
    is a subset of ``input_tokens``; pass it when prompt caching is active to
    apply the discounted (~10x cheaper) cached-input rate.
    """
    in_rate, out_rate = tier_rate(tier if tier in TIERS else "tier_1")  # type: ignore[arg-type]
    cached = max(int(cached_tokens or 0), 0)
    billable_in = max(int(input_tokens or 0) - cached, 0)
    billable_out = int(output_tokens or 0) + int(reasoning_tokens or 0)
    return (
        billable_in * in_rate
        + cached * in_rate * 0.1
        + billable_out * out_rate
    ) / 1_000_000


def usage_by_tier(inner_usage: list[dict] | None) -> dict[str, dict[str, int]]:
    """Fold pydantic_ai usage entries into per-tier token totals.

    Each entry should carry ``agent`` (sub-agent role), ``input_tokens``,
    ``output_tokens`` and optionally ``details.reasoning_tokens``. Returns
    ``{tier: {"input": int, "output": int, "reasoning": int}}`` — the shape
    stored under ``per_phase_stats[phase]["per_tier"]``.
    """
    out: dict[str, dict[str, int]] = {}
    for u in inner_usage or []:
        if not isinstance(u, dict):
            continue
        tier = tier_of_subagent(str(u.get("agent", "")))
        slot = out.setdefault(tier, {"input": 0, "output": 0, "reasoning": 0})
        slot["input"] += int(u.get("input_tokens", 0) or 0)
        slot["output"] += int(u.get("output_tokens", 0) or 0)
        details = u.get("details") or {}
        slot["reasoning"] += int(details.get("reasoning_tokens", 0) or 0)
    return out


def estimate_run_cost(
    per_phase_stats: dict | None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    tokens_reasoning: int | None = None,
) -> tuple[float, int]:
    """Return ``(cost_usd, reasoning_tokens_total)`` for one agent run.

    Prefers a per-tier breakdown — deep_search phases each carry a ``per_tier``
    dict under ``per_phase_stats``. Falls back to flat tier_1 pricing on the
    aggregate token counts for single-model agents (writer, memory, router).
    Never raises; returns ``(0.0, 0)`` on malformed input.
    """
    try:
        total_cost = 0.0
        total_reasoning = 0
        found = False
        for phase in (per_phase_stats or {}).values():
            if not isinstance(phase, dict):
                continue
            per_tier = phase.get("per_tier")
            if not isinstance(per_tier, dict):
                continue
            found = True
            for tier, toks in per_tier.items():
                if not isinstance(toks, dict):
                    continue
                r = int(toks.get("reasoning", 0) or 0)
                total_reasoning += r
                total_cost += cost_usd(
                    str(tier),
                    int(toks.get("input", 0) or 0),
                    int(toks.get("output", 0) or 0),
                    r,
                )
        if found:
            return round(total_cost, 6), total_reasoning
        # Single-model agent: bill aggregate tokens at tier_1.
        r = int(tokens_reasoning or 0)
        cost = cost_usd("tier_1", int(tokens_in or 0), int(tokens_out or 0), r)
        return round(cost, 6), r
    except Exception:
        return 0.0, 0
