"""pgvector cosine retrieval over ``system_templates`` for the writer planner.

Single public helper: ``search_templates(supabase, subtype, intent, top_n=5)``.

Pipeline:
    1. Map the executor's English ``WriterSubtype`` to the Arabic enum value
       used in ``system_templates.type`` (see ``_SUBTYPE_TO_AR``).
    2. Generate a 1024-dim embedding of ``intent`` via Alibaba text-embedding-v4
       (matches migration 046's `vector(1024)` column).
    3. Call the ``search_system_templates`` RPC (migration 054) with the
       embedding + Arabic type filter + top_n.
    4. Map rows to ``TemplateRef`` Pydantic models.

Empty-result paths (all surface as ``[]``, never raise):
    - subtype maps to ``None`` (e.g. "summary" — no template type applies)
    - ``system_templates`` has no ingested rows (v1 default — ingestion is a
      separate follow-up plan)
    - embedding service fails (logged WARN, returns ``[]``)
    - RPC missing or schema mismatch (logged WARN, returns ``[]``)

The planner's prompt covers the no-template path («إن لم توجد قوالب، أنشئ
هيكلاً مناسباً للنوع دون الاعتماد على قالب») so an empty result is a
normal outcome, not an error.
"""
from __future__ import annotations

import logging
from typing import Any

from agents.utils.embeddings import embed_regulation_query_alibaba
from agents.writer.models import TemplateRef, WriterSubtype

logger = logging.getLogger(__name__)


# English WriterSubtype → Arabic template_type_enum value (migration 046).
# ``None`` means "no template type applies" → skip the RPC, return [].
# Per .claude/plans/writer_planner.md § Templates — graceful no-results.
_SUBTYPE_TO_AR: dict[str, str | None] = {
    "contract":      "عقد",
    "memo":          "مذكرة",
    "legal_opinion": "رأي_قانوني",
    "defense_brief": "مذكرة",         # Saudi practice merges defense briefs into the مذكرة type
    "letter":        "إنذار",         # Closest match; v2 may split letters out
    "summary":       None,            # Summaries don't draft from a template
}


async def search_templates(
    supabase: Any,
    subtype: WriterSubtype | str,
    intent: str,
    top_n: int = 5,
) -> list[TemplateRef]:
    """Retrieve up to ``top_n`` system templates ranked by similarity to ``intent``.

    Args:
        supabase: Supabase client (sync; .rpc().execute() pattern matches the
            rest of the codebase).
        subtype: WriterSubtype the planner is drafting toward. Mapped to the
            Arabic enum value via ``_SUBTYPE_TO_AR``; subtypes that map to
            ``None`` short-circuit to [].
        intent: The planner's distilled intent (``WriterPackage.intent_ar``)
            or any short Arabic description of what the user wants drafted.
        top_n: Maximum rows to return. Defaults to 5 per the plan.

    Returns:
        List of ``TemplateRef`` (best match first). Empty list if subtype
        maps to None, intent is blank, embedding fails, the RPC fails, or
        the table simply has no matching rows.
    """
    # 0. Guard: empty intent or unmapped subtype → empty result, no work.
    if not intent or not intent.strip():
        return []
    type_filter = _SUBTYPE_TO_AR.get(str(subtype))
    if type_filter is None and subtype not in _SUBTYPE_TO_AR:
        logger.debug("search_templates: unknown subtype %r → []", subtype)
        return []
    if type_filter is None:
        # Subtype is known but maps to no template type (e.g. "summary").
        return []

    # 1. Embed the intent.
    try:
        query_embedding = await embed_regulation_query_alibaba(intent.strip())
    except Exception as exc:
        logger.warning("search_templates: embedding failed (%s) → []", exc)
        return []
    if not query_embedding:
        return []

    # 2. RPC call.
    try:
        result = supabase.rpc(
            "search_system_templates",
            {
                "query_embedding": query_embedding,
                "type_filter":     type_filter,
                "top_n":           max(int(top_n), 1),
            },
        ).execute()
    except Exception as exc:
        logger.warning(
            "search_templates: RPC search_system_templates failed (%s) → []",
            exc,
        )
        return []

    rows = getattr(result, "data", None) or []
    if not rows:
        return []

    # 3. Map rows to TemplateRef. Be tolerant of missing/odd shapes — graceful
    #    degradation: skip a row that fails to map; don't fail the whole call.
    refs: list[TemplateRef] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            refs.append(
                TemplateRef(
                    template_id=str(row["template_id"]),
                    template_type=str(row.get("template_type") or type_filter),
                    title=str(row.get("title") or ""),
                    body_md=str(row.get("body_md") or ""),
                    score=float(row.get("score") or 0.0),
                )
            )
        except (KeyError, ValueError, TypeError) as exc:
            logger.debug("search_templates: skip malformed row (%s)", exc)
            continue

    return refs


__all__ = ["search_templates"]
