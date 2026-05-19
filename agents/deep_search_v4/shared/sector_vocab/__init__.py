"""Shared sector vocabulary for the deep-search executors.

One unified ministry-sector taxonomy is shared across all three executor
corpora and the v4 planner — see :mod:`.unified` for the canonical 38-entry
``VALID_SECTORS`` list, ``SECTORS_PROMPT_LIST`` and ``canonicalize_sectors``.

Verified against the live DB (2026-05-17): ``regulations_v2.sectors`` and
``services.sectors`` carry the identical 38-entry set; ``cases.legal_domains``
is a 36-entry subset (two sectors have zero cases).

The :mod:`.regulations` and :mod:`.cases` submodules — plus the legacy paths
``agents.deep_search_v4.reg_search.sector_vocab`` and
``agents.deep_search_v4.case_search.sector_vocab`` — are thin re-exports of
:mod:`.unified`, kept only for import-path backward compatibility.
"""
from __future__ import annotations

from . import cases, regulations, unified

__all__ = ["cases", "regulations", "unified"]
