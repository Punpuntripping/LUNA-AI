# Expander — Round 1

## Usage
| Requests | Input tokens | Output tokens | Total tokens |
|----------|-------------|---------------|-------------|
| 1 | 2,009 | 2,530 | 4,539 |

## Output — 2 queries (task_count=2)

1. خدمة لرفع دعوى عمالية للمطالبة بمكافأة نهاية الخدمة يستفيد منها الموظف المنتهي عقده بدون استحقاق بهدف الحصول على حقوقه المالية المستحقة عبر القضاء العمالي.
   > الرواية تذكر أن الموظف انتهى عقده ولم يستلم مكافأة نهاية الخدمة منذ 5 شهور، ورفع قضية في مكتب العمل التي تتحول للمحكمة العمالية - هذا يمثل احتياجاً لخدمة رفع دعوى عمالية للمطالبة بالحقوق المستحقة.
2. خدمة للمطالبة بتعويض عن المماطلة والتأخير في صرف الحقوق العمالية يستفيد منها الموظف المتضرر من التأخير بهدف التعويض عن فترة الانتظار غير المبرر.
   > الموظف يسأل صراحة عن إمكانية المطالبة بتعويض عن فترة المماطلة (5 شهور) وكيف يحدد قيمته - هذا يمثل احتياجاً مستقلاً لخدمة المطالبة بتعويض عن التأخير في الصرف ضمن الدعوى العمالية.

## Reasoning (1 block(s))

The user is describing an employment dispute situation in Saudi Arabia. Let me analyze the story:

1. They worked at a company from 2012 without a contract until end of 2018
2. They were given contracts to sign about 4 months before the end of 2018
3. Their services were terminated at the end of the year
4. Last working day was 5/2/2019 with a one-month notice period
5. They haven't received their end-of-service benefits yet
6. They filed a case at the Labor Office (مكتب العمل)
7. The company is delaying - first meeting they didn't show, second meeting before Ramadan their lawyer came and asked for more time
8. The case is now being transferred to the Labor Court (المحكمة العمالية)
9. HR is threatening to delay until before the court date as punishment for complaining

The user has 4 questions:
1. How long does it take for a case to transfer from Labor Office to Labor Court?
2. Can they claim compensation for the 5 months of delay and how to determine the value?
3. If the company contacts them before court date with a small settlement without compensation, can they refuse?
4. If they hire a lawyer, will the company bear the costs?

Now I need to identify the EXECUTIVE NEEDS (الاحتياجات التنفيذية) - the government services they might need:

1. **End-of-service benefits claim** - The employee needs to claim their end-of-service rights (مكافأة نهاية الخدمة)
   - Beneficiary: Employee (الموظف)
   - Service: Claiming end-of-service benefits
   - Goal: Receiving unpaid end-of-service benefits

2. **Labor dispute case transfer/filing** - The case is moving from Labor Office to Labor Court
   - Beneficiary: Employee
   - Service: Filing/transfering labor dispute to labor court
   - Goal: Getting judicial resolution for labor dispute

3. **Compensation claim for delay** - They want to claim compensation for the delay period
   - Beneficiary: Employee
   - Service: Claiming compensation for delay in payment
   - Goal: Getting compensation for the 5-month delay

4. **Legal representation/lawyer** - They're asking about hiring a lawyer and costs
   - Beneficiary: Employee
   - Service: Legal representation in labor court
   - Goal: Having professional legal representation

However, I need to be careful about duplication. Let me think:

- Items 1 and 2 are related but distinct - one is about the end-of-service benefits specifically, the other is about the labor court case process
- Item 3 is about compensation for delay - this could be part of the same labor court case
- Item 4 is about legal representation - this is more of a service need

Looking at the executive needs more carefully:

1. **Claiming end-of-service benefits through labor dispute** - This is the core need. The employee wants to get their unpaid end-of-service benefits through the labor dispute system.

2. **Claiming compensation for delay in payment** - This is a separate claim within the labor dispute - compensation for the delay period.

3. **Legal representation in labor court** - They may need lawyer services for the court case.

Actually, I should focus on GOVERNMENT SERVICES specifically, not private services like hiring a lawyer. The lawyer question is more of a legal consultation question, not a government service need.

Let me reconsider:

1. **Labor dispute case filing/transfer to labor court** - Employee needs to file/transfer their labor dispute case to get judicial resolution for unpaid end-of-service benefits
   - This covers the main dispute resolution need

2. **Claiming compensation for payment delay** - Within the labor dispute, they can claim additional compensation for the delay period
   - This is technically part of the same case but represents a distinct claim type

Actually, these might be the same service - when you file a labor dispute case, you can claim both the end-of-service benefits AND compensation for delay in the same case. So this might be one need, not two.

Let me think about distinct executive needs:

1. **Labor dispute resolution through labor court** - Employee filing labor dispute case to claim unpaid end-of-service benefits and related compensation
   - This is the main government service need

2. **Enforcement of labor court judgment** - If they win the case, they may need to enforce the judgment if the company still doesn't pay
   - This could be a separate need

But the user hasn't won the case yet, so enforcement is premature.

Looking at the story again, the main executive needs are:

