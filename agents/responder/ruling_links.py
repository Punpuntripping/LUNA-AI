"""«اقرأ الحكم كاملًا»: a link to each ruling a prior-report reply quotes or corrects.

Plan artifact_analyzer_scope_full_text.md D14. The link rides the server-only
``reply_metadata`` event into ``messages.metadata.library_links``; the web app
renders it as a button under the reply.

Navigation only: a path to the ruling's page in our library, which enforces its
own access. Never a charge and never an unlock. A ruling with no published page
gets no link, never a guessed one.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from agents.memory.artifact_analyzer.models import AnalyzerResult

logger = logging.getLogger(__name__)

LABEL = "اقرأ الحكم كاملًا"
MAX_LINKS = 3


def quoted_rulings(result: AnalyzerResult) -> list[tuple[str, str]]:
    """(case_ref, ruling name) of each ruling whose own text the reply carries.

    A correction first, then the quotes, without repeats. A report's sentence
    about a ruling is not the ruling's text, so it does not count.
    """
    found: dict[str, str] = {}
    for c in result.reading_errors:
        if c.domain == "cases" and c.open_key:
            found.setdefault(c.open_key, c.source_name)
    for q in result.quotes:
        if q.domain == "cases" and q.open_key:
            found.setdefault(q.open_key, q.source_name)
    return list(found.items())[:MAX_LINKS]


def _case_ids(supabase: Any, case_refs: list[str]) -> dict[str, str]:
    rows = (
        supabase.table("cases")
        .select("id, case_ref")
        .in_("case_ref", case_refs)
        .execute()
        .data
    ) or []
    return {str(r["case_ref"]): str(r["id"]) for r in rows}


async def ruling_links(supabase: Any, result: AnalyzerResult) -> list[dict[str, str]]:
    """The buttons for this reply. [] when there is none. Never raises."""
    rulings = quoted_rulings(result)
    if not rulings:
        return []
    from backend.app.services.library_items_service import public_page_url

    try:
        ids = await asyncio.to_thread(_case_ids, supabase, [ref for ref, _ in rulings])
        links: list[dict[str, str]] = []
        for ref, title in rulings:
            case_id = ids.get(ref)
            url = await public_page_url(supabase, "judgment", case_id) if case_id else None
            if url:
                links.append({"label": LABEL, "url": url, "title": title})
        return links
    except Exception as exc:  # noqa: BLE001 — a missing button never costs the reply
        logger.warning("ruling_links failed: %s", exc)
        return []
