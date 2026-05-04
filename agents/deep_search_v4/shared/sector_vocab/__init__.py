"""Shared sector vocabularies for the deep-search executors.

Two distinct vocabularies live here, each indexing a different DB column:

- :mod:`.regulations` — 39 ministry-level Saudi gov sectors, stored on
  ``regulations.sectors[]``. Used by ``reg_search`` and by the v4 planner.
- :mod:`.cases` — 26 commercial-court case categories, stored on
  ``cases.legal_domains[]``. Used by ``case_search``.

The two are **not** mergeable: they index different DBs at different
granularities. The submodule split makes that explicit.

Backward-compat re-exports remain at the original paths
(``agents.deep_search_v4.reg_search.sector_vocab`` and
``agents.deep_search_v4.case_search.sector_vocab``) — old call-sites continue
to work unchanged.
"""
from __future__ import annotations

from . import cases, regulations

__all__ = ["cases", "regulations"]
