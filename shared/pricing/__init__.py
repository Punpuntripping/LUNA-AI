"""LLM model pricing — Postgres-backed in-memory cache.

Loaded once at FastAPI startup; cost_usd() reads via get_price(model_name).
Single source of truth: the model_pricing table.
"""
from shared.pricing.registry import (
    ModelPrice,
    cached_input_rate,
    get_price,
    load_pricing,
    refresh,
)

__all__ = [
    "ModelPrice",
    "cached_input_rate",
    "get_price",
    "load_pricing",
    "refresh",
]
