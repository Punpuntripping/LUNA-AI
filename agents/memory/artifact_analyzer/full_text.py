"""``read_full_text`` — a ruling, or a service with «الدليل الشامل», read in full.

Each source comes back as two texts: what the search read, which is the text the
report was written from, and the full text. The model checks a report's sentence
against the second and may quote only the second.

Modelled on ``open_sources``, which stays as it is. No unlock and no charge (plan
D7): this is the analyzer reading, not the user. Like ``open_sources`` it reads
the source tables directly and does not mask.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic_ai import Agent, RunContext

from agents.simple_search.unfold import (
    GUIDE_SLICE_GAP_AR,
    _fetch_service_guide,
    _fetch_service_guide_images,
    render_service_guide,
    slice_head_middle_tail,
)

from .deps import AnalyzerDeps
from .models import SourceRow

logger = logging.getLogger(__name__)

#: D8. Rulings: p90 16.8k chars. Guides: p90 24.3k.
FULL_TEXT_CHARS = 30_000
#: D9. Distinct sources per turn, across every call.
MAX_FULL_READS = 3
#: The slicer keeps whole lines, and some rulings are one 50k-character line.
#: Longer lines are broken at a space first, so the ending still fits. A break is
#: whitespace, so it never changes what a quote matches.
_WRAP_CHARS = 1_500

SEARCH_VIEW_HEADING = "#### ما قرأه البحث"
FULL_HEADING = {"cases": "#### النص الكامل", "compliance": "#### الدليل الشامل"}
_RULING_GAP = "> ⚠ [حُذف جزء من نص الحكم هنا لتجاوز الحد]"
_NO_SEARCH_VIEW = "(لا يوجد)"
_NOT_ELIGIBLE = "{label} لا يُقرأ كاملًا بهذه الأداة؛ استخدم open_sources"
_OVER_LIMIT = "{label}: تجاوزت الحد: ثلاثة مصادر في كل رد"
_UNREADABLE = "### [{label}] (تعذّرت قراءة النص الكامل لهذا المصدر)"


def eligible(source: SourceRow) -> bool:
    """D5: every ruling, and a service only when it has a guide."""
    return source.domain == "cases" or (source.domain == "compliance" and source.has_guide)


def _wrap_long_lines(text: str) -> str:
    out: list[str] = []
    for line in text.split("\n"):
        while len(line) > _WRAP_CHARS:
            cut = line.rfind(" ", 0, _WRAP_CHARS)
            if cut <= 0:
                cut = _WRAP_CHARS
            out.append(line[:cut])
            line = line[cut:].lstrip()
        out.append(line)
    return "\n".join(out)


def cap(text: str, domain: str) -> str:
    """At most FULL_TEXT_CHARS, keeping the beginning, the middle and the end.

    The end matters most for a ruling: its operative part comes last.
    """
    if len(text) <= FULL_TEXT_CHARS:
        return text
    sliced = slice_head_middle_tail(_wrap_long_lines(text), FULL_TEXT_CHARS)
    if domain == "cases":
        sliced = sliced.replace(GUIDE_SLICE_GAP_AR, _RULING_GAP)
    return sliced


def _ruling(supabase: Any, case_ref: str) -> str:
    rows = (
        supabase.table("cases")
        .select("case_ref, content")
        .eq("case_ref", case_ref)
        .limit(1)
        .execute()
        .data
    ) or []
    return str(rows[0].get("content") or "") if rows else ""


def _guide(supabase: Any, service_id: str) -> str:
    guide = _fetch_service_guide(supabase, service_id)
    if not guide:
        return ""
    images = _fetch_service_guide_images(supabase, str(guide.get("id") or ""))
    text, _unresolved = render_service_guide(guide, images)
    return text


def read_full(supabase: Any, source: SourceRow) -> str:
    """The full half, capped. "" when it cannot be read."""
    try:
        if source.domain == "cases":
            text = _ruling(supabase, source.open_key) if source.open_key else ""
        else:
            text = _guide(supabase, source.guide_service_id)
    except Exception as exc:  # noqa: BLE001 — the model is told it could not be read
        logger.warning("artifact_analyzer: full text of %s failed: %s", source.label, exc)
        return ""
    return cap(text.strip(), source.domain)


def render_full(source: SourceRow, full: str) -> str:
    return "\n".join([
        f"### [{source.label}] {source.name}",
        SEARCH_VIEW_HEADING,
        source.search_view.strip() or _NO_SEARCH_VIEW,
        FULL_HEADING[source.domain],
        full,
    ])


def take(deps: AnalyzerDeps, labels: list[str]) -> tuple[list[SourceRow], dict[str, str]]:
    """Which labels to read now, and a line for each one refused.

    Synchronous on purpose: a label is taken before any read starts, so two calls
    in the same round cannot pass the limit between them.
    """
    to_read: list[SourceRow] = []
    refused: dict[str, str] = {}
    for label in dict.fromkeys(str(x).strip().upper() for x in labels):
        source = deps.sources.get(label)
        if source is None:
            refused[label] = f"Unknown label: {label}"
        elif not eligible(source):
            refused[label] = _NOT_ELIGIBLE.format(label=label)
        elif label in deps.full_read:
            to_read.append(source)  # read again: free
        elif len(deps.full_read) >= MAX_FULL_READS:
            refused[label] = _OVER_LIMIT.format(label=label)
        else:
            deps.full_read.append(label)
            to_read.append(source)
    return to_read, refused


def register_read_full_text(agent: Agent[AnalyzerDeps, Any]) -> None:
    @agent.tool
    async def read_full_text(ctx: RunContext[AnalyzerDeps], labels: list[str]) -> str:
        """Read a ruling, or a service marked «له دليل شامل», in full, by label.

        Each source returns two texts: «ما قرأه البحث», the text the report was
        written from, and the full text. At most three sources per turn; reading
        one again is free. You may quote the full text only.

        Args:
            labels: Source labels, for example ["S3", "S7"].
        """
        deps = ctx.deps
        wanted = list(dict.fromkeys(str(x).strip().upper() for x in labels))
        to_read, refused = take(deps, wanted)
        fresh = [s for s in to_read if s.label not in deps.full_texts]
        texts = await asyncio.gather(*(
            asyncio.to_thread(read_full, deps.supabase, s) for s in fresh
        ))
        for source, text in zip(fresh, texts):
            if text:
                deps.full_texts[source.label] = text
            elif source.label in deps.full_read:
                # A failed read does not use up one of the three.
                deps.full_read.remove(source.label)

        blocks: list[str] = []
        for label in wanted:
            if label in refused:
                blocks.append(refused[label])
            elif label in deps.full_texts:
                blocks.append(render_full(deps.sources[label], deps.full_texts[label]))
            else:
                blocks.append(_UNREADABLE.format(label=label))
        return "\n\n".join(blocks) or "No labels given."
