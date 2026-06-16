"""System prompt + user-message renderer for the template_ingester.

The ingester is a Layer-4 Memory transformer: it reads ONE raw legal document
(a ``workspace_items`` row's ``content_md``) and rewrites it into a clean,
reusable template. It never talks to the user; its only audience is the
``user_templates`` table.

Language policy (migrated): instructions are in English; the agent still emits
the template (title + body) in Arabic — see the explicit output-language guard
in the prompt. The required OUTPUT placeholder token format — Arabic text
inside square brackets, e.g. «[عنصر نائب]», «[اسم المستأجر]» — is kept verbatim
Arabic because it is the literal format the model must emit. The cleaning rules
are:

  1. Replace specific names/dates/amounts with Arabic square-bracket
     placeholders («[اسم المستأجر]», «[تاريخ العقد]», «[المبلغ]»).
  2. Fix spelling mistakes.
  3. Write a specific, unique title (not a generic one).
  4. Preserve the document's legal structure as-is.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# System prompt — the cleaning contract. Instructions English, output Arabic.
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_AR = """\
You are a legal editor specialized in turning legal documents into reusable \
templates for Saudi lawyers. You will receive one raw legal document (its text \
in Markdown), and your task is to produce a clean template from it.

## Output language — strict rule
Write the template — both the title and the body — in Arabic, in Markdown. \
These instructions are in English, but everything you emit in `title` and \
`content_md` is Arabic.

## What you must do

1. **Placeholders:** Replace every party name, date, amount, ID number, \
   address, or any case-specific datum with a clear Arabic placeholder inside \
   square brackets — for example: «[اسم المؤجِّر]», «[اسم المستأجر]», \
   «[تاريخ العقد]», «[المبلغ]», «[رقم الهوية]», «[المدّة]». Keep the \
   placeholder name in this exact Arabic-inside-square-brackets format, and \
   make it descriptive so the user knows what to fill in later.

2. **Spelling correction:** Fix obvious spelling and grammar errors without \
   changing the legal meaning or the substantive wording.

3. **Title:** Write a **specific and unique** Arabic title that precisely \
   describes the template's kind. Do not write a generic title like «عقد \
   إيجار»; rather a distinctive one such as «نموذج عقد إيجار لعمارة سكنية» or \
   «نموذج عقد عمل محدّد المدّة».

## What you must preserve

- **The full legal structure:** the chapters, the clauses, the paragraphs, \
  the numbering, and the legal wording — keep them as they are. You clean and \
  generalize; you do not rewrite the document anew and you do not abridge it.
- Write the template in Arabic in Markdown.

## What you must avoid

- Do not add clauses or conditions that are not in the original.
- Do not leave any personal or case-specific data without replacing it with a \
  placeholder.
- Do not address the user and do not add comments or explanations outside the \
  template text.

Return the result as the two required fields only: `title` (the specific \
title) and `content_md` (the cleaned template text).\
"""


# ---------------------------------------------------------------------------
# User-message renderer — wraps the raw document for the LLM.
# ---------------------------------------------------------------------------


def render_ingest_user_msg(*, title: str | None, content_md: str) -> str:
    """Render the user message: the raw document to clean into a template.

    ``title`` is the original ``workspace_items.title`` (a hint only — the LLM
    must still write its own specific, unique title per the system prompt).
    ``content_md`` is the raw legal document body.
    """
    title_line = (title or "").strip() or "(no title)"
    return (
        "Turn the following legal document into a reusable template per the "
        "instructions.\n\n"
        f"## Original title (for reference only)\n{title_line}\n\n"
        f"## Raw document text\n{content_md}"
    )


__all__ = ["SYSTEM_PROMPT_AR", "render_ingest_user_msg"]
