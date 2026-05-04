# Expander — Round 1

## Usage
| Requests | Input tokens | Output tokens | Total tokens |
|----------|-------------|---------------|-------------|
| 1 | 1,908 | 883 | 2,791 |

## Output — 1 queries (task_count=1)

1. خدمة للمطالبة باسترداد إيجار مدفوع مقدمًا يستفيد منها المستأجر بهدف استعادة المبلغ عند عدم وجود عقد إيجار موقع وعدم بدء الفترة الإيجارية.
   > الرواية توضح أن المستخدم دفع إيجارًا لمحطة (محطة ادريس) لفتح بوفيه، لكن لم يوقع عقد إيجار، والفترة الإيجارية لم تبدأ بعد (11/11 هجري)، ويريد إلغاء المشروع واسترداد المبلغ. هذا احتياج تنفيذي واحد: استرداد مبلغ إيجار مدفوع دون عقد.

## Reasoning (1 block(s))

Let me analyze this user's narrative carefully.

The user is describing a situation where:
1. They paid rent on April 18th to "Idris Station" (محطة ادريس) for opening a buffet/cafe (بوفيه)
2. The rent start date is 11/11 (Hijri calendar)
3. Tomorrow is 7/11 (Hijri) - so the rental period hasn't started yet
4. They want to get their money back
5. They did NOT sign a rental contract
6. They want to cancel the project entirely
7. They haven't started any work on the buffet yet
8. They're cancelling due to financial pressure (need to pay debts)
9. There's no written or even verbal agreement

Key compliance needs I can identify:

1. **The tenant (user) wants to recover rent payment** - Since no contract was signed and the rental period hasn't started, they want to get their money back from the landlord/property owner.

2. **Potential dispute resolution** - If the landlord refuses to return the money, they may need to file a complaint or claim.

Let me think about the distinct compliance needs:

1. **Service for recovering rent payment** - The tenant (user) paid rent but no contract was signed, rental period hasn't started, they want to cancel and get money back. This is essentially a service for recovering prepaid rent when no binding contract exists.

2. **Service for terminating a rental arrangement before commencement** - Since the rental period hasn't started (11/11) and today is 7/11, they want to formally terminate before it begins.

Actually, these two are very similar - both are about the same user (tenant) wanting to recover money for a rental that never commenced due to no contract being signed. This should be ONE query.

Let me check if there are other distinct needs:
- The user mentions debts they need to pay - but this isn't a government service need, it's just context for why they're cancelling


- No employment issues, no family law issues, no other government services mentioned

So I think there's really just ONE core compliance need here: recovering the rent payment when no contract was signed and the rental period hasn't started.

Let me formulate the query:

"خدمة لاسترداد مبلغ إيجار مدفوع مقدمًا يستفيد منها المستأجر بهدف استعادة الأموال عند عدم توقيع عقد إيجار وبدء الفترة الإيجارية."

Or more precisely:

"خدمة لتقديم مطالبة باسترداد إيجار مدفوع يستفيد منها المستأجر بهدف استعادة المبلغ عند إلغاء الترتيب الإيجاري قبل بدئه دون عقد موقع."

Actually, I should make it clearer about the situation - no contract signed, rental hasn't started:

"خدمة للمطالبة باسترداد إيجار مدفوع مقدمًا يستفيد منها المستأجر بهدف استعادة المبلغ عند عدم وجود عقد إيجار موقع وعدم بدء الفترة الإيجارية."

This captures:
- Service description: claiming refund of prepaid rent
- Beneficiary: the tenant
- Goal: recovering the money when there's no signed contract and rental period hasn't started

This is ONE distinct compliance need. The user's situation is straightforward - they need to recover money paid for a rental arrangement that was never formalized and never commenced.

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

<details><summary>User Message (484 chars)</summary>

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

</details>
