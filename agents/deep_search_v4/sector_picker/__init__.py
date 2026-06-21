"""Sector-picker agent — Layer 3 Task, tier_2 deepseek-flash primary.

Replaces the planner_decider's old ``sectors`` output (diagnosed regression in
conv ``faa3b71e``: flat 38-name list gave the decider no way to distinguish
adjacent sector vocabularies). The picker fires in parallel with the
expanders; the executor filter steps await its result before passing chunks
to the reranker.

Public surface:
    run_sector_picker(query, mode, ...) -> list[str] | None

Returns ``None`` on every failure mode — picker is critical-path but the
filter it produces is a coarse pre-filter, so unfiltered fallback is safe.
"""
from .consume import SECTOR_PICKER_GRACE_S, resolve_sector_filter
from .deps import Mode, SectorPickerDeps
from .models import MAX_SECTORS, MIN_SECTORS, SectorPickerOutput
from .runner import SECTOR_PICKER_TIMEOUT_S, run_sector_picker

__all__ = [
    "MAX_SECTORS",
    "MIN_SECTORS",
    "Mode",
    "SECTOR_PICKER_GRACE_S",
    "SECTOR_PICKER_TIMEOUT_S",
    "SectorPickerDeps",
    "SectorPickerOutput",
    "resolve_sector_filter",
    "run_sector_picker",
]
