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
from typing import Literal

from pydantic_ai.models.fallback import FallbackModel

from agents.model_registry import create_model

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
    "router":                     ModelPolicy("tier_1"),
    "reg_search_expander":        ModelPolicy("tier_1"),
    "reg_search_reranker":        ModelPolicy("tier_2"),
    "reg_search_aggregator":      ModelPolicy("tier_1"),
    "case_search_expander":       ModelPolicy("tier_1"),
    "case_search_reranker":       ModelPolicy("tier_2"),
    "case_search_aggregator":     ModelPolicy("tier_1"),
    "compliance_search_expander": ModelPolicy("tier_1"),
    "compliance_search_reranker": ModelPolicy("tier_2"),
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
