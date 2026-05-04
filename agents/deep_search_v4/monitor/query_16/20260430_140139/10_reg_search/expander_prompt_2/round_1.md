# Expander — Round 1
**Prompt key:** `prompt_2`

## Usage
| Requests | Input tokens | Output tokens | Reasoning tokens | Total tokens |
|----------|-------------|---------------|-----------------|-------------|
| 1 | 2,147 | 2,776 | 2,397 | 4,923 |

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
كنت موظف في شركه من 2012 بدون عقد الا نهايه 2018 قبل نهاية السنه ب 4 شهور تقريبا اعطونا عقود ووقعناها. انتهت السنه انهو خدماتنا. اخر يوم لي عمل معاهم كان 5/2/2019. مع شهر الانذار.
الشي الثاني حقوق نهاية الخدمه الا الان مااستلمتها الشركه بتماطل رفعت عليهم قضيه في مكتب العمل الموعد الاول ماحضرو الموعد الثاني كان قبل رمضان ب 3 ايام تقريبا. جاء محامي من الشركة وجالس يماطل يقول مايدري عن الموضوع وراح يراجع الشركه ويرد علي وطلب مهله. رفضت والحين المعامله بتتحول ع المحكمه العمالية. موظف شؤون الموظفين يقول حنأخرك الين قبل موعد المحكمه او الجلسه الاولي كذا بس عشان اشتكيت.
السؤال؟
1/كم تأخذ القضيه عشان تنتقل من مكتب العمل الي المحكمه؟
2/اقدر اطالب بتعويض عن فترة المماطله. 5 شهور تقريبا وكيف احدد قيمة التعويض؟
3/اذا كلموني قبل موعد المحكمه بشيء بسيط عشان استلم الحقوق من دون التعويض اقدر ارفض؟
4/اذا وكلت محامي الشركة تتحمل تكاليفه...

سياق المستخدم:


اقتصر على عدد 5 من الاستعلامات الفرعية، ولا تتجاوز هذا الحد.
الصكاترات (المجالات القانونية) محددة مسبقاً من المُخطِّط: العمل والتوظيف. لا تحتاج لاختيار صكاترات جديدة.

## Output

**Queries (5):**

1. المدة النظامية لتحويل النزاع العمالي من مكتب العمل إلى المحكمة
   > نوع 1 (مباشر): يستهدف الإجابة المباشرة على سؤال المستخدم حول المدة الزمنية المتوقعة لنقل الملف من المرحلة الإدارية (مكتب العمل) إلى المرحلة القضائية.
2. أسس تقدير التعويض عن المماطلة في سداد مستحقات نهاية الخدمة
   > نوع 2 (تجريدي - Step-back): يبحث عن المبدأ العام القاضي بتقدير التعويضات عند التأخير في الوفاء بالالتزامات المالية العمالية، بدلاً من التركيز فقط على واقعة المماطلة المحددة.
3. قواعد تحميل أتعاب المحاماة في الدعاوي العمالية
   > نوع 3 (تفكيكي): مسألة فرعية مستقلة تتعلق بعبء التكاليف القانونية، حيث يسأل المستخدم عما إذا كانت الشركة تتحمل أتعاب محاميه.
4. إجراءات رفض الصلح الجزئي والتمسك بالحقوق النظامية
   > نوع 3 (تفكيكي): مسألة فرعية إجرائية حول حق العامل في رفض عروض التسوية التي لا تغطي كافة الحقوق، وهي خطوة تسبق الجلسة القضائية.
5. إثبات العلاقة العمالية المستمرة قبل توثيق العقد
   > نوع 3 (تفكيكي): مسألة فرعية جوهرية ضمنية؛ فعمل الموظف منذ 2012 بدون عقد يخلق حاجة لإثبات هذه الفترة لضمان احتساب نهاية الخدمة كاملة وليس فقط من تاريخ العقد 2018.

<details><summary>Model Messages (raw JSON)</summary>

