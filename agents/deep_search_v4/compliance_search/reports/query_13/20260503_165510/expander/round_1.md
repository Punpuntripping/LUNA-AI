# Expander — Round 1

## Usage
| Requests | Input tokens | Output tokens | Total tokens |
|----------|-------------|---------------|-------------|
| 1 | 1,753 | 1,716 | 3,469 |

## Output — 2 queries (task_count=2)

1. خدمة تتيح تقديم دفاع في نزاع عمالي ضد مطالبة تعويض عن إنهاء العقد يستفيد منها الموظف بهدف حماية حقوقه العمالية في مواجهة مطالبة صاحب العمل.
   > الرواية تذكر أن المستخدم (الموظف) أنهى عقده بموجب المادة ٨١ والشركة تطالب بتعويض بموجب المادة ٧٧ - هذا يخلق حاجة للموظف لخدمة دفاع في نزاع عمالي لحماية حقوقه.
2. خدمة تتيح تقديم مطالبة تعويض عن إنهاء عقد العمل قبل انتهاء مدته يستفيد منها صاحب العمل بهدف الحصول على تعويض عن الأضرار الناتجة عن الإنهاء المبكر.
   > الشركة تقدمت بطلب تعويض - هذا يمثل احتياجًا مستقلًا لصاحب العمل لخدمة مطالبة تعويض عن الإنهاء المبكر للعقد.

## Reasoning (1 block(s))

The user is describing a labor contract situation in Saudi Arabia. Let me analyze the story:

1. The user terminated their contract with their previous company
2. There were 4 months remaining on the contract
3. The termination was under Article 81 (which allows employee to terminate without notice for specific reasons like employer violations)
4. The company filed a compensation request under Article 77 (which deals with compensation for early termination)

This is a labor dispute scenario. Let me identify the potential beneficiaries and their needs:

1. **The employee (user)**: They terminated under Article 81, which typically means they believe the employer violated certain obligations. They may need:
   - Service to defend against the company's compensation claim
   - Service to file a counter-claim if they're entitled to end-of-service benefits or other rights
   - Service to get a labor dispute resolution/court case filed

2. **The employer (company)**: They filed for compensation under Article 77. They may need:
   - Service to pursue compensation claim for early contract termination

Since the user is the one telling the story, they're likely the employee. The main practical needs here are:

1. **For the employee**: Service to defend against the employer's compensation claim and/or file a labor dispute case to protect their rights
2. **For the employer**: Service to claim compensation for early contract termination (though this is from the company's perspective, not the user's)

Since we're identifying services that parties might need, I should focus on what the user (employee) would need, and potentially what the employer might need as a separate need.

Let me think about distinct compliance needs:

1. Employee needs to defend against compensation claim / file labor dispute - this is one need
2. Employer's compensation claim - this could be a separate need if we consider the employer as a potential beneficiary

However, since the user is the employee, the main practical needs from their perspective would be:
- Labor dispute resolution service to defend against the compensation claim


- Potentially a service to claim their own rights (end of service benefits, etc.)

These could be combined into one labor dispute service need, or separated if they're distinct actions.

Given the merging rules, I should avoid creating separate queries for what is essentially the same labor dispute resolution need. The core issue is a labor contract termination dispute.

Let me create 1-2 queries maximum:

1. Service for employee to defend against employer's compensation claim in labor dispute
2. Service for employer to file compensation claim for early contract termination (if we consider this as a separate potential need)

Actually, thinking more carefully - the user is asking about their situation. The practical services needed are:
1. For the employee: Labor dispute resolution/defense service
2. For the employer: Compensation claim service

These are two different beneficiaries with different goals, so they could be 2 separate queries. But since the user is the employee, maybe I should focus on their needs primarily.

Let me reconsider: The task says to identify all potential beneficiaries and their needs. So:

1. Employee (user): Needs service to defend against compensation claim and/or file labor dispute to protect their rights
2. Employer: Needs service to pursue compensation claim for early termination

