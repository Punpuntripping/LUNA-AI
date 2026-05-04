# Expander — Round 2

## Usage
| Requests | Input tokens | Output tokens | Total tokens |
|----------|-------------|---------------|-------------|
| 1 | 2,056 | 722 | 2,778 |

## Output — 2 queries (task_count=2)

1. خدمة لرفع دعوى تجارية للمطالبة باسترداد مبلغ إيجار مدفوع مقدمًا لعقار تجاري دون وجود عقد مكتوب يستفيد منها المستأجر بهدف استعادة الأموال المدفوعة قبل بدء فترة الإيجار.
   > الرواية تذكر دفع إيجار لمحطة تجارية (بوفيه) بدون عقد مكتوب والرغبة في استرداد المبلغ - هذا نزاع تجاري عقاري يحتاج خدمة قضائية للمطالبة بالاسترداد
2. خدمة لتسوية نزاع عقدي تجاري بين فرد وشركة خاصة بشأن إلغاء اتفاق إيجار تجاري واسترداد الدفعة المقدمة يستفيد منها دافع المبلغ بهدف فسخ الاتفاقية واسترداد المبلغ.
   > المستخدم يريد إلغاء المشروع التجاري واسترداد دفعة الإيجار من شركة خاصة بدون عقد رسمي - يحتاج خدمة تسوية نزاعات عقدية تجارية بين أفراد وشركات

## Reasoning (1 block(s))

The user is describing a situation where they paid rent on April 18th to "Idris Station" for opening a buffet/cafe. The rent start date is 11/11 (Hijri), and tomorrow is 7/11. They want to get their money back because they're canceling the project entirely. They haven't signed a rental contract, there's no verbal agreement either, and the rent hasn't started yet. They're in financial difficulty and need to recover the rent payment.

This is clearly a commercial/contractual dispute between an individual and a private company regarding:
1. Recovery of rent payment for a commercial property (buffet space)
2. No formal contract was signed
3. The rental period hasn't started yet
4. They want to cancel and get their money back

Based on the weak axes from the previous round:
- Missing services for commercial and contractual disputes between individuals and private companies (general judiciary/Ministry of Justice): Commercial real estate disputes - Rent recovery

I need to generate queries that address this specific situation without repeating successful queries from previous rounds.

Let me identify the needs:
1. The user (tenant/renter) needs to recover rent payment for a commercial property where no contract was signed
2. This is a commercial real estate dispute - rent recovery

Since the instruction says to expand queries to cover the weak axes ONLY and not repeat successful previous queries, I should focus on:
- Commercial real estate disputes
- Rent recovery services
- Contractual disputes between individuals and private companies

Let me create 1-2 queries that specifically address this:

1. Service for filing a commercial lawsuit to recover rent payments where no formal contract exists - benefits the payer (tenant) to recover funds paid for commercial property rental

2. Service for terminating a commercial rental agreement before start date and recovering advance payment - benefits the prospective tenant to cancel and recover funds

Actually, these might be too similar. Let me think about distinct needs:

1. Recovering rent payment for commercial property without signed contract
2. Commercial dispute resolution for contractual disagreements between individual and company


3. Since there's no formal contract, this becomes a payment recovery dispute rather than a contract termination issue

I should generate 1-2 focused queries that capture the core need: recovering funds paid for commercial rental space where no binding agreement exists.

<details><summary>System Prompt</summary>

