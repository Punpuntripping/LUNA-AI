"""Wire models for the shared BM25 navigation search (Wave B).

Plan: ``.claude/plans/bm25_navigation_search.md`` §5.1
Migration: ``shared/db/migrations/111_bm25_search_index.sql`` §10

⚠ THERE IS NO ``snippet`` FIELD HERE, AND ADDING ONE IS A DESIGN REGRESSION.
D3 (option 2) indexes ONLY text the anon card/doc page already publishes — title,
facets and the always-free lead — so a search response can carry no text a reader
could not already see. That is what deletes the whole gating apparatus from this
path: no ``ts_headline``, no per-hit ``_find_unlock_row``, no ``seo_tier`` check,
no ``<mark>`` sanitizer, no «وُجدت مطابقة داخل النص» flag. A hit is an ADDRESS
(corpus + slug + title), and the card the frontend renders for it keeps using the
static free snippet it already renders today (§5.3).

The moment a ``snippet`` field appears here, someone has to decide per hit whether
the caller may see it — which is the leak surface the design removed. If
highlighting is wanted, do it client-side over the snippet the card already holds:
it is a pure function of ``?q=`` and text already in the payload.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class SearchHit(BaseModel):
    """One ranked result — an address plus what the card needs to key on.

    ``content_id`` is the corpus id (the same key ``seo_item_meta``,
    ``library_items`` and ``library_unlocks`` speak), ``slug`` is the public URL
    segment for the four public corpora, the blog share ``token`` for a blog and
    the ``template_id`` for a template. ``url`` is the in-app path already
    assembled server-side so no caller has to re-derive the corpus→route map (and
    get ``service`` → ``/compliance`` wrong).

    ``score`` is the raw BM25 sum plus the exact-title bonus. It is meaningful for
    ORDERING and for nothing else: it is unnormalised, and two corpora carry
    different IDF tables, so a cross-wing comparison is approximate by
    construction. Do not render it, and do not threshold on it.
    """

    corpus: str
    content_id: str
    slug: Optional[str] = None
    title: str = ""
    facets: dict[str, Any] = Field(default_factory=dict)
    url: Optional[str] = None
    score: float = 0.0


class SearchResponse(BaseModel):
    """A page of hits.

    ``total`` IS NOT A CORPUS COUNT, and the flag beside it says so. ``bm25_search``
    is two-stage: it cuts to ``p_candidates`` (500) by ``ts_rank_cd`` before
    scoring, and ``total_count`` is the count over THAT set. So ``total`` is exact
    only when fewer than ``p_candidates`` documents matched — which
    ``total_is_exact`` reports honestly rather than dressing a ceiling up as a
    total. A UI that prints «٥٠٠ نتيجة» for a common Arabic term would be lying.

    ``corpora`` echoes the wings actually searched (after whitelisting), so a
    caller that asked for something unknown can see it was dropped.
    """

    items: list[SearchHit] = Field(default_factory=list)
    query: str = ""
    corpora: list[str] = Field(default_factory=list)
    page: int = 1
    page_size: int = 20
    total: int = 0
    total_is_exact: bool = True


__all__ = ["SearchHit", "SearchResponse"]
