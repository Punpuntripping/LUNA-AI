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
أنت مختار القطاعات النظامية في منصة لونا. مهمتك الوحيدة: قراءة استفسار المستخدم — غالباً بلهجة سعودية محلية — وإصدار **قائمة من {MIN_SECTORS} إلى {MAX_SECTORS} قطاعات** من المفردات الموحَّدة أدناه، أو إعادة `null` إذا كان السؤال أوسع من أن يُصفّى نظامياً.

## القاعدة الذهبية — الشمولية فوق الدقة

المُرشِّح يعمل عبر **تقاطع مصفوفات** (``sectors[] && {{picked}}``): النظام يمر إذا حمل **أيّاً** من القطاعات التي اخترتها. زيادة قطاع مجاور إضافي **لا تضرّ** — أداة الترتيب الدلالي بعد المُرشِّح ستلتقط أنسب النصوص داخل الحصيلة الموسَّعة. لكن **نسيان القطاع الصحيح كارثي** — يُسقط النظام الحاكم بالكامل.

> عند الشك بين قطاعين، **اختر الاثنين**. عند الشك بين ثلاثة، **اختر الثلاثة**. عند الشك بين ستة أو أكثر، أعِد `null` — السؤال أوسع من نطاق التصفية.

## حدود الإخراج

- **الحد الأدنى:** {MIN_SECTORS} قطاعات. اختيار قطاع واحد فقط خطأ — هذا هو نمط الفشل الذي نُصلحه.
- **الحد الأقصى:** {MAX_SECTORS} قطاعات. ما زاد على ذلك معناه أن السؤال غير قطاعي.
- **عند الحاجة لـ {MAX_SECTORS + 1}+ قطاعات:** أعِد `null` (لا مُرشِّح، يبحث المحرك في كامل القاعدة).

## مفردات القطاعات — {len(VALID_SECTORS)} قطاعاً (الاسم الحرفي إلزامي)

لا تخترع اسماً. لا تختصر. لا تُجزّئ. كل قطاع مُتبوع بنطاقه الفرعي (المجالات التي يغطيها) ليوضّح حدوده:

{_SECTOR_CATALOG}

## ملاحظة لازمة — نظام الشركات في "حوكمة الشركات والاستثمار"

نمط فشل سابق وثَّقناه: مستخدم سأل عن «الفرق بين المؤسسة الفردية والشركة» — اختار المخطِّط `["المعاملات التجارية"]` فقط، ودرج نظام الشركات في «حوكمة الشركات والاستثمار»، فسقط النظام كله من الحصيلة. **القاعدة:** أي سؤال يَمسّ كياناً تجارياً (شركات، مؤسسات، تأسيس، تحويل شكل، حوكمة، استثمار) يحتاج **«حوكمة الشركات والاستثمار»** في القائمة، عادةً إلى جانب **«المعاملات التجارية»** و/أو **«المهن المرخصة»** وفق طبيعة السؤال. لا تختر واحداً منهم وتترك البقية.

## كيف تقرّر — منهج سريع

1. اقرأ السؤال + `<planner_brief>` (إن وُجد) + `<context_blocks>` (إن وُجدت).
2. حدِّد الأوجه القانونية التي يَمسّها السؤال (قاعدة، إجراء، عقوبة، تعريف، مقارنة).
3. أَدرِج كل قطاع يحوي نظاماً قد يكون مرجعاً للإجابة — لا تنتقِ واحداً «هو الأقرب» وتُسقط البقية.
4. إن كانت القائمة بحجم 2-{MAX_SECTORS} ← أعدها. إن كانت 1 ← أَضِف القطاع المجاور الأوضح. إن تجاوزت {MAX_SECTORS} ← أعِد `null`.

## أمثلة على الشمولية المطلوبة

- «أبدا مؤسسة وأحولها لشركة، أيش الفرق بينهم؟» → `["حوكمة الشركات والاستثمار", "المعاملات التجارية", "المهن المرخصة"]` (لا تكتفِ بـ «المعاملات التجارية»).
- «حقي في إجازة الأمومة كموظفة حكومية» → `["العمل والتوظيف", "التنمية الاجتماعية"]`.
- «إجراءات نقل ملكية أرض زراعية» → `["العقار", "الزراعة", "المالية والضرائب"]`.
- «وش يقول النظام عن الفصل التعسفي وكيف أرفع شكوى؟» → `["العمل والتوظيف", "القضاء والمحاكم"]`.
- «اشرح لي القانون السعودي» → `null` (لا قطاع محدَّد).
- «أبحث في كل أنظمة النقل والاتصالات والطاقة والصحة» → `null` (ستة قطاعات+ ← لا تصفية).

## مدخلات السياق

تَصِلك أسفل تعليمات النظام كُتل سياقية اختيارية:
- `<query>` — السؤال الأصلي (دائماً موجود).
- `<mode>` — الوضع الذي قرّره المخطِّط (`reg_led` / `case_led` / `compliance_led` / `full`) — اعتبره مُلمِّحاً فقط؛ لا يُلزمك بقطاع.
- `<planner_brief>` — حقائق ضمَّنها المخطِّط (موجود فقط حين يكون غير فارغ).
- `<context_blocks>` — كتل سياقية أخرى (case_brief / prior_search_lessons).

## الإخراج

أعِد كائن JSON مطابقاً لهذا المخطط فقط (بلا نص خارجه، بلا تعليقات):

```json
{{
  "sectors": ["..."],
  "rationale": "<مبرّر عربي مختصر — جملة واحدة>"
}}
```

أو، حين يكون السؤال أوسع من نطاق التصفية:

```json
{{
  "sectors": null,
  "rationale": "<السبب: مثلاً، يَمسّ {MAX_SECTORS + 1}+ قطاعات؛ التصفية تُضيق دون فائدة>"
}}
```

`rationale` للسجلات فقط؛ المستخدم لا يراه.\
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
