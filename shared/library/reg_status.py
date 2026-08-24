"""Repeal status for a regulation — the ONE vocabulary every consumer reads.

``regulations_v2.status_class`` is the corpus's lifecycle field. Before this
module it was read by the public library's status badge and by nothing at all
in the agent pipeline — so a repealed «ملغي» regulation was retrieved,
reranked, cited and synthesized exactly as though it were current law.

Scope of this module is deliberately narrow: **repeal only**. The corpus also
carries consultation drafts (``consultation_ended`` — 42,335 retrievable topic
rows, 25% of the reg surface — plus ``under_consultation`` and ``in_progress``),
and those are equally not-the-law. They are NOT flagged here. Flagging them is a
separate, larger decision (it changes a quarter of every reranker batch); this
module answers one question only, and answers it for the 424 topic rows whose
parent law was repealed outright:

    status_class = 'cancelled'   ->  ملغي — لم يعد سارياً
    everything else              ->  "" (no line emitted, nothing changes)

Returning ``""`` rather than a label for every non-repealed state is what keeps
this change free: the reranker candidate blocks and the aggregator's reference
blocks grow a line ONLY for a repealed regulation, so in-force material costs
no extra prompt tokens and its prompt surface is byte-identical to before.

``status_raw`` is the corpus's own free-text status. Repeal has TWO live
spellings — «ملغي» (4 rows) and «لاغي» (24 rows) — which is exactly why nothing
should ever switch on it. Switch on ``status_class``.
"""
from __future__ import annotations

__all__ = [
    "REPEALED_STATUS_CLASS",
    "REPEALED_LABEL_AR",
    "is_repealed",
    "status_line",
]


#: The single ``status_class`` value meaning "repealed, no longer in force".
#: Covers both corpus spellings of the raw status («ملغي» and «لاغي»).
REPEALED_STATUS_CLASS = "cancelled"

#: What a model (and, via the reference projection, the reader) is told. Terse
#: on purpose — it rides on every block for a repealed regulation.
REPEALED_LABEL_AR = "ملغي — لم يعد سارياً"


def is_repealed(status_class: str | None) -> bool:
    """True only for a regulation the corpus records as repealed.

    Unknown / null -> ``False``. This is the permissive direction on purpose:
    the flag exists to raise an alarm about a *known* repeal, and inferring
    repeal from a missing or unrecognised status would put a false «ملغي» on
    current law — a worse error than staying silent.
    """
    return (status_class or "").strip() == REPEALED_STATUS_CLASS


def status_line(status_class: str | None, status_raw: str | None = None) -> str:
    """The status string embedded in prompts and rendered blocks.

    Returns ``""`` for every non-repealed regulation, so callers can emit the
    line unconditionally (``if line:``) and nothing changes for the ~99.7% of
    the corpus that was never repealed.

    Shape for a repealed regulation::

        ملغي — لم يعد سارياً (النص الأصلي: لاغي)

    ``status_raw`` is appended parenthetically when present — it is the wording
    the source page itself used, which is what a lawyer would go looking for.
    """
    if not is_repealed(status_class):
        return ""
    raw = (status_raw or "").strip()
    if raw and raw not in REPEALED_LABEL_AR:
        return f"{REPEALED_LABEL_AR} (النص الأصلي: {raw})"
    return REPEALED_LABEL_AR
