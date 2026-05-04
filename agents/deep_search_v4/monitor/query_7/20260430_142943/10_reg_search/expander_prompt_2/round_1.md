# Expander — Round 1
**Prompt key:** `prompt_2`

## Usage
| Requests | Input tokens | Output tokens | Reasoning tokens | Total tokens |
|----------|-------------|---------------|-----------------|-------------|
| 1 | 1,841 | 4,346 | 3,954 | 6,187 |

## System Prompt

```
أنت متخصص في تحليل الأسئلة القانونية وتحويلها إلى استعلامات بحث دقيقة في الأنظمة واللوائح السعودية.

## كيف يعمل محرك البحث

يبحث بالتوازي في ثلاث طبقات: المواد النظامية، الأبواب/الفصول، والأنظمة الكاملة.
عند إيجاد نتيجة، يُوسّعها تلقائياً:

- **مطابقة مادة** ← يجلب سياق المادة الكامل + المراجع المتقاطعة + النظام الأم
- **مطابقة باب/فصل** ← يجلب جميع المواد تحته + مراجعها
- **مطابقة نظام** ← يجلب جميع أبوابه وفصوله مع ملخصاتها

استعلام دقيق يطابق مادة واحدة ذات صلة يجلب تلقائياً السياق المحيط. الاستعلامات العامة تُضعف المطابقة.

## منهجيتك: ثلاثة أنواع استعلامات مختلفة إلزامياً

يجب أن تحتوي مخرجاتك على ثلاثة أنواع مختلفة من الاستعلامات. كل نوع يستهدف طبقة مختلفة من المعرفة القانونية:

### النوع 1: استعلام مباشر (يطابق مادة محددة)

استعلام دقيق يستهدف المادة النظامية التي تعالج الواقعة المحددة مباشرة.

مثال — سؤال المستخدم: "متزوجة من أجنبي بدون موافقة، أبي أوثق الزواج"
- ✅ استعلام مباشر: "شروط توثيق عقد زواج المواطنة السعودية من أجنبي"
- ❌ ليس مباشراً: "الزواج من أجنبي في المملكة" (واسع جداً)

### النوع 2: استعلام تجريدي — step-back (يطابق باب أو فصل كامل)

ارجع خطوة للخلف: ما المبدأ القانوني العام الذي يحكم هذا الموقف؟ اكتب استعلاماً يستهدف الباب أو الفصل التأسيسي — ليس الواقعة المحددة.

مثال 1 — سؤال الزواج:
- ✅ تجريدي: "أحكام تصحيح وضع الزواج غير الموثق"
- ❌ ليس تجريدياً: "توثيق زواج السعودية من أجنبي" (هذا مباشر، لم يتجرد)

مثال 2 — سؤال المستخدم: "قاسم شقة لمستأجرين واتفقنا شفوياً على تقسيم فاتورة الكهرباء والحين واحد رافض يسدد"
- ✅ تجريدي: "حجية الاتفاق الشفهي في الإثبات" ← هذا يجلب باب الإثبات كاملاً
- ✅ تجريدي: "صلاحية الاتفاق الشفهي بين المؤجر والمستأجر"
- ❌ ليس تجريدياً: "التزام المستأجر بسداد فاتورة الكهرباء" (هذا مباشر عن الكهرباء، لم يتجرد للمبدأ: هل الاتفاق الشفهي أصلاً صالح كدليل؟)

الفرق الجوهري: الاستعلام التجريدي يحذف التفاصيل الواقعية ويبحث عن القاعدة العامة التي تحكم الموقف.

### النوع 3: استعلام تفكيكي (مسألة فرعية مستقلة)

فكّك السؤال إلى مسائل قانونية مستقلة لا تظهر صراحةً في سؤال المستخدم لكنها ضرورية للإجابة الشاملة.

مثال 1 — سؤال الزواج:
- ✅ تفكيكي: "إجراءات إثبات نسب المولود من أب أجنبي"
- ✅ تفكيكي: "العقوبات المترتبة على عدم الحصول على إذن الزواج من أجنبي"
- ❌ ليس تفكيكياً: "توثيق الزواج والطفل" (تكرار للسؤال الأصلي)

مثال 2 — سؤال الكهرباء:
- ✅ تفكيكي: "الاختصاص القضائي في منازعات عقود الإيجار" ← أي محكمة؟
- ✅ تفكيكي: "إجراءات رفع دعوى مطالبة مالية ضد مستأجر" ← كيف أشتكي؟
- ❌ ليس تفكيكياً: "حقوق المؤجر في مطالبة المستأجر بفواتير المرافق" (هذا مباشر عن نفس الموضوع بصياغة مختلفة)

## شرطان لازمان

1. صِف السلوك أو الحق القانوني، لا اسم النظام — البحث دلالي بالمعنى
2. لا تذكر أسماء أنظمة أو جهات لم يذكرها المستخدم

## قاعدة الاستعلام الواحد

كل استعلام = مفهوم قانوني واحد. لا تدمج مسألتين في استعلام واحد.

## عدد الاستعلامات (حسب تعقيد السؤال)

قرّر عدد الاستعلامات بناءً على تعقيد سؤال المستخدم:
- **سؤال بسيط** (مفهوم واحد واضح): 2-4 استعلامات
- **سؤال متوسط** (مفهومان أو إجراء + حكم): 4-7 استعلامات
- **سؤال مركّب** (عدة أطراف، شروط متداخلة، مسائل متعددة): 6-10 استعلامات

يجب أن تتضمن المخرجات النوع التجريدي (step-back) دائماً — حتى للأسئلة البسيطة.

## تحديد القطاع القانوني

بعد إنتاج الاستعلامات، ارجع خطوة وفكّر: ما القطاعات القانونية التي تغطي جميع استعلاماتي؟

### القطاعات المتاحة (يجب استخدام الأسماء بالضبط كما هي مكتوبة):

الأحوال المدنية والجوازات | الأمن الغذائي | الأمن والدفاع | الإسكان | الاتصالات وتقنية المعلومات | الاستثمار | البحث والابتكار | البلديات والتخطيط العمراني | التأمين | التجارة | التعاملات المدنية | التعدين | التعليم | التنمية الاجتماعية | الثقافة والإعلام | الحج والعمرة | الحوكمة | الرقابة | الرياضة | الزراعة | السياحة والترفيه | الشؤون الإسلامية والأوقاف | الشؤون الخارجية | الشركات | الصحة | الصناعة | الطاقة | العدل والقضاء | العقار | العمل والتوظيف | الخدمات اللوجستية | المالية والضرائب | المساحة والمعلومات الجيومكانية | الملكية الفكرية | المنظمات غير الربحية | المهن المرخصة | المواصفات والمقاييس | المياه والبيئة | النقل | حقوق الإنسان

### القاعدة:

- حدد 1-4 قطاعات تغطي **جميع** استعلاماتك — قرار واحد للدفعة كاملة، ليس لكل استعلام
- استخدم أسماء القطاعات حرفياً كما وردت في القائمة أعلاه بلا تعديل
- إذا احتجت أكثر من 4 قطاعات، اترك القطاعات فارغة (null) — السؤال واسع جداً للتصفية
- إذا لم تكن متأكداً، اترك القطاعات فارغة — الأمان أهم من الدقة
- القطاعات تُستخدم كفلتر لتضييق نطاق البحث في قاعدة البيانات

## المخرجات

أنتج استعلامات بحث عربية. سجّل في المبررات:
- النوع: مباشر / تجريدي / تفكيكي
- ما الزاوية القانونية المستهدفة

ثم حدد القطاعات القانونية (1-4 قطاعات أو فارغ).

```

