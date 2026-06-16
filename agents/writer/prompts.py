"""Writer prompt variants -- one per subtype.

All prompts share:
    - Arabic-only output rule (CLAUDE.md rule #5)
    - Section-based structured output (WriterSection)
    - Inline citation rule when research_items are provided: cite by (n)
      where n is the index into the merged references[] of the supplied
      agent_search items.
    - No legal disclaimer in body -- appended at render time.
"""
from __future__ import annotations

import html
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import AnalyzedItem, WriterPackage, WriterInput

logger = logging.getLogger(__name__)


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=False)


_SHARED_ROLE_AR = """\
You are a professional legal writer within the ريحان Saudi legal AI platform.
Your task is to draft complete legal documents, based on:
1. The user's request in <user_request> inside the user message.
2. The writing package prepared by the planner inside <package> in the system
   message, which contains: <plan> (the approved plan), <templates> (templates),
   <sources> (sources), <references> (numbered references), <prior_draft> (a
   previous draft when revising), and <preferences> (style preferences).
3. The task frame (the described request, the task type, the style preferences)
   inside the current-task context block.

## Output language

Write the entire document (every `heading_ar` and every `body_md`) in formal
Modern Standard Arabic (العربية الفصحى), preserving precise legal terminology.
These instructions are in English, but the document you produce is Arabic.
Do NOT write the document in English UNLESS the user explicitly asked for another
language or a bilingual document — in that case follow the user's request.

## General rules

- The document is in formal Modern Standard Arabic (العربية الفصحى), preserving precise legal terminology.
- Do not invent laws, articles, or names of authorities. Cite only what appears
  in <references> or <sources> inside <package>.
- If the user asked you to draft a contract, include the parties, the subject,
  the obligations, the clauses, and the signature.
- If the user asked for a memo, follow the IRAC or CRAC pattern according to the
  nature of the request.
- Do not include the legal disclaimer inside the document -- it is appended programmatically.
- If a `<parties>` block exists in `<package>`, use the names and roles stated
  in it **verbatim** throughout the document. Do not write `[اسم الطرف]` or
  `[اسم المدعي]` when the real name is available in `<parties>`.
- Use the numeric citation `(n)` inside `body_md` only when a matching reference
  actually exists inside a `<refs>` belonging to one of the `<source>`/`<reference>`
  items in the writing package. This is what the lawyer reads directly.
- The `(n)` numeric citations in `body_md` stay as-is — this is what the lawyer
  sees. In the structured `citations_used` field, however, write for each citation
  a pair `{wi: "WI-N", n: K}` linking the number to its source (because the same
  `n` may appear in more than one `<source>`).
"""


_SUBTYPE_BODIES_AR: dict[str, str] = {
    "contract": """\
## Subtype: formal contract

- Begin with "بسم الله الرحمن الرحيم", then the contract title, then the date
  and place of execution.
- Divide the contract into: **الأطراف**, **التمهيد**, **الموضوع**,
  **الالتزامات والشروط**, **مدة العقد**, **التعويض والقيمة**, **حلّ النزاعات**,
  **التوقيعات**.
- Use the boilerplate phrasing of Saudi contracts ("اتفق الطرفان على ما يلي ...").
""",
    "memo": """\
## Subtype: formal legal memo

Follow IRAC: المسألة → القاعدة → التطبيق → النتيجة.
Each section gets a `## ...` heading. Cite every rule-based claim that is
supported by a reference.
""",
    "legal_opinion": """\
## Subtype: legal opinion

- A brief مقدمة on the nature of the consultation.
- عرض الوقائع as the user presented them.
- A تحليل قانوني supported by the references.
- The final التوصية with explicit reservations.
""",
    "defense_brief": """\
## Subtype: defense brief

- بيانات القضية (number, court, parties).
- الوقائع.
- The procedural الدفوع then the substantive ones.
- The closing الطلبات.
""",
    "letter": """\
## Subtype: formal letter

- ترويسة (sender, recipient, subject, date).
- جسم الخطاب (organized paragraphs, a respectful tone).
- The الخاتمة and the signature.
""",
    "summary": """\
## Subtype: summary

- Topical headings (## ...).
- Brief bullet points, no repetition of the source.
- An explicit reference to the file or note it was distilled from.
""",
}


