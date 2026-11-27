"""How the attachment agent's result is shown to the router (plan §2.2).

The router writes the user's reply from this block, so it carries only what the
router may say: statements, quotes with their code-set locations, the applied
changes, and a question to ask. Never internals (tool names, refs the user
cannot see). The agent-core build may refine the wording; keep it compact.
"""
from __future__ import annotations

from agents.attachment_agent.models import AttachmentAgentResult, QuoteOut

_OUTCOME_LINE = {
    "found": "وُجد في الملفات:",
    "not_found": "لم يُعثر عليه فيما قُرئ من الملفات.",
    "which_file": "لم يتضح أي ملف يقصده المستخدم.",
    "edited": "التعديلات التي طلبها المستخدم:",
    "reanalysis_started": "بدأت إعادة تحليل الخيوط، وتظهر النتيجة خلال دقائق.",
    "not_edited": "لم يُنفَّذ التعديل الذي طلبه المستخدم.",
    "needs_clarification": "الطلب يحتاج توضيحًا من المستخدم.",
    "error": "تعذّر الاطلاع على الملفات الآن.",
}


def _where(q: QuoteOut) -> str:
    parts = [f"«{q.file_title}»" + (f" ({q.wi})" if q.wi else "")]
    if q.section_what:
        parts.append(f"القسم: {q.section_what}")
    if q.owner:
        parts.append(f"صاحب الكلام: {q.owner}")
    if q.page:
        parts.append(f"ص {q.page}")
    return " — ".join(parts)


def render_for_router(result: AttachmentAgentResult) -> str:
    """Compact Arabic block for the router's tool result."""
    lines: list[str] = ["〔[نظام] نتيجة وكيل المرفقات〕", _OUTCOME_LINE.get(result.outcome, "")]
    for i, point in enumerate(result.points, 1):
        lines.append(f"{i}. {point.statement}")
        for q in point.quotes:
            lines.append(f"   - {_where(q)}")
            lines.append(f"     «{q.text}»")
    for edit in result.edits:
        lines.append(f"- {edit}")
    if result.note:
        lines.append(result.note)
    if result.ask_user:
        lines.append(f"سؤال للمستخدم: {result.ask_user}")
    lines.append(
        "〔[نظام] أجب المستخدم من هذه النتيجة فقط: لا تذكر موضعًا أو نصًا لم يرد هنا، "
        "وانقل الاقتباسات كما هي.〕"
    )
    if result.edits or result.outcome in ("edited", "reanalysis_started", "not_edited"):
        lines.append(
            "〔[نظام] أخبر المستخدم بوضوح بما تغيّر وما لم يتغيّر كما ورد أعلاه، "
            "ولا تذكر تعديلًا لم يرد هنا.〕"
        )
    return "\n".join(line for line in lines if line)


__all__ = ["render_for_router"]
