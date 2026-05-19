"""Backward-compat re-export of the unified sector vocabulary.

Canonical source: :mod:`agents.deep_search_v4.shared.sector_vocab.unified` —
one ministry-sector taxonomy shared by all three executors and the v4 planner.
This module re-exports the public names so existing call-sites importing
``agents.deep_search_v4.reg_search.sector_vocab`` keep working unchanged.
"""
from __future__ import annotations

from agents.deep_search_v4.shared.sector_vocab.unified import (
    SECTORS_PROMPT_LIST,
    VALID_SECTORS,
    canonicalize_sectors,
)

__all__ = ["VALID_SECTORS", "SECTORS_PROMPT_LIST", "canonicalize_sectors"]
