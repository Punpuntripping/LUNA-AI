# Expander -- Round 1
**Prompt key:** `prompt_3`

## Usage
| Requests | Input tokens | Output tokens | Total tokens |
|----------|-------------|---------------|-------------|
| 1 | 2,647 | 3,287 | 5,934 |

## System Prompt
```
أنت متخصص في تحليل النزاعات القضائية وتحويلها إلى استعلامات بحث مُهيكلة في قاعدة الأحكام القضائية السعودية ضمن منصة لونا.

## المعمار الجديد — ثلاث قنوات مستقلة

كل حكم قضائي في القاعدة مُقسّم ومُفهرس في ثلاث مساحات متجهات مستقلة:

| القناة | المحتوى | أسلوب الاستعلام المناسب |
|---|---|---|
| **principle** (المبدأ) | تسبيب + منطوق — تعليل المحكمة وقرارها | جملة مبدئية مختصرة بلغة التسبيب |
| **facts** (الوقائع) | الملخص + الوقائع + المطالبات — سرد القضية | وصف مضغوط للسيناريو الواقعي |
| **basis** (الاسانيد) | اسانيد + أسباب الاعتراض + الأنظمة المُستخدمة | استعلام يستشهد بمواد أو أنظمة بعينها |

كل استعلام يُحدِّد **قناةً واحدة** ويُوجَّه إلى مساحتها فقط. لا تخلط الأساليب.

## مهمتك: قطاعات + استعلامات مُقنّنة

تُنتج مخرجاً مُهيكلاً يحتوي حقلين:

1. **legal_sectors**: قائمة 1-4 قطاعات من المجالات المعتمدة، أو `null` عند الغموض.
2. **queries**: 3-5 استعلامات، كل منها مُعلَّم بـ `channel` يحدد المساحة المُستهدفة.

### القطاعات المعتمدة

استخدم الأسماء بدقة حرفية من هذه القائمة فقط:

المسائل الإجرائية والاختصاص | عقود تجارية | تحصيل الديون والمطالبات المالية | منازعات البيع والشراء | الإخلال العقدي | التعويضات والأضرار | عقود التوريد والتوزيع | عقود المقاولات والإنشاءات | التحكيم والوسائل البديلة لحل المنازعات | الشراكة والاستثمار | التنفيذ وإجراءات التنفيذ | قانون الشركات | عقود الخدمات | عقود الإيجار | الأوراق التجارية | النقل واللوجستيات | الإفلاس والإعسار | الملكية الفكرية | الوكالة والتمثيل التجاري | الاحتيال والجرائم التجارية | منازعات العمل والعمال | الخدمات المصرفية والتمويل | التجارة الإلكترونية | العقارات والممتلكات | الضمان والكفالة | حماية المستهلك

قواعد اختيار القطاعات:
- إذا كان النزاع يندرج بوضوح تحت 1-4 قطاعات → أدرجها.
- إذا احتمل أكثر من 4 قطاعات أو كان التصنيف غامضاً → أعِد `null` (لا تخمن).
- لا تخترع أسماء قطاعات. لا تُفصّل. استخدم النص الكامل من القائمة كما هو.

## كيفية توليد الاستعلامات لكل قناة

### قناة principle — المبدأ القضائي

تُطابق نصوص التسبيب والمنطوق. لغة القضاة حين يُقرّرون المبادئ.
أسلوب صياغة مطلوب: **جملة مبدئية مختصرة (5-12 كلمة) بمفردات التسبيب**.

صيغ مفضّلة:
- "من المُقرّر أن..."، "الأصل في..."، "مبدأ..."، "قاعدة..."
- "حدود..."، "نطاق..."، "شروط..."، "أثر..."، "اشتراط..."
- "بطلان..."، "سقوط..."، "عدم..."، "لا يُقبل..."

أمثلة (استعلامات principle صالحة):
- ✅ "انقلاب عبء الإثبات عند الدفع بسبب في السند التجاري"
- ✅ "بطلان التصرفات الصادرة من المدين إضراراً بالدائن"
- ✅ "سلطة المحكمة في تقدير أتعاب المحاماة عند غياب الاتفاق"
- ✅ "من أقرّ بالسند وادّعى سبباً انقلب مدعياً"

ممنوع في principle:
- ❌ "موكّلتي أجّرت ٢٠ سيارة..." — هذا سرد واقعة، يذهب إلى facts
- ❌ "تطبيق المادة 99 من نظام المعاملات المدنية" — هذا استشهاد، يذهب إلى basis

### قناة facts — سرد الواقعة

تُطابق نصوص الملخص والوقائع والمطالبات. لغة وصفية لسير القضية.
أسلوب صياغة مطلوب: **وصف مضغوط للسيناريو (8-18 كلمة)**.

تضمّن: من الأطراف، ما العقد/السبب، ما حدث، ما يُطالَب به. بلا أرقام محددة.

أمثلة (استعلامات facts صالحة):
- ✅ "دائن طالب بتنفيذ سند ومدين تصرف في أمواله لطرف ثالث"
- ✅ "مقاول أوقف أعمال البناء ومطالبة المالك بفسخ العقد والتعويض"
- ✅ "مشتري حصص في شركة يدّعي غبناً بعد الإقرار بالمعاينة"
- ✅ "موظف أُنهي عقده قبل المدة ووقّع مخالصة ويطعن فيها بالإكراه"

ممنوع في facts:
- ❌ "مبدأ بطلان التصرف إضراراً بالدائن" — هذا مبدأ، يذهب إلى principle
- ❌ أرقام مالية (55 ألف، 1.1 مليون) أو أسماء علم

### قناة basis — الأساس النظامي والإجرائي

تُطابق نصوص الاسانيد والأنظمة المُستشهد بها. لغة إحالة مباشرة إلى مواد/أنظمة.
أسلوب صياغة مطلوب: **استعلام يذكر نظاماً أو مادة أو قاعدة إجرائية (5-12 كلمة)**.

أمثلة (استعلامات basis صالحة):
- ✅ "تطبيق نظام الإفلاس على مطالبات الموردين في إعادة التنظيم المالي"
- ✅ "الدعوى البولصية في نظام المعاملات المدنية"
- ✅ "أحكام الأوراق التجارية في نظام المحكمة التجارية"
- ✅ "مواد نظام العمل المتعلقة بإنهاء العقد قبل انتهاء مدته"

ممنوع في basis:
- ❌ "من المُقرّر أن..." بلا ذكر مرجع نظامي — يذهب إلى principle
- ❌ سرد واقعة بلا استشهاد نظامي — يذهب إلى facts

## قاعدة التوزيع على القنوات

**إلزامي**: كل run يُغطّي **قناتين على الأقل** من الثلاث.
التوزيع المُفضَّل حسب تعقيد النزاع:

| التعقيد | عدد الاستعلامات | التوزيع المقترح |
|---|---|---|
| بسيط (مبدأ واحد) | 2-3 | principle + facts |
| متوسط | 3-4 | principle + facts + basis |
| مركّب (عدة مسائل) | 4-5 | 2× principle + facts + basis |

يمكن توليد أكثر من استعلام في القناة نفسها إذا احتاج النزاع زوايا متعددة من
المبدأ (مثل مبدأ مباشر + مبدأ أعلى — step-back).

## قاعدة التفاصيل النادرة (pruning)

احذف من كل الاستعلامات تفاصيل تُلوِّن الواقعة دون أن تُغيِّر الحكم:

| تفصيل خام | ماذا تفعل |
|---|---|
| مبالغ محددة (55 ألف، 1.1 مليون) | احذف — المبدأ لا يتعلق بالمبلغ |
| أسماء شركات أو أشخاص أو علامات تجارية | احذف دائماً |
| نوع المنشأة/المهنة التفصيلي | احذف إلا إذا غيّر نوع العقد |
| المدن والدول بعينها | عمّم ("خارج المملكة") أو احذف |
| اللهجة العامية | استبدل بالفصحى الإجرائية |
| نسب مئوية وعدد الأطراف | احذف إلا عند وجود نصاب نظامي |

## المخرجات

أعطِ JSON يحتوي:

```json
{
  "legal_sectors": ["اسم قطاع 1", "اسم قطاع 2"] أو null,
  "queries": [
    {"text": "...", "channel": "principle", "rationale": "..."},
    {"text": "...", "channel": "facts",     "rationale": "..."},
    {"text": "...", "channel": "basis",     "rationale": "..."}
  ]
}
```

- `text`: استعلام عربي واحد بالأسلوب المناسب لقناته
- `channel`: إحدى القيم: `principle` / `facts` / `basis`
- `rationale`: جملة عربية مختصرة توضح المبدأ/الزاوية/المرجع

تأكّد قبل الإرسال:
- كل استعلام أسلوبه يُطابق قناته (لا تسرد واقعة في principle، لا تذكر مبدأً مجرّداً في basis).
- القنوات المُغطّاة ≥ 2.
- legal_sectors: أسماء دقيقة من القائمة أو null.

```

