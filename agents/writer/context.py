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
    "low": "brief (low)",
    "standard": "moderate (standard)",
    "medium": "moderate (standard)",
    "high": "detailed (high)",
}

_TONE_LABELS: dict[str, str] = {
    "formal": "formal",
    "neutral": "neutral",
    "concise": "concise",
}


def format_writer_context(
    *,
    attached_items: list["WorkspaceItemSnapshot"],
    describe_query: str,
    revising_item_id: str | None,
    detail_level: str,
    tone: str,
) -> str:
    """Render workspace context as an Arabic system-prompt block.

    Args:
        attached_items: Router-selected workspace items (full ``content_md``
            already hydrated by the orchestrator).  At most 7 items
            (MAX_ATTACHED_ITEMS from ``agents/models.py``).
        describe_query: The router-emitted description of the user's query,
            forwarded from ``MajorAgentInput.describe_query`` (Wave 1
            redesign — was ``briefing`` pre-redesign).
        revising_item_id: item_id of the draft being revised.  When set, the
            revision target **must** appear as ``attached_items[0]``.
        detail_level: Stylistic hint — "low" | "standard" | "medium" | "high".
        tone: Stylistic hint — "formal" | "neutral" | "concise".

    Returns:
        A multi-line Arabic string that Pydantic AI appends as a second system
        message.  Empty describe_query and empty attached_items still produce
        a valid (short) block so the LLM call never receives a malformed prompt.
    """
    lines: list[str] = []

    lines.append("## Current task context")
    lines.append("")

    # --- Query description ---------------------------------------------
    lines.append("### The request")
    lines.append((describe_query or "").strip() or "(no request)")
    lines.append("")

    # --- Attached items -------------------------------------------------
    if attached_items:
        if revising_item_id:
            lines.append(
                "### The document under revision (please read it carefully before re-drafting)"
            )
        else:
            lines.append("### Items attached for context")
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
                lines.append(f"### [draft for revision] {title}")
            else:
                lines.append(f"### {title}")

            lines.append(f"({kind})")
            lines.append("")
            lines.append(content_md.strip())
            lines.append("")
    else:
        lines.append("### Items attached for context")
        lines.append("(no attached items)")
        lines.append("")

    # --- Preferences ----------------------------------------------------
    detail_label = _DETAIL_LABELS.get(detail_level, detail_level)
    tone_label = _TONE_LABELS.get(tone, tone)
    lines.append("### Style preferences")
    lines.append(f"- Detail level: {detail_label}")
    lines.append(f"- Tone: {tone_label}")
    lines.append("")

    return "\n".join(lines)


def format_writer_envelope(
    *,
    describe_query: str,
    task_label: str,
    revising_item_id: str | None,
    detail_level: str,
    tone: str,
) -> str:
    """Render the per-turn *envelope* as an Arabic system-prompt block.

    Used in the package path (when ``WriterDeps.package`` is set) — the
    package itself already carries all attached-item content via
    ``render_package_for_system_prompt``, so the envelope only needs to
    surface the human frame: what the user actually asked for, what
    kind of task this is, whether we're revising, and the style hints.

    This is ``format_writer_context`` minus the ``attached_items`` block.
    See ``.claude/plans/writer_redesign.md`` § Dynamic instructions.

    Args:
        describe_query: The router-emitted description of the user's query.
        task_label: Short Arabic content-derived label (≤80 chars).
        revising_item_id: item_id of the draft being revised, or None.
        detail_level: "low" | "standard" | "medium" | "high".
        tone: "formal" | "neutral" | "concise".

    Returns:
        A multi-line Arabic string. Always returns at least the section
        header so the LLM call never receives an empty dynamic block.
    """
    lines: list[str] = []

    lines.append("## Current task context")
    lines.append("")

    # --- Query description ---------------------------------------------
    lines.append("### The request")
    lines.append((describe_query or "").strip() or "(no request)")
    lines.append("")

    # --- Task label ----------------------------------------------------
    if (task_label or "").strip():
        lines.append("### Task description")
        lines.append(task_label.strip())
        lines.append("")

    # --- Revision marker ----------------------------------------------
    if revising_item_id:
        lines.append("### Revision mode")
        lines.append(
            "This task is a revision of a previous draft — review <prior_draft> "
            "inside <package> carefully before re-drafting."
        )
        lines.append("")

    # --- Preferences ---------------------------------------------------
    detail_label = _DETAIL_LABELS.get(detail_level, detail_level)
    tone_label = _TONE_LABELS.get(tone, tone)
    lines.append("### Style preferences")
    lines.append(f"- Detail level: {detail_label}")
    lines.append(f"- Tone: {tone_label}")
    lines.append("")

    return "\n".join(lines)


__all__ = ["format_writer_context", "format_writer_envelope"]
