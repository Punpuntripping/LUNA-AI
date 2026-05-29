"""Backward-compat re-export of the unified sector vocabulary.

The canonical implementation now lives in
:mod:`agents.deep_search_v4.shared.sector_vocab.unified` — one ministry-sector
taxonomy shared by reg_search (``regulations_v2.sectors``), compliance_search
(``services.sectors``), case_search (``cases.legal_domains``) and the v4
planner. This module re-exports the public names so existing call-sites that
import ``agents.deep_search_v4.shared.sector_vocab.regulations`` keep working
unchanged.
"""
from __future__ import annotations

from .unified import (
    SECTOR_ALIASES,
    SECTORS_PROMPT_LIST,
    VALID_SECTORS,
    canonicalize_sectors,
    resolve_sector,
)

__all__ = [
    "VALID_SECTORS",
    "SECTORS_PROMPT_LIST",
    "SECTOR_ALIASES",
    "resolve_sector",
    "canonicalize_sectors",
]