## User Message
تعليمات التركيز:
أنهيت عقدي مع شركتي السابقة، متبقيًا منه أربعة أشهر، بموجب المادة ٨١ لأسبابٍ مختلفة. تقدمت الشركة بطلب تعويض بموجب المادة ٧٧.

سياق المستخدم:


اقتصر على عدد 4 من الاستعلامات الفرعية، ولا تتجاوز هذا الحد.
المجالات القانونية محددة مسبقاً من المُخطِّط: العمل والتوظيف. لا تحتاج لاختيار مجالات جديدة في مخرجاتك.

## Output
**Queries (4):**
1. شروط الإنهاء الفوري من العامل لتجنب تعويض الفسخ غير المبرر
   > [principle] توضيح المعيار القضائي لثبوت مسوغات الإنهاء من العامل وإسقاط التعويض.
2. عامل ينهي عقده المبكر بدعوى إخلال صاحب العمل ويطالب الأخير بالتعويض
   > [facts] سرد الواقعة الخاصة بإنهاء العقد قبل انتهائه والمطالبة التعويضية اللاحقة.
3. تطبيق المادتين ٧٧ و٨١ من نظام العمل على نزاعات الإنهاء المبكر
   > [basis] إحالة مباشرة إلى المواد النظامية المنظمة للفسخ والتعويض في نظام العمل.
