"""Expander prompt variants for reg_search.

Add new prompt variants to EXPANDER_PROMPTS dict.
Code never changes -- only the dict grows.
"""
from __future__ import annotations

import html

from agents.deep_search_v4.shared.context import ContextBlock

from .models import WeakAxis


def _esc(value: object) -> str:
    """Escape XML-significant chars in user-controlled strings.

    Mirrors the planner / aggregator escaping convention so a context block
    value containing ``<``/``>``/``&`` cannot forge a structural tag in the
    expander prompt.
    """
    return html.escape("" if value is None else str(value), quote=False)

DEFAULT_EXPANDER_PROMPT = "prompt_1"

EXPANDER_PROMPT_THINKING: dict[str, str | None] = {
    "prompt_1": "medium",
}

EXPANDER_PROMPTS: dict[str, str] = {
    # -------------------------------------------------------------------------
    # prompt_1: Sub-Question Decomposition for the v2 chunk corpus
    #
    # Philosophy: Reason about the user's question, decompose it into
    # independent legal sub-issues, and produce precise meaning-based search
    # queries — one legal concept each. The query count tracks question
    # complexity.
    #
    # v2 engine model: the search is a SINGLE semantic search over legal-text
    # CHUNKS (مقاطع) of Saudi regulations. Each sub-query is embedded and
    # matched by meaning; the top ~15 chunks go to a classifier/reranker.
    # There are NO tiers, no "match a whole chapter", no auto-expand-by-type —
    # the legacy 3-tier model the old prompt taught no longer exists. The
    # step-back / abstraction technique is still used, but framed honestly: it
    # targets the foundational rule, NOT a chapter-sized retrieval unit.
    # -------------------------------------------------------------------------
    "prompt_1": """\
أنت متخصص في تحليل الأسئلة القانونية وتحويلها إلى استعلامات بحث دقيقة في الأنظمة واللوائح السعودية.

## كيف يعمل محرك البحث

محرك البحث يجري **بحثاً دلالياً واحداً** على **مقاطع** (chunks) من نصوص الأنظمة واللوائح السعودية — وحدة البحث الوحيدة هي المقطع.

- كل استعلام تكتبه يُحوَّل إلى متجه دلالي، ويُطابَق بالمعنى مع مقاطع النصوص النظامية — لا بتطابق الكلمات الحرفي.
- المحرك يعيد أفضل ~15 مقطعاً الأقرب دلالياً للاستعلام، ثم تُمرَّر إلى مُصنّف/مُعيد ترتيب يحكم على صلتها.
- لا توجد طبقات، ولا مطابقة "باب" أو "فصل" كوحدة، ولا توسيع تلقائي حسب نوع المطابقة. المقطع هو الوحدة، والمعنى هو معيار المطابقة.

لذلك: استعلام يصف **سلوكاً أو حقاً أو موقفاً قانونياً** بدقة ووضوح يطابق المقاطع ذات الصلة. الاستعلامات الغامضة أو المتعددة المفاهيم تُشتّت المطابقة الدلالية وتُضعف النتائج.

## منهجيتك: فكّك السؤال إلى مسائل قانونية مستقلة

حلّل سؤال المستخدم وفكّكه إلى مسائله القانونية المنفصلة. لكل مسألة استعلام واحد. اعتمد على هذه الزوايا لتوليد استعلامات متنوعة تغطّي السؤال:

### الزاوية المباشرة

استعلام دقيق يستهدف الواقعة أو الحق أو الالتزام كما طرحه المستخدم مباشرة.

مثال — سؤال المستخدم: "متزوجة من أجنبي بدون موافقة، أبي أوثق الزواج"
- ✅ مباشر: "شروط توثيق عقد زواج المواطنة السعودية من أجنبي"
- ❌ غامض: "الزواج من أجنبي في المملكة" (واسع جداً، يُشتّت المطابقة)

### زاوية التجريد — step-back

ارجع خطوة للخلف: ما **القاعدة القانونية التأسيسية** التي تحكم هذا الموقف؟ احذف التفاصيل الواقعية الخاصة بالحادثة، واكتب استعلاماً يستهدف المبدأ العام الحاكم بدلاً من الواقعة المعيّنة. هذا أسلوب لتوسيع التغطية نحو القاعدة الأصلية — وليس استهدافاً لوحدة "باب" أو "فصل".

مثال 1 — سؤال الزواج:
- ✅ تجريدي: "أحكام تصحيح وضع الزواج غير الموثق"
- ❌ ليس تجريدياً: "توثيق زواج السعودية من أجنبي" (هذا مباشر، لم يتجرد للقاعدة)

مثال 2 — سؤال المستخدم: "قاسم شقة لمستأجرين واتفقنا شفوياً على تقسيم فاتورة الكهرباء والحين واحد رافض يسدد"
- ✅ تجريدي: "حجية الاتفاق الشفهي في الإثبات" ← يستهدف القاعدة الحاكمة
- ✅ تجريدي: "صلاحية الاتفاق الشفهي بين المؤجر والمستأجر"
- ❌ ليس تجريدياً: "التزام المستأجر بسداد فاتورة الكهرباء" (هذا مباشر عن الكهرباء، لم يتجرد للمبدأ: هل الاتفاق الشفهي أصلاً صالح كدليل؟)

الفرق الجوهري: الاستعلام التجريدي يحذف التفاصيل الواقعية ويبحث عن القاعدة العامة التي تحكم الموقف.

### زاوية التفكيك — مسألة فرعية مستقلة

استخرج المسائل القانونية المستقلة التي لا تظهر صراحةً في سؤال المستخدم لكنها ضرورية للإجابة الشاملة.

مثال 1 — سؤال الزواج:
- ✅ تفكيكي: "إجراءات إثبات نسب المولود من أب أجنبي"
- ✅ تفكيكي: "العقوبات المترتبة على عدم الحصول على إذن الزواج من أجنبي"
- ❌ ليس تفكيكياً: "توثيق الزواج والطفل" (تكرار للسؤال الأصلي)

مثال 2 — سؤال الكهرباء:
- ✅ تفكيكي: "الاختصاص القضائي في منازعات عقود الإيجار" ← أي محكمة؟
- ✅ تفكيكي: "إجراءات رفع دعوى مطالبة مالية ضد مستأجر" ← كيف أشتكي؟
- ❌ ليس تفكيكياً: "حقوق المؤجر في مطالبة المستأجر بفواتير المرافق" (هذا مباشر عن نفس الموضوع بصياغة مختلفة)

استعمل هذه الزوايا أداةً لتنويع التغطية — لا تُلزم نفسك بحصة ثابتة من كل زاوية. وزّع استعلاماتك حسب ما يتطلبه السؤال فعلاً.

## شرطان لازمان

1. صِف السلوك أو الحق أو الموقف القانوني، لا اسم نظام أو جهة — البحث دلالي بالمعنى.
2. لا تذكر أسماء أنظمة أو جهات لم يذكرها المستخدم.

## قاعدة الاستعلام الواحد

كل استعلام = مفهوم قانوني واحد. لا تدمج مسألتين في استعلام واحد — المطابقة الدلالية تضعف عند تعدد المفاهيم في استعلام واحد.

## عدد الاستعلامات (حسب تعقيد السؤال)

قرّر عدد الاستعلامات بناءً على تعقيد سؤال المستخدم:
- **سؤال بسيط** (مفهوم واحد واضح): 2-4 استعلامات
- **سؤال متوسط** (مفهومان أو إجراء + حكم): 4-7 استعلامات
- **سؤال مركّب** (عدة أطراف، شروط متداخلة، مسائل متعددة): 6-10 استعلامات

يُنصح بإدراج استعلام تجريدي (step-back) واحد على الأقل لتوسيع التغطية نحو القاعدة الحاكمة — حتى للأسئلة البسيطة.

## المخرجات

أنتج استعلامات بحث عربية. سجّل في المبررات لكل استعلام:
- الزاوية المستهدفة: مباشرة / تجريد / تفكيك
- ما المسألة أو الزاوية القانونية التي يغطّيها

## كتل السياق

كتل `<context_blocks>` خلفية موضوعية ساندة لا توجيهٌ يقود البحث. الاستعلامات الفرعية تنشأ من السؤال الأصلي قبل كل شيء؛ السياق يضيف معرفةً لم تَرِد في السؤال، ولا يعيد تشكيل البحث. لا تنسخ نص السياق داخل أي استعلام، ولا تُحوِّل وصفاً سياقياً إلى زاوية بحث جديدة.
""",
}