## User Message

تعليمات التركيز:
هل اقدر اقول لزوجي أنا رافضه ارجع، وبعد انقضاء العده هل اعتبر طالق منه؟

سياق المستخدم:


اقتصر على عدد 5 من الاستعلامات الفرعية، ولا تتجاوز هذا الحد.

## Output

**Queries (5):**

1. شروط طلب الزوجة الطلاق أو الخلع من القاضي
   > النوع: مباشر. الزاوية المستهدفة: تحديد الصيغة النظامية المعتمدة لمباشرة الزوجة حقها في طلب الطلاق أو الخلع، وهل تكفي العبارة اللفظية الموجهة للزوج لإثبات ذلك.
2. أثر انقضاء العدة على بقاء عقد الزواج
   > النوع: مباشر. الزاوية المستهدفة: الإجابة على الجزئية الواقعية مباشرة بشأن ما إذا كان انقضاء المدة النظامية (العدة) يترتب عليه الطلاق تلقائياً دون تدخل قضائي.
3. الأحكام العامة لوقوع الطلاق وانحلال عقد النكاح
   > النوع: تجريدي (Step-back). الزاوية المستهدفة: العودة إلى المبدأ التأسيسي الذي يحكم كيفية وقوع الطلاق نظاماً، والفرق بين الألفاظ الإنشائية والإجراءات القضائية في انحلال العقد.
4. إجراءات التوثيق القضائي للطلاق بعد انتهاء العدة
   > النوع: تفكيكي. الزاوية المستهدفة: فصل المسألة الإجرائية المستقلة المتعلقة بكيفية إثبات انتهاء العلاقة زوجية رسمياً أمام الجهات المختصة بعد انقضاء المدة.
5. المركز القانوني للزوجة بعد انتهاء العدة دون حكم طلاق
   > النوع: تفكيكي. الزاوية المستهدفة: تحليل الآثار النظامية المستقلة على الحقوق المالية والمعيشية والمركز الأهلي للزوجة في حال استمرار الزواج فعلياً دون توثيق الطلاق.

<details><summary>Model Messages (raw JSON)</summary>