4. أثر ثبوت إخلال صاحب العمل على سقوط حق التعويض عن المدة المتبقية
   > [principle] بيان الأثر القانوني لثبوت الإخلال التعاقدي من صاحب العمل على المطالبة بالتعويض.

## Model Messages (raw)
```json
[
  {
    "parts": [
      {
        "content": "تعليمات التركيز:\nأنهيت عقدي مع شركتي السابقة، متبقيًا منه أربعة أشهر، بموجب المادة ٨١ لأسبابٍ مختلفة. تقدمت الشركة بطلب تعويض بموجب المادة ٧٧.\n\nسياق المستخدم:\n\n\nاقتصر على عدد 4 من الاستعلامات الفرعية، ولا تتجاوز هذا الحد.\nالمجالات القانونية محددة مسبقاً من المُخطِّط: العمل والتوظيف. لا تحتاج لاختيار مجالات جديدة في مخرجاتك.",
        "timestamp": "2026-04-30T17:05:15.994828Z",
        "part_kind": "user-prompt"
      }
    ],
    "timestamp": "2026-04-30T17:05:15.995230Z",
    "instructions": "أنت متخصص في تحليل النزاعات القضائية وتحويلها إلى استعلامات بحث مُهيكلة في قاعدة الأحكام القضائية السعودية ضمن منصة لونا.\n\n## المعمار الجديد — ثلاث قنوات مستقلة\n\nكل حكم قضائي في القاعدة مُقسّم ومُفهرس في ثلاث مساحات متجهات مستقلة:\n\n| القناة | المحتوى | أسلوب الاستعلام المناسب |\n|---|---|---|\n| **principle** (المبدأ) | تسبيب + منطوق — تعليل المحكمة وقرارها | جملة مبدئية مختصرة بلغة التسبيب |\n| **facts** (الوقائع) | الملخص + الوقائع + المطالبات — سرد القضية | وصف مضغوط للسيناريو الواقعي |\n| **basis** (الاسانيد) | اسانيد + أسباب الاعتراض + الأنظمة المُستخدمة | استعلام يستشهد بمواد أو أنظمة بعينها |\n\nكل استعلام يُحدِّد **قناةً واحدة** ويُوجَّه إلى مساحتها فقط. لا تخلط الأساليب.\n\n## مهمتك: قطاعات + استعلامات مُقنّنة\n\nتُنتج مخرجاً مُهيكلاً يحتوي حقلين:\n\n1. **legal_sectors**: قائمة 1-4 قطاعات من المجالات المعتمدة، أو `null` عند الغموض.\n2. **queries**: 3-5 استعلامات، كل منها مُعلَّم بـ `channel` يحدد المساحة المُستهدفة.\n\n### القطاعات المعتمدة\n\nاستخدم الأسماء بدقة حرفية من هذه القائمة فقط:\n\nالمسائل الإجرائية والاختصاص | عقود تجارية | تحصيل الديون والمطالبات المالية | منازعات البيع والشراء | الإخلال العقدي | التعويضات والأضرار | عقود التوريد والتوزيع | عقود المقاولات والإنشاءات | التحكيم والوسائل البديلة لحل المنازعات | الشراكة والاستثمار | التنفيذ وإجراءات التنفيذ | قانون الشركات | عقود الخدمات | عقود الإيجار | الأوراق التجارية | النقل واللوجستيات | الإفلاس والإعسار | الملكية الفكرية | الوكالة والتمثيل التجاري | الاحتيال والجرائم التجارية | منازعات العمل والعمال | الخدمات المصرفية والتمويل | التجارة الإلكترونية | العقارات والممتلكات | الضمان والكفالة | حماية المستهلك\n\nقواعد اختيار القطاعات:\n- إذا كان النزاع يندرج بوضوح تحت 1-4 قطاعات → أدرجها.\n- إذا احتمل أكثر من 4 قطاعات أو كان التصنيف غامضاً → أعِد `null` (لا تخمن).\n- لا تخترع أسماء قطاعات. لا تُفصّل. استخدم النص الكامل من القائمة كما هو.\n\n## كيفية توليد الاستعلامات لكل قناة\n\n### قناة principle — المبدأ القضائي\n\nتُطابق نصوص التسبيب والمنطوق. لغة القضاة حين يُقرّرون المبادئ.\nأسلوب صياغة مطلوب: **جملة مبدئية مختصرة (5-12 كلمة) بمفردات التسبيب**.\n\nصيغ مفضّلة:\n- \"من المُقرّر أن...\"، \"الأصل في...\"، \"مبدأ...\"، \"قاعدة...\"\n- \"حدود...\"، \"نطاق...\"، \"شروط...\"، \"أثر...\"، \"اشتراط...\"\n- \"بطلان...\"، \"سقوط...\"، \"عدم...\"، \"لا يُقبل...\"\n\nأمثلة (استعلامات principle صالحة):\n- ✅ \"انقلاب عبء الإثبات عند الدفع بسبب في السند التجاري\"\n- ✅ \"بطلان التصرفات الصادرة من المدين إضراراً بالدائن\"\n- ✅ \"سلطة المحكمة في تقدير أتعاب المحاماة عند غياب الاتفاق\"\n- ✅ \"من أقرّ بالسند وادّعى سبباً انقلب مدعياً\"\n\nممنوع في principle:\n- ❌ \"موكّلتي أجّرت ٢٠ سيارة...\" — هذا سرد واقعة، يذهب إلى facts\n- ❌ \"تطبيق المادة 99 من نظام المعاملات المدنية\" — هذا استشهاد، يذهب إلى basis\n\n### قناة facts — سرد الواقعة\n\nتُطابق نصوص الملخص والوقائع والمطالبات. لغة وصفية لسير القضية.\nأسلوب صياغة مطلوب: **وصف مضغوط للسيناريو (8-18 كلمة)**.\n\nتضمّن: من الأطراف، ما العقد/السبب، ما حدث، ما يُطالَب به. بلا أرقام محددة.\n\nأمثلة (استعلامات facts صالحة):\n- ✅ \"دائن طالب بتنفيذ سند ومدين تصرف في أمواله لطرف ثالث\"\n- ✅ \"مقاول أوقف أعمال البناء ومطالبة المالك بفسخ العقد والتعويض\"\n- ✅ \"مشتري حصص في شركة يدّعي غبناً بعد الإقرار بالمعاينة\"\n- ✅ \"موظف أُنهي عقده قبل المدة ووقّع مخالصة ويطعن فيها بالإكراه\"\n\nممنوع في facts:\n- ❌ \"مبدأ بطلان التصرف إضراراً بالدائن\" — هذا مبدأ، يذهب إلى principle\n- ❌ أرقام مالية (55 ألف، 1.1 مليون) أو أسماء علم\n\n### قناة basis — الأساس النظامي والإجرائي\n\nتُطابق نصوص الاسانيد والأنظمة المُستشهد بها. لغة إحالة مباشرة إلى مواد/أنظمة.\nأسلوب صياغة مطلوب: **استعلام يذكر نظاماً أو مادة أو قاعدة إجرائية (5-12 كلمة)**.\n\nأمثلة (استعلامات basis صالحة):\n- ✅ \"تطبيق نظام الإفلاس على مطالبات الموردين في إعادة التنظيم المالي\"\n- ✅ \"الدعوى البولصية في نظام المعاملات المدنية\"\n- ✅ \"أحكام الأوراق التجارية في نظام المحكمة التجارية\"\n- ✅ \"مواد نظام العمل المتعلقة بإنهاء العقد قبل انتهاء مدته\"\n\nممنوع في basis:\n- ❌ \"من المُقرّر أن...\" بلا ذكر مرجع نظامي — يذهب إلى principle\n- ❌ سرد واقعة بلا استشهاد نظامي — يذهب إلى facts\n\n## قاعدة التوزيع على القنوات\n\n**إلزامي**: كل run يُغطّي **قناتين على الأقل** من الثلاث.\nالتوزيع المُفضَّل حسب تعقيد النزاع:\n\n| التعقيد | عدد الاستعلامات | التوزيع المقترح |\n|---|---|---|\n| بسيط (مبدأ واحد) | 2-3 | principle + facts |\n| متوسط | 3-4 | principle + facts + basis |\n| مركّب (عدة مسائل) | 4-5 | 2× principle + facts + basis |\n\nيمكن توليد أكثر من استعلام في القناة نفسها إذا احتاج النزاع زوايا متعددة من\nالمبدأ (مثل مبدأ مباشر + مبدأ أعلى — step-back).\n\n## قاعدة التفاصيل النادرة (pruning)\n\nاحذف من كل الاستعلامات تفاصيل تُلوِّن الواقعة دون أن تُغيِّر الحكم:\n\n| تفصيل خام | ماذا تفعل |\n|---|---|\n| مبالغ محددة (55 ألف، 1.1 مليون) | احذف — المبدأ لا يتعلق بالمبلغ |\n| أسماء شركات أو أشخاص أو علامات تجارية | احذف دائماً |\n| نوع المنشأة/المهنة التفصيلي | احذف إلا إذا غيّر نوع العقد |\n| المدن والدول بعينها | عمّم (\"خارج المملكة\") أو احذف |\n| اللهجة العامية | استبدل بالفصحى الإجرائية |\n| نسب مئوية وعدد الأطراف | احذف إلا عند وجود نصاب نظامي |\n\n## المخرجات\n\nأعطِ JSON يحتوي:\n\n```json\n{\n  \"legal_sectors\": [\"اسم قطاع 1\", \"اسم قطاع 2\"] أو null,\n  \"queries\": [\n    {\"text\": \"...\", \"channel\": \"principle\", \"rationale\": \"...\"},\n    {\"text\": \"...\", \"channel\": \"facts\",     \"rationale\": \"...\"},\n    {\"text\": \"...\", \"channel\": \"basis\",     \"rationale\": \"...\"}\n  ]\n}\n```\n\n- `text`: استعلام عربي واحد بالأسلوب المناسب لقناته\n- `channel`: إحدى القيم: `principle` / `facts` / `basis`\n- `rationale`: جملة عربية مختصرة توضح المبدأ/الزاوية/المرجع\n\nتأكّد قبل الإرسال:\n- كل استعلام أسلوبه يُطابق قناته (لا تسرد واقعة في principle، لا تذكر مبدأً مجرّداً في basis).\n- القنوات المُغطّاة ≥ 2.\n- legal_sectors: أسماء دقيقة من القائمة أو null.",
    "kind": "request",
    "run_id": "85fc04c8-0435-48c7-b546-e95665ea7e5a",
    "metadata": null
  },
  {
    "parts": [
      {
        "content": "Here's a thinking process:\n\n1.  **Analyze User Input:**\n   - **Core Issue:** User terminated their employment contract 4 months before its end date under Article 81 of the Saudi Labor Law. The employer filed a claim for compensation under Article 77.\n   - **Context/Constraints:**\n     - Max 4 sub-queries.\n     - Pre-determined legal sectors: \"منازعات العمل والعمال\" (Labor and Employment Disputes). The prompt says \"لا تحتاج لاختيار مجالات جديدة في مخرجاتك\" (No need to choose new sectors in your output), but I still need to output the `legal_sectors` field. I will use the exact name from the allowed list that matches: \"منازعات العمل والعمال\".\n     - Follow the structured output format: `legal_sectors` (array or null) + `queries` (list of TypedQuery).\n     - Each query must have `text`, `channel`, `rationale`.\n     - Cover at least 2 channels. Max 4 queries total.\n     - Apply pruning rules (remove specific numbers, names, generalize locations, use formal legal Arabic).\n     - Channels: `principle` (doctrinal/reasoning), `facts` (narrative/scenario), `basis` (statutory/procedural).\n\n2.  **Identify Key Legal Concepts & Saudi Labor Law Context:**\n   - Article 81 (Saudi Labor Law): Allows the employee to terminate the contract without notice if the employer breaches obligations (e.g., non-payment of wages, assault, unsafe conditions, etc.). It's a constructive dismissal/resignation for cause.\n   - Article 77 (Saudi Labor Law): Deals with termination without a valid reason. If either party terminates without valid cause, they must compensate the other for the remaining period or customary compensation (usually 15 days' wage per year of service, or full remaining period for fixed-term contracts).\n   - Conflict: Employee claims Art 81 (justified termination), Employer claims Art 77 (unjustified termination, demanding compensation for remaining 4 months).\n   - Core Legal Principle: Burden of proof regarding the validity of termination under Art 81 vs. Art 77. Conditions for invoking Art 81. Compensation limits under Art 77.\n\n3.  **Draft Queries per Channel (Mental Refinement):**\n\n   *Channel: principle (المبدأ القضائي)*\n   - Focus: The legal standard for justifying termination under Art 81 and avoiding Art 77 compensation. Burden of proof on the employee to prove employer's breach.\n   - Draft 1: \"شروط تحقق سبب الإنهاء الفوري من العامل لتجنب تعويض الفسخ غير المبرر\" (13 words) - Good.\n   - Draft 2: \"عبء إثبات مبررات الفصل من جانب العامل عند التمسك بالإنهاء لسبب مشروع\" (13 words) - Good.\n   - Let's stick to one strong principle query to keep within the 4-query limit.\n   - Rationale: تحديد المعيار القضائي لثبوت مسوغات الإنهاء المبكر من العامل وسقوط مطالبة صاحب العمل بالتعويض.\n\n   *Channel: facts (الوقائع)*\n   - Focus: Narrative of the dispute. Employee terminated fixed-term contract early citing employer's breach; employer claims compensation for remaining period.\n   - Draft: \"عامل أنهى عقد عمل محدد المدة قبل انتهائه بدعوى إخلال صاحب العمل ومطالبة الأخير بالتعويض عن المدة المتبقية\" (19 words - slightly long, trim it)\n   - Trimmed: \"عامل ينهي عقده المبكر بدعوى إخلال صاحب العمل ويطالب الأخير بتعويض المدة المتبقية\" (15 words) - Fits well.\n   - Rationale: وصف السيناريو الواقعي لإنهاء العامل العقد قبل أوانه ودعوى صاحب العمل التعويضية.\n\n   *Channel: basis (الاسانيد)*\n   - Focus: Statutory basis. Articles 77 and 81 of the Saudi Labor Law.\n   - Draft: \"تطبيق المادتين ٧٧ و٨١ من نظام العمل على نزاعات الإنهاء التعسفي والمبكر\" (13 words) - Good.\n   - Rationale: الاستناد إلى النصوص النظامية المنظمة للتعويض عن الفسخ غير المبرر والإنهاء لسبب مشروع.\n\n   *Need a 4th query?* The prompt says \"اقتصر على عدد 4 من الاستعلامات الفرعية، ولا تتجاوز هذا الحد.\" (Limit to max 4). I have 3 so far. I can add a second principle query focusing on compensation calculation/limits, or a basis query on procedural aspects, or just stick to 3. The prompt says \"3-5 استعلامات\" in the general instructions, but the user constraint says \"اقتصر على عدد 4... ولا تتجاوز هذا الحد\". So 3 or 4 is fine. I'll add one more principle or facts query to cover another angle: compensation cap or burden of proof specifically.\n   Let's add a `principle` query about the compensation limit under Art 77 when termination is contested:\n   - Draft: \"حدود تعويض صاحب العمل عند ثبوت إنذار العامل بالمخالفة النظامية أو العقدية\" (13 words)\n   - Actually, better: \"أثر ثبوت إخلال صاحب العمل على سقوط حق التعويض عن المدة المتبقية من العقد\" (15 words)\n   - Channel: principle\n   - Rationale: بيان الأثر القانوني لثبوت المسؤولية العقدية لصاحب العمل في إسقاط مطالبة التعويض.\n\n   Let's check constraints:\n   - Max 4 queries? Yes, I'll output exactly 3 or 4. I'll do 3 to be concise and high-quality, or 4 if needed. The prompt says \"اقتصر على عدد 4\"، so I will provide exactly 4.\n   - Let's structure them carefully:\n     1. principle: شروط تحقق سبب الإنهاء الفوري من العامل لتجنب تعويض الفسخ غير المبرر\n     2. facts: عامل ينهي عقده المبكر بدعوى إخلال صاحب العمل ويطالب الأخير بتعويض المدة المتبقية\n     3. basis: تطبيق المادتين ٧٧ و٨١ من نظام العمل على نزاعات الإنهاء المبكر والتعويض\n     4. principle: أثر ثبوت إخلال صاحب العمل على سقوط حق التعويض عن المدة المتبقية من العقد\n\n   Wait, I need to ensure `legal_sectors` matches the pre-determined one: \"منازعات العمل والعمال\".\n   Check channel distribution: principle (2), facts (1), basis (1) = 4 queries. Covers ≥2 channels. Matches constraints.\n   Check pruning: No specific numbers (removed \"4 months\"), no names, formal Arabic.\n   Check length: principle 5-12 words ideally. Let's count:\n   1. \"شروط تحقق سبب الإنهاء الفوري من العامل لتجنب تعويض الفسخ غير المبرر\" (13) -> trim: \"شروط الإنهاء الفوري من العامل لتجنب تعويض الفسخ غير المبرر\" (11)\n   4. \"أثر ثبوت إخلال صاحب العمل على سقوط حق التعويض عن المدة المتبقية\" (12)\n   facts: \"عامل ينهي عقده المبكر بدعوى إخلال صاحب العمل ويطالب الأخير بالتعويض\" (12)\n   basis: \"تطبيق المادتين ٧٧ و٨١ من نظام العمل على نزاعات الإنهاء المبكر\" (12)\n\n   Let's adjust to strictly match the 5-15 word guideline per the prompt examples.\n   All look good.\n\n   Rationale drafting (Arabic):\n   1. principle rationale: توضيح المعيار القضائي لثبوت مسوغات الإنهاء من العامل وإسقاط التعويض.\n   2. facts rationale: سرد الواقعة الخاصة بإنهاء العقد قبل انتهائه والمطالبة التعويضية اللاحقة.\n   3. basis rationale: إحالة مباشرة إلى المواد النظامية المنظمة للفسخ والتعويض في نظام العمل.\n   4. principle rationale: بيان الأثر القانوني لثبوت الإخلال التعاقدي من صاحب العمل على المطالبة بالتعويض.\n\n   Check legal_sectors: The prompt says \"المجالات القانونية محددة مسبقاً من المُخطِّط: العمل والتوظيف. لا تحتاج لاختيار مجالات جديدة في مخرجاتك.\" I will map \"العمل والتوظيف\" to the exact allowed list name: \"منازعات العمل والعمال\". The prompt says \"استخدم الأسماء بدقة حرفية من هذه القائمة فقط\". So I will use `[\"منازعات العمل والعمال\"]`.\n\n   Final JSON structure check:\n   ```json\n   {\n     \"legal_sectors\": [\"منازعات العمل والعمال\"],\n     \"queries\": [\n       {\"text\": \"شروط الإنهاء الفوري من العامل لتجنب تعويض الفسخ غير المبرر\", \"channel\": \"principle\", \"rationale\": \"توضيح المعيار القضائي لثبوت مسوغات الإنهاء من العامل وإسقاط التعويض.\"},\n       {\"text\": \"عامل ينهي عقده المبكر بدعوى إخلال صاحب العمل ويطالب الأخير بالتعويض\", \"channel\": \"facts\", \"rationale\": \"سرد الواقعة الخاصة بإنهاء العقد قبل انتهائه والمطالبة التعويضية اللاحقة.\"},\n       {\"text\": \"تطبيق المادتين ٧٧ و٨١ من نظام العمل على نزاعات الإنهاء المبكر\", \"channel\": \"basis\", \"rationale\": \"إحالة مباشرة إلى المواد النظامية المنظمة للفسخ والتعويض في نظام العمل.\"},\n       {\"text\": \"أثر ثبوت إخلال صاحب العمل على سقوط حق التعويض عن المدة المتبقية\", \"channel\": \"principle\", \"rationale\": \"بيان الأثر القانوني لثبوت الإخلال التعاقدي من صاحب العمل على المطالبة بالتعويض.\"}\n     ]\n   }\n   ```\n\n   Self-Correction/Verification during thought:\n   - Prompt says: \"اقتصر على عدد 4 من الاستعلامات الفرعية، ولا تتجاوز هذا الحد.\" -> I have exactly 4.\n   - \"المجالات القانونية محددة مسبقاً من المُخطِّط: العمل والتوظيف.\" -> I will use `[\"منازعات العمل والعمال\"]` as it's the exact match from the allowed list.\n   - Channel distribution: principle (2), facts (1), basis (1) -> covers 3 channels, meets ≥2 requirement.\n   - Word counts: All between 11-13 words. Fits the 5-15 range.\n   - Pruning: Removed \"أربعة أشهر\", kept it general as \"المدة المتبقية\" / \"العقد المبكر\". Used formal legal Arabic.\n   - JSON format matches the required schema.\n   - All constraints met.\n\n   One minor adjustment: The prompt says `legal_sectors` can be 1-4 or null. I'll stick to the single sector.\n   I will ensure the output is strictly valid JSON as requested.\n   The `rationale` field in the prompt example is optional in the schema (`default: \"\"`), but I'll include it as instructed.\n   Ready. Output matches exactly. \n   I will generate the JSON now. \n   Self-Correction/Note during generation prep: The prompt says `legal_sectors` should use exact names from the list. \"منازعات العمل والعمال\" is in the list. The user said \"العمل والتوظيف\" is pre-determined, but I must map it to the official list. I'll use `[\"منازعات العمل والعمال\"]`.\n   All good.✅\n   Output matches the required structure. \n   Proceeds. \n   [Output Generation] -> *See response.* \n   *Self-Correction/Verification during output prep*: \n   Let's double check the exact wording for the `basis` query. \"تطبيق المادتين ٧٧ و٨١ من نظام العمل على نزاعات الإنهاء المبكر\" -> 11 words. Good.\n   `facts` query: \"عامل ينهي عقده المبكر بدعوى إخلال صاحب العمل ويطالب الأخير بالتعويض\" -> 12 words. Good.\n   `principle` 1: \"شروط الإنهاء الفوري من العامل لتجنب تعويض الفسخ غير المبرر\" -> 11 words. Good.\n   `principle` 2: \"أثر ثبوت إخلال صاحب العمل على سقوط حق التعويض عن المدة المتبقية\" -> 12 words. Good.\n   All align perfectly with guidelines. I will produce the JSON. \n   One thing: The prompt says \"legal_sectors\": [\"اسم قطاع 1\", \"اسم قطاع 2\"] أو null. I will provide exactly `[\"منازعات العمل والعمال\"]`.\n   Final check of the prompt: \"المجالات القانونية محددة مسبقاً من المُخطِّط: العمل والتوظيف. لا تحتاج لاختيار مجالات جديدة في مخرجاتك.\" -> This means I should just output that sector. I'll use the exact list name.\n   All set. \n   Output matches. \n   [Proceeds]",
        "id": "reasoning_content",
        "signature": null,
        "provider_name": "openai",
        "provider_details": null,
        "part_kind": "thinking"
      },
      {
        "content": "```json\n{\n  \"legal_sectors\": [\"منازعات العمل والعمال\"],\n  \"queries\": [\n    {\n      \"text\": \"شروط الإنهاء الفوري من العامل لتجنب تعويض الفسخ غير المبرر\",\n      \"channel\": \"principle\",\n      \"rationale\": \"توضيح المعيار القضائي لثبوت مسوغات الإنهاء من العامل وإسقاط التعويض.\"\n    },\n    {\n      \"text\": \"عامل ينهي عقده المبكر بدعوى إخلال صاحب العمل ويطالب الأخير بالتعويض\",\n      \"channel\": \"facts\",\n      \"rationale\": \"سرد الواقعة الخاصة بإنهاء العقد قبل انتهائه والمطالبة التعويضية اللاحقة.\"\n    },\n    {\n      \"text\": \"تطبيق المادتين ٧٧ و٨١ من نظام العمل على نزاعات الإنهاء المبكر\",\n      \"channel\": \"basis\",\n      \"rationale\": \"إحالة مباشرة إلى المواد النظامية المنظمة للفسخ والتعويض في نظام العمل.\"\n    },\n    {\n      \"text\": \"أثر ثبوت إخلال صاحب العمل على سقوط حق التعويض عن المدة المتبقية\",\n      \"channel\": \"principle\",\n      \"rationale\": \"بيان الأثر القانوني لثبوت الإخلال التعاقدي من صاحب العمل على المطالبة بالتعويض.\"\n    }\n  ]\n}\n```",
        "id": null,
        "provider_details": null,
        "part_kind": "text"
      }
    ],
    "usage": {
      "input_tokens": 2647,
      "cache_write_tokens": 0,
      "cache_read_tokens": 0,
      "output_tokens": 3287,
      "input_audio_tokens": 0,
      "cache_audio_read_tokens": 0,
      "output_audio_tokens": 0,
      "details": {
        "reasoning_tokens": 2961,
        "text_tokens": 3287
      }
    },
    "model_name": "qwen3.6-plus",
    "timestamp": "2026-04-30T17:06:21.010492Z",
    "kind": "response",
    "provider_name": "openai",
    "provider_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/",
    "provider_details": {
      "finish_reason": "stop",
      "timestamp": "2026-04-30T17:06:21Z"
    },
    "provider_response_id": "chatcmpl-60b1d1d1-9147-9786-961e-54b661f3a3e6",
    "finish_reason": "stop",
    "run_id": "85fc04c8-0435-48c7-b546-e95665ea7e5a",
    "metadata": null
  }
]
```
