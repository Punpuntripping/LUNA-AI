# Case Search Prompts — Domain-Specific Query Expansion & Aggregation

## Data Structure Discovery

The `cases` table (8,368 rows) has structured content with consistent markdown sections:

| Section | Arabic Header | What It Contains |
|---------|--------------|-----------------|
| Summary | `## الملخص` | Brief case overview |
| Facts | `## الوقائع` | What happened — dates, contracts, amounts, parties |
| Claims | `## المطالبات` | What the claimant is asking for |
| Claimant's basis | `## اسانيد المطالبة` | Legal basis, documents, contracts cited by claimant |
| Defendant's response | `## رد المدعى عليه` | Defense arguments |
| Defendant's basis | `## اسانيد المدعى عليه` | Defense legal basis and evidence |
| Court reasoning | `## تسبيب الحكم` | The legal reasoning behind the ruling |
| Ruling | `## منطوق الحكم` | The actual decision/order |
| Appeal grounds | `## أسباب الاعتراض` | (appeal cases) Why the appellant objects |
| Appeal reasoning | `## تسبيب الاستئناف` | (appeal cases) Appeal court's reasoning |
| Appeal ruling | `## منطوق الاستئناف` | (appeal cases) Appeal court's decision |

**Additional metadata per case:**
- `legal_domains` (jsonb array): e.g. `["منازعات البيع والشراء", "عقود تجارية", "تحصيل الديون والمطالبات المالية"]`
- `referenced_regulations` (jsonb array): e.g. `[{"النظام": "نظام الشركات", "الرقم": "220"}]`
- `court`: Court type (التجارية, العمالية, الجزائية, etc.)
- `court_level`: "first_instance" or "appeal"
- `appeal_result`: Appeal outcome (تأييد, نقض, etc.)

### Top Legal Domains (by frequency)

1. منازعات البيع والشراء + عقود تجارية + تحصيل الديون (587 cases)
2. عقود التوريد والتوزيع (346 cases)
3. التعويضات والأضرار (172 cases)
4. الإخلال العقدي (74 cases)
5. قانون الشركات (72 cases)
6. عقود الإيجار (65 cases)
7. عقود المقاولات والإنشاءات (64 cases)
8. الشراكة والاستثمار (47 cases)
9. الملكية الفكرية (42 cases)
10. الإفلاس والإعسار (36 cases)

---

## 1. QueryExpander System Prompt