_OUTPUT_CONTRACT_AR = """\
## Output schema

Return JSON matching this structure exactly (with no text outside the JSON).
The `title_ar`, `heading_ar`, and `body_md` values MUST be written in Modern
Standard Arabic — only the JSON field identifiers are English:

```
{
  "title_ar": "عنوان المستند",
  "sections": [
    {"heading_ar": "## الأطراف", "body_md": "..."},
    {"heading_ar": "## الموضوع", "body_md": "..."}
  ],
  "citations_used": [
    {"wi": "WI-2", "n": 5},
    {"wi": "WI-1", "n": 17}
  ],
  "confidence": "high | medium | low",
  "notes_ar": ["نقطة تحتاج مراجعة المستخدم", "..."],
  "chat_summary": "جملة أو جملتان تصفان المستند المُسوَّد — 500 حرف كحد أقصى.",
  "key_findings": [
    "أبرز نقطة يجب على المستخدم مراجعتها",
    "نقطة ثانية",
    "نقطة ثالثة"
  ]
}
```

- `sections` are ordered as they will appear in the final document.
- Do not repeat the full title in `sections[0]` -- it is added from `title_ar`.
- `citations_used` includes every actual citation that appeared in body_md as `(n)` — each entry is a `(wi, n)` pair that pinpoints the source precisely (e.g. `{wi: "WI-2", n: 5}`). The number `n` is the same one shown in `(n)` inside the body; the `wi` field identifies the source item (from `<source wi="WI-N">` in the writing package) to remove ambiguity when sources overlap.
- `chat_summary`: a brief description of the document in **500 characters maximum, strict**. Do not re-draft the whole document.
- `key_findings`: **3 to 5 items maximum, strict**. Each item is a point that needs the user's attention or review. Do not exceed 5 items under any circumstances.
"""


WRITER_PROMPTS: dict[str, str] = {
    sub: f"{_SHARED_ROLE_AR}\n{body}\n{_OUTPUT_CONTRACT_AR}"
    for sub, body in _SUBTYPE_BODIES_AR.items()
}


def get_writer_prompt(subtype: str) -> str:
    """Fetch a system prompt by subtype; raises KeyError on unknown subtypes."""
    if subtype not in WRITER_PROMPTS:
        raise KeyError(
            f"Unknown writer subtype: {subtype!r}. "
            f"Available: {sorted(WRITER_PROMPTS.keys())}"
        )
    return WRITER_PROMPTS[subtype]


def _ws_context_get(ctx: object, key: str, default):
    """Read a field from either a dict or a dataclass-like context block."""
    if ctx is None:
        return default
    if isinstance(ctx, dict):
        return ctx.get(key, default)
    return getattr(ctx, key, default)