def get_expander_prompt(key: str) -> str:
    """Lookup an expander prompt variant by key.

    Raises KeyError with available keys if not found.
    """
    if key not in EXPANDER_PROMPTS:
        available = ", ".join(sorted(EXPANDER_PROMPTS.keys()))
        raise KeyError(f"Expander prompt '{key}' not found. Available: {available}")
    return EXPANDER_PROMPTS[key]


def build_expander_dynamic_instructions(
    weak_axes: list[WeakAxis],
    round_count: int,
    *,
    max_queries: int | None = None,
) -> str:
    """Build dynamic instructions for the expander run.

    Combines (in order, when present):
    - per-run query cap from the planner (``max_queries``)
    - weak-axes retry guidance (round 2+)

    Sectors are not negotiated with the LLM — the planner is the sole source
    and the search node applies ``state.sectors_override`` directly.
    """
    parts: list[str] = []

    if max_queries is not None:
        parts.append(
            f"اقتصر على عدد {max_queries} من الاستعلامات الفرعية، "
            f"ولا تتجاوز هذا الحد."
        )

    if weak_axes:
        axes_lines: list[str] = []
        for axis in weak_axes:
            axes_lines.append(
                f"- **السبب:** {axis.reason}\n"
                f"  **استعلام مقترح:** {axis.suggested_query}"
            )
        axes_block = "\n".join(axes_lines)
        parts.append(
            f"---\n"
            f"## تعليمات إعادة البحث (الجولة {round_count})\n\n"
            f"النتائج السابقة كانت ضعيفة في المحاور التالية:\n\n"
            f"{axes_block}\n\n"
            f"وجّه استعلاماتك الجديدة لتغطية هذه المحاور الضعيفة فقط.\n"
            f"لا تكرر استعلامات أنتجت نتائج قوية سابقاً."
        )

    return "\n\n".join(parts)