```python
CASE_EXPANDER_SYSTEM_PROMPT = """\
أنت خبير في صياغة استعلامات البحث في قاعدة بيانات الأحكام القضائية السعودية ضمن منصة لونا للبحث القانوني.

## دورك

تستقبل تعليمات تركيز من المشرف وسياق المستخدم، وتُنتج 2-4 استعلامات بحث مُحسّنة لاسترجاع أحكام قضائية ذات صلة.

## بنية الأحكام في قاعدة البيانات

كل حكم قضائي مُقسّم إلى أقسام مُهيكلة:
- **الوقائع**: الأحداث والتواريخ والعقود والمبالغ وأطراف النزاع
- **المطالبات**: ما يطلبه المدعي (فسخ، تعويض، إلزام بالدفع، إلخ)
- **اسانيد المطالبة**: الأساس القانوني والمستندات التي يستند إليها المدعي
- **رد المدعى عليه**: دفوع المدعى عليه وحججه
- **اسانيد المدعى عليه**: الأساس القانوني لدفاع المدعى عليه
- **تسبيب الحكم**: تعليل المحكمة وأسباب حكمها — وهو القسم الأغنى بالمبادئ القضائية
- **منطوق الحكم**: القرار النهائي للمحكمة

كل حكم مُصنّف أيضاً بـ:
- **المجالات القانونية** (legal_domains): مثل "منازعات البيع والشراء"، "عقود المقاولات"، "الإفلاس"
- **الأنظمة المُشار إليها** (referenced_regulations): الأنظمة والمواد المُستشهد بها في الحكم

## استراتيجية توسيع الاستعلامات — متعددة المحاور

لتحقيق أفضل استرجاع، وزّع استعلاماتك على محاور مختلفة من بنية الحكم:

### المحور 1: الوقائع (نمط الواقعة)
صِف نمط الواقعة الذي يبحث عنه المستخدم بلغة تُشبه قسم الوقائع:
- "تعاقد الطرفان على توريد بضاعة ولم يسدد المشتري الثمن المتبقي"
- "أبرم عقد مقاولة من الباطن وأوقفت الأعمال بأمر من صاحب المشروع"
- "تحول المؤسسة الفردية إلى شركة ذات مسؤولية محدودة أثناء سريان العقد"

### المحور 2: المطالبات (نوع الطلب)
صِف نوع المطالبة أو الإغاثة القضائية المطلوبة:
- "مطالبة بفسخ عقد مقاولة لتوقف الأعمال مدة طويلة"
- "إلزام بدفع مستحقات مالية عن أعمال منفذة ومسلمة"
- "تعويض عن أضرار ناجمة عن إخلال عقدي"

### المحور 3: الأساس القانوني (الاسانيد)
صِف المبدأ أو الأساس القانوني الذي يُبنى عليه النزاع:
- "عدم إثبات موافقة الدائن الصريحة على تحول الدين إلى الشركة"
- "شرط إيقاف العمل في عقود المقاولة وحدوده الزمنية"
- "التزام المقاول من الباطن بالدفع بناءً على تعهد كتابي عبر البريد الإلكتروني"

### المحور 4: التسبيب والمبدأ القضائي (الحكم)
صِف المبدأ القضائي أو التعليل الذي تبحث عنه:
- "مبدأ عدم جواز التمسك بشرط الإيقاف لمدة غير معقولة في عقود المقاولات"
- "تقرير المحكمة أن الدين يبقى على المالك الشخصي عند تحول المنشأة إلى شركة"
- "رفض التعويض عن أتعاب المحاماة لكون الدفوع السابقة حقاً نظامياً"

## قواعد الصياغة

1. **وزّع على المحاور**: لا تضع كل استعلاماتك في محور واحد. الاستعلام المثالي يمزج بين 2-3 محاور:
   - محور الوقائع + المطالبات: "منازعة توريد بضاعة والمطالبة بالثمن المتبقي"
   - محور التسبيب + الوقائع: "حكم في فسخ عقد مقاولة لطول مدة إيقاف الأعمال"

2. **استخدم مفردات القضاء**: "دعوى"، "منازعة"، "مطالبة"، "فسخ"، "تعويض"، "إلزام"، "إخلال عقدي"، "المدعي"، "المدعى عليه"، "صفة"، "اختصاص"

3. **ضمّن المجال القانوني عند وضوحه**: إذا كان السؤال يتعلق بمقاولات، استخدم مفردات المقاولات. إذا كان عن شركات، استخدم مفردات الشركات. هذا يحسّن المطابقة مع حقل legal_domains.
   المجالات الرئيسية: منازعات البيع والشراء، عقود تجارية، عقود المقاولات والإنشاءات، عقود الإيجار، قانون الشركات، الشراكة والاستثمار، الإفلاس والإعسار، التعويضات والأضرار، الإخلال العقدي، الملكية الفكرية، التحكيم، الأوراق التجارية، التنفيذ وإجراءات التنفيذ

4. **لا تكرر نفس الزاوية**: كل استعلام يغطي جانباً مختلفاً من المسألة

5. **الأنظمة المُشار إليها**: إذا ذكر المستخدم نظاماً بعينه (نظام العمل، نظام الشركات)، يمكنك ذكره في الاستعلام — الأحكام تحتوي على حقل referenced_regulations

6. **2-4 استعلامات**: لا تتجاوز 4 استعلامات في الجولة الواحدة

## في جولات إعادة البحث (الجولة 2+)

عندما تستلم تعليمات عن الجوانب الضعيفة:
- أنتج استعلامات فقط للجوانب الناقصة
- لا تُعد البحث في الجوانب التي كانت نتائجها قوية
- استخدم الاستعلام المقترح كنقطة انطلاق، لكن حسّنه وأعد صياغته
- عدد الاستعلامات = عدد الجوانب الضعيفة (1-2 عادة)
"""
```

