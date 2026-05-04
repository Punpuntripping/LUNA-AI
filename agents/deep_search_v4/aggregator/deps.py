"""Dependencies for the aggregator agent.

Model selection:
- Primary: qwen3.6-plus (Alibaba DashScope)
- Fallback: gemini-3-flash (Google)

Override via env: LUNA_AGG_PRIMARY_MODEL, LUNA_AGG_FALLBACK_MODEL.
Register aliases in agents/utils/agent_models.py AGENT_MODELS dict:
    "aggregator_v2_primary": "qwen3.6-plus"
    "aggregator_v2_fallback": "gemini-3-flash"
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

PRIMARY_MODEL_DEFAULT = "qwen3.6-plus"
FALLBACK_MODEL_DEFAULT = "gemini-3-flash"

# Standard disclaimer appended to every synthesis. Safe to override per-deployment.
DEFAULT_DISCLAIMER_AR = (
    "هذه المعلومات لأغراض قانونية عامة ولا تُعدّ استشارة قانونية رسمية. "
    "للحصول على رأي ملزم ينبغي مراجعة محامٍ مرخّص."
)

DEFAULT_DETAIL_LEVEL: Literal["low", "medium", "high"] = "medium"
_VALID_DETAIL_LEVELS: set[str] = {"low", "medium", "high"}


@dataclass
class AggregatorDeps:
    """Dependencies injected into the aggregator runner.

    LLM-first: the synthesis itself is pure text-in / text-out. ``supabase`` is
    only used by the post-preprocess source-view stage (see
    ``agents.deep_search_v4.source_viewer``); when ``None``, references ship
    without ``source_view`` payloads (legacy callers + tests).
    """

    primary_model: str = PRIMARY_MODEL_DEFAULT
    fallback_model: str = FALLBACK_MODEL_DEFAULT
    temperature: float = 0.2
    max_retries_primary: int = 1      # attempts on the primary before falling back
    disclaimer_ar: str = DEFAULT_DISCLAIMER_AR
    build_artifact: bool = True
    emit_sse: Callable[[dict], None] | None = None
    logger: Any | None = None         # AggregatorLogger; None = disabled
    detail_level: Literal["low", "medium", "high"] = DEFAULT_DETAIL_LEVEL
    supabase: Any | None = None       # Sync supabase-py client; powers source_view lookup
    # Mutable run-state ------------------------------------------------------
    _events: list[dict] = field(default_factory=list)


def build_aggregator_deps(
    primary_model: str | None = None,
    fallback_model: str | None = None,
    temperature: float | None = None,
    build_artifact: bool = True,
    emit_sse: Callable[[dict], None] | None = None,
    logger: Any | None = None,
    disclaimer_ar: str | None = None,
    detail_level: Literal["low", "medium", "high"] | None = None,
    supabase: Any | None = None,
) -> AggregatorDeps:
    """Build AggregatorDeps with env override + keyword override precedence.

    Precedence: kwargs > env > defaults.
    """
    primary = (
        primary_model
        or os.getenv("LUNA_AGG_PRIMARY_MODEL")
        or PRIMARY_MODEL_DEFAULT
    )
    fallback = (
        fallback_model
        or os.getenv("LUNA_AGG_FALLBACK_MODEL")
        or FALLBACK_MODEL_DEFAULT
    )
    temp = temperature if temperature is not None else 0.2

    raw_level = detail_level or os.getenv("LUNA_AGG_DETAIL_LEVEL") or DEFAULT_DETAIL_LEVEL
    level_norm = str(raw_level).strip().lower()
    if level_norm not in _VALID_DETAIL_LEVELS:
        level_norm = DEFAULT_DETAIL_LEVEL
    level: Literal["low", "medium", "high"] = level_norm  # type: ignore[assignment]

    return AggregatorDeps(
        primary_model=primary,
        fallback_model=fallback,
        temperature=temp,
        disclaimer_ar=disclaimer_ar or DEFAULT_DISCLAIMER_AR,
        build_artifact=build_artifact,
        emit_sse=emit_sse,
        logger=logger,
        detail_level=level,
        supabase=supabase,
    )