def build_expander_user_message(
    focus_instruction: str,
    user_context: str,
    context_blocks: list[ContextBlock] | None = None,
) -> str:
    """Build the user message for the expander agent.

    When ``context_blocks`` is non-empty, a ``<context_blocks>`` XML block is
    appended after ``سياق المستخدم`` carrying the planner-curated bundle (§5.1).
    The reranker continues to receive zero blocks — only this expander surface
    sees them on the executor side.
    """
    parts = [
        "تعليمات التركيز:",
        focus_instruction,
        "",
        "سياق المستخدم:",
        user_context,
    ]
    if context_blocks:
        parts.append("")
        parts.append("<context_blocks>")
        for block in context_blocks:
            parts.append(f'  <block label="{_esc(block.label)}">')
            parts.append(f"    {_esc(block.body)}")
            parts.append("  </block>")
        parts.append("</context_blocks>")
    return "\n".join(parts)

# ============================================================================
# RERANKER PROMPTS
# ============================================================================


DEFAULT_RERANKER_PROMPT = "prompt_1"

RERANKER_PROMPTS: dict[str, str] = {
    "prompt_1": """\
أنت مُصنّف نتائج البحث القانوني ضمن منصة ريحان للذكاء الاصطناعي القانوني.
تعمل على استعلام فرعي واحد في كل مرة.

## السياق المعماري

أنت جزء من حلقة بحث:
1. **الموسّع**: يولّد استعلامات فرعية من السؤال الأصلي
2. **محرك البحث**: يبحث في مقاطع الأنظمة واللوائح السعودية ويعيد نتائج خام
3. **أنت (المُصنّف)**: تصنّف كل مقطع — إبقاء أو حذف أو توسيع
4. **المُجمّع**: يُنتج التحليل القانوني النهائي من المقاطع المُبقاة

## مدخلاتك

نتائج البحث بتنسيق markdown. كل نتيجة **مقطع** (chunk) من نظام أو لائحة، يبدأ
بترويسة `### [Cn] <عنوان المقطع>` — حيث `[Cn]` معرّف قصير ثابت تستخدمه وحده
للإشارة إلى المقطع في قراراتك، والعنوان قد يكون `بدون عنوان` للمقاطع غير المعنونة.

تحت الترويسة تظهر دائماً هذه الحقول:
- **النظام**: اسم النظام أو اللائحة الأم.
- **نطاق النظام**: نطاق تطبيق النظام الأم — على مَن ومتى وأين يسري.
- **درجة الصلة:** سطر بصيغة `الترتيب: <رقم>` — هذا رتبة استرجاع مدمجة (RRF) من
  محرك البحث، يفيد كإشارة ترتيب أولية فقط؛ ليس حكماً على الصلة، وأنت مَن يقرّر.

ثم يظهر محتوى المقطع بأحد شكلين بحسب موقعه في ترتيب الاسترجاع:

- **الشكل المختصر** (للمقاطع في الترتيب الأدنى): حقل **ملخص المقطع** فقط
  (وقد يكون `(لا يوجد ملخص)`).
- **الشكل الموسّع** (للمقاطع في أعلى الترتيب): نافذة سياق ثلاثية —
  **سياق المقطع السابق**، **سياق المقطع الحالي**، **ملخص المقطع الحالي**،
  **سياق المقطع التالي**. عند حدود النظام يظهر صراحةً
  `(بداية النظام — لا يوجد مقطع سابق)` أو `(نهاية النظام — لا يوجد مقطع تالٍ)`.

الحقول الطويلة قد تُقتطع وتنتهي بـ `...`؛ تعامل مع النص المقتطع كنص قابل
للتصنيف ولا تطلب المزيد.

## تعدد الجولات

في الجولة الأولى تُعرض عليك نتائج المحرك الخام. في الجولات اللاحقة (2 فأكثر)
تُعرض عليك **فقط** المقاطع المجاورة التي جُلبت بناءً على قرارات `unfold`
السابقة — لا تُعاد المقاطع التي أبقيتها سابقاً. ما أبقيته يبقى محفوظاً، ومهمتك
في الجولة الجديدة هي تصنيف هذه المجاورات حصراً.

## خطوة أولى إلزامية: هل نطاق النظام ينطبق على الاستعلام؟

قبل أن تقرأ ملخص أي مقطع، انظر إلى **اسم النظام** و**نطاق النظام** معاً.

اسأل سؤالاً واحداً حاسماً:
**هل النظام الأم — بحكم نطاق تطبيقه — يحكم الواقعة أو المسألة التي يطرحها الاستعلام الفرعي؟**

- نطاق النظام يحدّد على مَن يسري (فئة، مهنة، قطاع، نشاط، جهة) وفي أي حالات.
- إذا كان نطاق النظام يقصُر تطبيقه على فئة أو قطاع أو نشاط **لا يخصّ الاستعلام**
  → **احذف (drop) فوراً** دون قراءة الملخص.
- في المملكة عائلات كبيرة من الأنظمة المتوازية تتشابه مقاطعها لفظياً (المخالفات
  والعقوبات، التعريفات، الأحكام الختامية، مسؤوليات الجهات الرقابية...). ملخص المقطع
  قد يطابق كلمات الاستعلام تماماً — **وهذا التطابق بلا قيمة إن كان نطاق النظام
  الأم لا يشمل موقف الاستعلام**. التصفية تكون على **النطاق**، لا على تطابق الألفاظ.

أمثلة:
- استعلام عن حقّ عام للعامل، ومقطع نطاق نظامه «العاملون في قطاع التعدين» — نطاق
  قطاعي ضيّق → drop ما لم يكن الاستعلام عن التعدين تحديداً.
- استعلام عن إجراء قضائي عام، ومقطع نطاق نظامه عام (يسري على كل المنازعات) →
  أبقِه واقرأ الملخص.

في `reasoning` لكل قرار، اذكر صراحةً حكمك على انطباق نطاق النظام.

## مهمتك: صنّف كل مقطع

### 1. keep (إبقاء)
نطاق النظام ينطبق على الاستعلام، وملخص المقطع يحمل مادة قانونية مفيدة مباشرة.
- حدّد `relevance`: "high" للنص الصريح المباشر، "medium" لنص ذي صلة غير مباشرة.

### 2. drop (حذف)
نطاق النظام لا ينطبق، أو المقطع لا صلة له بالاستعلام الفرعي.

### 3. unfold (توسيع — على المقطع المجاور فقط)
نطاق النظام ينطبق والمقطع واعد، لكن ملخصه يدلّ على أن النص المطلوب يقع في المقطع
**المجاور** (التتمة، الاستثناء، التفصيل، الإحالة...).
- اضبط `action: "unfold"` **مع** `direction`: "prev" للمقطع السابق أو "next"
  للمقطع التالي. تحديد `direction` **إلزامي** مع كل قرار unfold — قرار unfold
  بلا `direction` غير صالح.
- لا يجوز التوسيع إلا إلى مقطع واحد مجاور (سابق أو تالٍ) في القرار الواحد.
- سيُجلب المقطع المجاور ويُعرض عليك في الجولة التالية لتصنيفه.

## قاعدة الـ 80%

بعد تصنيف كل المقاطع:
- المقاطع المُبقاة تكفي بنسبة ≥80% للإجابة → `sufficient=True`
- يوجد توسيع مطلوب، أو التغطية ناقصة → `sufficient=False`
- (إرشاد تابع لا بديل: بقاء محور رئيسي من `query_axes` بلا تغطية يميل بك نحو `sufficient=false`.)

## قواعد المخرجات

- `query_axes`: 2-3 محاور تمييزية من الاستعلام الفرعي — **للتوثيق والإرشاد فقط**، لا تُغيّر بها قرارات keep/drop/unfold.
- `label`: معرّف المقطع كما ظهر `[Cn]` بالضبط — لا تختلق معرّفات.
- `action`: keep / drop / unfold
- `direction`: prev / next — **فقط** مع unfold (اتركه فارغاً غير ذلك).
- `relevance`: high / medium — **فقط** مع keep (اتركه فارغاً غير ذلك).
- `satisfies_axes`: فهارس المحاور التي يغطيها المقطع — **فقط** مع keep.
- `reasoning`: جملة عربية مختصرة، تذكر فيها حكمك على انطباق نطاق النظام.
- **التغطية الكاملة إلزامية:** أنتج قراراً واحداً لكل مقطع معروض — **عدد عناصر
  `decisions` يساوي عدد المقاطع بالضبط**. لكل معرّف `[Cn]` ظهر في النتائج قرار
  مقابل. لا تُغفل أي مقطع مهما بدا واضح الحذف.
- `summary_note`: ملاحظة عربية مختصرة عن التقييم الجماعي.

## ممنوعات

- لا تستقبل السؤال الأصلي — ركّز على الاستعلام الفرعي فقط.
- لا تحاول الإجابة — مهمتك التصنيف فقط.
- لا تختلق معرّفات مقاطع غير موجودة في النتائج.
""",
}


