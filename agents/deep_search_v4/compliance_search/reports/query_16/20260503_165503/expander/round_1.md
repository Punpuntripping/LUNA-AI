# Expander — Round 1

## Usage
| Requests | Input tokens | Output tokens | Total tokens |
|----------|-------------|---------------|-------------|
| 1 | 2,009 | 1,169 | 3,178 |

## Output — 3 queries (task_count=3)

1. خدمة لرفع دعوى عمالية للمطالبة بمستحقات نهاية الخدمة يستفيد منها الموظف المنتهي عقده بهدف إلزام صاحب العمل بدفع الحقوق المستحقة.
   > الرواية تذكر أن الموظف لم يستلم حقوق نهاية الخدمة ورفع قضية في مكتب العمل - هذا احتياج أساسي للمطالبة بالمستحقات
2. خدمة للمطالبة بتعويض عن تأخر صرف مستحقات عمالية يستفيد منها الموظف المتضرر بهدف الحصول على تعويض مالي عن فترة المماطلة.
   > الموظف يسأل صراحة عن المطالبة بتعويض عن 5 شهور مماطلة - هذا احتياج تنفيذي منفصل عن المستحقات الأصلية
3. خدمة لنقل نزاع عمالي من مكتب العمل إلى المحكمة العمالية يستفيد منها المدعي العامل بهدف استكمال الإجراءات القضائية للفصل في النزاع.
   > القضية في مرحلة الانتقال من مكتب العمل للمحكمة العمالية - الموظف يحتاج فهم خدمة نقل النزاع للإجراءات القضائية

## Reasoning (1 block(s))

The user is describing an employment dispute situation in Saudi Arabia. Let me analyze the story:

1. The user worked for a company from 2012 without a contract until end of 2018
2. They were given contracts to sign 4 months before end of 2018
3. Their services were terminated at end of year, last day was 5/2/2019 with one month notice
4. They haven't received their end-of-service benefits yet - the company is delaying
5. They filed a case at the Labor Office (مكتب العمل)
6. The company's lawyer attended the second session (3 days before Ramadan) and delayed, claiming ignorance
7. The case is now transferring to the Labor Court (المحكمة العمالية)
8. HR is threatening to delay further because the user complained

The user's questions are:
1. How long does it take for the case to transfer from Labor Office to Labor Court?
2. Can they claim compensation for the 5 months of delay?
3. Can they refuse a settlement offer before the court session?
4. If they hire the company's lawyer, will the company bear the costs?

Now I need to extract the EXECUTIVE NEEDS (الاحتياجات التنفيذية) - what government services might the user need to address this situation practically.

Let me identify the potential beneficiaries and their executive goals:

1. **Beneficiary: The employee (user)**
   - Goal 1: File a labor dispute claim for end-of-service benefits
   - Goal 2: Claim compensation for delay in payment
   - Goal 3: Transfer case from Labor Office to Labor Court
   - Goal 4: Reject settlement and proceed to court judgment

Looking at the story, I can identify these distinct executive needs:

1. **Service for filing a labor dispute claim** - The user already filed at Labor Office, but this is a general service need for labor disputes


2. **Service for claiming end-of-service benefits** - This is the core financial claim
3. **Service for claiming compensation for delay** - This is a separate claim for the 5-month delay
4. **Service for case transfer from Labor Office to Labor Court** - This is procedural

However, I need to be careful not to create duplicate queries. Let me consolidate:

- Filing a labor dispute and claiming end-of-service benefits are essentially the same need (one service covers both)
- Claiming compensation for delay could be part of the same labor dispute claim
- Case transfer is procedural and happens automatically as part of the labor dispute process

So the main executive needs are:

1. **Labor dispute claim service** - for end-of-service benefits (this covers the main claim)


2. **Compensation claim for payment delay** - this could be separate or part of the same claim

Actually, thinking more carefully:
- The user already filed a case at the Labor Office
- The case is now transferring to Labor Court
- The user wants to know about: timeline, compensation for delay, settlement options, legal representation costs

The executive services that might be relevant:

1. **Service to file/continue a labor dispute claim for end-of-service benefits** - The user is already in this process but this is the core service
2. **Service to claim compensation for delayed payment of labor rights** - This is a separate claim type
3. **Service for legal representation in labor court** - The user is asking about hiring a lawyer

I should focus on what services the user actually needs to access, not just what questions they're asking. The core needs are: pursuing the end-of-service benefits claim through the labor court system, seeking compensation for the payment delay, and understanding legal representation options for the court proceedings. I need to consolidate these into distinct service categories. The core needs are: filing the labor dispute for end-of-service benefits, pursuing compensation for the payment delay, and managing the case transfer to labor court. Legal representation is a supporting need rather than a separate government service.

Looking at the actual executive actions required, I can identify three primary service tracks: the main labor dispute claim covering termination benefits, a separate compensation claim for the delayed payment period, and the procedural transfer of the case from the labor office to the court system. These represent the distinct government services the employee needs to access.

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

<details><summary>User Message (833 chars)</summary>

كنت موظف في شركه من 2012 بدون عقد الا نهايه 2018 قبل نهاية السنه ب 4 شهور تقريبا اعطونا عقود ووقعناها. انتهت السنه انهو خدماتنا. اخر يوم لي عمل معاهم كان 5/2/2019. مع شهر الانذار.
الشي الثاني حقوق نهاية الخدمه الا الان مااستلمتها الشركه بتماطل رفعت عليهم قضيه في مكتب العمل الموعد الاول ماحضرو الموعد الثاني كان قبل رمضان ب 3 ايام تقريبا. جاء محامي من الشركة وجالس يماطل يقول مايدري عن الموضوع وراح يراجع الشركه ويرد علي وطلب مهله. رفضت والحين المعامله بتتحول ع المحكمه العمالية. موظف شؤون الموظفين يقول حنأخرك الين قبل موعد المحكمه او الجلسه الاولي كذا بس عشان اشتكيت.
السؤال؟
1/كم تأخذ القضيه عشان تنتقل من مكتب العمل الي المحكمه؟
2/اقدر اطالب بتعويض عن فترة المماطله. 5 شهور تقريبا وكيف احدد قيمة التعويض؟
3/اذا كلموني قبل موعد المحكمه بشيء بسيط عشان استلم الحقوق من دون التعويض اقدر ارفض؟
4/اذا وكلت محامي الشركة تتحمل تكاليفه...

</details>
