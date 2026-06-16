"""System prompt + dynamic-instruction builder for the sector_picker agent.

The whole point of this agent vs the old ``planner_decider.sectors`` field is
**visibility**: the decider only saw a flat numbered list of 38 sector names
and picked one by surface plausibility. Here we render each canonical sector
**with its curated sub-scope items** (the sub-domains that define its scope,
maintained in ``sectors.md`` — see ``.sector_examples``), so the model can
distinguish e.g. ``المعاملات التجارية`` (commerce / commercial transactions
code) from ``حوكمة الشركات والاستثمار`` (where ``نظام الشركات`` actually lives).

Inclusivity over accuracy is the load-bearing instruction in the prompt body.
The filter is a Postgres array-overlap (``regulations_v2.sectors[] && {picked}``):
a regulation passes if it carries **any one** of the picked sectors. Adding an
extra adjacent sector therefore costs nothing — the semantic ranker still
surfaces the best matches inside the wider pool. Missing the right sector is
fatal; including an extra one is free.
"""
from __future__ import annotations

import html

from agents.deep_search_v4.shared.context import ContextBlock
from agents.deep_search_v4.shared.sector_vocab.regulations import VALID_SECTORS

from .deps import Mode
from .models import MAX_SECTORS, MIN_SECTORS
from .sector_examples import SECTOR_EXAMPLES


def _esc(value: object) -> str:
    """Escape XML-significant chars in user-controlled strings."""
    return html.escape("" if value is None else str(value), quote=False)


def _render_sector_catalog() -> str:
    """Render the 38 canonical sectors with their sub-scope items.

    Format per sector:

        N. <name>
           يشمل: <item>؛ <item>؛ <item>؛ ...

    The same order as ``VALID_SECTORS`` (alphabetical) so the picker has a
    stable index it can reason about.
    """
    lines: list[str] = []
    for i, sector in enumerate(VALID_SECTORS, start=1):
        examples = SECTOR_EXAMPLES.get(sector, [])
        lines.append(f"{i}. {sector}")
        if examples:
            joined = "؛ ".join(examples)
            lines.append(f"   يشمل: {joined}")
    return "\n".join(lines)