def get_reranker_prompt(key: str) -> str:
    """Lookup a reranker prompt variant by key."""
    if key not in RERANKER_PROMPTS:
        available = ", ".join(sorted(RERANKER_PROMPTS.keys()))
        raise KeyError(f"Reranker prompt '{key}' not found. Available: {available}")
    return RERANKER_PROMPTS[key]


def build_reranker_user_message(
    query: str,
    rationale: str,
    results_markdown: str,
    round_num: int = 1,
) -> str:
    """Build the user message for one reranker classification run.

    Args:
        query: The expanded sub-query text.
        rationale: Expander's rationale for this query.
        results_markdown: Search results markdown (raw or re-assembled after unfold).
        round_num: Which classification round (1=initial, 2+=after unfold).

    No keep-cap instruction is injected. The cap is a downstream resource limit
    enforced in code (`reranker.py`); telling the LLM about it only makes it
    self-limit to a quota and suppresses the `unfold` action.
    """
    lines: list[str] = [
        "## الاستعلام الفرعي",
        query,
    ]
    if rationale:
        lines.append(f"**المبرر:** {rationale}")
    lines.append("")

    if round_num > 1:
        lines.append(
            f"**الجولة {round_num}:** المقاطع أدناه هي مقاطع مجاورة جُلبت بناءً "
            f"على قرارات التوسيع السابقة — صنّفها."
        )
        lines.append("")


    lines.append("---")
    lines.append("")
    lines.append("## نتائج البحث")
    lines.append("")
    lines.append(results_markdown)
    return "\n".join(lines)
