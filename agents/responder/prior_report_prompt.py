"""Prompt and package for the ``prior_report`` mode (plan artifact_analyzer.md §4.6).

Its own module so ``prompts.py`` keeps one line per mode. Leaf module: it imports
nothing from the responder package, so both ``prompts.py`` and ``prior_report.py``
can use it.
"""
from __future__ import annotations

import html
from typing import Any

PRIOR_REPORT_BLOCK = """\
# This turn: answering from what we already hold

Work done earlier in this conversation bears on the user's message. The turn package gives the question, the exact passages, what we do not hold, and any facts only the user can give. **Code prints the passages word for word under your text, each with its source.** Your job is the plain sentence above them.

You emit five fields: `chat_summary_md`, `next_steps`, `follow_up_question`, `question_basis`, `search_offer`.

## `chat_summary_md`

- One or two plain sentences that tell this user what the passages mean for their case. The passages carry the legal text; you carry the meaning.
- Do not repeat or reword the passages. State no rule, article, number or deadline that is not in them.
- Do not mention a card, and do not use a closing line.
- When the verdict is `partial`, add one sentence on what the passages do not settle, taken from `<missing>`. Say only that we do not have it. Never say or imply what a search would find, change or confirm.
- When the package has corrections, on any verdict: add one sentence for each, saying what the full text shows, as a correction of our earlier reading, for example «عند الرجوع إلى النص الكامل للحكم تبيّن أن…». Code prints the full text's words under your text.

## `follow_up_question`

From `<ask_user>` only: the single fact that would change the answer most. null when that list is empty, and always null when the verdict is `partial`, because the offer is the question.

## `search_offer`

When the verdict is `partial`: one short question offering to search for the missing part, naming it. It promises nothing about the result, for example «تبغاني أبحث عن مدة التظلم في النظام القديم؟». When the verdict is `sufficient`: null.

## `next_steps` for this mode

Allowed kinds: `draft`, `apply`, and `answer` with a follow-up question. **Never `open` or `narrow_search`.** On `partial`, code adds the yes and no chips, so never add them yourself.
"""

CLOSING_INSTRUCTION = (
    "Now write `chat_summary_md`, `next_steps`, `follow_up_question`, `question_basis` "
    "and `search_offer` (all user-facing text in Arabic), according to the system rules "
    "above."
)


def _esc(value: object) -> str:
    return html.escape(str(value or ""), quote=False)


def _bullets(tag: str, items: list[str]) -> str:
    if not items:
        return f"<{tag}/>"
    lines = "\n".join(f"- {_esc(i)}" for i in items)
    return f"<{tag}>\n{lines}\n</{tag}>"


def render_prior_report_package(artifact: Any) -> str:
    passages = "\n".join(
        f"<passage source=\"{_esc(q.source_name)}\">{_esc(q.text)}</passage>"
        for q in artifact.quotes
    )
    parts = [
        f"## The question\n{_esc(artifact.question)}",
        f"## Verdict\n{artifact.verdict}",
        f"## The passages (code prints these; do not repeat them)\n{passages or '(none)'}",
        f"## What we do not hold\n{_bullets('missing', artifact.missing)}",
        f"## Facts only the user can give\n{_bullets('ask_user', artifact.ask_user)}",
    ]
    corrections = getattr(artifact, "corrections", None) or []
    if corrections:
        parts.append(
            "## Corrections to our earlier reading\n" + "\n".join(
                f"<correction source=\"{_esc(c.source_name)}\">"
                f"<report_said>{_esc(c.report_text)}</report_said>"
                f"<full_text>{_esc(c.corrected_text)}</full_text></correction>"
                for c in corrections
            )
        )
    if artifact.version_rule:
        parts.append(artifact.version_rule)
    return "\n\n".join(parts)
