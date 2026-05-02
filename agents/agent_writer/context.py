"""Pure context-rendering helper for agent_writer.

`format_writer_context` produces the second system block that Pydantic AI's
`@agent.system_prompt` decorator injects at run time.  It is a pure function
with no DB access, making it trivially unit-testable.

Constraint on revision path
----------------------------
When `revising_item_id` is set the caller **must** include the draft being
revised as the first item in `attached_items`.  The function does not fetch
anything from the DB — it relies entirely on what is already present in
`attached_items`.  The runner (or orchestrator, in Wave 10) is responsible for
ensuring the revision target is hydrated and placed first before invoking the
writer.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents.models import WorkspaceItemSnapshot


_DETAIL_LABELS: dict[str, str] = {
    "low": "مختصر (منخفض)",
    "standard": "معتدل (قياسي)",
    "medium": "معتدل (قياسي)",
    "high": "مفصّل (عالٍ)",
}

_TONE_LABELS: dict[str, str] = {
    "formal": "رسمي",
    "neutral": "محايد",
    "concise": "موجز",
}


def format_writer_context(
    *,
    attached_items: list["WorkspaceItemSnapshot"],
    briefing: str,
    revising_item_id: str | None,
    detail_level: str,
    tone: str,
) -> str:
    """Render workspace context as an Arabic system-prompt block.

    Args:
        attached_items: Router-selected workspace items (full ``content_md``
            already hydrated by the orchestrator).  At most 7 items
            (MAX_ATTACHED_ITEMS from ``agents/models.py``).
        briefing: The user's task statement / drafting brief, forwarded from
            ``MajorAgentInput.briefing``.
        revising_item_id: item_id of the draft being revised.  When set, the
            revision target **must** appear as ``attached_items[0]``.
        detail_level: Stylistic hint — "low" | "standard" | "medium" | "high".
        tone: Stylistic hint — "formal" | "neutral" | "concise".

    Returns:
        A multi-line Arabic string that Pydantic AI appends as a second system
        message.  Empty briefing and empty attached_items still produce a
        valid (short) block so the LLM call never receives a malformed prompt.
    """
    lines: list[str] = []

    lines.append("## سياق المهمة الحالية")
    lines.append("")

    # --- Briefing -------------------------------------------------------
    lines.append("### الطلب")
    lines.append((briefing or "").strip() or "(لا يوجد طلب)")
    lines.append("")

    # --- Attached items -------------------------------------------------
    if attached_items:
        if revising_item_id:
            lines.append(
                "### المستند المُراجَع (يرجى قراءته بعناية قبل إعادة الصياغة)"
            )
        else:
            lines.append("### العناصر المرفقة للسياق")
        lines.append("")

        for idx, item in enumerate(attached_items):
            title = (item.title if hasattr(item, "title") else item.get("title", "")) or ""
            kind = (item.kind if hasattr(item, "kind") else item.get("kind", "")) or ""
            content_md = (
                item.content_md
                if hasattr(item, "content_md")
                else item.get("content_md", "")
            ) or ""

            # Mark revision target explicitly
            if revising_item_id and idx == 0:
                lines.append(f"### [مسوّدة للمراجعة] {title}")
            else:
                lines.append(f"### {title}")

            lines.append(f"({kind})")
            lines.append("")
            lines.append(content_md.strip())
            lines.append("")
    else:
        lines.append("### العناصر المرفقة للسياق")
        lines.append("(لا توجد عناصر مرفقة)")
        lines.append("")

    # --- Preferences ----------------------------------------------------
    detail_label = _DETAIL_LABELS.get(detail_level, detail_level)
    tone_label = _TONE_LABELS.get(tone, tone)
    lines.append("### تفضيلات الأسلوب")
    lines.append(f"- مستوى التفصيل: {detail_label}")
    lines.append(f"- النبرة: {tone_label}")
    lines.append("")

    return "\n".join(lines)


__all__ = ["format_writer_context"]
