# Expander — Round 2

## Usage
| Requests | Input tokens | Output tokens | Total tokens |
|----------|-------------|---------------|-------------|
| 1 | 1,753 | 935 | 2,688 |

## Output — 1 queries (task_count=1)

1. خدمة لرفع دعوى عمالية للدفاع عن حقوق العامل ضد مطالبة صاحب العمل بالتعويض واستحقاق المستحقات المالية المستحقة عند إنهاء العقد يستفيد منها العامل المنتهي عقده بهدف حماية حقوقه المالية والمطالبة بما له.
   > الرواية تذكر إنهاء عقد عمل من قبل العامل بموجب المادة 81 ومطالبة الشركة بتعويض بموجب المادة 77. المستفيد هو العامل (الراوي) الذي يحتاج إلى خدمة رسمية للتعامل مع النزاع العمالي - سواء للدفاع ضد مطالبة التعويض أو للمطالبة بمستحقاته. الهدف التنفيذي هو حماية الحقوق المالية والعينية في إطار نزاع عمالي واحد.

## Reasoning (1 block(s))

Let me analyze this user narrative carefully.

The user is describing an employment contract termination situation:
- They terminated their contract with their previous company
- There were 4 months remaining on the contract
- They terminated under Article 81 (which allows employee to terminate without notice for specific valid reasons)
- The company filed a compensation claim under Article 77

Let me identify the potential beneficiaries and their needs:

1. **The Employee (user)**: They terminated the contract under Article 81. They may need:
   - Service to defend against the company's compensation claim
   - Service to claim their end-of-service benefits
   - Service to file a counter-claim or objection to the company's demand

2. **The Employer (company)**: They filed for compensation under Article 77. But the user is asking from their perspective, so I should focus on what the USER might need.

The key compliance needs I can identify:

1. **Defense against compensation claim**: The employee needs a service to respond to/defend against the employer's compensation claim under Article 77. This is about filing a defense or objection in the labor dispute.

2. **End of service benefits**: After contract termination, the employee may need to claim their end-of-service rewards/benefits that are due to them.

3. **Labor dispute filing**: If there's a disagreement, the employee might need to file a labor complaint/dispute to resolve the matter officially.

Let me consolidate:
- The main need here is dealing with the labor dispute - the company is claiming compensation, and the employee needs to respond/defend themselves
- There might also be a need to claim what's owed to the employee (end of service benefits, unpaid wages, etc.)

These could be 2 distinct needs:
1. Service to file a defense/objection against employer's compensation claim - beneficiary: employee, goal: protect rights against unjustified compensation demand
2. Service to claim end-of-service benefits and dues - beneficiary: employee, goal: receive entitled financial benefits after contract termination


   
I should verify these aren't overlapping. The first addresses responding to the company's claim, while the second focuses on recovering what the employee is owed. These represent separate administrative actions with different objectives, so both warrant distinct queries.

Actually, both fall under labor dispute resolution at the labor court. The core need is one: resolving the employment termination dispute through official labor channels. Whether defending against the compensation claim or claiming owed benefits, both happen within the same labor case framework.

I'll consolidate into a single query covering the employee's need to file a labor dispute that addresses both defending against the employer's claim and asserting their own financial entitlements.

Wait—there's a meaningful distinction here. Defending against Article 77 compensation is reactive (responding to the employer's claim), while claiming end-of-service benefits is proactive (initiating a claim for owed amounts). These could be separate proceedings or combined in one case depending on the situation.

Given the user's narrative focuses on the company's compensation claim, the priority need is responding to that claim. I'll create two queries to capture both the defensive and proactive aspects of this labor dispute.

Actually, I should consolidate these. Both needs stem from the same employment termination dispute and would likely be handled in one labor court case. The employee's core need is comprehensive labor dispute resolution that covers both defending against the compensation claim and claiming their entitled benefits.

Let me finalize with one query that encompasses the full scope of what the employee needs to address in this situation.

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
