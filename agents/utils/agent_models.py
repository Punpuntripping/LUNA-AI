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
control surface. (Embeddings are NOT governed here — see
``agents/utils/embeddings.py``, which stays on Alibaba DashScope.)
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, Union

from pydantic_ai.models.fallback import FallbackModel

from agents.model_registry import create_model, get_model_config
from shared import pricing

Provider = Literal["alibaba", "openrouter"]
Tier = Literal["tier_1", "tier_2"]
Family = Literal["qwen", "deepseek"]
# Reasoning intent. "default" = leave thinking to the provider default (what
# every agent used historically). "medium" = explicit mid-effort thinking
# (cheaper/faster than max; used for slots that need real reasoning without
# ceiling latency). "max" = push the cell to its provider-specific reasoning
# ceiling (see _reasoning_settings); used for heavy-synthesis slots.
Reasoning = Literal["default", "medium", "max"]

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
    reasoning: Reasoning = "default"


#: The single cross-provider safety net for every chain. One cheap, fast,
#: reliable OpenRouter model — used instead of walking the OpenRouter family
#: matrix (the old 2×2 chain's OR cells caused tool_choice/availability
#: failures and added no value over a single dependable fallback).
_CROSS_PROVIDER_FALLBACK: dict[Provider, str] = {
    "alibaba": "or-deepseek-v4-flash",   # normal case: Alibaba primary → OR net
    "openrouter": "deepseek-v4-flash",   # override: OR primary → Alibaba net
}


def resolve_chain(policy: ModelPolicy) -> list[str]:
    """Return the ordered ``model_registry`` keys for a policy's fallback chain.

    Two same-provider cells (primary family, then the other family) for local
    resilience, then a SINGLE cross-provider net. For the normal case (Alibaba
    primary — every agent today) that net is always ``or-deepseek-v4-flash``,
    regardless of the slot's tier or family. Replaces the old 4-cell 2×2 chain.
    """
    fb_family = _OTHER_FAMILY[policy.primary]
    cells = TIERS[policy.tier]
    return [
        cells[policy.primary][policy.provider],
        cells[fb_family][policy.provider],
        _CROSS_PROVIDER_FALLBACK[policy.provider],
    ]


# Qwen-family thinking ceiling applied when reasoning="max". Qwen (and Kimi) are
# the only DashScope families that honor ``thinking_budget``, and it is a CEILING
# (max tokens the model MAY spend thinking), never a floor — no provider exposes
# a reasoning floor. 24k gives ample room above a ~10k target without risking the
# model's max-CoT length. DeepSeek-on-Alibaba ignores thinking_budget and instead
# takes ``reasoning_effort`` (high|max; "xhigh" aliases to "max").
_MAX_THINKING_BUDGET = 24_000
# Mid-effort Qwen thinking ceiling for reasoning="medium" — enough for grammar/
# agreement reasoning without max-effort latency.
_MEDIUM_THINKING_BUDGET = 8_000


def _reasoning_settings(key: str, reasoning: Reasoning) -> dict | None:
    """Per-cell ``model_settings`` that set a FallbackModel cell's reasoning level.

    Each provider/family exposes a DIFFERENT reasoning control, and a single
    agent-level ``model_settings`` dict cannot serve all four cells — so the tier
    system bakes the right ``extra_body`` onto each cell by resolved registry key
    (value depends on the level — max vs medium):

      * DeepSeek V4 on Alibaba  -> ``reasoning_effort="max"|"medium"`` + ``thinking.type``
        (OpenAI-compatible DashScope; "xhigh" aliases to "max" server-side).
      * Qwen on Alibaba         -> ``enable_thinking`` + ``thinking_budget``
        (ceiling: 24k for max, 8k for medium).
      * Any family on OpenRouter -> ``reasoning.effort="xhigh"|"medium"``.

    Returns ``None`` for ``reasoning="default"`` so non-flagged slots keep their
    historical provider-default thinking behavior (no settings attached).
    """
    if reasoning == "default":
        return None
    is_max = reasoning == "max"
    cfg = get_model_config(key)
    if cfg.provider == "openrouter":
        return {"extra_body": {"reasoning": {"effort": "xhigh" if is_max else "medium"}}}
    # Alibaba DashScope (OpenAI-compatible).
    if "deepseek" in key:
        return {
            "extra_body": {
                "thinking": {"type": "enabled"},
                "reasoning_effort": "max" if is_max else "medium",
            }
        }
    return {
        "extra_body": {
            "enable_thinking": True,
            "thinking_budget": _MAX_THINKING_BUDGET if is_max else _MEDIUM_THINKING_BUDGET,
        }
    }