```
انت موسّع استعلامات متخصص في الخدمات الحكومية الإلكترونية السعودية ضمن منصة لونا للذكاء الاصطناعي القانوني.

## مهمتك

قراءة رواية المستخدم (قد تكون استشارة شخصية، أو سؤال قانوني، أو سرد موقف) واستخلاص الاحتياجات التنفيذية الكامنة — أي الخدمات الحكومية التي قد يحتاجها أحد الأطراف لمعالجة الموقف عمليًا. ثم توليد استعلام بحث دلالي واحد لكل احتياج مستقل.

## كيف تفكر في الاحتياجات

الرواية نادرًا ما تذكر الخدمة صراحةً. مهمتك استنتاجها:

1. **من هو المستفيد المحتمل؟** ليس دائمًا الشخص الذي يروي القصة. قد يكون: الزوج، الزوجة، الحاضن، العامل، صاحب العمل، المؤجر، المستأجر، المقاول، صاحب المشروع، المريض، الطبيب، الوريث، الوكيل، الولي، ولي الأمر، الأب، الأم، المتضرر، المدّعي، المدعى عليه...
2. **ما هو الهدف التنفيذي؟** ماذا يريد هذا المستفيد أن يُنجز رسميًا؟ (رفع دعوى، توثيق عقد، إيقاع طلاق، تنفيذ حكم، إنهاء علاقة تعاقدية، طلب نفقة، تسجيل حضانة، إشعار بعدم التجديد، تقديم شكوى، استرداد مبلغ، نقل ملكية، إفراغ، توكيل، هبة موثّقة…).
3. **ما هي الخدمة الحكومية المقابلة؟** صِف الخدمة بلغة عامة (ما تفعله الخدمة) دون ربطها بمنصة أو تطبيق بعينه.

قد تحتوي الرواية الواحدة على أكثر من مستفيد محتمل وأكثر من هدف؛ كل ثنائي (مستفيد + هدف) = احتياج مستقل = استعلام.

## بنية كل استعلام (إلزامية)

كل استعلام يجب أن يتكون من ثلاثة مكوّنات نصيّة متجاورة بالعربية، في جملة واحدة:

- **وصف الخدمة:** ما الذي تقوم به الخدمة الحكومية (فعل إداري/قضائي/توثيقي مجرد)
- **المستفيد المحتمل:** من الذي يُقدم على الخدمة في هذا الموقف
- **الهدف من الخدمة:** النتيجة العملية التي يسعى إليها المستفيد

مثال صياغة: «خدمة تتيح <وصف الخدمة> يستفيد منها <المستفيد المحتمل> بهدف <الهدف>.»
مثال تطبيقي: «خدمة لتقديم دعوى مطالبة بنفقة زوجة وأولاد يستفيد منها الزوجة الحاضنة بهدف إلزام الزوج بالإنفاق المنتظم.»
مثال آخر: «خدمة لإشعار عامل منتهية مدة عقده بعدم الرغبة في التجديد يستفيد منها صاحب العمل بهدف إنهاء العلاقة التعاقدية نظاميًا قبل الانتهاء بشهر.»

## ممنوعات صياغة

1. **لا تذكر اسم أي منصة أو تطبيق أو بوّابة** (لا تكتب: أبشر، ناجز، قوى، إيجار، نافذ، مساند، موارد، مقيم، بلدي، توكلنا، أي اسم منصة). هذا overfitting يضر بالبحث الدلالي.
2. لا تذكر اسم جهة حكومية بعينها إلا إذا كانت جزءًا لا يتجزأ من اسم الخدمة (مثلًا: «محكمة الأحوال الشخصية» مقبول لأنه يصف نوع الخدمة، بينما «وزارة العدل» يُفضَّل تجنبه).
3. لا تكتب نصوصًا قانونية أو أرقام مواد أنظمة — هذه وظيفة مسار آخر.
4. لا تكرر استعلامات ناجحة من جولات سابقة.
5. تجنّب الأسئلة («كيف…؟»، «ما هي…؟»)؛ صِغ كل استعلام كوصف للخدمة.

## دمج النوايا المتشابهة (إلزامي قبل الإخراج)

قبل أن تُرجع `queries`، **راجع قائمتك المبدئية** واحذف التكرارات الدلالية:

- ثنائي (مستفيد + هدف) واحد = استعلام واحد فقط. إذا وجدت نفس المستفيد بنفس الهدف صِيغ مرتين بكلمات مختلفة، احتفظ بأقواها وألغِ البقية.
- إذا كانت خدمتان تشتركان في نفس **الفعل الإداري** (توثيق، رفع دعوى، إنهاء عقد، إصدار شهادة...) ونفس **الغاية النهائية**، فهما احتياج واحد حتى لو اختلفت صياغة الوصف.
- صياغات متطابقة بمرادفات (مثل: «إلزام بالنفقة» و«المطالبة بالنفقة» لنفس الزوجة) = استعلام واحد.
- الفروق في **الجهة المتوقعة** فقط (محكمة عمالية vs ديوان المظالم) ليست مبرراً لاستعلامين منفصلين — تحديد الجهة وظيفة المُصنِّف لاحقاً، وظيفتك أنت تحديد الاحتياج.
- النتيجة المثلى عادةً 1-3 استعلامات؛ تجاوز ذلك يجب أن يقابله 4+ احتياجات تنفيذية **مستقلة فعلاً**، وإلا فأنت تُكرِّر.

اعتبر القائمة النهائية بعد الدمج هي ما يجب أن يظهر في `queries`. لا تُرجع نسخاً متعددة لنفس النية تحت ذرائع لفظية.

## استراتيجية تحديد عدد الاستعلامات

عدد الاستعلامات = عدد الاحتياجات التنفيذية المستقلة (ثنائي مستفيد+هدف)، لا تعقيد الرواية:

| الوضع | عدد الاستعلامات |
|-------|----------------|
| موضوع تنفيذي واحد (مثلًا: توثيق زواج فقط) | 1–2 |
| موضوعان مستقلان (مثلًا: طلاق + حضانة، أو إنهاء عقد + مكافأة نهاية خدمة) | 2–3 |
| 3 مواضيع أو أكثر (طلاق + نفقة + حضانة + توثيق) | 3–5 |
| الحد الأقصى | 5 |

## مخرجك الهيكلي (ExpanderOutput)

- **queries**: قائمة استعلامات (1-5) بالعربية، كل استعلام بالبنية الثلاثية (وصف + مستفيد + هدف) بدون ذكر أي منصة.
- **rationales**: مبرر داخلي مختصر لكل استعلام يوضح: أي جزء من الرواية أثار هذا الاحتياج، ومن هو المستفيد، وما الهدف. (للتسجيل فقط، لا يُرسل للبحث.)
- **task_count**: عدد الاحتياجات التنفيذية المستقلة التي استخرجتها.

```

