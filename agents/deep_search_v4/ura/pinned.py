"""Pinned references — a مادة the aggregator must see when a principle reaches it.

Plan ``official_principles_refs.md`` D6: whenever at least one official judicial
principle of entity **18329** (the «المبادئ والقرارات…» book — المحكمة العليا
and the bodies before it) survives into the URA, **المادة 40 من اللائحة
التنفيذية لطرق الاعتراض على الأحكام** rides along as a citable reference. It is
what turns a principle from background into an objection ground: a ruling that
contradicts a principle issued by المحكمة العليا (or one its circuits adopted)
may be objected to as a violation of the law. The aggregator's prompt decides
whether the user's matter makes that relevant; this module only makes sure the
text is there to cite.

Contract:
- Runs AFTER ``enrich_ura`` (the principles' ``entity_ref`` is only certain
  once hydrated — a case-side shell arrives without it) and only behind
  ``OFFICIAL_PRINCIPLES_ENABLED`` (the orchestrator's gate, not this module's).
- The article is fetched by its ``articles_v2.id``; its text is never
  hardcoded. Its chunk (``17642_reg_068_chunk_006``) owns مواد 40–46, so the pin
  is the ARTICLE ref, never the chunk.
- Appended at the END of ``ura.medium_results`` with relevance ``medium`` — it
  never takes ``[1]`` — and ``appears_in_sub_queries`` = the union of the
  triggering principles', so coverage checks see it where they see them.
- Fail-soft: a failed or empty fetch logs and skips the pin. Never raises.
- Never pinned for other entities (e.g. 17486, the administrative court).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from agents.deep_search_v4.ura.enrich import _doc_type_label
from agents.deep_search_v4.ura.schema import ArticleURAResult, PrincipleURAResult

logger = logging.getLogger(__name__)

#: ``articles_v2.id`` of المادة 40 من اللائحة التنفيذية لطرق الاعتراض على
#: الأحكام (regulation ``b61a5150-e9a1-47a5-acd3-1a02f1ba084d``, in force).
ARTICLE_40_ID = "6a1c7915-1473-54db-9fb6-3b6b9d4e61a9"
ARTICLE_40_REF_ID = f"article:{ARTICLE_40_ID}"

#: The only corpus whose principles trigger the pin.
ARTICLE_40_ENTITY_REF = "18329"

#: The columns simple_search's article path reads — the same two reads
#: (``searcher._enrich_articles`` + ``_regulation_display``), so the pinned
#: card is labelled from exactly the fields a simple_search مادة card is.
_ARTICLE_COLUMNS = "id, regulation_id, article_number, content"
_REGULATION_COLUMNS = "id, clean_title, title, doc_type_raw, landing_url"


def _fetch_one(supabase, table: str, columns: str, row_id: str) -> dict | None:
    """One row by id, or ``None`` (miss or error — logged)."""
    try:
        resp = (
            supabase.table(table)
            .select(columns)
            .eq("id", row_id)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        return rows[0] if rows else None
    except Exception as e:  # best-effort — the pin is optional
        logger.warning("pinned: fetch %s %s failed: %s", table, row_id, e)
        return None


def fetch_article_result(
    supabase,
    article_id: str,
    *,
    relevance: str = "medium",
    reasoning: str = "",
    appears_in_sub_queries: list[int] | None = None,
) -> ArticleURAResult | None:
    """``articles_v2.id`` → a fully hydrated :class:`ArticleURAResult`, or ``None``.

    SYNC (two round-trips: the مادة, then its parent نظام — ``articles_v2`` is a
    VIEW, so there is no FK to embed). ``None`` when the article is gone or its
    body is empty: an empty مادة is not worth a citation slot.
    """
    article = _fetch_one(supabase, "articles_v2", _ARTICLE_COLUMNS, article_id)
    if not article or not str(article.get("content") or "").strip():
        return None
    reg: dict[str, Any] = {}
    reg_id = str(article.get("regulation_id") or "").strip()
    if reg_id:
        reg = _fetch_one(supabase, "regulations_v2", _REGULATION_COLUMNS, reg_id) or {}
    return ArticleURAResult(
        ref_id=f"article:{article_id}",
        relevance=relevance if relevance in ("high", "medium") else "medium",
        reasoning=reasoning,
        appears_in_sub_queries=sorted(set(appears_in_sub_queries or [])),
        article_id=str(article.get("id") or article_id),
        regulation_id=reg_id,
        article_number=str(article.get("article_number") or "").strip(),
        reg_title=str(reg.get("clean_title") or reg.get("title") or "").strip(),
        content=str(article.get("content") or "").strip(),
        landing_url=str(reg.get("landing_url") or "").strip(),
        doc_type=_doc_type_label(reg.get("doc_type_raw")),
    )


async def pin_article_40(supabase, ura) -> bool:
    """Append Article 40 to ``ura.medium_results`` when a 18329 principle is kept.

    Returns ``True`` when the pin was appended, ``False`` otherwise (no trigger,
    already present, or the fetch failed / came back empty). Never raises.
    """
    if ura is None:
        return False
    try:
        kept = list(ura.high_results or []) + list(ura.medium_results or [])
        triggers = [
            r for r in kept
            if isinstance(r, PrincipleURAResult)
            and (r.entity_ref or "").strip() == ARTICLE_40_ENTITY_REF
        ]
        if not triggers:
            return False
        if any(getattr(r, "ref_id", "") == ARTICLE_40_REF_ID for r in kept):
            return False

        sub_queries = sorted(
            {i for r in triggers for i in (r.appears_in_sub_queries or [])}
        )
        pinned = await asyncio.to_thread(
            fetch_article_result,
            supabase,
            ARTICLE_40_ID,
            relevance="medium",
            appears_in_sub_queries=sub_queries,
        )
        if pinned is None:
            logger.warning(
                "pinned: Article 40 fetch returned nothing — pin skipped "
                "(%d triggering principle(s))",
                len(triggers),
            )
            return False
        ura.medium_results.append(pinned)
        logger.info(
            "pinned: Article 40 appended after %d principle(s) of entity %s",
            len(triggers), ARTICLE_40_ENTITY_REF,
        )
        return True
    except Exception as e:  # fail-soft by contract
        logger.warning("pinned: Article 40 pin failed — skipped: %s", e)
        return False


__all__ = [
    "ARTICLE_40_ID",
    "ARTICLE_40_REF_ID",
    "ARTICLE_40_ENTITY_REF",
    "fetch_article_result",
    "pin_article_40",
]
