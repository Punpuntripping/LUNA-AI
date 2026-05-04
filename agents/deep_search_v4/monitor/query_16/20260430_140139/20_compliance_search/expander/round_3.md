# Expander — Round 3

## Usage
| Requests | Input tokens | Output tokens | Total tokens |
|----------|-------------|---------------|-------------|
| 1 | 2,266 | 1,432 | 3,698 |

## Output — 2 queries (task_count=2)

1. خدمة تتيح متابعة حالة نقل ملف نزاع عمالي من مكتب العمل إلى المحكمة العمالية ومعرفة المدة المتوقعة للانتقال يستفيد منها الموظف المطالب بحقوقه بهدف تتبع إجراءات القضية والاستعداد للجلسة.
   > الاستعلام الأول: من سؤال المستخدم عن المدة المتوقعة لنقل القضية من مكتب العمل للمحكمة العمالية - المستفيد: الموظف المدعي، الهدف: متابعة حالة النقل ومعرفة التوقيت المتوقع
2. خدمة تتيح المطالبة بتعويض عن التأخير في صرف مستحقات نهاية الخدمة حسب نظام العمل يستفيد منها الموظف المتأخرة مستحقاته بهدف الحصول على تعويض مالي عن فترة المماطلة.
   > الاستعلام الثاني: من سؤال المستخدم عن المطالبة بتعويض عن 5 شهور مماطلة - المستفيد: الموظف المتأخرة مستحقاته، الهدف: الحصول على تعويض عن فترة التأخير في الصرف

## Reasoning (1 block(s))

Let me analyze this user's story carefully to identify the distinct compliance needs and generate appropriate search queries.

**Story Summary:**
- Employee worked from 2012 without a contract until end of 2018
- Got contracts 4 months before end of 2018
- Services terminated at end of year
- Last work day: 5/2/2019 with 1 month notice
- End of service benefits not received yet - company is delaying
- Filed case at Labor Office
- First appointment: company didn't show
- Second appointment: 3 days before Ramadan, company lawyer came and delayed, asked for time
- User refused the delay
- Case now transferring to Labor Court
- HR employee says they will delay until before court date as retaliation for the complaint

**User's Questions:**
1. How long does it take for a case to transfer from Labor Office to Labor Court?
2. Can I claim compensation for the delay period (5 months)? How to determine the value?
3. If they contact me before court date with a small amount without compensation, can I refuse?
4. If I hire the company's lawyer, will they bear the costs?

**Identifying Distinct Compliance Needs:**

From the previous round's weak points, I need to focus on:
1. Service to track/follow up on case transfer from Labor Office to Labor Court and know expected timeline
2. Service to calculate/claim compensation for delay in paying end-of-service benefits

Let me identify the independent needs:

1. **Tracking case transfer from Labor Office to Labor Court** - This is a follow-up/tracking service for the employee to know the status and expected timeline of their case transfer
   - Beneficiary: Employee (الموظف)
   - Service: Tracking case transfer status from Labor Office to Labor Court
   - Goal: Know the status and expected timeline of the transfer

2. **Claiming compensation for delay in end-of-service benefits** - This is about claiming compensation for the delay period
   - Beneficiary: Employee (الموظف)
   - Service: Claiming compensation for delay in paying end-of-service benefits
   - Goal: Obtain compensation for the delay period (5 months)

3. **Settlement negotiation before court date** - The user asks if they can refuse a settlement offer before court
   - This could be: Service for employee to negotiate or reject settlement offers before labor court hearing
   - Beneficiary: Employee
   - Goal: Protect rights by refusing inadequate settlement offers

4. **Legal representation costs** - About lawyer fees and who bears them
   - This could be: Service to determine legal representation cost allocation in labor disputes
   - Beneficiary: Employee
   - Goal: Know who bears lawyer costs in labor disputes

I need to consolidate these into 5 queries maximum, focusing on the weak points from previous round:
1. Case transfer tracking from Labor Office to Labor Court
2. Compensation claim for delay in end-of-service benefits

Let me draft the queries following the required structure:
"خدمة تتيح <وصف الخدمة> يستفيد منها <المستفيد المحتمل> بهدف <الهدف>."

**Query 1 - Case Transfer Tracking:**
خدمة تتيح متابعة حالة نقل ملف نزاع عمالي من مكتب العمل إلى المحكمة العمالية ومعرفة المدة المتوقعة للانتقال يستفيد منها الموظف المطالب بحقوقه بهدف تتبع إجراءات القضية والاستعداد للجلسة.

