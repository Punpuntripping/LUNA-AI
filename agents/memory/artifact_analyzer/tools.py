"""``open_sources`` — read whole sources by label. No cap on how many (D8).

Reads the source tables rather than ``ura_json``: the search result clips some
bodies (case text stops at about 6k characters). The stored text is only the
fallback for when a table read fails.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic_ai import Agent, RunContext

from .deps import AnalyzerDeps
from .models import SourceRow

logger = logging.getLogger(__name__)

#: domain → (table, key column, whole-text column)
DOMAIN_TABLES: dict[str, tuple[str, str, str]] = {
    "regulations": ("chunks_v2", "id", "content"),
    "cases": ("cases", "case_ref", "content"),
    "circulars": ("circulars", "id", "content"),
    "compliance": ("services", "service_ref", "original_markdown"),
}


def _fetch_texts(supabase: Any, domain: str, keys: list[str]) -> dict[str, str]:
    table = DOMAIN_TABLES.get(domain)
    if table is None or not keys:
        return {}
    name, key_col, text_col = table
    try:
        rows = (
            supabase.table(name)
            .select(f"{key_col}, {text_col}")
            .in_(key_col, keys)
            .execute()
            .data
        ) or []
    except Exception as exc:  # noqa: BLE001 — the stored text is the fallback
        logger.warning("artifact_analyzer: reading %s failed: %s", name, exc)
        return {}
    return {str(r[key_col]): str(r.get(text_col) or "") for r in rows}


def read_sources(deps: AnalyzerDeps, sources: list[SourceRow]) -> dict[str, str]:
    """Whole text per label. Empty text means the source could not be read."""
    keys_by_domain: dict[str, list[str]] = {}
    for source in sources:
        if source.open_key:
            keys_by_domain.setdefault(source.domain, []).append(source.open_key)
    texts = {
        domain: _fetch_texts(deps.supabase, domain, keys)
        for domain, keys in keys_by_domain.items()
    }
    return {
        s.label: texts.get(s.domain, {}).get(s.open_key) or s.stored_text
        for s in sources
    }


def register_open_sources(agent: Agent[AnalyzerDeps, Any]) -> None:
    @agent.tool
    async def open_sources(ctx: RunContext[AnalyzerDeps], labels: list[str]) -> str:
        """Open whole sources from <sources>, by label.

        Open every source that might answer the user, all in one call. There is
        no limit on how many. You may quote a source only after opening it.

        Args:
            labels: Source labels, for example ["S1", "S2", "S4"].
        """
        deps = ctx.deps
        wanted = list(dict.fromkeys(str(x).strip().upper() for x in labels))
        known = [deps.sources[x] for x in wanted if x in deps.sources]
        unknown = [x for x in wanted if x not in deps.sources]

        texts = await asyncio.to_thread(read_sources, deps, known)
        blocks: list[str] = []
        for source in known:
            text = texts.get(source.label, "")
            if not text:
                blocks.append(f"### [{source.label}] (تعذّر فتح هذا المصدر)")
                continue
            deps.opened[source.label] = text
            blocks.append(f"### [{source.label}] {source.name}\n{text}")
        if unknown:
            blocks.append("Unknown labels: " + ", ".join(unknown))
        return "\n\n".join(blocks) or "No labels given."