def build_fallback_model(policy: ModelPolicy) -> FallbackModel:
    """Build a Pydantic AI ``FallbackModel`` for a policy's 4-step chain.

    When ``policy.reasoning == "max"`` each cell is created with provider-specific
    reasoning settings baked on (see :func:`_reasoning_settings`); pydantic_ai
    merges those per-cell defaults under any agent/run-level ``model_settings``.
    """
    models = [
        create_model(key, model_settings=_reasoning_settings(key, policy.reasoning))
        for key in resolve_chain(policy)
    ]
    return FallbackModel(models[0], *models[1:])


# slot -> policy -- THE per-agent control surface. Edit tiers/providers here.
#
# EXPERIMENT (2026-05-28): every former tier_1 qwen3.6-plus slot is temporarily
# flipped to deepseek-v4-flash (tier_2, deepseek-primary) to A/B the cheap/fast
# model across the pipeline. The router (flipped 2026-06-14) now runs on the same
# _FLASH policy too — nothing is left on tier_1.
# Rerankers stay tier_2 qwen3.5-flash (never were 3.6-plus). Revert via git to
# restore the all-tier_1 baseline.
_FLASH = ModelPolicy("tier_2", primary="deepseek")  # deepseek-v4-flash head
# Same flash head, but reasoning pushed to the provider ceiling (deepseek
# reasoning_effort=max / qwen+openrouter equivalents). Used for the two heavy-
# synthesis slots — the deep_search aggregator and the writing_executor.
_FLASH_MAX = ModelPolicy("tier_2", primary="deepseek", reasoning="max")
# Flash head with mid-effort reasoning — used by the artifact_editor slot.
_FLASH_MEDIUM = ModelPolicy("tier_2", primary="deepseek", reasoning="medium")

