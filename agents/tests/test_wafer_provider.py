"""Wafer as a DeepSeek V4.1 Flash 1017 host (2026-11-11).

Wafer heads every V4.1 slot with Baseten V4.1 as backup: the router, both
deep_search expanders and the artifact_analyzer since 2026-11-11, the aggregator
and agent_writer since 2026-11-18. These tests pin the chains, the reasoning
each Wafer cell sends, the cost lookup of Wafer's echo name, and the
missing-key skip.
"""
from __future__ import annotations

import pytest

from agents import model_registry
from agents.model_registry import registry_key_for
from agents.utils import agent_models
from agents.utils.agent_models import (
    AGENT_MODELS,
    apply_override,
    build_fallback_model,
    cost_usd,
    model_of_subagent,
    primary_model_of_slot,
    resolve_chain,
)
from shared.pricing import registry as pricing_registry

WAFER = "wafer-deepseek-v4.1-flash-1017"
BASETEN = "baseten-deepseek-v4.1-flash-1017"
ALIBABA = "deepseek-v4-flash"
OPENROUTER = "or-deepseek-v4-flash"

SYNTHESIS = ("aggregator", "agent_writer")
WAFER_HEADED = (
    "router", "reg_compliance_expander", "case_search_expander", "artifact_analyzer", *SYNTHESIS,
    "extended_planner", "extended_merger",
)


@pytest.mark.parametrize("slot", WAFER_HEADED)
def test_wafer_headed_slots_fall_back_to_baseten_v41(slot: str) -> None:
    assert resolve_chain(AGENT_MODELS[slot]) == [WAFER, BASETEN, ALIBABA, OPENROUTER]
    assert primary_model_of_slot(slot) == WAFER


def test_no_slot_heads_on_baseten_v41() -> None:
    # Baseten V4.1 is a backup cell only (owner, 2026-11-18).
    heads = {slot: primary_model_of_slot(slot) for slot in AGENT_MODELS}
    assert [slot for slot, head in heads.items() if head == BASETEN] == []


@pytest.mark.parametrize("slot", SYNTHESIS)
def test_aggregator_and_writer_keep_max_reasoning(slot: str) -> None:
    assert AGENT_MODELS[slot].reasoning == "max"
    assert agent_models._reasoning_settings(WAFER, "max") == {
        "extra_body": {"reasoning_effort": "max"}
    }


def test_aggregator_usage_bills_at_the_wafer_head() -> None:
    # Aggregator usage entries without a model name are priced by the role's head.
    assert model_of_subagent("aggregator") == WAFER


def test_extended_search_slots_follow_the_planner_and_aggregator_policies() -> None:
    # «بحث موسّع»: the planner on V4.1 default (as tested 11-23), the merger = the aggregator's policy.
    assert AGENT_MODELS["extended_planner"] == AGENT_MODELS["router"]
    assert AGENT_MODELS["extended_planner"].reasoning == "default"
    assert AGENT_MODELS["extended_merger"] == AGENT_MODELS["aggregator"]


def test_reasoning_levels_are_unchanged_by_the_move() -> None:
    assert AGENT_MODELS["router"].reasoning == "default"
    assert AGENT_MODELS["artifact_analyzer"].reasoning == "default"
    assert AGENT_MODELS["reg_compliance_expander"].reasoning == "medium"
    assert AGENT_MODELS["case_search_expander"].reasoning == "medium"


@pytest.mark.parametrize(
    ("level", "effort"), [("default", "high"), ("medium", "high"), ("max", "max")]
)
def test_wafer_always_sends_an_effort(level: str, effort: str) -> None:
    # Wafer runs 1017 with reasoning OFF unless an effort is sent.
    assert agent_models._reasoning_settings(WAFER, level) == {
        "extra_body": {"reasoning_effort": effort}
    }


def test_override_clears_the_pinned_backup() -> None:
    policy = apply_override("router", "alibaba")
    assert policy.head is None and policy.backup is None
    assert WAFER not in resolve_chain(policy) and BASETEN not in resolve_chain(policy)


def test_wafer_echo_resolves_to_the_wafer_key() -> None:
    assert registry_key_for("DeepSeek-V4.1-Flash-1017") == WAFER
    assert registry_key_for("deepseek-ai/DeepSeek-V4.1-Flash-1017") == BASETEN
    # The Baseten bare-name rule still disambiguates the shared 0731 echo.
    assert registry_key_for("deepseek-v4-flash-0731") == ALIBABA
    assert registry_key_for("deepseek-v4-flash-0731", provider="baseten") == "baseten-deepseek-v4-flash-0731"


def test_wafer_calls_bill_at_wafer_prices(monkeypatch) -> None:
    monkeypatch.setitem(pricing_registry._CACHE, WAFER, pricing_registry.ModelPrice(WAFER, 0.13, 0.50, 0.008))
    monkeypatch.setitem(pricing_registry._CACHE, BASETEN, pricing_registry.ModelPrice(BASETEN, 0.30, 1.20, 0.007))
    cost = cost_usd("DeepSeek-V4.1-Flash-1017", 1_000_000, 1_000_000, cached_tokens=0)
    assert cost == pytest.approx(0.63)
    # Expander usage entries carry no model name — priced by the role's head.
    assert model_of_subagent("expander") == WAFER


def _keys(monkeypatch, missing: str | None) -> None:
    monkeypatch.setattr(
        model_registry, "get_api_key", lambda provider: "" if provider == missing else "test-key"
    )


def test_wafer_cell_carries_its_effort(monkeypatch) -> None:
    _keys(monkeypatch, missing=None)
    model = build_fallback_model(AGENT_MODELS["router"])
    head = model.models[0]
    assert head.model_name == "DeepSeek-V4.1-Flash-1017"
    assert head.settings == {"extra_body": {"reasoning_effort": "high"}}
    assert len(model.models) == 4


def test_missing_wafer_key_skips_to_baseten(monkeypatch) -> None:
    _keys(monkeypatch, missing="wafer")
    model = build_fallback_model(AGENT_MODELS["router"])
    assert [m.model_name for m in model.models][:1] == ["deepseek-ai/DeepSeek-V4.1-Flash-1017"]
    assert len(model.models) == 3
