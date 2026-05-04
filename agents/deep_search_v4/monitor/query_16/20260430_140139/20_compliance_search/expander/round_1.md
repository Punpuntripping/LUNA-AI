# Expander — Round 1

## Usage
| Requests | Input tokens | Output tokens | Total tokens |
|----------|-------------|---------------|-------------|
| 1 | 2,030 | 1,111 | 3,141 |

## Output — 5 queries (task_count=5)

1. خدمة نقل نزاع عمالي من مكتب العمل إلى المحكمة العمالية يستفيد منها الموظف المنتهي عقده بهدف المتابعة القضائية للمطالبة بحقوقه.
   > الرواية تذكر أن القضية ستتحول للمحكمة العمالية - المستفيد: الموظف، الهدف: نقل الدعوى للمحكمة
2. خدمة المطالبة بتعويض عن تأخير صرف مكافأة نهاية الخدمة يستفيد منها الموظف المنتهي عقده بهدف الحصول على تعويض مالي عن فترة المماطلة.
   > المستخدم يسأل عن التعويض عن 5 شهور مماطلة - المستفيد: الموظف، الهدف: المطالبة بتعويض التأخير
3. خدمة رفض تسوية ودية في نزاع عمالي قبل جلسة المحكمة يستفيد منها الموظف المنتهي عقده بهدف الحفاظ على حقه في المطالبة الكاملة.
   > المستخدم يسأل عن حقه في رفض التسوية البسيطة - المستفيد: الموظف، الهدف: رفض تسوية غير كاملة
4. خدمة طلب تحميل صاحب العمل تكاليف أتعاب المحامي في دعوى عمالية يستفيد منها الموظف المنتهي عقده بهدف استرداد مصاريف التقاضي.
   > المستخدم يسأل عن تكاليف المحامي - المستفيد: الموظف، الهدف: تحميل الشركة أتعاب المحامي
5. خدمة المطالبة القضائية بمكافأة نهاية الخدمة المستحقة يستفيد منها الموظف المنتهي عقده بهدف الحصول على حقوقه المالية المتأخرة.
   > الجوهر الأساسي هو المطالبة بحقوق نهاية الخدمة - المستفيد: الموظف، الهدف: استلام المكافأة المستحقة

## Reasoning (1 block(s))

Let me analyze this user's narrative to identify the compliance needs and generate appropriate search queries.

**Understanding the Story:**
- Employee worked at a company from 2012 without a contract until end of 2018
- Contracts were given 4 months before end of 2018 and signed
- Services were terminated at end of year
- Last working day was 5/2/2019 with one month notice
- End of service benefits still not received - company is delaying
- Filed a case at Labor Office, first appointment company didn't show, second appointment company's lawyer came and delayed
- Case is about to transfer to Labor Court
- HR employee says they will delay until before court date as retaliation for the complaint

**User's Questions:**
1. How long does it take for a case to transfer from Labor Office to Labor Court?
2. Can I claim compensation for the delay period (5 months)? How to determine compensation value?
3. If they contact me before court date with a small amount without compensation, can I refuse?
4. If I hire a lawyer, will the company bear the costs?

**Identifying Compliance Needs:**

1. **Transfer case from Labor Office to Labor Court** - The user needs to know about the procedure and timeline for transferring a labor dispute from the Labor Office to the Labor Court.
   - Beneficiary: Employee (terminated worker)
   - Service: Transfer labor dispute case from Labor Office to Labor Court
   - Goal: Proceed with legal litigation for end of service benefits

2. **Claim compensation for delay** - The user wants to know about claiming compensation for the company's delay in paying end of service benefits.
   - Beneficiary: Employee (terminated worker)
   - Service: File claim for compensation due to employer's delay in paying end of service benefits
   - Goal: Obtain financial compensation for the delay period

3. **Reject settlement offer** - The user wants to know about their right to reject a settlement offer that doesn't include full compensation.
   - Beneficiary: Employee (terminated worker)
   - Service: Reject settlement offer in labor dispute before court hearing
   - Goal: Preserve right to full compensation including delay damages

4. **Legal representation costs** - The user wants to know about lawyer fees and whether the company can be made to bear these costs.
   - Beneficiary: Employee (terminated worker)
   - Service: Request employer to bear legal representation costs in labor dispute
   - Goal: Recover attorney fees as part of the judgment

5. **End of service benefits claim** - The core issue is claiming unpaid end of service benefits.
   - Beneficiary: Employee (terminated worker)
   - Service: File claim for unpaid end of service benefits
   - Goal: Receive full end of service entitlements

I need to consolidate these into distinct, non-overlapping queries. The main compliance needs are: transferring the case to court, claiming delay compensation, rejecting inadequate settlement offers, recovering legal fees, and securing unpaid end-of-service benefits. Each represents a separate procedural or substantive right the employee needs to understand. I need to identify the distinct legal services this employee requires. The core needs are: pursuing unpaid end-of-service benefits through the labor court system, seeking compensation for the employer's delay in payment, understanding settlement negotiation rights before litigation, and determining whether legal fees can be recovered from the employer. These represent separate procedural and substantive claims that should each have their own search query.

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

<details><summary>User Message (895 chars)</summary>

كنت موظف في شركه من 2012 بدون عقد الا نهايه 2018 قبل نهاية السنه ب 4 شهور تقريبا اعطونا عقود ووقعناها. انتهت السنه انهو خدماتنا. اخر يوم لي عمل معاهم كان 5/2/2019. مع شهر الانذار.
الشي الثاني حقوق نهاية الخدمه الا الان مااستلمتها الشركه بتماطل رفعت عليهم قضيه في مكتب العمل الموعد الاول ماحضرو الموعد الثاني كان قبل رمضان ب 3 ايام تقريبا. جاء محامي من الشركة وجالس يماطل يقول مايدري عن الموضوع وراح يراجع الشركه ويرد علي وطلب مهله. رفضت والحين المعامله بتتحول ع المحكمه العمالية. موظف شؤون الموظفين يقول حنأخرك الين قبل موعد المحكمه او الجلسه الاولي كذا بس عشان اشتكيت.
السؤال؟
1/كم تأخذ القضيه عشان تنتقل من مكتب العمل الي المحكمه؟
2/اقدر اطالب بتعويض عن فترة المماطله. 5 شهور تقريبا وكيف احدد قيمة التعويض؟
3/اذا كلموني قبل موعد المحكمه بشيء بسيط عشان استلم الحقوق من دون التعويض اقدر ارفض؟
4/اذا وكلت محامي الشركة تتحمل تكاليفه...

اقتصر على عدد 5 من الاستعلامات الفرعية، ولا تتجاوز هذا الحد.

</details>