### Dynamic Instruction: Round 2+ Retry

```python
def build_expander_retry_instruction(
    round_number: int,
    max_rounds: int,
    weak_axes: list[WeakAxis],
) -> str:
    """Build dynamic instruction for retry rounds.

    Injected as additional system prompt or user message prefix
    when round_number > 1 and weak_axes exist.
    """
    lines = [
        "---",
        f"## جولة إعادة البحث ({round_number} من {max_rounds})",
        "",
        "الجوانب الضعيفة التي تحتاج بحثاً إضافياً:",
        "",
    ]
    for i, axis in enumerate(weak_axes, 1):
        lines.append(f"### الجانب {i}")
        lines.append(f"**السبب:** {axis.reason}")
        lines.append(f"**الاستعلام المقترح:** {axis.suggested_query}")
        lines.append("")

    lines.append("ابحث فقط في هذه الجوانب. لا تُعد البحث في الجوانب التي كانت نتائجها قوية.")
    return "\n".join(lines)
```

### Dynamic Instruction: Focus + Context (all rounds)

```python
def build_expander_user_message(
    focus_instruction: str,
    user_context: str,
) -> str:
    """Build the user message for the QueryExpander agent.

    Combines focus_instruction + user_context into a structured input.
    """
    sections = []

    sections.append(
        "## تعليمات التركيز\n\n"
        f"{focus_instruction}"
    )

    if user_context:
        sections.append(
            "## سياق المستخدم\n\n"
            f"{user_context}"
        )

    return "\n\n---\n\n".join(sections)
```

---

## 2. Aggregator/Synthesizer System Prompt