**Query 2 - Compensation for Delay:**
خدمة تتيح المطالبة بتعويض عن التأخير في صرف مستحقات نهاية الخدمة حسب نظام العمل يستفيد منها الموظف المتأخرة مستحقاته بهدف الحصول على تعويض مالي عن فترة المماطلة.

**Query 3 - Settlement Refusal Rights:**
خدمة تتيح للموظف رفض عرض تسوية غير كافٍ قبل جلسة المحكمة العمالية يستفيد منها الموظف المدعي بحقوق عمالية بهدف الحفاظ على حقه في المطالبة الكاملة أمام القضاء.

**Query 4 - Lawyer Cost Allocation:**
خدمة تتيح تحديد جهة تحمل تكاليف التمثيل القانوني في النزاعات العمالية يستفيد منها الموظف في نزاع عمالي بهدف معرفة من يتحمل أتعاب المحامي في القضية.

I should consolidate these to avoid overlap and stay within the 5-query limit. The core needs are: tracking the case transfer, claiming delay compensation, understanding settlement rights, and clarifying legal cost responsibility. I'm identifying the core needs here. The user has two primary concerns that weren't fully addressed before: tracking the case transfer timeline from the labor office to court, and claiming compensation for the payment delay. These are distinct from the actual end-of-service benefits claim itself.

I should focus my queries on these two gaps - one for monitoring the transfer process and expected duration, another for pursuing delay compensation specifically. The other questions about settlement options and legal representation are secondary to these main procedural needs. I need to focus on the two weak points from the previous round rather than expanding to all four questions. The priority should be:

1. Tracking the case transfer from Labor Office to Labor Court with expected timeline
2. Claiming compensation for the delay in receiving end-of-service benefits

These are the gaps that need addressing. I'll limit myself to 2-3 queries maximum to stay focused on what was identified as weak, rather than covering every question the user asked. I need to focus on the two specific weak points from the previous round rather than expanding to cover all four questions. The priority should be the case transfer tracking service and the compensation claim for delayed end-of-service benefits. I'll draft queries that directly address these gaps without adding unnecessary scope around settlement negotiations or legal fees.

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

<details><summary>User Message (1,323 chars)</summary>

كنت موظف في شركه من 2012 بدون عقد الا نهايه 2018 قبل نهاية السنه ب 4 شهور تقريبا اعطونا عقود ووقعناها. انتهت السنه انهو خدماتنا. اخر يوم لي عمل معاهم كان 5/2/2019. مع شهر الانذار.
الشي الثاني حقوق نهاية الخدمه الا الان مااستلمتها الشركه بتماطل رفعت عليهم قضيه في مكتب العمل الموعد الاول ماحضرو الموعد الثاني كان قبل رمضان ب 3 ايام تقريبا. جاء محامي من الشركة وجالس يماطل يقول مايدري عن الموضوع وراح يراجع الشركه ويرد علي وطلب مهله. رفضت والحين المعامله بتتحول ع المحكمه العمالية. موظف شؤون الموظفين يقول حنأخرك الين قبل موعد المحكمه او الجلسه الاولي كذا بس عشان اشتكيت.
السؤال؟
1/كم تأخذ القضيه عشان تنتقل من مكتب العمل الي المحكمه؟
2/اقدر اطالب بتعويض عن فترة المماطله. 5 شهور تقريبا وكيف احدد قيمة التعويض؟
3/اذا كلموني قبل موعد المحكمه بشيء بسيط عشان استلم الحقوق من دون التعويض اقدر ارفض؟
4/اذا وكلت محامي الشركة تتحمل تكاليفه...

اقتصر على عدد 5 من الاستعلامات الفرعية، ولا تتجاوز هذا الحد.

المحاور الضعيفة من الجولة السابقة:
- لا توجد خدمة تتيح للمستخدم متابعة حالة نقل ملفه من مكتب العمل إلى المحكمة العمالية أو معرفة المدة المتوقعة للانتقال.: خدمة متابعة شكوى مكتب العمل نقل للمحكمة العمالية
- لا توجد خدمة محددة لحساب أو المطالبة بتعويض عن المماطلة في صرف مستحقات نهاية الخدمة.: التعويض عن التأخير في صرف حقوق نهاية الخدمة نظام العمل

وسّع استعلاماتك لتغطية هذه المحاور الضعيفة فقط. لا تكرر استعلامات ناجحة سابقة.

</details>