</details>

<details><summary>User Message (746 chars)</summary>

دفعت ايجار بتاريخ 18 ابريل لمحطة ادريس لغرض فتح بوفيه و موعد الايجار 11/11 هجري و بكرا 7/11 و ابي اخذ فلوسي
و ما وقعت عقد ايجار معهم
بكنسل المشروع كلي و ما حل الايجار باقي
و ابي اسحب فلوسي من عندهم
الموضوع ضروري استاذة انوار باقي ماسويت شي في البوفيه و لا اشتغلت
و انا بكنسل طفران و ابي اسدد الديون اللي علي عشان كذا الغيت المشروع و ابي اسحب فلوس الايجار
و لا اقدر اكمل
اقدر اخذ فلوسي و لا لا
لا ما وقعت عقد معهم
ان الايجار يبدا من 11/11
تسوي قبول او موافقة للعقد
مافيه حتى اتفاق شفوي

المحاور الضعيفة من الجولة السابقة:
- غياب خدمات الاختصاص في القضايا التجارية والعقدية بين الأفراد والشركات الخاصة (القضاء العام / وزارة العدل): نزاعات عقارات تجارية - استرداد إيجار

وسّع استعلاماتك لتغطية هذه المحاور الضعيفة فقط. لا تكرر استعلامات ناجحة سابقة.

</details>
