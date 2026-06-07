"""Input / output contracts for the template_ingester (Layer-4 Memory) agent.

This module is the single source of truth for the ingester's call surface and
its LLM output shape. The ingester takes ONE raw legal document (a single
``workspace_items`` row's ``content_md``) and turns it into a clean, reusable
template that is saved into the user's personal "قوالبي" library
(``user_templates``).

Design notes:

- ``IngestInput`` is the caller-facing request: just the ``item_id`` to clean
  and the ``user_id`` the resulting template is owned by. The Supabase client +
  HTTP client live on ``IngesterDeps`` (mirrors ``item_analyzer``).
- ``IngestResult`` is the runner's return value — a small, total result object.
  EVERY failure (fetch / LLM / insert) collapses into the SAME Arabic
  ``error_ar`` string so the endpoint + frontend chip surface one consistent
  message. ``ok=True`` carries the new ``template_id`` + ``title``.
- ``CleanedTemplate`` is the LLM output schema. The model NEVER supplies the
  ``user_id`` (provenance + ownership are set by the runner, not the model).
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field


# The single Arabic failure message — surfaced for ANY ingestion failure
# (missing item, LLM error, insert error). Kept here as the one source of truth
# so the runner, the endpoint, and the frontend chip all agree on the wording.
INGEST_FAILED_AR = "فشل حفظ القالب، يمكنك حفظه يدويًا من خلال قوالبي"


# ---------------------------------------------------------------------------
# Caller-facing request — what feeds ``handle_template_ingestion`` (via deps).
# ---------------------------------------------------------------------------


@dataclass
class IngestInput:
    """The two identifiers the ingester needs.

    Attributes:
        item_id: The ``workspace_items.item_id`` whose ``content_md`` is the raw
            legal document to clean into a template.
        user_id: The internal ``users.user_id`` who owns the resulting template
            row. Used for RLS-scoped reads and as the ``user_templates.user_id``
            on insert. NEVER sent to the LLM.
    """

    item_id: str
    user_id: str


# ---------------------------------------------------------------------------
# Runner result — total, Arabic-error-carrying. Never raises to the caller.
# ---------------------------------------------------------------------------


class IngestResult(BaseModel):
    """Final result returned by ``handle_template_ingestion``.

    Exactly one of two shapes:

    - success:  ``ok=True``,  ``template_id`` + ``title`` set, ``error_ar=None``.
    - failure:  ``ok=False``, ``template_id``/``title`` ``None``, ``error_ar``
      set to :data:`INGEST_FAILED_AR`.
    """

    ok: bool
    template_id: str | None = None
    title: str | None = None
    error_ar: str | None = None


# ---------------------------------------------------------------------------
# LLM output schema — what the agent is asked to produce.
#
# The field descriptions are Arabic-first because they are surfaced to the
# model in the structured-output tool schema and steer the cleaning behaviour
# (bracketed Arabic placeholders + a specific, unique title).
# ---------------------------------------------------------------------------


class CleanedTemplate(BaseModel):
    """The cleaned, reusable template the ingester LLM emits.

    Two semantic fields — a unique descriptive title and the placeholder'd
    markdown body. Ownership/provenance are NOT here: the runner sets
    ``user_id`` + ``created_by='agent'`` on insert, never the model.
    """

    title: str = Field(
        description=(
            "عنوان عربي محدّد وفريد للقالب يصف نوعه الدقيق — وليس عنواناً عاماً. "
            "مثال جيّد: «نموذج عقد إيجار لعمارة سكنية» بدلاً من «عقد إيجار». "
            "يجب أن يكون غير فارغ."
        ),
    )
    content_md: str = Field(
        description=(
            "نصّ القالب بصيغة ماركداون: نفس البنية القانونية للوثيقة الأصلية مع "
            "استبدال الأسماء والتواريخ والمبالغ المحدّدة بعناصر نائبة عربية بين "
            "أقواس مربّعة مثل «[اسم المستأجر]» و«[تاريخ العقد]» و«[المبلغ]»، "
            "وتصحيح الأخطاء الإملائية. يجب أن يكون غير فارغ."
        ),
    )

    def tracking_output(self) -> dict[str, object]:
        """Bounded telemetry view — the heavy ``content_md`` is summarised to a
        char count so the tracking layer never logs the full body. Consumed by
        ``agents/utils/tracking.py`` (``output.*`` span attributes)."""
        return {
            "title": self.title,
            "content_md_chars": len(self.content_md or ""),
        }


__all__ = [
    "INGEST_FAILED_AR",
    "IngestInput",
    "IngestResult",
    "CleanedTemplate",
]