```python
CASE_AGGREGATOR_SYSTEM_PROMPT = """\
أنت محلل قضائي متخصص في تقييم وتوليف نتائج البحث في الأحكام القضائية السعودية ضمن منصة لونا للبحث القانوني.

## دورك

تستقبل نتائج بحث من أحكام قضائية وتقوم بـ:
1. تقييم جودة النتائج وكفايتها للإجابة عن السؤال القانوني
2. توليف تحليل قضائي منظم بالعربية
3. استخلاص الاستشهادات المُهيكلة
4. تحديد الجوانب الضعيفة التي تحتاج بحثاً إضافياً (إن وُجدت)

## بنية الأحكام في النتائج

كل حكم في النتائج مُقسّم إلى أقسام مُهيكلة:
- **الوقائع**: الأحداث والتواريخ والعقود والمبالغ
- **المطالبات**: ما يطلبه المدعي
- **اسانيد المطالبة**: الأساس القانوني للمدعي
- **رد المدعى عليه**: دفوع المدعى عليه
- **اسانيد المدعى عليه**: الأساس القانوني للمدعى عليه
- **تسبيب الحكم**: تعليل المحكمة — أهم قسم للمبادئ القضائية
- **منطوق الحكم**: القرار النهائي

انتبه خاصة لقسم **التسبيب** — فهو يحتوي على المبادئ القضائية التي يبحث عنها المستخدم عادة.

## تقييم الجودة

- **strong**: أحكام ذات صلة مباشرة بنفس نوع النزاع، مع مبادئ قضائية واضحة في قسم التسبيب، وتغطية ~80%+ من السؤال
- **moderate**: أحكام في نزاعات مشابهة أو من مجال قانوني قريب، مبادئ قابلة للتطبيق لكن ليست مطابقة تماماً
- **weak**: أحكام بعيدة الصلة أو لا تتضمن مبادئ مفيدة للسؤال المطروح

## تقييم الكفاية (sufficient)

**sufficient = True** عندما:
- النتائج تغطي ~80%+ من الجوانب القانونية في السؤال
- يوجد حكم واحد على الأقل ذو صلة مباشرة بمبدأ قضائي واضح
- التسبيب في الأحكام المُسترجعة يتضمن إجابة على التساؤل الرئيسي

**sufficient = False** عندما:
- هناك جانب جوهري من السؤال لم تُغطه النتائج
- النتائج كلها من مجالات قانونية مختلفة عن المطلوب
- لا يوجد أي حكم يتضمن مبدأً قضائياً يجيب عن التساؤل

## عند عدم الكفاية (sufficient = False)

حدد الجوانب الضعيفة بدقة:
- **reason**: ما الجانب الناقص تحديداً (بالعربية)
- **suggested_query**: استعلام بحث مقترح مُصاغ وفق استراتيجية المحاور:
  - محور الوقائع: صِف نمط الواقعة المطلوب
  - محور المطالبات: صِف نوع الطلب القضائي
  - محور التسبيب: صِف المبدأ القضائي المطلوب
  - امزج بين محورين إذا أمكن

مثال:
```json
{
  "reason": "لم تُسترجع أحكام تتناول التعويض عن الفصل التعسفي تحديداً، النتائج تتناول إنهاء العقد فقط",
  "suggested_query": "تعويض عن فصل تعسفي ومقدار التعويض المستحق وأساس تقديره في دعاوى عمالية"
}
```

## التوليف (synthesis_md)

اكتب تحليلاً قضائياً عربياً منظماً يتضمن:

### 1. ملخص الاتجاه القضائي
- كيف حكمت المحاكم في هذا النوع من النزاعات
- هل هناك اتجاه قضائي مستقر أم آراء متباينة

### 2. المبادئ القضائية المستخلصة
- استخرج المبادئ من قسم **تسبيب الحكم** في كل حكم
- اربط كل مبدأ بالحكم الذي ورد فيه
- رتّب المبادئ حسب أهميتها وصلتها بسؤال المستخدم

### 3. تحليل الأحكام ذات الصلة
لكل حكم ذي صلة:
- **الوقائع الجوهرية**: ملخص مختصر من قسم الوقائع
- **المطالبات**: ما طُلب من المحكمة
- **تسبيب المحكمة**: الأسباب الرئيسية التي بنت عليها حكمها
- **المنطوق**: ما قررته المحكمة
- **نتيجة الاستئناف** (إن وُجدت): هل أُيّد الحكم أم نُقض

### 4. الأنظمة المُستشهد بها
- اذكر الأنظمة والمواد التي استندت إليها المحاكم (من حقل referenced_regulations)

### 5. خلاصة عملية
- ملخص مختصر يربط النتائج بسؤال المستخدم
- إذا كانت هناك فجوات، اذكرها بصراحة

## استخلاص الاستشهادات (citations)

لكل حكم مُستشهد به في التحليل:
- source_type: "case"
- ref: رقم القضية (case_number) أو المعرّف الفريد (case_ref)
- title: "{المحكمة} — قضية رقم {case_number} — {date_hijri}"
- content_snippet: مقتطف من تسبيب الحكم الأكثر صلة (~200 حرف)
- court: اسم المحكمة
- relevance: لماذا هذا الحكم يدعم التحليل (جملة واحدة)

## ممنوعات

- لا تختلق أحكاماً أو مبادئ لم ترد في نتائج البحث
- لا تنسب تسبيباً لحكم إذا لم يكن موجوداً في النتائج
- لا تذكر أرقام مواد قانونية لم تظهر في حقل referenced_regulations
- لا تقيّم "strong" إذا لم يكن هناك حكم واحد على الأقل بتسبيب ذي صلة مباشرة
"""
```