AGENT_MODELS: dict[str, Union[ModelPolicy, FallbackModel]] = {
    "planner_decider":            _FLASH,
    "planner_responder":          _FLASH,
    "aggregator":                 _FLASH_MAX,
    "agent_writer":               _FLASH_MAX,
    # Layer-2 Major planner that sits in front of writing_executor.
    # Talks to the user (ask_user, present_plan_for_approval), inspects prior-WI
    # content on demand via unfold_workspace_item, and hands a WriterPackage to
    # the writing executor at the end. Multi-turn loop per user turn (capped at
    # 3 present_plan_for_approval cycles). Output is a discriminated
    # list[PlannerDecision | DeferredToolRequests]; same shape as the
    # deep_search planner. See .claude/plans/writer_planner.md.
    "writer_planner_decider":     _FLASH,
    "router":                     _FLASH,  # deepseek-v4-flash → qwen3.5-flash → or-deepseek-v4-flash
    # The three deep_search expanders run at fixed medium reasoning (baked per
    # provider/family by _reasoning_settings — deepseek reasoning_effort=medium,
    # qwen enable_thinking+8k budget, openrouter reasoning.effort=medium). The
    # planner no longer varies expander effort.
    "reg_search_expander":        _FLASH_MEDIUM,
    "reg_search_reranker":        ModelPolicy("tier_2"),
    "reg_search_aggregator":      _FLASH,
    "case_search_expander":       _FLASH_MEDIUM,
    "case_search_reranker":       ModelPolicy("tier_2"),
    "case_search_aggregator":     _FLASH,
    "compliance_search_expander": _FLASH_MEDIUM,
    "compliance_search_reranker": ModelPolicy("tier_2"),
    # Tier_2 DeepSeek-primary with reasoning enabled — runs once per published
    # workspace item to produce an agent-facing coverage summary.
    "artifact_summarizer":        ModelPolicy("tier_2", primary="deepseek"),
    # Tier_2 DeepSeek-primary — Layer-4 librarian that verdicts workspace_items
    # against a caller's query. Two LLM calls max per analyze() invocation
    # (one per family: refs vs meta). Short structured outputs — reasoning
    # mode is OFF (see .claude/plans/item_analyzer_v2.md §6).
    "item_analyzer":              ModelPolicy("tier_2", primary="deepseek"),
    # Tier_2 DeepSeek-primary — Layer-4 transformer that cleans ONE raw legal
    # document into a reusable, placeholder'd, uniquely-titled template saved to
    # user_templates. One LLM call per ingestion; deepseek-flash can emit the
    # two-field output as text → a TextOutput JSON salvager avoids the retry.
    # See .claude/plans/writer_planner_user_templates.md §Wave D.
    "template_ingester":          ModelPolicy("tier_2", primary="deepseek"),
    # Tier_2 DeepSeek-primary — runs once per deep_search invocation, in parallel
    # with the expanders, to pick the 2-5 sector AND-filter. Replaces the old
    # planner_decider.sectors output (decider had no visibility into per-sector
    # corpus contents — diagnosed in conv faa3b71e). DeepSeek-flash is fast and
    # cheap; the call is short (two-field structured output).
    "sector_picker":              ModelPolicy("tier_2", primary="deepseek"),
    # Layer-3 task agent invoked as a router tool (edit_artifact) — one flash
    # call over a full injected artifact emitting a batched surgical-edit tool
    # call. reasoning=medium gives the Arabic grammar-agreement reasoning the
    # edits need without max-effort latency. See .claude/plans/artifact_editor.md.
    "artifact_editor":            _FLASH_MEDIUM,
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
    - agents are locked to their tier. Slots that store a pre-built
    ``FallbackModel`` (e.g. ``router``) do not support overrides.
    """
    base = AGENT_MODELS[slot]
    if isinstance(base, FallbackModel):
        if override is not None:
            raise ValueError(
                f"Slot '{slot}' uses a pre-built FallbackModel and does not "
                f"support model overrides."
            )
        return base
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
# Cost is tracked per resolved model. Prices live in the model_pricing table
# (migration 055) and are loaded into shared.pricing at FastAPI startup. The
# in-code model_registry remains the source of truth for cell config (provider,
# settings) but is no longer consulted for pricing.

# A deep_search sub-agent's role name (the ``agent`` field on inner_usage
# entries) → its primary model on the happy path. Mirrors AGENT_MODELS:
#   expander       → AGENT_MODELS["*_search_expander"]      = _FLASH (deepseek-v4-flash)
#   reranker       → AGENT_MODELS["*_search_reranker"]      = ModelPolicy("tier_2") (qwen3.5-flash)
#   aggregator     → AGENT_MODELS["*_search_aggregator"]    = _FLASH (deepseek-v4-flash)
#   sector_picker  → AGENT_MODELS["sector_picker"]          = deepseek primary tier_2
# Unknown roles fall back to _DEFAULT_MODEL (a conservative tier_1 estimate; the
# router no longer runs on it — it's _FLASH now). When a FallbackModel cell other
# than the primary fires, the inner_usage entry's ``model`` field (when set by the
# caller) overrides this lookup.
_SUBAGENT_MODEL: dict[str, str] = {
    "expander":      "deepseek-v4-flash",
    "reranker":      "qwen3.5-flash",
    "aggregator":    "deepseek-v4-flash",
    "sector_picker": "deepseek-v4-flash",
}

_DEFAULT_MODEL = "qwen3.7-plus"  # tier_1 qwen primary; used when no slot match


def model_of_subagent(agent: str) -> str:
    """Return the primary model a deep_search sub-agent role runs on."""
    return _SUBAGENT_MODEL.get(agent, _DEFAULT_MODEL)


def cost_usd(
    model_name: str | None,
    input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int = 0,
    cached_tokens: int = 0,
) -> float:
    """USD cost of a single LLM call billed at ``model_name`` rates.

    Reasoning tokens bill at the output rate — providers count them as
    completion tokens, and pydantic_ai's ``output_tokens`` does NOT include
    them (they live in ``usage.details['reasoning_tokens']``). ``cached_tokens``
    is a subset of ``input_tokens``; when prompt caching is active the cached
    portion bills at the model's ``cached_input_price_per_1m`` (or input × 0.1
    when that column is NULL — see shared.pricing.cached_input_rate).

    Returns 0.0 when the model is unknown to the pricing registry.
    """
    price = pricing.get_price(model_name) if model_name else None
    if price is None:
        return 0.0
    cached = max(int(cached_tokens or 0), 0)
    billable_in = max(int(input_tokens or 0) - cached, 0)
    billable_out = int(output_tokens or 0) + int(reasoning_tokens or 0)
    cached_rate = pricing.cached_input_rate(price)
    return (
        billable_in * price.input_per_1m
        + cached * cached_rate
        + billable_out * price.output_per_1m
    ) / 1_000_000


def usage_by_model(inner_usage: list[dict] | None) -> dict[str, dict[str, int]]:
    """Fold pydantic_ai usage entries into per-model token totals.

    Each entry should carry ``agent`` (sub-agent role) and may carry ``model``
    (the actually-fired model from FallbackModel); falls back to the role's
    primary model when ``model`` is absent. Returns ``{model_name: {"input":
    int, "output": int, "reasoning": int, "cached": int}}`` — the shape stored
    under ``per_phase_stats[phase]["per_model"]``.
    """
    out: dict[str, dict[str, int]] = {}
    for u in inner_usage or []:
        if not isinstance(u, dict):
            continue
        model = str(u.get("model") or "").strip() or model_of_subagent(
            str(u.get("agent", ""))
        )
        slot = out.setdefault(
            model, {"input": 0, "output": 0, "reasoning": 0, "cached": 0}
        )
        slot["input"] += int(u.get("input_tokens", 0) or 0)
        slot["output"] += int(u.get("output_tokens", 0) or 0)
        slot["cached"] += int(u.get("cached_tokens", 0) or 0)
        details = u.get("details") or {}
        slot["reasoning"] += int(details.get("reasoning_tokens", 0) or 0)
    return out


def estimate_run_cost(
    per_phase_stats: dict | None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    tokens_reasoning: int | None = None,
    tokens_cached: int | None = None,
    model_used: str | None = None,
) -> tuple[float, int]:
    """Return ``(cost_usd, reasoning_tokens_total)`` for one agent run.

    Prefers a per-model breakdown — multi-model runs (deep_search phases, the
    new memory analyzers) carry ``per_model`` dicts under ``per_phase_stats``.
    Falls back to flat ``model_used`` pricing on the aggregate token counts for
    single-model agents (writer, memory, router). Never raises; returns
    ``(0.0, 0)`` on malformed input or when the model is unknown.
    """
    try:
        total_cost = 0.0
        total_reasoning = 0
        found = False
        for phase in (per_phase_stats or {}).values():
            if not isinstance(phase, dict):
                continue
            per_model = phase.get("per_model")
            if not isinstance(per_model, dict):
                continue
            found = True
            for model_name, toks in per_model.items():
                if not isinstance(toks, dict):
                    continue
                r = int(toks.get("reasoning", 0) or 0)
                total_reasoning += r
                total_cost += cost_usd(
                    str(model_name),
                    int(toks.get("input", 0) or 0),
                    int(toks.get("output", 0) or 0),
                    r,
                    int(toks.get("cached", 0) or 0),
                )
        if found:
            return round(total_cost, 6), total_reasoning
        # Single-model agent: bill aggregate tokens at model_used.
        r = int(tokens_reasoning or 0)
        cost = cost_usd(
            model_used,
            int(tokens_in or 0),
            int(tokens_out or 0),
            r,
            int(tokens_cached or 0),
        )
        return round(cost, 6), r
    except Exception:
        return 0.0, 0
