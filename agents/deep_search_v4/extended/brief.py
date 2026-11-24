"""Each search's ``planner_brief`` — plan §4.9 (b), built by code (no model writes it).

Layout, exactly::

    تحليل آلي لملف المستخدم (ليس نصاً من الملف ولا كلام المستخدم):
    - جهة المستخدم: {client_side} — {understanding}
    - تركيز هذا البحث: {request.focus}
    - وجه {A3} [{kind_ar} · {strength_ar}]: {title} — {argument} سؤال البحث: {legal_question} ما يضعفه: {weakness} الأقسام: D1·S7 (الموكّل: {owner})، D1·S12 (الخصم: {owner})
    - أقسام يقرؤها البحث بتركيز: D1·S7، D1·S12        ← only when the request lists sections

    {SEARCH_INSTRUCTION}

One line per angle of the request, in the request's order; ``ما يضعفه …`` only when the
angle has a weakness; each section with its label الموكّل / الخصم / محايد relative to the
angle's side (the planner's view, §4.2) and its owner «owner، قدّمه …». Refs are written
document-qualified as the markers show them (``D1·S7``) — the searches read several
documents. ``client_side`` and ``understanding`` are the planner's (its side wins over
the map's).
"""
from __future__ import annotations

from .models import AngleCard, ExtendedPlan, ExtendedRequest, PlannerView, SectionView
from .prompts import SEARCH_INSTRUCTION
from .view import display_ref

HEADER_AR = "تحليل آلي لملف المستخدم (ليس نصاً من الملف ولا كلام المستخدم):"
KIND_AR: dict[str, str] = {"strengthen": "يقوّي", "attack": "يطعن", "answer": "يجب الرد"}
STRENGTH_AR: dict[str, str] = {"strong": "قوي", "medium": "متوسط", "weak": "ضعيف"}


def _section(s: SectionView) -> str:
    ref = display_ref(s.ref)
    if s.label == "?" or not s.label:
        return ref
    return f"{ref} ({s.label}: {s.owner or '-'})"


def angle_line(a: AngleCard) -> str:
    """``- وجه A3 [يقوّي · قوي]: title — argument سؤال البحث: … ما يضعفه: … الأقسام: …``."""
    line = (
        f"- وجه {a.id} [{KIND_AR.get(a.kind, a.kind)} · {STRENGTH_AR.get(a.strength, a.strength)}]: "
        f"{a.title} — {a.argument} سؤال البحث: {a.legal_question}"
    )
    if a.weakness.strip():
        line += f" ما يضعفه: {a.weakness}"
    if a.sections:
        line += " الأقسام: " + "، ".join(_section(s) for s in a.sections)
    return line


def build_planner_brief(request: ExtendedRequest, *, plan: ExtendedPlan, view: PlannerView) -> str:
    """The §4.9 (b) brief of one request of ``plan`` (decoded) over ``view``."""
    cards = {a.id: a for a in view.angles}
    lines = [
        HEADER_AR,
        f"- جهة المستخدم: {plan.client_side} — {plan.understanding}",
        f"- تركيز هذا البحث: {request.focus}",
    ]
    for aid in request.angles:
        card = cards.get(aid)
        if card is not None:
            lines.append(angle_line(card))
    if request.sections:
        lines.append("- أقسام يقرؤها البحث بتركيز: " + "، ".join(display_ref(s) for s in request.sections))
    return "\n".join(lines) + "\n\n" + SEARCH_INSTRUCTION


__all__ = ["HEADER_AR", "KIND_AR", "SEARCH_INSTRUCTION", "STRENGTH_AR", "angle_line", "build_planner_brief"]