```json
[
  {
    "parts": [
      {
        "content": "تعليمات التركيز:\nكنت موظف في شركه من 2012 بدون عقد الا نهايه 2018 قبل نهاية السنه ب 4 شهور تقريبا اعطونا عقود ووقعناها. انتهت السنه انهو خدماتنا. اخر يوم لي عمل معاهم كان 5/2/2019. مع شهر الانذار.\nالشي الثاني حقوق نهاية الخدمه الا الان مااستلمتها الشركه بتماطل رفعت عليهم قضيه في مكتب العمل الموعد الاول ماحضرو الموعد الثاني كان قبل رمضان ب 3 ايام تقريبا. جاء محامي من الشركة وجالس يماطل يقول مايدري عن الموضوع وراح يراجع الشركه ويرد علي وطلب مهله. رفضت والحين المعامله بتتحول ع المحكمه العمالية. موظف شؤون الموظفين يقول حنأخرك الين قبل موعد المحكمه او الجلسه الاولي كذا بس عشان اشتكيت.\nالسؤال؟\n1/كم تأخذ القضيه عشان تنتقل من مكتب العمل الي المحكمه؟\n2/اقدر اطالب بتعويض عن فترة المماطله. 5 شهور تقريبا وكيف احدد قيمة التعويض؟\n3/اذا كلموني قبل موعد المحكمه بشيء بسيط عشان استلم الحقوق من دون التعويض اقدر ارفض؟\n4/اذا وكلت محامي الشركة تتحمل تكاليفه...\n\nسياق المستخدم:\n\n\nاقتصر على عدد 5 من الاستعلامات الفرعية، ولا تتجاوز هذا الحد.\nالصكاترات (المجالات القانونية) محددة مسبقاً من المُخطِّط: العمل والتوظيف. لا تحتاج لاختيار صكاترات جديدة.",
        "timestamp": "2026-04-30T14:02:03.379156Z",
        "part_kind": "user-prompt"
      }
    ],
    "timestamp": "2026-04-30T14:02:03.379482Z",
    "instructions": "أنت متخصص في تحليل الأسئلة القانونية وتحويلها إلى استعلامات بحث دقيقة في الأنظمة واللوائح السعودية.\n\n## كيف يعمل محرك البحث\n\nيبحث بالتوازي في ثلاث طبقات: المواد النظامية، الأبواب/الفصول، والأنظمة الكاملة.\nعند إيجاد نتيجة، يُوسّعها تلقائياً:\n\n- **مطابقة مادة** ← يجلب سياق المادة الكامل + المراجع المتقاطعة + النظام الأم\n- **مطابقة باب/فصل** ← يجلب جميع المواد تحته + مراجعها\n- **مطابقة نظام** ← يجلب جميع أبوابه وفصوله مع ملخصاتها\n\nاستعلام دقيق يطابق مادة واحدة ذات صلة يجلب تلقائياً السياق المحيط. الاستعلامات العامة تُضعف المطابقة.\n\n## منهجيتك: ثلاثة أنواع استعلامات مختلفة إلزامياً\n\nيجب أن تحتوي مخرجاتك على ثلاثة أنواع مختلفة من الاستعلامات. كل نوع يستهدف طبقة مختلفة من المعرفة القانونية:\n\n### النوع 1: استعلام مباشر (يطابق مادة محددة)\n\nاستعلام دقيق يستهدف المادة النظامية التي تعالج الواقعة المحددة مباشرة.\n\nمثال — سؤال المستخدم: \"متزوجة من أجنبي بدون موافقة، أبي أوثق الزواج\"\n- ✅ استعلام مباشر: \"شروط توثيق عقد زواج المواطنة السعودية من أجنبي\"\n- ❌ ليس مباشراً: \"الزواج من أجنبي في المملكة\" (واسع جداً)\n\n### النوع 2: استعلام تجريدي — step-back (يطابق باب أو فصل كامل)\n\nارجع خطوة للخلف: ما المبدأ القانوني العام الذي يحكم هذا الموقف؟ اكتب استعلاماً يستهدف الباب أو الفصل التأسيسي — ليس الواقعة المحددة.\n\nمثال 1 — سؤال الزواج:\n- ✅ تجريدي: \"أحكام تصحيح وضع الزواج غير الموثق\"\n- ❌ ليس تجريدياً: \"توثيق زواج السعودية من أجنبي\" (هذا مباشر، لم يتجرد)\n\nمثال 2 — سؤال المستخدم: \"قاسم شقة لمستأجرين واتفقنا شفوياً على تقسيم فاتورة الكهرباء والحين واحد رافض يسدد\"\n- ✅ تجريدي: \"حجية الاتفاق الشفهي في الإثبات\" ← هذا يجلب باب الإثبات كاملاً\n- ✅ تجريدي: \"صلاحية الاتفاق الشفهي بين المؤجر والمستأجر\"\n- ❌ ليس تجريدياً: \"التزام المستأجر بسداد فاتورة الكهرباء\" (هذا مباشر عن الكهرباء، لم يتجرد للمبدأ: هل الاتفاق الشفهي أصلاً صالح كدليل؟)\n\nالفرق الجوهري: الاستعلام التجريدي يحذف التفاصيل الواقعية ويبحث عن القاعدة العامة التي تحكم الموقف.\n\n### النوع 3: استعلام تفكيكي (مسألة فرعية مستقلة)\n\nفكّك السؤال إلى مسائل قانونية مستقلة لا تظهر صراحةً في سؤال المستخدم لكنها ضرورية للإجابة الشاملة.\n\nمثال 1 — سؤال الزواج:\n- ✅ تفكيكي: \"إجراءات إثبات نسب المولود من أب أجنبي\"\n- ✅ تفكيكي: \"العقوبات المترتبة على عدم الحصول على إذن الزواج من أجنبي\"\n- ❌ ليس تفكيكياً: \"توثيق الزواج والطفل\" (تكرار للسؤال الأصلي)\n\nمثال 2 — سؤال الكهرباء:\n- ✅ تفكيكي: \"الاختصاص القضائي في منازعات عقود الإيجار\" ← أي محكمة؟\n- ✅ تفكيكي: \"إجراءات رفع دعوى مطالبة مالية ضد مستأجر\" ← كيف أشتكي؟\n- ❌ ليس تفكيكياً: \"حقوق المؤجر في مطالبة المستأجر بفواتير المرافق\" (هذا مباشر عن نفس الموضوع بصياغة مختلفة)\n\n## شرطان لازمان\n\n1. صِف السلوك أو الحق القانوني، لا اسم النظام — البحث دلالي بالمعنى\n2. لا تذكر أسماء أنظمة أو جهات لم يذكرها المستخدم\n\n## قاعدة الاستعلام الواحد\n\nكل استعلام = مفهوم قانوني واحد. لا تدمج مسألتين في استعلام واحد.\n\n## عدد الاستعلامات (حسب تعقيد السؤال)\n\nقرّر عدد الاستعلامات بناءً على تعقيد سؤال المستخدم:\n- **سؤال بسيط** (مفهوم واحد واضح): 2-4 استعلامات\n- **سؤال متوسط** (مفهومان أو إجراء + حكم): 4-7 استعلامات\n- **سؤال مركّب** (عدة أطراف، شروط متداخلة، مسائل متعددة): 6-10 استعلامات\n\nيجب أن تتضمن المخرجات النوع التجريدي (step-back) دائماً — حتى للأسئلة البسيطة.\n\n## تحديد القطاع القانوني\n\nبعد إنتاج الاستعلامات، ارجع خطوة وفكّر: ما القطاعات القانونية التي تغطي جميع استعلاماتي؟\n\n### القطاعات المتاحة (يجب استخدام الأسماء بالضبط كما هي مكتوبة):\n\nالأحوال المدنية والجوازات | الأمن الغذائي | الأمن والدفاع | الإسكان | الاتصالات وتقنية المعلومات | الاستثمار | البحث والابتكار | البلديات والتخطيط العمراني | التأمين | التجارة | التعاملات المدنية | التعدين | التعليم | التنمية الاجتماعية | الثقافة والإعلام | الحج والعمرة | الحوكمة | الرقابة | الرياضة | الزراعة | السياحة والترفيه | الشؤون الإسلامية والأوقاف | الشؤون الخارجية | الشركات | الصحة | الصناعة | الطاقة | العدل والقضاء | العقار | العمل والتوظيف | الخدمات اللوجستية | المالية والضرائب | المساحة والمعلومات الجيومكانية | الملكية الفكرية | المنظمات غير الربحية | المهن المرخصة | المواصفات والمقاييس | المياه والبيئة | النقل | حقوق الإنسان\n\n### القاعدة:\n\n- حدد 1-4 قطاعات تغطي **جميع** استعلاماتك — قرار واحد للدفعة كاملة، ليس لكل استعلام\n- استخدم أسماء القطاعات حرفياً كما وردت في القائمة أعلاه بلا تعديل\n- إذا احتجت أكثر من 4 قطاعات، اترك القطاعات فارغة (null) — السؤال واسع جداً للتصفية\n- إذا لم تكن متأكداً، اترك القطاعات فارغة — الأمان أهم من الدقة\n- القطاعات تُستخدم كفلتر لتضييق نطاق البحث في قاعدة البيانات\n\n## المخرجات\n\nأنتج استعلامات بحث عربية. سجّل في المبررات:\n- النوع: مباشر / تجريدي / تفكيكي\n- ما الزاوية القانونية المستهدفة\n\nثم حدد القطاعات القانونية (1-4 قطاعات أو فارغ).",
    "kind": "request",
    "run_id": "144a4fb2-6202-402a-9c02-e664e5bde50a",
    "metadata": null
  },
  {
    "parts": [
      {
        "content": "The user is an employee in Saudi Arabia asking about labor law issues.\nHere's a breakdown of the user's situation:\n1.  Worked from 2012 to 2019 without a contract, then signed a contract for the last few months of 2018. Services terminated end of 2019 (last day 5/2/2019).\n2.  End of service benefits not received. Company is delaying.\n3.  Filed a case at the Labor Office (first hearing: company absent, second hearing: lawyer delayed/asked for time).\n4.  Case is transferring to the Labor Court. Employee affairs staff says they will delay until the court hearing date as retaliation for the complaint.\n5.  Questions:\n    *   Timeframe for case transfer from Labor Office to Labor Court.\n    *   Can I claim compensation for delay (approx. 5 months)? How to determine value?\n    *   Can I reject a settlement offer before the court date if it excludes compensation?\n    *   If I hire a lawyer, does the company pay for it?\n\nThe user specifically requested a limit of 5 sub-queries.\nThe sector is pre-determined as \"العمل والتوظيف\" (Labor and Employment).\n\nI need to generate queries based on the methodology:\n1.  **Direct (نوع 1)**: Targeting specific regulations about End of Service Benefits (EOSB), contract duration, and case transfer procedures.\n2.  **Abstract (Step-back) (نوع 2)**: Targeting general principles of compensation for delay or procedural abuse in labor disputes, or the legal status of unwritten contracts vs written ones.\n3.  **Deconstructive (نوع 3)**: Targeting the right to legal fees or settlement rejection procedures.\n\nPotential queries:\n1.  (Direct): إجراءات ومدة تحويل النزاعات العمالية من مكتب العمل إلى المحكمة (Procedure and duration for transferring labor disputes from labor office to court).\n2.  (Direct): أحكام تعويض المدة المتبقية من العقد أو التعويض عن إنهاء الخدمة بدون سبب مشروع (Compensation for remaining contract term or termination without valid cause - though the user says services ended, often if fired without cause, compensation is due. However, the user specifically asks about delay compensation). Let's stick to the user's explicit question about delay compensation if possible, or general delay claims in labor.\n    *   Better Direct: المطالبة بالتعويض عن التأخير في صرف مستحقات نهاية الخدمة (Claiming compensation for delay in paying EOSB).\n3.  (Abstract): التعويض عن الضرر الناتج عن المماطلة في الفصل في النزاعات العمالية (Compensation for damage resulting from delay in labor dispute resolution).\n4.  (Deconstructive): التزام صاحب العمل بتحمل أتعاب المحاماة في القضايا العمالية (Employer's obligation to bear attorney fees in labor cases).\n5.  (Deconstructive): إجراءات التصالح والصلح في المنازعات العمالية قبل الجلسة القضائية (Reconciliation and settlement procedures in labor disputes before court hearing).\n\nLet's refine them to be precise and within the 5 query limit.\n\nQuery 1 (Direct): \"إجراءات ومدة تحويل ملف النزاع العمالي من مكتب العمل إلى المحكمة\" (Targeting the specific procedural question #1).\nQuery 2 (Abstract/Deconstructive - Focus on delay/compensation): \"التعويض عن المماطلة في سداد مستحقات نهاية الخدمة\" (Targeting question #2).\nQuery 3 (Deconstructive - Focus on fees): \"قواعد تحميل أتعاب المحاماة في الدعاوي العمالية\" (Targeting question #4).\nQuery 4 (Direct/Deconstructive - Focus on settlement): \"أهلية الموظف رفض عرض الصلح الجزئي في القضايا العمالية\" (Targeting question #3).\nQuery 5 (Abstract - Broad principle): \"حجية العقد غير المكتوب في إثبات مدة الخدمة\" (Addressing the 2012-2018 period without contract, which is crucial context for calculating benefits, though the user didn't explicitly ask \"how much money\", the context implies calculating rights based on 7 years, not just the contract period). *Correction*: The user asks about delay, transfer time, and lawyer fees. The unwritten contract part is background. However, it's legally significant. If I have to stick to the *questions asked*, I should focus on those. The user asks: 1. Transfer time. 2. Compensation for delay. 3. Rejecting settlement. 4. Lawyer fees.\nSo I will prioritize queries answering these specific questions.\n\nRefined list:\n1.  \"المدة النظامية لتحويل قضية العمل من التسوية الودية إلى المحكمة\" (Direct - Answers Q1).\n2.  \"المطالبة بالتعويض عن التأخير في صرف مستحقات نهاية الخدمة\" (Direct/Abstract - Answers Q2).\n3.  \"حق العامل في رفض الصلح الجزئي قبل موعد الجلسة\" (Deconstructive - Answers Q3).\n4.  \"قواعد تحميل الطرف الخاسر أتعاب المحاماة في القضايا العمالية\" (Deconstructive - Answers Q4).\n5.  \"إثبات مدة الخدمة السابقة دون عقد عمل مكتوب\" (Abstract - Important context for the \"2012-2018\" period which might affect the base amount, even if not a specific question, it's a \"hidden\" sub-issue essential for the full answer). Or maybe \"المماطلة في حضور جلسات التسوية العمالية وعقوبتها\" (Abstract - Addresses the behavior of the company). Let's stick to the \"2012-2018\" issue because the user mentioned \"worked without contract\" which usually implies a need to prove that period to get the full EOSB, otherwise they only get EOSB for the contract period.\nActually, the user's specific questions are about the *process* (delay, transfer time, fees). The context implies the underlying rights are established, but the company is delaying.\nI will stick to the questions asked + the general principle of delay/abuse.\n\nLet's select:\n1.  \"الإجراءات والمدة النظامية لنقل النزاع العمالي من مكتب العمل للمحكمة\" (Direct - Q1).\n2.  \"حق العامل في المطالبة بالتعويض عن المماطلة في صرف المستحقات\" (Abstract - Q2).\n3.  \"قواعد تحميل أتعاب المحاماة في القضايا العمالية\" (Deconstructive - Q4).\n4.  \"حكم رفض عرض الصلح في النزاعات العمالية قبل الجلسة القضائية\" (Deconstructive - Q3).\n5.  \"آلية المطالبة بتعويض الضرر من المماطلة في التقاضي\" (Step-back/Abstract - General principle of procedural delay damages).\n\nWait, the user asked \"how to determine the value of compensation?\". This is tricky because Saudi law might not explicitly have a fixed table for \"delay compensation\" in labor offices like a penalty clause. It might fall under general civil liability or discretionary compensation for harm.\nSo, an abstract query about \"Compensation for harm caused by unjustified delay in labor cases\" is good.\n\nLet's finalize the 5 queries.\n1. Direct: \"المدة النظامية لتحويل ملف التسوية من مكتب العمل إلى المحكمة العمالية\".\n2. Abstract: \"التعويض عن الضرر الناشئ عن المماطلة في سداد مستحقات نهاية الخدمة\".\n3. Deconstructive: \"قواعد وأحكام تحميل أتعاب المحاماة في الدعاوي العمالية\".\n4. Deconstructive: \"حق العامل في رفض تسوية جزئية وعدم التنازل عن الحقوق النظامية\".\n5. Direct (or Abstract): \"إثبات العلاقة التعاقدية السابقة لكتابة العقد الرسمي\" (Addressing the 2012-2018 period context which is vital for calculating the base EOSB, as the user worked 2012-2018 without contract, then signed. If the company claims EOSB only from 2018, the user loses money. Even though not explicitly asked as a question, it is a necessary sub-issue). *Self-correction*: The user asked 4 specific questions. I should probably answer those. The \"2012-2018\" is context. I will include a query about proving that period as it's a common trap.\n\nLet's stick to the 4 questions + the \"proof of period\" as it's critical.\n\n1. \"المدة النظامية لتحويل النزاع العمالي من التسوية الودية إلى القضاء\" (Answers Q1).\n2. \"التعويض عن المماطلة في سداد مستحقات نهاية الخدمة\" (Answers Q2).\n3. \"قواعد تحميل الطرف الخاسر أتعاب المحاماة في القضايا العمالية\" (Answers Q4).\n4. \"حق العامل في رفض الصلح الجزئي قبل الجلسة\" (Answers Q3).\n5. \"إثبات مدة الخدمة الفعلية في غياب عقد عمل مكتوب\" (Addresses the 2012-2018 context).\n\nThis looks solid.\n\nRationales:\n1. Direct: Answers the specific question about the timeframe for transfer.\n2. Abstract/Step-back: Looks for the legal principle regarding compensation for administrative/judicial delay by the employer.\n3. Deconstructive: Addresses the lawyer fees question.\n4. Deconstructive: Addresses the right to reject settlement.\n5. Deconstructive: Addresses the hidden issue of proving the pre-contract period.\n\nI need to make sure I don't use names of laws if not mentioned, but describing the behavior is key.\n\nOne adjustment: The user asks \"how to determine the value of compensation?\". The abstract query should capture the basis for this.\nQuery 2: \"أسس تقدير التعويض عن التأخير في الوفاء بالالتزامات العمالية\". (Basis for estimating compensation for delay in fulfilling labor obligations). This is better than just \"compensation for delay\".\n\nLet's refine the Arabic phrasing for the tool output.\n1. المدة النظامية لتحويل النزاع العمالي من مكتب العمل إلى المحكمة\n2. أسس تقدير التعويض عن المماطلة في سداد مستحقات نهاية الخدمة\n3. قواعد تحميل أتعاب المحاماة في الدعاوي العمالية\n4. إجراءات رفض الصلح الجزئي والتمسك بالحقوق النظامية\n5. إثبات العلاقة العمالية المستمرة قبل توثيق العقد\n\nSectors: العمل والتوظيف (Pre-determined).\n\nFinal check on constraints:\n- 5 queries max. (I have exactly 5).\n- Sector: العمل والتوظيف.\n- Types: Direct, Abstract, Deconstructive.\n\nQuery 1 is Direct.\nQuery 2 is Abstract (Step-back to principle of estimation for delay).\nQuery 3 is Deconstructive (Procedural issue).\nQuery 4 is Deconstructive (Procedural/Right issue).\nQuery 5 is Abstract/Deconstructive (Evidentiary issue).\n\nLooks good.",
        "id": "reasoning_content",
        "signature": null,
        "provider_name": "openai",
        "provider_details": null,
        "part_kind": "thinking"
      },
      {
        "tool_name": "final_result",
        "args": "{\"queries\": \n[\"المدة النظامية لتحويل النزاع العمالي من مكتب العمل إلى المحكمة\", \"أسس تقدير التعويض عن المماطلة في سداد مستحقات نهاية الخدمة\", \"قواعد تحميل أتعاب المحاماة في الدعاوي العمالية\", \"إجراءات رفض الصلح الجزئي والتمسك بالحقوق النظامية\", \"إثبات العلاقة العمالية المستمرة قبل توثيق العقد\"]\n\n, \"rationales\": \n[\"نوع 1 (مباشر): يستهدف الإجابة المباشرة على سؤال المستخدم حول المدة الزمنية المتوقعة لنقل الملف من المرحلة الإدارية (مكتب العمل) إلى المرحلة القضائية.\", \"نوع 2 (تجريدي - Step-back): يبحث عن المبدأ العام القاضي بتقدير التعويضات عند التأخير في الوفاء بالالتزامات المالية العمالية، بدلاً من التركيز فقط على واقعة المماطلة المحددة.\", \"نوع 3 (تفكيكي): مسألة فرعية مستقلة تتعلق بعبء التكاليف القانونية، حيث يسأل المستخدم عما إذا كانت الشركة تتحمل أتعاب محاميه.\", \"نوع 3 (تفكيكي): مسألة فرعية إجرائية حول حق العامل في رفض عروض التسوية التي لا تغطي كافة الحقوق، وهي خطوة تسبق الجلسة القضائية.\", \"نوع 3 (تفكيكي): مسألة فرعية جوهرية ضمنية؛ فعمل الموظف منذ 2012 بدون عقد يخلق حاجة لإثبات هذه الفترة لضمان احتساب نهاية الخدمة كاملة وليس فقط من تاريخ العقد 2018.\"]\n\n, \"sectors\": \"[\\\"العمل والتوظيف\\\"]\"}",
        "tool_call_id": "call_b934a9efbc8b44a59d368df5",
        "id": null,
        "provider_details": null,
        "part_kind": "tool-call"
      }
    ],
    "usage": {
      "input_tokens": 2147,
      "cache_write_tokens": 0,
      "cache_read_tokens": 0,
      "output_tokens": 2776,
      "input_audio_tokens": 0,
      "cache_audio_read_tokens": 0,
      "output_audio_tokens": 0,
      "details": {
        "reasoning_tokens": 2397,
        "text_tokens": 2776
      }
    },
    "model_name": "qwen3.6-plus",
    "timestamp": "2026-04-30T14:02:54.749496Z",
    "kind": "response",
    "provider_name": "openai",
    "provider_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/",
    "provider_details": {
      "finish_reason": "tool_calls",
      "timestamp": "2026-04-30T14:02:53Z"
    },
    "provider_response_id": "chatcmpl-6aa53d1e-33e4-97dc-a40d-ece3947038e1",
    "finish_reason": "tool_call",
    "run_id": "144a4fb2-6202-402a-9c02-e664e5bde50a",
    "metadata": null
  },
  {
    "parts": [
      {
        "tool_name": "final_result",
        "content": "Final result processed.",
        "tool_call_id": "call_b934a9efbc8b44a59d368df5",
        "metadata": null,
        "timestamp": "2026-04-30T14:02:54.750908Z",
        "part_kind": "tool-return"
      }
    ],
    "timestamp": "2026-04-30T14:02:54.750935Z",
    "instructions": null,
    "kind": "request",
    "run_id": "144a4fb2-6202-402a-9c02-e664e5bde50a",
    "metadata": null
  }
]
```

</details>
