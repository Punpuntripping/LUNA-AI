"""Postgres-backed pricing registry.

The model_pricing table is the single source of pricing truth. Rows are loaded
into a module-level dict at FastAPI startup via load_pricing(supabase) and read
on the hot path via get_price(model_name). No per-request DB hit.

Model name normalisation: the FallbackModel chain uses an `or-` prefix to denote
OpenRouter-routed cells (e.g. `or-qwen3.6-plus`). After migration 055 unified
provider pricing, both cells share the same logical model row, so we strip the
prefix before lookup.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from supabase import Client as SupabaseClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelPrice:
    model_name: str
    input_per_1m: float
    output_per_1m: float
    # NULL in DB → fall back to input_per_1m × 0.1 in cached_input_rate().
    cached_input_per_1m: Optional[float] = None


_CACHE: dict[str, ModelPrice] = {}


def _normalise(model_name: str) -> str:
    name = (model_name or "").strip()
    if name.startswith("or-"):
        name = name[3:]
    return name


def load_pricing(supabase: SupabaseClient) -> int:
    """Populate the in-memory cache from model_pricing. Returns row count.

    Idempotent — clears and rebuilds. Safe to call multiple times (startup +
    manual refresh). Failures are logged and leave the previous cache intact.
    """
    try:
        result = (
            supabase.table("model_pricing")
            .select("model_name,prompt_price_per_1m,completion_price_per_1m,cached_input_price_per_1m,is_active")
            .eq("is_active", True)
            .execute()
        )
        rows = getattr(result, "data", None) or []
        new_cache: dict[str, ModelPrice] = {}
        for row in rows:
            name = _normalise(str(row.get("model_name", "")))
            if not name:
                continue
            cached = row.get("cached_input_price_per_1m")
            new_cache[name] = ModelPrice(
                model_name=name,
                input_per_1m=float(row.get("prompt_price_per_1m", 0) or 0),
                output_per_1m=float(row.get("completion_price_per_1m", 0) or 0),
                cached_input_per_1m=float(cached) if cached is not None else None,
            )
        _CACHE.clear()
        _CACHE.update(new_cache)
        logger.info("pricing.load_pricing: loaded %d active models", len(_CACHE))
        return len(_CACHE)
    except Exception as e:
        logger.warning("pricing.load_pricing failed (keeping prior cache of %d): %s", len(_CACHE), e)
        return len(_CACHE)


def refresh(supabase: SupabaseClient) -> int:
    return load_pricing(supabase)


def get_price(model_name: str) -> Optional[ModelPrice]:
    """Lookup price for a model. Returns None if unknown — callers must
    treat None as `cost unknowable, bill zero` rather than raise."""
    if not model_name:
        return None
    return _CACHE.get(_normalise(model_name))


def cached_input_rate(price: ModelPrice) -> float:
    """USD per 1M cached-input tokens. Falls back to input × 0.1 when the
    DB column is NULL (legacy behaviour from agents/utils/agent_models.py)."""
    if price.cached_input_per_1m is not None:
        return price.cached_input_per_1m
    return price.input_per_1m * 0.1