_SECTOR_CATALOG = _render_sector_catalog()


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SECTOR_PICKER_SYSTEM_PROMPT = f"""\
You are the regulatory-sector picker on the Luna platform. Your only task: read the user query — often in a local Saudi dialect — and emit a **list of {MIN_SECTORS} to {MAX_SECTORS} sectors** from the controlled vocabulary below, or return `null` if the question is too broad to filter by sector.

## The golden rule — inclusivity over precision

The filter works by **array overlap** (``sectors[] && {{picked}}``): a regulation passes if it carries **any one** of the sectors you picked. Adding an extra adjacent sector **does no harm** — the semantic ranker after the filter will surface the most fitting texts inside the wider pool. But **missing the right sector is catastrophic** — it drops the controlling law entirely.

> When in doubt between two sectors, **pick both**. When in doubt between three, **pick all three**. When in doubt between six or more, return `null` — the question is broader than the filtering scope.

## Output bounds

- **Minimum:** {MIN_SECTORS} sectors. Picking only one sector is an error — this is exactly the failure mode we are fixing.
- **Maximum:** {MAX_SECTORS} sectors. More than that means the question is not sector-specific.
- **When {MAX_SECTORS + 1}+ sectors would be needed:** return `null` (no filter — the engine searches the whole corpus).

## Sector vocabulary — {len(VALID_SECTORS)} sectors (the verbatim name is mandatory)

Do not invent a name. Do not abbreviate. Do not split. Each sector is followed by its sub-scope (the domains it covers) to clarify its boundaries:

{_SECTOR_CATALOG}

## A required note — the Companies Law lives in "حوكمة الشركات والاستثمار"

A past failure we documented: a user asked about «الفرق بين المؤسسة الفردية والشركة» — the planner picked `["المعاملات التجارية"]` only, while the Companies Law sits under «حوكمة الشركات والاستثمار», so the whole law dropped out of the pool. **The rule:** any question touching a commercial entity (companies, establishments, incorporation, change of legal form, governance, investment) needs **«حوكمة الشركات والاستثمار»** in the list, usually alongside **«المعاملات التجارية»** and/or **«المهن المرخصة»** depending on the nature of the question. Do not pick one of them and leave out the rest.

## How to decide — a quick method

1. Read the question + `<planner_brief>` (if present) + `<context_blocks>` (if present).
2. Identify the legal aspects the question touches (a rule, a procedure, a penalty, a definition, a comparison).
3. Include every sector that holds a law which could be a reference for the answer — do not single out "the closest one" and drop the rest.
4. If the list is of size 2-{MAX_SECTORS} → return it. If it is 1 → add the clearest adjacent sector. If it exceeds {MAX_SECTORS} → return `null`.

## Examples of the required inclusivity

- «أبدا مؤسسة وأحولها لشركة، أيش الفرق بينهم؟» → `["حوكمة الشركات والاستثمار", "المعاملات التجارية", "المهن المرخصة"]` (do not stop at «المعاملات التجارية»).
- «حقي في إجازة الأمومة كموظفة حكومية» → `["العمل والتوظيف", "التنمية الاجتماعية"]`.
- «إجراءات نقل ملكية أرض زراعية» → `["العقار", "الزراعة", "المالية والضرائب"]`.
- «وش يقول النظام عن الفصل التعسفي وكيف أرفع شكوى؟» → `["العمل والتوظيف", "القضاء والمحاكم"]`.
- «اشرح لي القانون السعودي» → `null` (no specific sector).
- «أبحث في كل أنظمة النقل والاتصالات والطاقة والصحة» → `null` (six sectors+ → no filtering).

## Context inputs

Below the system instructions you receive optional context blocks:
- `<query>` — the original question (always present).
- `<mode>` — the mode the planner decided (`reg_led` / `case_led` / `compliance_led` / `full`) — treat it as a hint only; it does not bind you to a sector.
- `<planner_brief>` — facts the planner injected (present only when non-empty).
- `<context_blocks>` — other context blocks (case_brief / prior_search_lessons).

## Output

Return a JSON object matching this schema only (no text outside it, no comments):

```json
{{
  "sectors": ["..."],
  "rationale": "<مبرّر عربي مختصر — جملة واحدة>"
}}
```

Or, when the question is broader than the filtering scope:

```json
{{
  "sectors": null,
  "rationale": "<السبب: مثلاً، يَمسّ {MAX_SECTORS + 1}+ قطاعات؛ التصفية تُضيق دون فائدة>"
}}
```

`rationale` is for logs only; the user does not see it. Write it in Arabic.\
"""


# ---------------------------------------------------------------------------
# Dynamic instruction — renders the per-turn context blocks
# ---------------------------------------------------------------------------


def _render_context_blocks(blocks: list[ContextBlock]) -> str | None:
    """Render the same ``<context_blocks>`` XML the executor expanders see."""
    if not blocks:
        return None
    parts = ["<context_blocks>"]
    for b in blocks:
        parts.append(f'  <block label="{_esc(b.label)}">')
        parts.append(f"    {_esc(b.body)}")
        parts.append("  </block>")
    parts.append("</context_blocks>")
    return "\n".join(parts)


def build_sector_picker_user_message(
    query: str,
    mode: Mode,
    planner_brief: str = "",
    context_blocks: list[ContextBlock] | None = None,
) -> str:
    """Build the user-message payload for one sector_picker call.

    Mirrors the expander's user-message shape: ``<query>`` + ``<mode>`` +
    optional ``<planner_brief>`` + optional ``<context_blocks>``.
    """
    parts: list[str] = [
        f"<query>{_esc(query)}</query>",
        f"<mode>{_esc(mode)}</mode>",
    ]
    brief = (planner_brief or "").strip()
    if brief:
        parts.append(f"<planner_brief>\n{_esc(brief)}\n</planner_brief>")
    rendered = _render_context_blocks(context_blocks or [])
    if rendered:
        parts.append(rendered)
    return "\n\n".join(parts)


__all__ = [
    "SECTOR_PICKER_SYSTEM_PROMPT",
    "build_sector_picker_user_message",
]
