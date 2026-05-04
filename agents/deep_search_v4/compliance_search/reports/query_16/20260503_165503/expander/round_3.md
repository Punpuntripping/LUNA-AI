# Expander — Round 3

## Usage
| Requests | Input tokens | Output tokens | Total tokens |
|----------|-------------|---------------|-------------|
| 1 | 2,009 | 1,421 | 3,430 |

## Output — 3 queries (task_count=3)

1. خدمة لرفع دعوى عمالية للمطالبة بمستحقات نهاية الخدمة يستفيد منها الموظف المنتهي عقده بهدف الحصول على المستحقات المالية المستحقة له.
   > الرواية تذكر أن المستخدم رفع قضية في مكتب العمل للمطالبة بحقوق نهاية الخدمة ولم يستلمها حتى الآن - الاحتياج الأساسي هو متابعة المطالبة بهذه المستحقات عبر المحكمة العمالية.
2. خدمة للمطالبة بتعويض عن تأخر صرف مستحقات عمالية يستفيد منها العامل بهدف الحصول على تعويض مالي عن فترة المماطلة.
   > المستخدم يسأل صراحة عن إمكانية المطالبة بتعويض عن 5 شهور مماطلة - هذا احتياج مستقل عن المستحقات الأساسية.
3. خدمة لتوكيل محامٍ في نزاع عمالي أمام المحكمة يستفيد منها الموظف بهدف الحصول على تمثيل قانوني في القضية.
   > المستخدم يسأل عن توكيل محامي وتكاليفه - هذا احتياج للتمثيل القانوني في القضية العمالية.

## Reasoning (1 block(s))

Let me analyze this user's story carefully to identify the underlying compliance needs and government services they might need.

**Understanding the Story:**
- The user was an employee from 2012 without a contract until end of 2018
- They were given contracts to sign 4 months before end of 2018
- Their services were terminated at end of 2018
- Last working day was 5/2/2019 with one month notice
- They haven't received their end-of-service benefits
- They filed a case at the Labor Office
- First hearing: company didn't show up
- Second hearing: company's lawyer came and delayed, asked for time to review
- User refused the delay
- Case is now being transferred to Labor Court
- HR employee says they will delay until before the court date because user complained

**User's Questions:**
1. How long does it take for the case to transfer from Labor Office to Labor Court?
2. Can they claim compensation for the 5-month delay? How to determine the value?
3. If company contacts them before court date with a small settlement (without compensation), can they refuse?
4. If they hire the company's lawyer, will the company bear the costs?

**Identifying Compliance Needs (Beneficiary + Goal pairs):**

1. **Need 1: Transfer labor dispute from Labor Office to Labor Court**
   - Beneficiary: The employee (user)
   - Goal: Get the case transferred to Labor Court for judicial resolution
   - Service: Labor dispute case transfer from administrative to judicial level

2. **Need 2: Claim compensation for delay in receiving end-of-service benefits**
   - Beneficiary: The employee (user)
   - Goal: Obtain compensation for the company's delay in paying end-of-service benefits
   - Service: Filing a compensation claim for delayed payment of labor rights

3. **Need 3: Legal representation in labor court**
   - Beneficiary: The employee (user)
   - Goal: Get proper legal representation for the labor court case
   - Service: Hiring/authorizing a lawyer for labor court representation

4. **Need 4: Enforce end-of-service benefits payment**
   - Beneficiary: The employee (user)
   - Goal: Actually receive the end-of-service benefits owed
   - Service: Enforcement of labor rights payment through court judgment

Let me consolidate similar needs:

- Need 1 and Need 4 are related but distinct: one is about the transfer process, the other is about enforcement of payment
- Need 2 is about claiming additional compensation for delay
- Need 3 is about legal representation

Actually, looking more carefully:

The core needs are:
1. **Labor dispute case progression** - from Labor Office to Labor Court (procedural)
2. **Claim for end-of-service benefits** - the main financial claim
3. **Claim for delay compensation** - additional compensation for the 5-month delay
4. **Legal representation** - hiring a lawyer for the court case

But I need to think about what government SERVICES these map to:

1. Service for transferring labor dispute from Labor Office to Labor Court - this is more of a procedural question, not really a service the user needs to request
2. Service for filing/claiming end-of-service benefits through labor court
3. Service for claiming compensation for delayed payment
4. Service for legal aid or lawyer authorization

Let me reconsider based on the framework:

The user already filed a case at the Labor Office. The case is now being transferred to Labor Court. The questions are about:
1. Timeline for transfer (informational)
2. Claiming delay compensation (this is a substantive claim)
3. Settlement negotiation (strategic decision)
4. Lawyer fees (procedural/financial)

**Actual compliance needs I can identify:**

1. **End-of-service benefits claim through labor court** - The user needs to pursue their end-of-service benefits through the labor court system
   - Beneficiary: Employee
   - Goal: Receive owed end-of-service benefits

2. **Compensation claim for payment delay** - The user wants to claim additional compensation for the 5-month delay in receiving their rights
   - Beneficiary: Employee
   - Goal: Obtain compensation for delayed payment of labor rights

3. **Legal representation for labor court case** - The user is considering hiring a lawyer and wants to know about fee arrangements
   - Beneficiary: Employee
   - Goal: Get legal representation for labor court proceedings

These are 3 distinct needs. Let me formulate the queries:

1. خدمة لرفع دعوى عمالية للمطالبة بمستحقات نهاية الخدمة يستفيد منها الموظف المنتهي عقده بهدف الحصول على المستحقات المالية المستحقة له.

2. خدمة للمطالبة بتعويض عن تأخر صرف مستحقات عمالية يستفيد منها العامل بهدف الحصول على تعويض مالي عن فترة المماطلة.

3. خدمة لتوكيل محامٍ في نزاع عمالي أمام المحكمة يستفيد منها الموظف بهدف الحصول على تمثيل قانوني في القضية.

Actually, I should reconsider - the user already has a case in progress. 

The real needs are:
1. Following through with the labor court case for end-of-service benefits
2. Adding a claim for delay compensation to the existing case
3. Legal representation for the court proceedings

Let me finalize 3 queries that capture these distinct needs without mentioning specific platforms.

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