These are 2 distinct (beneficiary + goal) pairs, so 2 queries would be appropriate.

Let me draft:

1. "خدمة لتقديم دفاع في نزاع عمالي ضد مطالبة صاحب العمل بتعويض عن إنهاء العقد مبكرًا يستفيد منها الموظف بهدف حماية حقوقه العمالية."

2. "خدمة لتقديم مطالبة تعويض عن إنهاء عقد العمل قبل انتهاء مدته يستفيد منها صاحب العمل بهدف الحصول على تعويض عن الأضرار الناتجة عن الإنهاء المبكر."

Wait, I need to check if these are truly distinct needs or if they're two sides of the same dispute. According to the merging rules, if they share the same administrative action and final goal, they should be one query. But here:
- Different beneficiaries (employee vs employer)
- Different goals (defend vs claim compensation)

So they are distinct. However, since the user is the employee, maybe I should focus on what the employee needs. Let me think about what the employee might need:

1. Service to file/defend labor dispute case
2. Service to claim end-of-service benefits (if applicable)

Actually, looking at the story again, the main issue is the labor dispute around contract termination. The employee terminated under Article 81, the employer is claiming compensation under Article 77. This is one dispute with two sides.

For the employee's practical needs:
- Labor court/dispute resolution service to defend against the compensation claim
- Possibly service to claim their own entitlements

I think 2 queries would be appropriate:
1. For employee: Labor dispute defense service
2. For employer: Compensation claim service (as this is mentioned in the story as something that happened)

Or I could focus only on the employee's perspective since they're the user:
1. Labor dispute resolution service for employee
2. Service to claim employee rights/entitlements

Let me go with 2 queries that cover the main practical needs from this situation:

1. Employee defending against compensation claim
2. Employer's compensation claim (since it's mentioned as an action taken)

Actually, re-reading the instructions: "من هو المستفيد المحتمل؟ ليس دائمًا الشخص الذي يروي القصة. قد يكون: ... العامل، صاحب العمل..."

So I should consider both employee and employer as potential beneficiaries. Let me create 2 queries:

1. For the employee (user): Service to defend in labor dispute against employer's compensation claim
2. For the employer: Service to file compensation claim for early contract termination

These are 2 distinct (beneficiary + goal) pairs.

Let me finalize the queries in Arabic:

1. "خدمة لتقديم دفاع في نزاع عمالي ضد مطالبة تعويض عن إنهاء العقد يستفيد منها الموظف بهدف حماية حقوقه في مواجهة مطالبة صاحب العمل."

2. "خدمة لتقديم مطالبة تعويض عن إنهاء عقد العمل قبل انتهاء مدته يستفيد منها صاحب العمل بهدف الحصول على تعويض عن الأضرار الناتجة."

Wait, I should make sure these follow the required structure: وصف الخدمة + المستفيد المحتمل + الهدف من الخدمة

Let me revise:

1. "خدمة تتيح تقديم دفاع في نزاع عمالي ضد مطالبة تعويض عن إنهاء العقد يستفيد منها الموظف بهدف حماية حقوقه العمالية في مواجهة مطالبة صاحب العمل."

2. "خدمة تتيح تقديم مطالبة تعويض عن إنهاء عقد العمل قبل انتهاء مدته يستفيد منها صاحب العمل بهدف الحصول على تعويض عن الأضرار الناتجة عن الإنهاء المبكر."

These look good. Let me also consider if there's a third need - like the employee claiming their own entitlements (end of service benefits, etc.). But that might be over-complicating. The main issue here is the dispute around the termination and compensation claim.

I'll go with 2 queries as they represent 2 distinct (beneficiary + goal) pairs.

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

<details><summary>User Message (125 chars)</summary>

أنهيت عقدي مع شركتي السابقة، متبقيًا منه أربعة أشهر، بموجب المادة ٨١ لأسبابٍ مختلفة. تقدمت الشركة بطلب تعويض بموجب المادة ٧٧.

</details>