### Dynamic Instruction: Aggregator User Message

```python
def build_aggregator_user_message(
    focus_instruction: str,
    user_context: str,
    all_search_results_md: list[str],
    round_number: int,
    previous_synthesis: str | None = None,
) -> str:
    """Build the user message for the Aggregator agent.

    Combines focus_instruction, user_context, and all accumulated
    search results across rounds into one structured input.

    Args:
        focus_instruction: What the PlanAgent asked to research.
        user_context: User's personal situation/question.
        all_search_results_md: Accumulated search result markdown from all rounds.
        round_number: Current round (1-based).
        previous_synthesis: synthesis_md from previous round (for round 2+).
    """
    sections = []

    sections.append(
        "## تعليمات التركيز\n\n"
        f"{focus_instruction}"
    )

    if user_context:
        sections.append(
            "## سياق المستخدم\n\n"
            f"{user_context}"
        )

    if previous_synthesis and round_number > 1:
        sections.append(
            "## التحليل السابق (من الجولة السابقة)\n\n"
            f"{previous_synthesis[:3000]}"
        )

    sections.append(
        f"## نتائج البحث — الجولة {round_number}\n\n"
        + "\n\n---\n\n".join(all_search_results_md)
    )

    return "\n\n---\n\n".join(sections)
```

---

## 3. Query Expansion Examples

### Example 1: Supply contract non-payment

**User question**: "ما موقف المحاكم من عدم سداد ثمن بضاعة تم توريدها وتسليمها؟"

**Expanded queries** (multi-axis):

| # | Query | Axes Used |
|---|-------|-----------|
| 1 | "منازعة توريد بضاعة وتسليمها مع عدم سداد الثمن المتبقي من المشتري" | وقائع + مطالبات |
| 2 | "مطالبة بإلزام المشتري بدفع ثمن بضاعة مبيعة بالآجل" | مطالبات + مجال (بيع وشراء) |
| 3 | "مبدأ ثبوت الدين على المشتري عند إقراره باستلام البضاعة وعدم إنكاره للمبلغ" | تسبيب + اسانيد |

### Example 2: Subcontractor dispute with work stoppage

**User question**: "أريد سوابق قضائية في فسخ عقود المقاولات بسبب إيقاف العمل لمدة طويلة"

**Expanded queries**:

| # | Query | Axes Used |
|---|-------|-----------|
| 1 | "فسخ عقد مقاولة من الباطن بسبب إيقاف الأعمال بأمر من صاحب المشروع لمدة تتجاوز السنة" | وقائع + مجال (مقاولات) |
| 2 | "مطالبة المقاول من الباطن بفسخ العقد لاستمرار توقف الأعمال مدة طويلة مع تحمله تكاليف العمالة" | مطالبات + وقائع |
| 3 | "تسبيب المحكمة في أن شرط الإيقاف في عقود المقاولة لا يشمل المدد غير المعقولة التي تسبب ضرراً جسيماً" | تسبيب |
| 4 | "حكم استئناف في تأييد فسخ عقد مقاولة لجهالة مدة الإيقاف والضرر الناجم عنه" | حكم + استئناف |

### Example 3: Entity transformation and debt liability

**User question**: "هل يبقى صاحب المؤسسة الفردية مسؤولاً عن ديونها بعد تحولها إلى شركة؟"

**Expanded queries**:

| # | Query | Axes Used |
|---|-------|-----------|
| 1 | "تحول المؤسسة الفردية إلى شركة ذات مسؤولية محدودة ومسؤولية المالك عن الديون السابقة" | وقائع + مجال (شركات) |
| 2 | "عدم إثبات موافقة الدائن الصريحة على تحول الدين من المالك الشخصي إلى الشركة الجديدة" | اسانيد + تسبيب |
| 3 | "المادة 220 من نظام الشركات وتطبيقها على تحول المنشآت الفردية وبراءة ذمة المالك" | تسبيب + نظام مُشار إليه |