def build_writer_user_message(writer_in: "WriterInput") -> str:
    """Render a WriterInput as the LLM user message.

    Layout:
        <user_request>...</user_request>
        <research>
          <ref n="1">...</ref>
          <ref n="2">...</ref>
        </research>
        <workspace_context>
          <note title="...">...</note>
          <attachment title="...">...</attachment>
        </workspace_context>
        <preferences detail_level="..." tone="..." />

    The numbering inside <research> is *global* across all research_items so
    the LLM's `(n)` citations resolve unambiguously.
    """
    lines: list[str] = []

    lines.append("<user_request>")
    lines.append(_esc(writer_in.user_request.strip()))
    lines.append("</user_request>")
    lines.append("")

    if writer_in.research_items:
        lines.append("<research>")
        global_n = 0
        for item in writer_in.research_items:
            refs = (item.get("metadata") or {}).get("references") or []
            for ref in refs:
                global_n += 1
                title = ref.get("title") or ref.get("regulation_title") or ""
                snippet = ref.get("snippet") or ""
                lines.append(f"  <ref n=\"{global_n}\">")
                lines.append(f"    <title>{_esc(title)}</title>")
                lines.append(f"    <snippet>{_esc(snippet)}</snippet>")
                lines.append(f"  </ref>")
        lines.append("</research>")
        lines.append("")

    if writer_in.workspace_context is not None:
        ctx = writer_in.workspace_context
        notes = _ws_context_get(ctx, "notes", []) or []
        attachments = _ws_context_get(ctx, "attachments", []) or []
        if notes or attachments:
            lines.append("<workspace_context>")
            for note in notes:
                title = note.get("title", "") if isinstance(note, dict) else ""
                body = note.get("content_md", "") if isinstance(note, dict) else ""
                lines.append(f"  <note title=\"{_esc(title)}\">")
                lines.append(_esc((body or "").strip()))
                lines.append("  </note>")
            for att in attachments:
                title = att.get("title", "") if isinstance(att, dict) else ""
                extracted = (
                    att.get("extracted_text", "") if isinstance(att, dict) else ""
                )
                lines.append(f"  <attachment title=\"{_esc(title)}\">")
                if extracted:
                    lines.append(_esc(extracted.strip()))
                lines.append("  </attachment>")
            lines.append("</workspace_context>")
            lines.append("")

    lines.append(
        f"<preferences detail_level=\"{_esc(writer_in.detail_level)}\" "
        f"tone=\"{_esc(writer_in.tone)}\" />"
    )
    lines.append("")
    lines.append("Write the complete draft according to the schema above.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# WriterPackage rendering (writer_planner → writing_executor handoff)
# ---------------------------------------------------------------------------
#
# When the planner hands a WriterPackage to the executor, this renderer
# replaces the legacy build_writer_user_message. The legacy path
# is preserved for backward-compat callers (tests, ad-hoc smoke runs).
#
# XML shape (per .claude/plans/writer_planner.md § Updated writer-side XML
# rendering):
#
#     <plan>...</plan>
#     <templates>
#       <template source="user|system" ...>...</template>
#     </templates>
#     <sources>
#       <source kind="..." item_id="..." need="full">
#         {body_md}
#         <refs>{resolved_refs_md}</refs>   <!-- refs-family only; carries
#                                                 every USED ref from the WI
#                                                 so [n] citations in body_md
#                                                 are resolvable. -->
#       </source>
#       <source kind="..." item_id="..." need="partial">
#         {body_md}
#         <refs>{resolved_refs_md}</refs>   <!-- refs-family only; carries
#                                                 the analyzer's refs_needed
#                                                 subset of refs. -->
#       </source>
#       <source kind="..." item_id="..." need="partial">
#         <facts>k: v\n...</facts>            <!-- meta-family only -->
#         {body_md if body_md else ""}
#       </source>
#     </sources>
#     <references>...same shape as <source>...</references>
#     <prior_draft>
#       <body item_id="..." need="full|partial">{body_md}</body>
#     </prior_draft>
#     <user_request>{intent_ar}</user_request>
#     <preferences detail_level="..." tone="..." />
#
# Per the v2 redesign: NO `source='raw|distilled'` attribute. `need` carries
# that information (full → raw passthrough, partial → distilled slice).


_REFS_KINDS: frozenset[str] = frozenset({"agent_search", "agent_writer"})
_META_KINDS: frozenset[str] = frozenset({"attachment", "notes"})


def _render_facts_block(metadata: dict[str, str], indent: str = "  ") -> list[str]:
    """Render extracted_metadata as a <facts>...</facts> block. Empty if no keys."""
    if not metadata:
        return []
    out = [f"{indent}<facts>"]
    for k, v in metadata.items():
        out.append(f"{indent}  {_esc(k)}: {_esc(v)}")
    out.append(f"{indent}</facts>")
    return out


def _render_item_inner(ai: "AnalyzedItem", indent: str = "    ") -> list[str]:
    """Render the inner body of one AnalyzedItem (body_md + refs/facts as needed).

    Caller wraps the result in the appropriate outer tag (<source>, <reference>,
    <body>, <template>).
    """
    lines: list[str] = []
    is_meta = ai.kind in _META_KINDS

    if ai.need == "partial" and is_meta:
        # Meta-family partial: facts FIRST, then optional body_md.
        lines.extend(_render_facts_block(ai.extracted_metadata, indent=indent))
        if ai.body_md.strip():
            lines.append(_esc(ai.body_md.strip()))
    else:
        # full (any family) OR partial refs-family: body_md FIRST.
        if ai.body_md.strip():
            lines.append(_esc(ai.body_md.strip()))
        # Refs-family items render <refs>...</refs> whenever resolved_refs_md
        # is populated — regardless of need. Full refs-family items get every
        # used [n] from workspace_item_references unfolded (so the writer can
        # ground citations copied forward from the raw body). Partial refs-
        # family items get the analyzer's refs_needed subset. Meta-family
        # items don't have references and skip this block.
        if (
            not is_meta
            and ai.resolved_refs_md
            and ai.resolved_refs_md.strip()
        ):
            lines.append(f"{indent}<refs>")
            lines.append(_esc(ai.resolved_refs_md.strip()))
            lines.append(f"{indent}</refs>")

    return lines


def _render_analyzed_item(
    ai: "AnalyzedItem",
    *,
    outer_tag: str,
    extra_attrs: dict[str, str] | None = None,
    indent: str = "  ",
) -> list[str]:
    """Wrap one AnalyzedItem in its outer tag with attributes.

    `outer_tag` is the tag name (source / reference / template / body).
    For prior_draft the caller passes outer_tag='body' (no kind attribute).

    Per the agent communication protocol, the LLM-facing handle for a
    workspace item is the ``WI-{wi_seq}`` alias — not the raw item_id UUID.
    When ``ai.wi_seq`` is populated, emit ``wi="WI-N"``. As a defensive
    fallback for legacy items predating migration 052 (no ``wi_seq``),
    fall back to emitting ``item_id="UUID"`` (and log a warning) so the
    LLM still has *some* per-source handle.
    """
    attrs: list[str] = []
    if outer_tag != "body":
        attrs.append(f'kind="{_esc(ai.kind)}"')
    if ai.wi_seq is not None:
        attrs.append(f'wi="WI-{int(ai.wi_seq)}"')
    else:
        # Defensive fallback: pre-migration-052 row OR case-only item OR
        # constructed without wi_seq in a test. Emit the raw item_id so
        # the prompt still carries a handle, but warn so the protocol
        # violation is observable.
        logger.warning(
            "writer.prompts: AnalyzedItem item_id=%s has no wi_seq — "
            "falling back to item_id attribute (UUID will reach the LLM).",
            ai.item_id,
        )
        attrs.append(f'item_id="{_esc(ai.item_id)}"')
    attrs.append(f'need="{_esc(ai.need)}"')
    if extra_attrs:
        for k, v in extra_attrs.items():
            attrs.append(f'{k}="{_esc(v)}"')
    open_tag = f"{indent}<{outer_tag} {' '.join(attrs)}>"
    close_tag = f"{indent}</{outer_tag}>"
    return [open_tag, *_render_item_inner(ai, indent=indent + "  "), close_tag]


_PACKAGE_PREAMBLE_AR = "The following is the writing package prepared by the planner:"


def render_package_for_system_prompt(package: "WriterPackage") -> str:
    """Render a WriterPackage as a system-prompt block.

    Wraps the package content (plan + templates + sources + references +
    prior_draft + preferences) in a top-level ``<package>...</package>`` tag,
    preceded by a one-line preamble so the model can locate the block.

    Excluded from this rendering (they now live in the *user* message):
      * ``<user_request>`` — built by ``build_writer_user_message_minimal``.
      * The trailing "Write the complete draft …" directive — also user-side.

    Per ``.claude/plans/writer_redesign.md`` § Dynamic instructions:
    this function is called from the ``@agent.instructions`` callable
    ``package_content_block`` in ``agent.py``. It's a pure render — no I/O.

    Block order inside ``<package>``: ``<plan>`` → ``<templates>`` →
    ``<sources>`` → ``<references>`` → ``<prior_draft>`` → ``<preferences>``.
    Same shape as the legacy ``build_writer_user_message_from_package`` minus
    the user-request and directive trailer.
    """
    lines: list[str] = []
    lines.append(_PACKAGE_PREAMBLE_AR)
    lines.append("<package>")

    # 0. <parties> — confirmed case parties (name + role), validated by the planner.
    if package.parties:
        lines.append("  <parties>")
        for p in package.parties:
            lines.append(f"    <party name=\"{_esc(p.name)}\" role=\"{_esc(p.role)}\" />")
        lines.append("  </parties>")
        lines.append("")

    # 1. <plan> — the user-approved plan (or planner-committed plan in clean-turn path).
    if package.plan_md.strip():
        lines.append("  <plan>")
        lines.append(_esc(package.plan_md.strip()))
        lines.append("  </plan>")
        lines.append("")

    # 2. <templates> — user-attached this turn (role='template' AnalyzedItems,
    #    source="user") + قوالبي library picks (TemplateRef from
    #    package.templates, source="library").
    user_templates = package.user_templates()
    library_templates = package.templates
    if user_templates or library_templates:
        lines.append("  <templates>")
        for tmpl in user_templates:
            # User-attached template: stored as an AnalyzedItem with role='template'.
            attrs = {"source": "user"}
            lines.extend(
                _render_analyzed_item(
                    tmpl, outer_tag="template", extra_attrs=attrs, indent="    "
                )
            )
        for lib_tmpl in library_templates:
            # قوالبي pick: stored as a TemplateRef (no need/role concept; optional type).
            type_attr = (
                f' type="{_esc(lib_tmpl.template_type)}"'
                if lib_tmpl.template_type
                else ""
            )
            lines.append(
                f'    <template source="library" template_id="{_esc(lib_tmpl.template_id)}"'
                f'{type_attr} title="{_esc(lib_tmpl.title)}">'
            )
            if lib_tmpl.body_md.strip():
                lines.append(_esc(lib_tmpl.body_md.strip()))
            lines.append("    </template>")
        lines.append("  </templates>")
        lines.append("")

    # 3. <sources> — role='source' AnalyzedItems.
    sources = package.sources()
    if sources:
        lines.append("  <sources>")
        for src in sources:
            lines.extend(_render_analyzed_item(src, outer_tag="source", indent="    "))
        lines.append("  </sources>")
        lines.append("")

    # 4. <references> — role='reference' AnalyzedItems.
    references = package.references()
    if references:
        lines.append("  <references>")
        for ref in references:
            lines.extend(_render_analyzed_item(ref, outer_tag="reference", indent="    "))
        lines.append("  </references>")
        lines.append("")

    # 5. <prior_draft> — at most one role='prior_draft' AnalyzedItem.
    prior = package.prior_draft()
    if prior is not None:
        lines.append("  <prior_draft>")
        lines.extend(_render_analyzed_item(prior, outer_tag="body", indent="    "))
        lines.append("  </prior_draft>")
        lines.append("")

    # 6. <preferences>
    lines.append(
        f'  <preferences detail_level="{_esc(package.style.detail_level)}" '
        f'tone="{_esc(package.style.tone)}" '
        f'edit_mode="{_esc(package.edit_mode)}" />'
    )

    lines.append("</package>")
    return "\n".join(lines)


def build_writer_user_message_minimal(package: "WriterPackage") -> str:
    """Render the *minimal* user message for the writer executor.

    Carries only the planner-distilled intent inside ``<user_request>``
    plus a one-line directive that points the model at the system-prompt
    ``<package>`` block. All structured content (plan, templates, sources,
    references, prior_draft, preferences) lives in the system prompt via
    ``render_package_for_system_prompt`` — see ``.claude/plans/writer_redesign.md``
    § User message.

    Length: a few hundred bytes at most.
    """
    lines: list[str] = []
    lines.append("<user_request>")
    lines.append(_esc(package.intent_ar.strip()))
    lines.append("</user_request>")
    lines.append("")
    lines.append("Write the complete draft according to what is in <package> above.")
    return "\n".join(lines)


def build_writer_user_message_from_package(package: "WriterPackage") -> str:
    """DEPRECATED — kept only for backward-compat with any external import.

    Use ``render_package_for_system_prompt`` (for the system block) and
    ``build_writer_user_message_minimal`` (for the user message) instead.
    See ``.claude/plans/writer_redesign.md`` § File manifest.

    This thin alias concatenates the two so a straggler caller still gets
    the merged shape (everything in one string). New code should NOT use it.
    """
    return (
        render_package_for_system_prompt(package)
        + "\n\n"
        + build_writer_user_message_minimal(package)
    )


__all__ = [
    "WRITER_PROMPTS",
    "get_writer_prompt",
    "build_writer_user_message",
    "build_writer_user_message_minimal",
    "render_package_for_system_prompt",
    "build_writer_user_message_from_package",
]