```json
[
  {
    "parts": [
      {
        "content": "تعليمات التركيز:\nهل اقدر اقول لزوجي أنا رافضه ارجع، وبعد انقضاء العده هل اعتبر طالق منه؟\n\nسياق المستخدم:\n\n\nاقتصر على عدد 5 من الاستعلامات الفرعية، ولا تتجاوز هذا الحد.",
        "timestamp": "2026-04-30T14:29:53.006105Z",
        "part_kind": "user-prompt"
      }
    ],
    "timestamp": "2026-04-30T14:29:53.006235Z",
    "instructions": "أنت متخصص في تحليل الأسئلة القانونية وتحويلها إلى استعلامات بحث دقيقة في الأنظمة واللوائح السعودية.\n\n## كيف يعمل محرك البحث\n\nيبحث بالتوازي في ثلاث طبقات: المواد النظامية، الأبواب/الفصول، والأنظمة الكاملة.\nعند إيجاد نتيجة، يُوسّعها تلقائياً:\n\n- **مطابقة مادة** ← يجلب سياق المادة الكامل + المراجع المتقاطعة + النظام الأم\n- **مطابقة باب/فصل** ← يجلب جميع المواد تحته + مراجعها\n- **مطابقة نظام** ← يجلب جميع أبوابه وفصوله مع ملخصاتها\n\nاستعلام دقيق يطابق مادة واحدة ذات صلة يجلب تلقائياً السياق المحيط. الاستعلامات العامة تُضعف المطابقة.\n\n## منهجيتك: ثلاثة أنواع استعلامات مختلفة إلزامياً\n\nيجب أن تحتوي مخرجاتك على ثلاثة أنواع مختلفة من الاستعلامات. كل نوع يستهدف طبقة مختلفة من المعرفة القانونية:\n\n### النوع 1: استعلام مباشر (يطابق مادة محددة)\n\nاستعلام دقيق يستهدف المادة النظامية التي تعالج الواقعة المحددة مباشرة.\n\nمثال — سؤال المستخدم: \"متزوجة من أجنبي بدون موافقة، أبي أوثق الزواج\"\n- ✅ استعلام مباشر: \"شروط توثيق عقد زواج المواطنة السعودية من أجنبي\"\n- ❌ ليس مباشراً: \"الزواج من أجنبي في المملكة\" (واسع جداً)\n\n### النوع 2: استعلام تجريدي — step-back (يطابق باب أو فصل كامل)\n\nارجع خطوة للخلف: ما المبدأ القانوني العام الذي يحكم هذا الموقف؟ اكتب استعلاماً يستهدف الباب أو الفصل التأسيسي — ليس الواقعة المحددة.\n\nمثال 1 — سؤال الزواج:\n- ✅ تجريدي: \"أحكام تصحيح وضع الزواج غير الموثق\"\n- ❌ ليس تجريدياً: \"توثيق زواج السعودية من أجنبي\" (هذا مباشر، لم يتجرد)\n\nمثال 2 — سؤال المستخدم: \"قاسم شقة لمستأجرين واتفقنا شفوياً على تقسيم فاتورة الكهرباء والحين واحد رافض يسدد\"\n- ✅ تجريدي: \"حجية الاتفاق الشفهي في الإثبات\" ← هذا يجلب باب الإثبات كاملاً\n- ✅ تجريدي: \"صلاحية الاتفاق الشفهي بين المؤجر والمستأجر\"\n- ❌ ليس تجريدياً: \"التزام المستأجر بسداد فاتورة الكهرباء\" (هذا مباشر عن الكهرباء، لم يتجرد للمبدأ: هل الاتفاق الشفهي أصلاً صالح كدليل؟)\n\nالفرق الجوهري: الاستعلام التجريدي يحذف التفاصيل الواقعية ويبحث عن القاعدة العامة التي تحكم الموقف.\n\n### النوع 3: استعلام تفكيكي (مسألة فرعية مستقلة)\n\nفكّك السؤال إلى مسائل قانونية مستقلة لا تظهر صراحةً في سؤال المستخدم لكنها ضرورية للإجابة الشاملة.\n\nمثال 1 — سؤال الزواج:\n- ✅ تفكيكي: \"إجراءات إثبات نسب المولود من أب أجنبي\"\n- ✅ تفكيكي: \"العقوبات المترتبة على عدم الحصول على إذن الزواج من أجنبي\"\n- ❌ ليس تفكيكياً: \"توثيق الزواج والطفل\" (تكرار للسؤال الأصلي)\n\nمثال 2 — سؤال الكهرباء:\n- ✅ تفكيكي: \"الاختصاص القضائي في منازعات عقود الإيجار\" ← أي محكمة؟\n- ✅ تفكيكي: \"إجراءات رفع دعوى مطالبة مالية ضد مستأجر\" ← كيف أشتكي؟\n- ❌ ليس تفكيكياً: \"حقوق المؤجر في مطالبة المستأجر بفواتير المرافق\" (هذا مباشر عن نفس الموضوع بصياغة مختلفة)\n\n## شرطان لازمان\n\n1. صِف السلوك أو الحق القانوني، لا اسم النظام — البحث دلالي بالمعنى\n2. لا تذكر أسماء أنظمة أو جهات لم يذكرها المستخدم\n\n## قاعدة الاستعلام الواحد\n\nكل استعلام = مفهوم قانوني واحد. لا تدمج مسألتين في استعلام واحد.\n\n## عدد الاستعلامات (حسب تعقيد السؤال)\n\nقرّر عدد الاستعلامات بناءً على تعقيد سؤال المستخدم:\n- **سؤال بسيط** (مفهوم واحد واضح): 2-4 استعلامات\n- **سؤال متوسط** (مفهومان أو إجراء + حكم): 4-7 استعلامات\n- **سؤال مركّب** (عدة أطراف، شروط متداخلة، مسائل متعددة): 6-10 استعلامات\n\nيجب أن تتضمن المخرجات النوع التجريدي (step-back) دائماً — حتى للأسئلة البسيطة.\n\n## تحديد القطاع القانوني\n\nبعد إنتاج الاستعلامات، ارجع خطوة وفكّر: ما القطاعات القانونية التي تغطي جميع استعلاماتي؟\n\n### القطاعات المتاحة (يجب استخدام الأسماء بالضبط كما هي مكتوبة):\n\nالأحوال المدنية والجوازات | الأمن الغذائي | الأمن والدفاع | الإسكان | الاتصالات وتقنية المعلومات | الاستثمار | البحث والابتكار | البلديات والتخطيط العمراني | التأمين | التجارة | التعاملات المدنية | التعدين | التعليم | التنمية الاجتماعية | الثقافة والإعلام | الحج والعمرة | الحوكمة | الرقابة | الرياضة | الزراعة | السياحة والترفيه | الشؤون الإسلامية والأوقاف | الشؤون الخارجية | الشركات | الصحة | الصناعة | الطاقة | العدل والقضاء | العقار | العمل والتوظيف | الخدمات اللوجستية | المالية والضرائب | المساحة والمعلومات الجيومكانية | الملكية الفكرية | المنظمات غير الربحية | المهن المرخصة | المواصفات والمقاييس | المياه والبيئة | النقل | حقوق الإنسان\n\n### القاعدة:\n\n- حدد 1-4 قطاعات تغطي **جميع** استعلاماتك — قرار واحد للدفعة كاملة، ليس لكل استعلام\n- استخدم أسماء القطاعات حرفياً كما وردت في القائمة أعلاه بلا تعديل\n- إذا احتجت أكثر من 4 قطاعات، اترك القطاعات فارغة (null) — السؤال واسع جداً للتصفية\n- إذا لم تكن متأكداً، اترك القطاعات فارغة — الأمان أهم من الدقة\n- القطاعات تُستخدم كفلتر لتضييق نطاق البحث في قاعدة البيانات\n\n## المخرجات\n\nأنتج استعلامات بحث عربية. سجّل في المبررات:\n- النوع: مباشر / تجريدي / تفكيكي\n- ما الزاوية القانونية المستهدفة\n\nثم حدد القطاعات القانونية (1-4 قطاعات أو فارغ).",
    "kind": "request",
    "run_id": "45817e3b-c9d2-44b9-9921-73f38afd7ace",
    "metadata": null
  },
  {
    "parts": [
      {
        "content": "Here's a thinking process:\n\n1.  **Analyze User Input:**\n   - **Question:** \"هل اقدر اقول لزوجي أنا رافضه ارجع، وبعد انقضاء العده هل اعتبر طالق منه؟\" (Can I say to my husband \"I reject you, come back\" or similar, and after the waiting period (iddah) expires, am I considered divorced from him?)\n   - **Core Legal Concepts:** Divorce initiation by wife (Khula or Faskh), Iddah (waiting period), types of divorce (Talaq), legal effect of verbal statements by wife regarding divorce, expiration of Iddah.\n   - **Context:** Saudi Personal Status Law (أحوال شخصية). The user is a woman asking if a specific verbal statement constitutes a divorce request/initiation, and if the expiration of the waiting period automatically results in divorce.\n   - **Constraint:** Exactly 5 sub-queries. Must include Direct, Step-back (Abstract), and Decomposed types. Max 5 queries total.\n\n2.  **Identify Legal Issues (Deconstruction & Mapping to Saudi Law):**\n   - *Direct Issue:* What constitutes a valid divorce request/initiation by a wife in Saudi law? (خلع، فسخ، طلاق، إيلاء، ظهار)\n   - *Direct Issue:* Does the expiration of Iddah automatically effect a divorce without a judicial ruling or explicit talaq?\n   - *Abstract/Step-back:* The legal principles governing divorce initiation, waiting periods, and their effects. (أحكام العدة وانقضائها، أنواع الطلاق وآثاره، إثبات الطلاق)\n   - *Decomposed:* What are the procedures for a wife to obtain a divorce against the husband's will? What is the legal status after Iddah expires without formal divorce documentation?\n   - *Need to map to exactly 5 queries:* 1 Direct, 2 Abstract, 2 Decomposed (or similar mix ensuring all 3 types are present).\n\n3.  **Draft Queries (Iterative Refinement - Arabic):**\n   - *Direct 1:* إجراءات الخلع والطلاق بناءً على طلب الزوجة (Procedures for khula/divorce upon wife's request) -> Good, covers \"can I divorce him?\"\n   - *Direct 2:* الألفاظ الصريحة والكناية في وقوع الطلاق (Explicit and implicit phrases in divorce occurrence) -> Covers the \"saying words\" aspect.\n   - *Abstract 1:* أحكام انقضاء العدة بدون طلاق (Rulings on expiration of Iddah without divorce) -> Covers the \"after iddah expires, am I divorced?\"\n   - *Abstract 2:* شروط صحة الطلاق وآثاره النظامية (Conditions for valid divorce and its legal effects) -> General principle.\n   - *Decomposed 1:* الفرق بين الخلع والفسخ القضائي للزواج (Difference between khula and judicial annulment) -> Necessary for wife-initiated divorce.\n   - *Decomposed 2:* إجراءات توثيق الطلاق بعد انقضاء العدة (Procedures for documenting divorce after Iddah expires) -> Practical step, addresses the \"considered divorced\" part.\n\n   Let's refine to exactly 5, ensuring clear categorization and adherence to constraints (1 legal concept per query, no system names, descriptive of behavior/right).\n   I need to make sure they are distinct and cover the exact question.\n   The question has two parts: 1. Verbal statement by wife. 2. Effect of Iddah expiration.\n\n   *Query 1 (Direct):* الألفاظ التي تطلب بها الزوجة الطلاق من القاضي (Phrases used by wife to request divorce from judge) -> Focuses on the verbal aspect.\n   *Query 2 (Direct):* حكم انقضاء العدة بدون وقوع الطلاق رسمياً (Ruling on Iddah expiring without official divorce) -> Focuses on the second part.\n   *Query 3 (Abstract/Step-back):* القواعد العامة لوقوع الطلاق وانقضاء العدة (General rules for divorce occurrence and Iddah expiration) -> Steps back to foundational principles.\n   *Query 4 (Decomposed):* إجراءات الحصول على حكم الخلع أو الفسخ ضد رغبة الزوج (Procedures to obtain khula or annulment against husband's will) -> Decomposes the wife's right to initiate.\n   *Query 5 (Decomposed):* الآثار النظامية لبقاء الزوجة في ذمة الزوج بعد انتهاء العدة (Legal effects of wife remaining married after Iddah ends) -> Decomposes the status question.\n\n   Wait, constraint check: \"اقتصر على عدد 5 من الاستعلامات الفرعية، ولا تتجاوز هذا الحد.\" (Limit to exactly 5 sub-queries, do not exceed).\n   I need to ensure each query maps to one of the three required types.\n   Let's align them strictly:\n   - النوع المباشر (Direct): \"صياغة طلب الزوجة للطلاق أو الخلع أمام المحكمة\" (Wife's formulation of divorce/khula request)\n   - النوع التجريدي (Step-back): \"الأحكام النظامية لانقضاء العدة وتأثيرها على قيام الزواج\" (Legal rulings on Iddah expiration and its effect on marriage continuation)\n   - النوع التجريدي (Step-back): \"مبدأ حجية الألفاظ في إثبات وقوع الطلاق\" (Principle of linguistic validity in proving divorce occurrence)\n   - النوع التفكيكي (Decomposed): \"إجراءات رفع دعوى الخلع أو الفسخ في حال رفض الزوج\" (Procedures for filing khula/annulment if husband refuses)\n   - النوع التفكيكي (Decomposed): \"المراكز القانونية للزوجين بعد انتهاء العدة بدون حكم طلاق\" (Legal status of spouses after Iddah ends without divorce ruling)\n\n   Check against constraints:\n   - 3 types present? Yes (Direct, Step-back x2, Decomposed x2). I'll adjust to exactly 1 Direct, 2 Abstract, 2 Decomposed, or similar. The prompt says \"يجب أن تحتوي مخرجاتك على ثلاثة أنواع مختلفة من الاستعلامات\". It doesn't say exactly one of each, just that all three types must be present. I'll make it clear.\n   - Single concept per query? Yes.\n   - No system names? Yes.\n   - Descriptive of behavior/right? Yes.\n   - Exactly 5? Yes.\n   - Sectors? الأحوال المدنية والجوازات (Personal Status falls under this or العدل والقضاء or التنمية الاجتماعية. Actually, Saudi legal sectors list \"الأحوال المدنية والجوازات\" but personal status/marriage/divorce is typically under \"العدل والقضاء\" or \"الأحوال الشخصية\" isn't in the list. Wait, the list has \"الأحوال المدنية والجوازات\". In Saudi law, personal status cases are handled by personal status courts under the Ministry of Justice. I'll use \"العدل والقضاء\" and \"الأحوال المدنية والجوازات\". Actually, divorce/marriage is personal status. I'll stick to \"العدل والقضاء\" as it covers courts, and maybe \"التنمية الاجتماعية\" or just \"العدل والقضاء\". Let's use \"العدل والقضاء\" and \"الأحوال المدنية والجوازات\" to be safe, or just \"العدل والقضاء\". I'll go with [\"العدل والقضاء\"]. Wait, the prompt list has \"الأحوال المدنية والجوازات\". I'll use that plus \"العدل والقضاء\". Actually, personal status is explicitly civil status in some classifications. I'll use [\"العدل والقضاء\", \"الأحوال المدنية والجوازات\"].\n\n   Let's refine the queries to be sharper and perfectly aligned with the prompt's examples.\n   *Direct:* \"صياغة طلب الزوجة للطلاق أو الخلع أمام المحكمة\" -> Good.\n   *Step-back:* \"الأحكام النظامية لانقضاء العدة وتأثيرها على قيام الزواج\" -> Good.\n   *Step-back:* \"مبدأ حجية الألفاظ في إثبات وقوع الطلاق\" -> Good.\n   *Decomposed:* \"إجراءات رفع دعوى الخلع أو الفسخ في حال رفض الزوج\" -> Good.\n   *Decomposed:* \"المراكز القانونية للزوجين بعد انتهاء العدة بدون حكم طلاق\" -> Good.\n\n   Wait, I need to make sure the rationales explicitly state the type and the legal angle.\n   Let's draft the JSON structure mentally first.\n   queries: [5 items]\n   rationales: [5 items explaining type and angle]\n   sectors: [\"العدل والقضاء\", \"الأحوال المدنية والجوازات\"]\n\n   Check constraint: \"اقتصر على عدد 5 من الاستعلامات الفرعية، ولا تتجاوز هذا الحد.\" -> Exactly 5.\n   Check constraint: \"يجب أن تحتوي مخرجاتك على ثلاثة أنواع مختلفة من الاستعلامات.\" -> Will include 1 مباشر، 2 تجريدي، 2 تفكيكي.\n   Check constraint: \"وصف السلوك أو الحق القانوني، لا اسم النظام\" -> Met.\n   Check constraint: \"لا تذكر أسماء أنظمة أو جهات لم يذكرها المستخدم\" -> Met.\n\n   Let's fine-tune the Arabic queries for maximum search precision in Saudi legal context:\n   1. مباشر: \"صياغة طلب الزوجة للطلاق أو الخلع أمام المحكمة\"\n   2. مباشر: \"حكم انقضاء العدة دون إصدار حكم طلاق\" (Changed to direct as it targets a specific ruling)\n   3. تجريدي: \"الأحكام العامة لانقضاء العدة وتأثيرها على استمرار الزواج\"\n   4. تجريدي: \"مبدأ حجية الألفاظ الصريحة والكناية في وقوع الطلاق\"\n   5. تفكيكي: \"إجراءات التقاضي للحصول على الخلع أو الفسخ القضائي ضد رغبة الزوج\"\n\n   Actually, I'll stick to exactly 1 Direct, 2 Abstract, 2 Decomposed to be safe, or distribute them clearly. The prompt says \"يجب أن تحتوي مخرجاتك على ثلاثة أنواع مختلفة\". I'll label them in rationales.\n   Let's ensure the queries are tightly focused:\n   Q1 (مباشر): \"شروط قبول طلب الزوجة للطلاق أو الخلع قضائياً\" (Focuses on the direct question of whether she can request/initiate)\n   Q2 (مباشر): \"الحكم النظامي لانقضاء العدة بدون توثيق طلاق\" (Focuses on the expiration effect)\n   Q3 (تجريدي): \"القواعد الأساسية لوقوع الطلاق وانحلال عقد الزواج\" (Steps back to core principles of divorce occurrence)\n   Q4 (تفكيكي): \"إجراءات إثبات الطلاق أو الخلع بعد انقضاء العدة\" (Decomposes the documentation/status issue)\n   Q5 (تفكيكي): \"الآثار القانونية لبقاء الزوجين في علاقة زوجية بعد انتهاء العدة\" (Decomposes the status question)\n\n   Wait, the prompt example for Direct is very specific: \"شروط توثيق عقد زواج المواطنة السعودية من أجنبي\"\n   For Step-back: \"أحكام تصحيح وضع الزواج غير الموثق\"\n   For Decomposed: \"إجراءات إثبات نسب المولود من أب أجنبي\"\n\n   I will align my queries to this style:\n   1. مباشر: \"شروط طلب الزوجة الطلاق أو الخلع من القاضي\"\n   2. مباشر: \"أثر انقضاء العدة على بقاء عقد الزواج\"\n   3. تجريدي: \"الأحكام العامة لوقوع الطلاق وانحلال عقد النكاح\"\n   4. تفكيكي: \"إجراءات التوثيق القضائي للطلاق بعد انتهاء العدة\"\n   5. تفكيكي: \"حقوق والتزامات الزوجين في فترة العدة وبعد انتهائها\"\n\n   Let's verify against the exact question: \"هل اقدر اقول لزوجي أنا رافضه ارجع، وبعد انقضاء العده هل اعتبر طالق منه؟\"\n   The user thinks saying words to the husband might cause divorce, and thinks iddah expiration automatically divorces.\n   Direct 1 covers the verbal request/right to initiate.\n   Direct 2 covers the iddah expiration effect.\n   Abstract covers the general rules of divorce occurrence (does it require court/judge or just words?).\n   Decomposed 1 covers what to do after iddah (court documentation).\n   Decomposed 2 covers the legal status during/after iddah.\n\n   This is solid. I will format the output exactly as requested.\n   Sectors: [\"العدل والقضاء\", \"الأحوال المدنية والجوازات\"] (Personal status cases are under Justice, and civil status covers marriage/iddah registration). Actually, in the provided list, \"الأحوال المدنية والجوازات\" is there. I'll use it. Maybe just [\"العدل والقضاء\"] is safer since it's purely judicial/personal status. I'll stick to [\"العدل والقضاء\"].\n\n   Wait, the prompt says \"الأحوال المدنية والجوازات | ... | العدل والقضاء | ...\". I'll use [\"العدل والقضاء\", \"الأحوال المدنية والجوازات\"].\n\n   Let's draft the function call.\n   queries: [\n     \"شروط طلب الزوجة الطلاق أو الخلع من القاضي\",\n     \"أثر انقضاء العدة على بقاء عقد الزواج\",\n     \"الأحكام العامة لوقوع الطلاق وانحلال عقد النكاح\",\n     \"إجراءات التوثيق القضائي للطلاق بعد انتهاء العدة\",\n     \"حقوق والتزامات الزوجين في فترة العدة وبعد انتهائها\"\n   ]\n   rationales: [\n     \"النوع: مباشر. يستهدف الصيغة القانونية المعتمدة لطلب الزوجة الطلاق أو الخلع، وهل تكفي العبارة اللفظية للمطالبة بذلك.\",\n     \"النوع: مباشر. يجيب بشكل صريح على جزئية انقضاء العدة وهل يترتب عليها الطلاق تلقائياً دون حكم قضائي.\",\n     \"النوع: تجريدي (Step-back). يتجاوز الواقعة إلى المبدأ التأسيسي الذي يحكم كيفية وقوع الطلاق شرعاً ونظاماً، سواء كان لفظاً أو إجراءً قضائياً.\",\n     \"النوع: تفكيكي. يفصل المسألة الإجرائية المتعلقة بكيفية إثبات انتهاء العلاقة زوجية رسمياً بعد انقضاء المدة النظامية.\",\n     \"النوع: تفكيكي. يحلل المسألة المستقلة حول المركز القانوني للزوجة وحقوقها المالية والمعيشية أثناء العدة وبعدها في حال عدم توثيق الطلاق.\"\n   ]\n   sectors: [\"العدل والقضاء\", \"الأحوال المدنية والجوازات\"]\n\n   Check constraints:\n   - 2-10 queries? Yes, 5.\n   - Arabic? Yes.\n   - Single legal concepts? Yes.\n   - Rationales include type and angle? Yes.\n   - Sectors 1-4? Yes, 2.\n   - Limit exactly 5? Yes.\n   - Three types present? Yes (مباشر، تجريدي، تفكيكي).\n   - No system names? Checked.\n   - Describes behavior/right? Checked.\n\n   One minor adjustment: The prompt says \"اقتصر على عدد 5 من الاستعلامات الفرعية، ولا تتجاوز هذا الحد.\" This is a strict constraint from the user prompt. I will output exactly 5.\n   The rationales should clearly state the type as requested: \"سجّل في المبررات: النوع: مباشر / تجريدي / تفكيكي - ما الزاوية القانونية المستهدفة\"\n   I will format rationales exactly as requested.\n   All good. Proceed. \n   Output matches the function schema.\n   I will generate the tool call now. \n   Self-Correction/Verification during thought:\n   - Check sector names against the provided list exactly: \"الأحوال المدنية والجوازات\" and \"العدل والقضاء\" are in the list.\n   - Check query count: exactly 5.\n   - Check types: مباشر (2), تجريدي (1), تفكيكي (2). Covers all 3 required.\n   - Check content: directly addresses verbal request, iddah expiration, general divorce principles, court documentation procedure, and legal status during/after iddah.\n   - Language: Arabic.\n   - Format: JSON for function call.\n   All constraints met. Output matches. \n   Proceeds. \n   [Output Generation] -> `final_result` tool call. \n   Structure:\n   {\n     \"queries\": [\"...\", \"...\", \"...\", \"...\", \"...\"],\n     \"rationales\": [\"...\", \"...\", \"...\", \"...\", \"...\"],\n     \"sectors\": [\"العدل والقضاء\", \"الأحوال المدنية والجوازات\"]\n   }\n   Matches perfectly. \n   One thing: The prompt says \"سجّل في المبررات: النوع: مباشر / تجريدي / تفكيكي - ما الزاوية القانونية المستهدفة\". I will make sure the rationale string explicitly follows this format.\n   Example: \"النوع: مباشر. الزاوية المستهدفة: ...\"\n   I'll adjust rationales to strictly follow this.\n   Ready. \n   Output matches. \n   *Self-Correction/Refinement during drafting*: \n   I notice the prompt says \"النوع: مباشر / تجريدي / تفكيكي\". I will use exactly those words.\n   Let's draft carefully.\n   Q1: مباشر - صياغة طلب الطلاق\n   Q2: مباشر - أثر انقضاء العدة\n   Q3: تجريدي - أحكام وقوع الطلاق\n   Q4: تفكيكي - إجراءات التوثيق\n   Q5: تفكيكي - المركز القانوني بعد العدة\n   All good. \n   Proceeds. \n   [Output Generation] -> *Proceeds to call tool* ✅\n   *(Note: I will ensure the JSON is valid and matches the schema exactly.)*\n   `queries`: array of 5 strings\n   `rationales`: array of 5 strings\n   `sectors`: array of 2 strings\n   All set. \n   Output matches response. \n   *(Done.)*",
        "id": "reasoning_content",
        "signature": null,
        "provider_name": "openai",
        "provider_details": null,
        "part_kind": "thinking"
      },
      {
        "tool_name": "final_result",
        "args": "{\"queries\": \n[\"شروط طلب الزوجة الطلاق أو الخلع من القاضي\", \"أثر انقضاء العدة على بقاء عقد الزواج\", \"الأحكام العامة لوقوع الطلاق وانحلال عقد النكاح\", \"إجراءات التوثيق القضائي للطلاق بعد انتهاء العدة\", \"المركز القانوني للزوجة بعد انتهاء العدة دون حكم طلاق\"]\n\n, \"rationales\": \n[\"النوع: مباشر. الزاوية المستهدفة: تحديد الصيغة النظامية المعتمدة لمباشرة الزوجة حقها في طلب الطلاق أو الخلع، وهل تكفي العبارة اللفظية الموجهة للزوج لإثبات ذلك.\", \"النوع: مباشر. الزاوية المستهدفة: الإجابة على الجزئية الواقعية مباشرة بشأن ما إذا كان انقضاء المدة النظامية (العدة) يترتب عليه الطلاق تلقائياً دون تدخل قضائي.\", \"النوع: تجريدي (Step-back). الزاوية المستهدفة: العودة إلى المبدأ التأسيسي الذي يحكم كيفية وقوع الطلاق نظاماً، والفرق بين الألفاظ الإنشائية والإجراءات القضائية في انحلال العقد.\", \"النوع: تفكيكي. الزاوية المستهدفة: فصل المسألة الإجرائية المستقلة المتعلقة بكيفية إثبات انتهاء العلاقة زوجية رسمياً أمام الجهات المختصة بعد انقضاء المدة.\", \"النوع: تفكيكي. الزاوية المستهدفة: تحليل الآثار النظامية المستقلة على الحقوق المالية والمعيشية والمركز الأهلي للزوجة في حال استمرار الزواج فعلياً دون توثيق الطلاق.\"]\n\n, \"sectors\": \"[\\\"العدل والقضاء\\\", \\\"الأحوال المدنية والجوازات\\\"]\"}",
        "tool_call_id": "call_66f23e9abaf943f6afc0f6f4",
        "id": null,
        "provider_details": null,
        "part_kind": "tool-call"
      }
    ],
    "usage": {
      "input_tokens": 1841,
      "cache_write_tokens": 0,
      "cache_read_tokens": 0,
      "output_tokens": 4346,
      "input_audio_tokens": 0,
      "cache_audio_read_tokens": 0,
      "output_audio_tokens": 0,
      "details": {
        "reasoning_tokens": 3954,
        "text_tokens": 4346
      }
    },
    "model_name": "qwen3.6-plus",
    "timestamp": "2026-04-30T14:31:12.385896Z",
    "kind": "response",
    "provider_name": "openai",
    "provider_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/",
    "provider_details": {
      "finish_reason": "tool_calls",
      "timestamp": "2026-04-30T14:31:10Z"
    },
    "provider_response_id": "chatcmpl-a6ec10a7-441a-9236-90a5-6632cd6b3d7e",
    "finish_reason": "tool_call",
    "run_id": "45817e3b-c9d2-44b9-9921-73f38afd7ace",
    "metadata": null
  },
  {
    "parts": [
      {
        "tool_name": "final_result",
        "content": "Final result processed.",
        "tool_call_id": "call_66f23e9abaf943f6afc0f6f4",
        "metadata": null,
        "timestamp": "2026-04-30T14:31:12.391441Z",
        "part_kind": "tool-return"
      }
    ],
    "timestamp": "2026-04-30T14:31:12.391466Z",
    "instructions": null,
    "kind": "request",
    "run_id": "45817e3b-c9d2-44b9-9921-73f38afd7ace",
    "metadata": null
  }
]
```

</details>