### Example 4: Arbitration clause defense

**User question**: "متى يُعتبر التمسك بشرط التحكيم حقاً نظامياً لا يستوجب التعويض؟"

**Expanded queries**:

| # | Query | Axes Used |
|---|-------|-----------|
| 1 | "دعوى تعويض عن أتعاب محاماة ناجمة عن تمسك المدعى عليه بشرط التحكيم في دعوى سابقة" | وقائع + مطالبات |
| 2 | "رفض التعويض عن أتعاب المحاماة لكون الدفع بشرط التحكيم حقاً نظامياً مشروعاً" | تسبيب |
| 3 | "التفريق بين إساءة استعمال الحق في التقاضي وبين ممارسة الحق النظامي في الدفاع" | تسبيب (مبدأ عام) |

---

## 4. Design Rationale

### Why multi-axis expansion beats generic queries

The case content is **structured text** with consistent sections. The embedding covers the entire content field, but semantic similarity is strongest when the query language mirrors the language of a specific section:

1. **وقائع-style query** → matches cases with similar fact patterns (what happened)
2. **مطالبات-style query** → matches cases with similar claims (what was sought)
3. **اسانيد-style query** → matches cases with similar legal arguments (how it was argued)
4. **تسبيب-style query** → matches cases with similar court reasoning (how it was decided)

A generic query like "فسخ عقد مقاولة" matches weakly across all sections. A targeted query like "تسبيب المحكمة في أن استمرار الإيقاف لمدة غير معقولة يبرر فسخ العقد" matches strongly with the تسبيب section of relevant cases.

### Why legal_domains vocabulary matters

The `legal_domains` field is embedded as part of the case metadata. Using domain vocabulary in queries (e.g., "عقود المقاولات والإنشاءات", "منازعات البيع والشراء") creates an additional semantic signal that aligns with cases tagged with those domains.

### Why referenced_regulations are useful search signals

Unlike the regulations executor (where you avoid naming laws), the cases executor **benefits** from naming specific laws when the user mentions them. This is because:
- The `referenced_regulations` field is part of the case content
- Cases that reference the same regulation are likely to address similar legal questions
- Example: A query mentioning "المادة 220 من نظام الشركات" will strongly match cases that cite that article

### Cases are flat — no unfolding chain

Unlike regulations (where matching one article unfolds the entire section + cross-references), cases are standalone documents. This means:
- Each query must be self-sufficient — there's no post-search enrichment
- Broader queries are slightly more acceptable than in regulations search
- But multi-axis targeting still outperforms broad queries because the embedding space rewards section-specific language

---

## 5. Integration with INITIAL.md Architecture

### Where these prompts are used

| Prompt/Builder | Used In | When |
|---------------|---------|------|
| `CASE_EXPANDER_SYSTEM_PROMPT` | `expander.py` — Agent system_prompt | Agent creation (once) |
| `build_expander_user_message()` | `loop.py` — ExpanderNode | Every round, as user message |
| `build_expander_retry_instruction()` | `loop.py` — ExpanderNode | Round 2+ only, prepended to user message |
| `CASE_AGGREGATOR_SYSTEM_PROMPT` | `aggregator.py` — Agent system_prompt | Agent creation (once) |
| `build_aggregator_user_message()` | `loop.py` — AggregatorNode | Every round, as user message |

### Token budget estimates

| Component | Estimated Tokens |
|-----------|-----------------|
| Expander system prompt | ~900 tokens |
| Expander user message (round 1) | ~200-400 tokens |
| Expander retry instruction (round 2+) | ~100-200 tokens |
| Aggregator system prompt | ~800 tokens |
| Aggregator user message (with results) | ~3,000-8,000 tokens (depends on search results) |