1. **Labor dispute case at labor court** - To claim end-of-service benefits and compensation for delay
   - Beneficiary: Employee (الموظف المنتهي عقده)
   - Service: Filing labor dispute case at labor court
   - Goal: Getting judicial ruling on unpaid end-of-service benefits and delay compensation

This could be one comprehensive query. But let me see if there are other distinct needs...

The user is also asking about:
- Timeline for case transfer (this is informational, not a service need)
- Whether they can refuse settlement (this is strategic/legal advice, not a service need)
- Lawyer costs (this is about private legal services, not government services)

So the main government service needs are:

1. **Labor dispute case filing** - For claiming end-of-service benefits
2. **Compensation claim for delay** - This could be part of the same case

I think I should create 2 queries:

1. One for the labor dispute case to claim end-of-service benefits
2. One for claiming compensation for payment delay within labor dispute

But these are really the same service - when you file a labor dispute, you can claim multiple things in the same case. So maybe just 1 query?

However, from a search perspective, someone might search for "end of service benefits labor dispute" separately from "compensation for delay in labor payment". So having 2 queries might be useful for search coverage.

Let me create 2-3 queries:

1. Labor dispute case for end-of-service benefits
2. Compensation claim for payment delay in labor dispute
3. Maybe something about labor court case procedures/timeline

Actually, I should focus on SERVICES, not informational queries. The user's questions 1, 3, 4 are informational/legal advice questions. Only questions about claiming benefits and compensation represent actual government service needs.

Final decision: 2 queries

1. **Labor dispute case for end-of-service benefits** - Employee filing labor dispute to claim unpaid end-of-service benefits
2. **Compensation claim for payment delay** - Employee claiming compensation for delay in receiving labor rights

Let me write these in Arabic following the required structure:

Query 1: «خدمة لرفع دعوى عمالية للمطالبة بمكافأة نهاية الخدمة يستفيد منها الموظف المنتهي عقده بهدف الحصول على حقوقه المالية المستحقة.»

Query 2: «خدمة للمطالبة بتعويض عن التأخير في صرف الحقوق العمالية يستفيد منها الموظف المتضرر من المماطلة بهدف التعويض عن فترة الانتظار.»

Wait, I need to make sure these are truly distinct services. In the Saudi labor dispute system, when you file a labor case, you can claim multiple things in the same case - end-of-service benefits, unpaid salary, compensation for delay, etc. So these aren't really separate government services - they're different claims within the same service.

So maybe I should have just 1 query for the labor dispute case, which encompasses all claims.

But from a search perspective, someone might search specifically for "compensation for delay in labor payment" vs "end of service benefits labor dispute". Having both queries could help capture different search intents.

I'll keep 2 queries but make sure they're semantically distinct enough:

1. Focus on the main labor dispute case for end-of-service benefits
2. Focus on the compensation claim for delay specifically

Actually, looking at the merging rules again: "إذا كانت خدمتان تشتركان في نفس الفعل الإداري (توثيق، رفع دعوى، إنهاء عقد...) ونفس الغاية النهائية، فهما احتياج واحد حتى لو اختلفت صياغة الوصف."

Both queries involve "رفع دعوى" (filing a case) and the ultimate goal is getting money from the employer. So they might be the same need.

But the specific claims are different - one is for end-of-service benefits (a specific right), the other is for compensation for delay (a different type of claim). These could be searched separately.

I think 2 queries is reasonable here, as they represent different types of claims within the labor dispute system.

Let me finalize:

Query 1: Labor dispute for end-of-service benefits
Query 2: Compensation claim for payment delay

Actually, I'm overthinking this. Let me just create 2 clear queries that cover the main service needs:

1. **Labor dispute case for end-of-service benefits** - This is the primary need
2. **Labor dispute case for compensation/damages** - This is the secondary claim

Both are filed through the same system (labor court), but they represent different types of claims that someone might search for separately.

Final queries in Arabic:

1. «خدمة لرفع دعوى عمالية للمطالبة بمكافأة نهاية الخدمة يستفيد منها الموظف المنتهي عقده بهدف الحصول على حقوقه المالية المستحقة.»

2. «خدمة للمطالبة بتعويض عن المماطلة في صرف الحقوق العمالية يستفيد منها الموظف المتضرر من التأخير بهدف التعويض عن فترة الانتظار.»

Task count: 2 (two distinct claim types within labor dispute system)

Actually, I should reconsider - the user's main need is ONE labor dispute case that covers both end-of-service benefits AND compensation for delay. These are claims within the same case, not separate services.

So maybe just 1 query is more accurate:

«خدمة لرفع دعوى عمالية للمطالبة بالحقوق المالية المستحقة بما فيها مكافأة نهاية الخدمة والتعويض عن التأخير يستفيد منها الموظف المنتهي عقده بهدف الحصول على جميع حقوقه عبر القضاء العمالي.»

This covers everything in one query.

But from a search perspective, having 2 queries might capture more search variations. Let me go with 2 queries as they represent different claim types that users might search for separately.

Final decision: 2 queries

Query 1: End-of-service benefits claim through labor court
Query 2: Compensation for delay claim through labor court

Let me write them properly:

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
