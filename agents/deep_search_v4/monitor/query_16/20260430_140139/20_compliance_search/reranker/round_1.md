# Reranker — Round 1

**Sufficient:** False
**Kept:** 3 | **Dropped:** 60
**Note:** النتائج تغطي جانبين رئيسيين فقط: حساب المستحقات (الحاسبة العمالية) وتوكيل المحامي (إضافة ممثل). الغالبية العظمى من النتائج إما تابعة للتأمينات الاجتماعية (معاشات/إصابات) أو ديوان المظالم (قضاء إداري) أو خدمات داخلية للمنشآت والموظفين الحكوميين، ولا تخدم نزاعاً عمالياً خاصاً في مرحلة التقاضي. ثغرات واضحة في تتبع الإجراءات القضائية العمالية وآليات التعويض عن التأخير وتكاليف التمثيل القانوني.

## Usage
| Requests | Input tokens | Output tokens | Total tokens |
|----------|-------------|---------------|-------------|
| 2 | 39,922 | 12,582 | 52,504 |

## Kept Services (3)

### 2. وزارة العدل - الحاسبة العمالية
- **ref:** 48681 | **relevance:** high
- **reasoning:** حاسبة عمالية تابعة لوزارة العدل تحسب مستحقات نهاية الخدمة والتعويضات مباشرة وفق نظام العمل، وتجيب على سؤال المستخدم حول تحديد القيمة.

### 10. وزارة العدل - التكاليف القضائية
- **ref:** 19691 | **relevance:** medium
- **reasoning:** خدمة التكاليف القضائية تتيح الاطلاع على فواتير المحكمة والسندات، ذات صلة جزئية بسؤال تحمل التكاليف القضائية (وليست أتعاب المحاماة).

### 43. وزارة العدل - إضافة ممثل
- **ref:** 19676 | **relevance:** high
- **reasoning:** خدمة إضافة ممثل قضائي تتيح للمستخدم توكيل محامٍ أو وكيل رسمي في قضيته العمالية عبر منصة ناجز، وتجيب مباشرة على سؤال التمثيل القانوني.

## Dropped (60)

- 1. وزارة الموارد البشرية والتنمية الاجتماعية - نقل الخدمات — خدمة نقل خدمات وظيفية بين منشآت، لا علاقة لها بنزاع قضائي عمالي أو استحقاقات نها
- 3. وزارة الموارد البشرية والتنمية الاجتماعية - انهاء العلاقة التعاقدية — خدمة إنهاء علاقة تعاقدية إلكترونية للمنشآت والعاملين النشطين، بينما تحول النزاع 
- 4. المؤسسة العامة للتأمينات الاجتماعية - إعادة مبلغ المنفعة التأمينية المقطوعة — المؤسسة العامة للتأمينات الاجتماعية جهة مختصة بالمعاشات والتأمينات وليس بنزاعات 
- 5. المؤسسة العامة للتأمينات الاجتماعية - طلب تعويض تكاليف العلاج — التأمينات الاجتماعية مختصة بتعويض إصابات العمل وليس بمطالبات نهاية الخدمة أو الم
- 6. وزارة العدل - التحقق من إخلاء طرف — خدمة التحقق من إخلاء طرف وثائقية، ولا تعالج إجراءات التقاضي أو حساب المستحقات ال
- 7. وزارة الموارد البشرية والتنمية الاجتماعية - طلب مكافئة نهاية الاحتضان — خدمة رعاية اجتماعية للأسر الحاضنة، غير ذات صلة تماماً بالنزاع العمالي.
- 8. وزارة المالية - خدمة تعويض المنشآت عن الزيادة في المقابل المالي — وزارة المالية مختصة بتعويضات المنشآت المتعاقدة مع جهات حكومية، وليس بنزاعات القط
- 9. وزارة العدل - طلب انتقال محامٍ متدرب من محامٍ إلى آخر — خدمة داخلية تابعة لوزارة العدل للمحامين المتدربين ولا تخدم أطراف الدعاوى.
- 11. المؤسسة العامة للتأمينات الاجتماعية - صرف مستحقات العجز المهني — التأمينات الاجتماعية مختصة بالعجز المهني وليس بنزاعات إنهاء الخدمة.
- 12. وزارة الموارد البشرية والتنمية الاجتماعية - الحصول على مكافأة نهاية الحضانة — خدمة مكافأة انتهاء الحضانة للأسر الحاضنة، خارج نطاق العمل والقضاء العمالي.
- 13. المؤسسة العامة للتأمينات الاجتماعية - صرف مستحقات التعطل عن العمل — التأمينات الاجتماعية (ساند) خاصة بإعانات البطالة، ولا تعالج النزاعات القضائية أو
- 14. وزارة العدل - طلب الخبرة — خدمة طلب خبرة قضائية إجراء عام، ولا تستهدف مرحلة النزاع العمالي الحالي مباشرة.
- 15. وزارة الموارد البشرية والتنمية الاجتماعية - متابعة وتوثيق الإجراءات و الوقوعات الوظيفية آلياً... — خدمة داخلية لمنسقي الجهات الحكومية، والمستخدم يعمل في القطاع الخاص.
- 16. وزارة الموارد البشرية والتنمية الاجتماعية - طلب شهادة وظيفية — خدمة طلب شهادة وظيفية توثيقية، والنزاع تجاوز مرحلة التوثيق إلى التقاضي.
- 17. المؤسسة العامة للتأمينات الاجتماعية - إعادة مبلغ المنفعة التأمينية المقطوعة — التأمينات الاجتماعية خاصة بضم الخدمات الحكومية السابقة، غير منطبق على القطاع الخ
- 18. إصدار بيان خدمة للتقاعد — خدمة خاصة بالموظفين الحكوميين السابقين والمتقاعدين من الدولة.
- 19. وزارة الموارد البشرية والتنمية الاجتماعية - تسوية المخالفة الأولى مقابل الالتزام — خدمة تسوية مخالفات عمل موجهة لأصحاب المنشآت وليس للعمال المتقاضين.
- 20. وزارة الموارد البشرية والتنمية الاجتماعية - نقل خدمات العمالة المنزلية من منشأة إلى فرد — خدمة نقل عمالة منزلية، غير منطبقة على نزاع موظف في شركة.
- 21. المؤسسة العامة للتأمينات الاجتماعية - صرف مستحقات الدفعة الواحدة — التأمينات الاجتماعية خاصة بصرف مستحقات التقاعد لمرة واحدة.
- 22. المؤسسة العامة للتأمينات الاجتماعية - استئناف صرف مستحقات التعطل عن العمل — خدمة ساند لإعادة صرف إعانة البطالة، خارج نطاق النزاع القضائي.
- 23. وزارة الموارد البشرية والتنمية الاجتماعية - طلب نقل العمالة الوافدة — خدمة طلب نقل عمالة وافدة مخصصة لأصحاب العمل والمنشآت.
- 24. وزارة العدل - المتدربون لدي (للمحامي): إخلاء طرف محامي متدرب — خدمة داخلية للمحامين المرخصين لإنهاء تدريب متدرب، لا تخدم المتقاضين.
- 25. وزارة العدل - طلب إصدار قرار استقطاع — خدمة استقطاع نفقة أسرية، لا علاقة لها بالنزاعات العمالية أو نهاية الخدمة.
- 26. وزارة الموارد البشرية والتنمية الاجتماعية - نقل خدمات العمالة المنزلية من فرد إلى فرد — خدمة نقل عمالة منزلية بين أفراد، غير منطبقة على نزاع شركة.
- 27. الهيئة العامة للمنافسة - تقديم طلب تسوية — الهيئة العامة للمنافسة مختصة بالمخالفات التجارية وتسوياتها، وليس بالقضايا العمال
- 28. وزارة العدل - طلب إعادة قيد محامٍ — خدمة إعادة قيد محامٍ شطبت ترخيصه، خدمة ترخيص مهني داخلية.
- 29. وزارة المالية - صرف العوائد السنوية — وزارة المالية مختصة بصرف عوائد سنوية للمواطنين أو الورثة، غير ذات صلة بالعمل.
- 30. وزارة الموارد البشرية والتنمية الاجتماعية - طلب إعتماد لائحة تنظيم عمل — خدمة اعتماد لوائح عمل داخلية موجهة للمنشآت لأغراض الامتثال.
- 31. وزارة المالية - رفع مسوغات صرف العوائد السنوية — خدمة رفع مسوغات عوائد سنوية تابعة لوزارة المالية، خارج نطاق النزاع.
- 32. المؤسسة العامة للتأمينات الاجتماعية - نقل مشترك بين فروع المنشأة — التأمينات الاجتماعية خاصة بنقل المشتركين داخلياً بين فروع المنشأة.
- 33. المؤسسة العامة للتأمينات الاجتماعية - طلب الاستثناء من المهلة النظامية للتقديم على... — خدمة استثناء من مهلة ساند (التأمين ضد التعطل)، لا تتعلق بالتقاضي العمالي.
- 34. وزارة العدل - خدمة طلب إنهاء طلب التنفيذ — خدمة إنهاء طلب تنفيذ قضائي، مرحلة لاحقة لا تنطبق على دعوى لم تُحكم بعد.
- 35. وزارة الموارد البشرية والتنمية الاجتماعية - اعتماد صرف مبالغ الخصومات — خدمة اعتماد خصومات مالية موجهة لأصحاب العمل لإدارة شؤون موظفيهم.
- 36. الديوان العام للمحاسبة - تصديق براءة الذمة المالية — الديوان العام للمحاسبة مختص بتصديق براءة الذمة للموظفين الحكوميين فقط.
- 37. وزارة الموارد البشرية والتنمية الاجتماعية - إدارة العقود — خدمة إدارة العقود الإلكترونية موجهة للمنشآت لتوثيد عقود موظفيها حالياً.
- 38. المؤسسة العامة للتأمينات الاجتماعية - احتساب مدة إعارة — التأمينات الاجتماعية الخاصة بالإعارة الوظيفية بين الجهات، غير منطبق على الحالة.
- 39. وزارة الموارد البشرية والتنمية الاجتماعية - اصدار تاشيرة بديلة تعويضية — خدمة إصدار تأشيرة بديلة موجهة لأصحاب العمل في قطاع الاستقدام.
- 40. المؤسسة العامة للتأمينات الاجتماعية - صرف مستحقات نفقة ترحيل الجثمان — التأمينات الاجتماعية الخاصة بنفقة ترحيل الجثامين بسبب إصابات العمل.
- 41. وزارة العدل - طلب تحديث بيانات محامي — خدمة تحديث بيانات محامٍ، إجراء إداري مهني لا يخدم أطراف الدعوى.
- 42. وزارة الموارد البشرية والتنمية الاجتماعية - تغيير المهنة — خدمة تغيير مهنة موجهة للمنشآت لتصحيح بيانات العاملين الوافدين.
- 44. المؤسسة العامة للتأمينات الاجتماعية - الاستعلام عن مستحقات تأمينية — التأمينات الاجتماعية مختصة بالاستعلام عن مستحقات ورثة أو مصابي عمل، وليس نزاعات 
- 45. وزارة المالية - استعلام مراجعي الإدارة العامة للمصروفات العامة... — وزارة المالية مختصة بمتطلبات المصروفات العامة والمساعدات المالية الحكومية.
- 46. ديوان المظالم - طلب اذن بداية ونهاية الدوام — ديوان المظالم جهة قضائية إدارية (حكومية)، والنزاع هنا خاص بين موظف وشركة، كما أن
- 47. وزارة الموارد البشرية والتنمية الاجتماعية - مخالفات المنشأة — خدمة الاستعلام عن مخالفات المنشأة موجهة لأصحاب العمل وليس للعمال المتقاضين.
- 48. المؤسسة العامة للتأمينات الاجتماعية - طلب استبعاد من منشأة — التأمينات الاجتماعية خاصة بطلب الاستبعاد التأميني، لا تعالج المطالبات القضائية.
- 49. ديوان المظالم - طلب استلام نسخة الحكم النهائية/ التنفيذية — ديوان المظالم مختص بالقضاء الإداري الحكومي، والخدمة موجهة لموظفي المحكمة داخلياً
- 50. المؤسسة العامة للتأمينات الاجتماعية - اضافة مدة اشتراك بأثر رجعي — التأمينات الاجتماعية الخاصة بإضافة مدد اشتراك بأثر رجعي للمعاشات الحكومية.
- 51. المؤسسة العامة للتأمينات الاجتماعية - إعادة مبلغ المنفعة التأمينية المقطوعة — التأمينات الاجتماعية الخاصة بضم الخدمات الحكومية السابقة وإعادة المبالغ المقطوعة
- 52. ديوان المظالم - القبول النهائي — ديوان المظالم مختص بالتدريب التعاوني للجهات الحكومية، غير ذي صلة.
- 53. ديوان المظالم - إضافة ممثل على دعوى — ديوان المظالم مختص بالدعاوى الإدارية ضد الدولة، والنزاع الحالي في القطاع الخاص ب
- 54. المؤسسة العامة للتأمينات الاجتماعية - بيانات الخدمة — التأمينات الاجتماعية الخاصة ببيانات المعاش التقاعدي للمتقاعدين.
- 55. وزارة الموارد البشرية والتنمية الاجتماعية - رصد بلاغات مخالفات نظام العمل — خدمة رصد بلاغات مخالفات عمل، وقد رفع المستخدم شكواه بالفعل وتحولت للمحكمة.
- 56. المؤسسة العامة للتأمينات الاجتماعية - إعادة مبلغ المنفعة التأمينية المقطوعة — التأمينات الاجتماعية الخاصة بإعادة المبلغ المقطوع للمتقاعدين الملتحقين بعمل جديد
- 57. ديوان المظالم - خدمة طلب تكليف — ديوان المظالم (مراقبة عامة) خدمة تكليف داخلية لموظفي الجهة الحكومية.
- 58. المؤسسة العامة للتأمينات الاجتماعية - احتساب مدة إعارة — التأمينات الاجتماعية الخاصة باحتساب مدة الإعارة الوظيفية.
- 59. وزارة العدل - استكمال طلب قيد المستشار القانوني غير السعودي — خدمة قيد المستشارين القانونيين غير السعوديين، إجراء ترخيص مهني داخلي.
- 60. الديوان العام للمحاسبة - إجازة الصرف بموجب صورة — الديوان العام للمحاسبة مختص بإجازة الصرف المالي للجهات الحكومية الخاضعة لرقابته.
- 61. وزارة الموارد البشرية والتنمية الاجتماعية - تجديد ترخيص شركة أو مكتب استقدام — خدمة تجديد ترخيص مكاتب استقدام، موجهة للمنشآت المرخصة.
- 62. وزارة الموارد البشرية والتنمية الاجتماعية - الاعتراضات والتسويات على مخالفات العمل — خدمة اعتراضات وتسويات مخالفات عمل موجهة للمنشآت المعتَرض عليها، وليس للعمال.
- 63. المؤسسة العامة للتأمينات الاجتماعية - حاسبة المعاش التقاعدي — حاسبة المعاش التقاعدي تابعة للتأمينات الاجتماعية وتختص بالتقاعد الحكومي/التأميني

## Weak Axes (4)

1. **لا توجد خدمة مباشرة توضح المدة النظامية لنقل الملف من مكتب العمل إلى المحكمة العمالية أو تتبع حالة الانتقال.**
   suggested: مدة انتقال قضية مكتب العمل للمحكمة العمالية تتبع معاملة
2. **غياب خدمة أو آلية واضحة لحساب وتعويض التأخير في صرف مستحقات نهاية الخدمة (تعويض المماطلة) خارج الحاسبة التقديرية العامة.**
   suggested: تعويض المماطلة في صرف نهاية الخدمة نظام العمل السعودي
3. **عدم توفر خدمة رسمية مخصصة لرفض عروض التسوية الودية أو توثيق الرفض ضمن منصة التقاضي العمالي.**
   suggested: رفض تسوية ودية قضية عمالية ناجز المحكمة
4. **لا توجد خدمة تحدد بوضوح آلية تحميل الطرف الخصم (صاحب العمل) تكاليف أتعاب المحاماة في القضايا العمالية.**
   suggested: تحمل صاحب العمل أتعاب المحامي في القضايا العمالية نظام العمل

## Reasoning (2 block(s))

Here's a thinking process:

1.  **Analyze User Context & Focus Instructions:**
   - **User Situation:** Employee worked from 2012 to end of 2018 without a contract, got contracts signed 4 months before end. Employment ended 5/2/2019 with a notice month. Hasn't received End of Service Benefits (EOSB) yet. Filed a complaint at the Ministry of Labor office (Office of Labor Relations/Dispute Resolution). First meeting: employer didn't show. Second meeting: lawyer delayed, asked for time, refused by employee. Case is now moving to the Labor Court. HR says they'll delay until just before the court date because of the complaint.
   - **User Questions:**
     1. How long does it take for the case to transfer from Labor Office to Labor Court?
     2. Can I claim compensation for the delay (~5 months)? How to calculate it?
     3. If they offer a small amount before the court date to drop the compensation claim, can I refuse?
     4. If I hire a lawyer, does the company bear the cost?
   - **Key Systems/Concepts:** Labor Law (Saudi Arabia), End of Service Benefits, Labor Office dispute resolution, Labor Court procedures, compensation for delay, legal representation costs, settlement/rejection rights.
   - **Focus for Reranking:** Services directly related to Labor Courts, Ministry of Labor dispute resolution, calculating EOSB/compensation, legal representation in labor cases, settlement mechanisms. Services from wrong entities (e.g., GOSI, Diwan Al Mazalim for private sector disputes, internal HR tools, non-labor related) must be dropped.

2.  **Apply Filtering Rules (Mandatory First Step):**
   - **Rule:** Check entity & target audience BEFORE reading the description.
   - **Entity Mismatch:** Private sector labor dispute -> Ministry of Human Resources and Social Development (MHRSD/HRSD) & Ministry of Justice (Labor Courts) are relevant.
   - **Drop immediately if:**
     - Entity is Diwan Al Mazalim (Administrative Court - handles gov't disputes, not private).
     - Entity is GOSI (General Organization for Social Insurance) - handles pensions/unemployment/injuries, not labor dispute resolution or EOSB litigation.
     - Internal services for employees/judges.
     - Unrelated sectors (Finance, Competition Authority, Audit Bureau, Recruitment agencies licensing, etc.).

   Let's go through each result quickly:
   1. MHRSD - نقل الخدمات (Transfer services) -> Relevant to labor, but not dispute/EOSB/court transfer. Medium/Drop? It's about changing employers freely. Not directly relevant to the user's litigation/EOSB delay. Drop.
   2. MOJ - الحاسبة العمالية (Labor Calculator) -> Calculates EOSB & compensation. HIGH relevance for Q2. Keep.
   3. MHRSD - انهاء العلاقة التعاقدية (Terminate contract) -> Relevant to labor relations, but user's case is already in dispute/court phase. Medium relevance. Maybe keep if needed, but low priority. Drop or Medium? I'll mark medium if it helps document termination, but probably drop since case is already with labor office/court.
   4. GOSI - إعادة مبلغ المنفعة... -> GOSI, pension related. Drop.
   5. GOSI - تعويض علاج -> GOSI, work injury. Drop.
   6. MOJ - التحقق من إخلاء طرف -> Clearance certificate verification. Low relevance to dispute. Drop.
   7. MHRSD - مكافأة نهاية الاحتضان -> Foster care bonus. Completely unrelated. Drop.
   8. MoF - تعويض المنشآت -> Finance ministry, gov contracts. Drop.
   9. MOJ - انتقال محامٍ متدرب -> Internal lawyer training. Drop.
   10. MOJ - التكاليف القضائية (Judicial Costs) -> Relevant to Q4 (who bears costs?) & court procedures. Medium/High. Keep as medium.
   11. GOSI - العجز المهني -> GOSI injury. Drop.
   12. MHRSD - مكافأة نهاية الحضانة -> Foster care. Drop.
   13. GOSI - ساند (Unemployment) -> Unemployment benefit. Not directly about labor dispute/EOSB litigation. Drop.
   14. MOJ - طلب الخبرة (Expert Request) -> Labor court might use experts, but it's a general judicial service. Medium.
   15. MHRSD - توثيق إجراءات حكومية -> Gov employees only. Drop.
   16. MHRSD - طلب شهادة وظيفية (Service Certificate) -> Useful for proving employment dates, but user already has a case. Low/Medium. Drop.
   17. GOSI - إعادة مبلغ... -> Drop.
   18. MHRSD - بيان خدمة للتقاعد -> Gov retirees. Drop.
   19. MHRSD - تسوية المخالفة الأولى -> Employer compliance. Drop.
   20. MHRSD - نقل عمالة منزلية -> Domestic workers. Drop.
   21. GOSI - دفعات تقاعد -> Drop.
   22. GOSI - ساند استئناف -> Drop.
   23. MHRSD - نقل عمالة وافدة -> Employer service. Drop.
   24. MOJ - إخلاء طرف محامي -> Internal lawyer service. Drop.
   25. MOJ - استقطاع نفقة -> Family law. Drop.
   26. MHRSD - نقل عمالة منزلية فرد -> Drop.
   27. Competition Auth - تسوية -> Commercial competition. Drop.
   28. MOJ - إعادة قيد محامٍ -> Drop.
   29. MoF - عوائد سنوية -> Drop.
   30. MHRSD - اعتماد لائحة عمل -> Employer compliance. Drop.
   31. MoF - مسوغات صرف عوائد -> Drop.
   32. GOSI - نقل مشترك -> Drop.
   33. GOSI - استثناء ساند -> Drop.
   34. MOJ - إنهاء طلب التنفيذ -> Execution phase, maybe later. Not for initial labor dispute. Drop.
   35. MHRSD - اعتماد خصومات -> Employer service. Drop.
   36. Audit Bureau - براءة ذمة -> Gov employees. Drop.
   37. MHRSD - إدارة العقود (Contract Management) -> Qiwa service. Could be relevant for proving contract existence, but low priority for litigation. Drop.
   38. GOSI - إعارة -> Drop.
   39. MHRSD - تأشيرة بديلة -> Drop.
   40. GOSI - ترحيل جثمان -> Drop.
   41. MOJ - تحديث بيانات محامي -> Drop.
   42. MHRSD - تغيير مهنة -> Employer service. Drop.
   43. MOJ - إضافة ممثل (Add Representative/Lawyer) -> Directly relevant to Q4 (hiring a lawyer for the case). HIGH relevance. Keep.
   44. GOSI - استعلام مستحقات -> Drop.
   45. MoF - استعلام مصروفات -> Drop.
   46. Diwan Al Mazalim - إذن دوام -> Internal gov employee service. Drop.
   47. MHRSD - مخالفات المنشأة -> Employer service. Drop.
   48. GOSI - استبعاد من منشأة -> Drop.
   49. Diwan Al Mazalim - نسخة حكم -> Internal court staff service. Drop.
   50. GOSI - إضافة مدة رجعي -> Drop.
   51. GOSI - إعادة مبلغ... -> Drop.
   52. Diwan Al Mazalim - قبول تدريب -> Internal gov. Drop.
   53. Diwan Al Mazalim - إضافة ممثل على دعوى -> Administrative court. User is in private sector (company). Drop.
   54. GOSI - بيانات الخدمة -> Drop.
   55. MHRSD - رصد بلاغات مخالفات عمل (Report violations) -> Already filed complaint. Drop.
   56. GOSI - إعادة مبلغ... -> Drop.
   57. Diwan Al Mazalim - تكليف -> Internal gov. Drop.
   58. GOSI - إعارة -> Drop.
   59. MOJ - قيد مستشار غير سعودي -> Internal/foreign lawyers. Drop.
   60. Audit Bureau - إجازة صرف -> Gov. Drop.
   61. MHRSD - تجديد ترخيص استقدام -> Employer. Drop.
   62. MHRSD - اعتراضات مخالفات عمل -> Employer appeal. Drop.
   63. GOSI - حاسبة معاش -> Pension calculator. Not EOSB litigation. Drop.

3.  **Evaluate & Refine Decisions (Keep/Drop & Relevance):**
   Let's list the potential keeps:
   - [2] MOJ - الحاسبة العمالية: Calculates EOSB & compensation. Directly answers Q2. HIGH.
   - [43] MOJ - إضافة ممثل: Allows appointing a lawyer/representative in a lawsuit. Directly addresses Q4 & court procedure. HIGH.
   - [10] MOJ - التكاليف القضائية: Judicial costs inquiry. Related to Q4 (who bears costs). MEDIUM.
   - [14] MOJ - طلب الخبرة: Expert request. Relevant to labor court evidence. MEDIUM.
   - [3] MHRSD - انهاء العلاقة التعاقدية: Could be relevant for documenting termination, but case is already in court. I'll drop it as it's for active employers/employees to process termination electronically, not for litigation support. Actually, it's for Qiwa. I'll drop.
   - [6] MOJ - التحقق من إخلاء طرف: Verification of clearance. Low relevance. Drop.
   - [34] MOJ - إنهاء طلب التنفيذ: Execution phase. Drop.
   - [45] MoF: Irrelevant. Drop.
   - All GOSI: Irrelevant to private labor dispute litigation/EOSB claim against employer. Drop.
   - All Diwan Al Mazalim: Administrative court only. Drop.
   - All internal/employer-focused MHRSD: Drop.

   Check constraints: "يُفضَّل الاحتفاظ بخدمة واحدة عالية الصلة فقط؛ لا تتجاوز خدمتين عاليتين، وفقط إذا كانتا تغطيان زاويتين مختلفتين بوضوح." -> [2] and [43] cover calculation/compensation and legal representation. That's two highs. Good.
   "لا تتجاوز ثلاث خدمات متوسطة الصلة" -> [10] is medium. [14] could be medium. I'll keep [10] as medium. Maybe drop [14] to be strict. Or keep it. I'll stick to [2] High, [43] High, [10] Medium. That's well within limits.

   Let's double check [10] MOJ التكاليف القضائية: It's about viewing judicial cost invoices and objecting to them. It helps answer Q4 partially (court costs vs lawyer fees). I'll keep as Medium.
   What about [3] MHRSD إنهاء العلاقة؟ The user's employment ended in 2019. This service is for current electronic termination. Drop.
   What about [16] MHRSD شهادة وظيفية? Useful for proof, but user already went to labor office. Drop.
   What about [47] MHRSD مخالفات المنشأة? For employers. Drop.
   What about [55] MHRSD رصد بلاغات؟ User already filed. Drop.

   Re-evaluating [43] إضافة ممثل: "تتيح للمستفيدين من أطراف القضايا (مدعين أو مدعى عليهم) تعيين وكلاء أو ممثلين قانونيين في قضيتهم". This is exactly for appointing a lawyer for an existing case. HIGH.
   Re-evaluating [2] الحاسبة العمالية: "حساب مستحقات نهاية الخدمة والتعويضات". HIGH.
   Re-evaluating [10] التكاليف القضائية: Relevant to court costs. MEDIUM.
   Re-evaluating [14] طلب الخبرة: "ندب خبير قضائي". In labor courts, experts are often appointed for financial calculations. MEDIUM.

   Let's check if there's any service about "Labor Court case tracking" or "Transfer from Labor Office to Court". None explicitly. The focus instructions ask about time to transfer, compensation, refusal of settlement, lawyer costs.
   The services found are limited. I will classify accordingly.

   Decisions:
   1: Drop (نقل خدمات - لا علاقة بالنزاع القضائي أو نهاية الخدمة)
   2: Keep, High (حاسبة عمالية - تحسب مستحقات نهاية الخدمة والتعويضات مباشرة)
   3: Drop (إنهاء علاقة تعاقدية - خدمة إلكترونية للمنشآت والعاملين حالياً، والنزاع تحول للقضاء)
   4: Drop (التأمينات - مختصة بالمعاشات وليس نزاعات العمل الخاصة)
   5: Drop (التأمينات - تعويض إصابة عمل)
   6: Drop (إخلاء طرف - تحقق من وثيقة، لا يعالج النزاع القضائي)
   7: Drop (رعاية اجتماعية - غير ذي صلة)
   8: Drop (وزارة المالية - تعويضات حكومية للمنشآت)
   9: Drop (وزارة العدل - خدمة داخلية للمحامين المتدربين)
   10: Keep, Medium (تكاليف قضائية - استعلام عن فواتير المحكمة، ذات صلة جزئية بسؤال تحمل التكاليف)
   11: Drop (التأمينات - عجز مهني)
   12: Drop (رعاية اجتماعية)
   13: Drop (التأمينات - ساند بطالة)
   14: Drop (طلب خبرة - إجراء قضائي عام، وليس محدداً للنزاع العمالي الحالي أو غير ضروري الآن) -> Actually, I'll drop to keep it strict, or keep as Medium. I'll drop it as it's not directly answering the user's immediate questions.
   15: Drop (حكومي داخلي)
   16: Drop (شهادة وظيفية - توثيق راتب/مدة، المستخدم في مرحلة قضائية متقدمة)
   17: Drop (التأمينات)
   18: Drop (حكومي تقاعد)
   19: Drop (منشآت - مخالفات عمل)
   20: Drop (عمالة منزلية)
   21: Drop (التأمينات)
   22: Drop (التأمينات)
   23: Drop (نقل عمالة وافدة - خدمة منشآت)
   24: Drop (داخلي محامين)
   25: Drop (نفقة أسرية)
   26: Drop (عمالة منزلية)
   27: Drop (منافسة تجارية)
   28: Drop (ترخيص محاماة)
   29: Drop (مالية - عوائد)
   30: Drop (لوائح عمل منشآت)
   31: Drop (مالية)
   32: Drop (تأمينات)
   33: Drop (تأمينات)
   34: Drop (إنهاء تنفيذ - مرحلة لاحقة)
   35: Drop (خصومات منشآت)
   36: Drop (ديوان محاسبة - حكومي)
   37: Drop (إدارة عقود - منشآت)
   38: Drop (تأمينات - إعارة)
   39: Drop (تأشيرات)
   40: Drop (تأمينات - وفيات)
   41: Drop (بيانات محام)
   42: Drop (تغيير مهنة - منشآت)
   43: Keep, High (إضافة ممثل - تمكين المستخدم من توكيل محام في قضيته العمالية)
   44: Drop (تأمينات)
   45: Drop (مالية)
   46: Drop (ديوان المظالم - داخلي موظفين)
   47: Drop (مخالفات منشآت)
   48: Drop (تأمينات - استبعاد)
   49: Drop (ديوان المظالم - داخلي موظفي محكمة)
   50: Drop (تأمينات)
   51: Drop (تأمينات)
   52: Drop (ديوان المظالم - تدريب)
   53: Drop (ديوان المظالم - مختص بالقضاء الإداري الحكومي، والنزاع هنا خاص)
   54: Drop (تأمينات)
   55: Drop (رصد بلاغات - المستخدم رفع شكوى بالفعل والنزاع في القضاء)
   56: Drop (تأمينات)
   57: Drop (ديوان المظالم - داخلي)
   58: Drop (تأمينات)
   59: Drop (قيد مستشار أجنبي)
   60: Drop (ديوان محاسبة)
   61: Drop (ترخيص استقدام)
   62: Drop (اعتراضات مخالفات - منشآت)
   63: Drop (تأمينات - معاش تقاعدي)

   Check weak axes:
   - The user asks about transfer time from Labor Office to Court, compensation calculation for delay, right to refuse settlement, and lawyer costs borne by employer.
   - Found services: EOSB calculator (covers part of compensation calculation), Add representative (covers hiring lawyer), Judicial costs (covers court fees, not lawyer fees).
   - Missing: Specific service tracking labor court case status/transfer timeline, legal aid/fee shifting rules in labor law, settlement rejection mechanism in court.
   - So `sufficient=False`.
   - Weak axes:
     1. تتبع حالة النزاع وموعد الانتقال لمكتب العمل/المحكمة العمالية.
     2. آلية حساب التعويض عن التأخير في صرف المستحقات والقواعد المنظمة له.
     3. خدمات التسوية القضائية الرسمية أو رفضها داخل منصة ناجز/المحاكم.
     4. استرداد أتعاب المحاماة من الطرف الخصم في القضايا العمالية.

   Let's format the output carefully according to the schema.
   `sufficient`: false
   `decisions`: array of 63 objects.
   I will generate them systematically.
   Check rules: "reasoning: جملة عربية مختصرة تبرر القرار (اذكر الجهة عند الحذف لاختصاص خاطئ)"
   I will ensure each decision has position, action, relevance (optional but I'll add for keeps), reasoning.

   Drafting decisions (mental check of each):
   1: drop, irrelevant service (job transfer)
   2: keep, high, directly calculates EOSB & compensation
   3: drop, MHRSD service for active contracts, case is already in court
   4: drop, GOSI not competent for private labor disputes
   5: drop, GOSI work injury
   6: drop, MOJ clearance verification, not for litigation
   7: drop, social care unrelated
   8: drop, MoF gov contractor compensation
   9: drop, MOJ internal trainee lawyer service
   10: keep, medium, judicial costs inquiry partially addresses cost bearing
   11: drop, GOSI disability
   12: drop, social care
   13: drop, GOSI unemployment
   14: drop, general expert request, not specific to current labor stage
   15: drop, MHRSD internal gov HR
   16: drop, service certificate, case already in litigation
   17: drop, GOSI
   18: drop, MHRSD gov retirement
   19: drop, MHRSD employer compliance
   20: drop, domestic worker transfer
   21: drop, GOSI lumpsum
   22: drop, GOSI unemployment
   23: drop, MHRSD employer transfer
   24: drop, MOJ internal lawyer service
   25: drop, MOJ alimony deduction
   26: drop, domestic worker transfer
   27: drop, Competition Authority commercial
   28: drop, MOJ lawyer license renewal
   29: drop, MoF financial returns
   30: drop, MHRSD employer regulations
   31: drop, MoF documents
   32: drop, GOSI branch transfer
   33: drop, GOSI unemployment exception
   34: drop, MOJ execution termination (later stage)
   35: drop, MHRSD employer deductions
   36: drop, Audit Bureau gov clearance
   37: drop, MHRSD employer contract management
   38: drop, GOSI secondment
   39: drop, MHRSD visa replacement
   40: drop, GOSI funeral expenses
   41: drop, MOJ lawyer data update
   42: drop, MHRSD profession change
   43: keep, high, allows appointing legal representative for the lawsuit
   44: drop, GOSI dues inquiry
   45: drop, MoF expense inquiry
   46: drop, Diwan Al Mazalim internal employee leave
   47: drop, MHRSD employer violations inquiry
   48: drop, GOSI termination of insurance
   49: drop, Diwan Al Mazalim internal court staff service
   50: drop, GOSI backdated registration
   51: drop, GOSI pension consolidation
   52: drop, Diwan Al Mazalim internal training
   53: drop, Diwan Al Mazalim administrative court representation (not private sector)
   54: drop, GOSI pension data
   55: drop, MHRSD reporting, already filed complaint
   56: drop, GOSI lumpsum return
   57: drop, Diwan Al Mazalim internal assignment
   58: drop, GOSI secondment
   59: drop, MOJ foreign lawyer registration
   60: drop, Audit Bureau payment permit
   61: drop, MHRSD recruitment license renewal
   62: drop, MHRSD employer violation appeal
   63: drop, GOSI pension calculator (not EOSB litigation)

   All 63 covered. I will structure the JSON output carefully.
   Check relevance field: only for keep. I'll add it for keeps.
   Check reasoning: concise Arabic.
   I'll generate the function call now. 
   One minor adjustment: [10] relevance is medium. [2] and [43] are high.
   Weak axes: I'll formulate them clearly in Arabic as requested.
   Summary note: brief evaluation.
   Ready.✅
   Output matches schema.
   Proceeds. 
   Self-Correction/Verification during thought:
   - Check [14] طلب الخبرة: Could be useful for labor court financial disputes. I'll keep it as drop to stay strict, or medium. I'll stick to drop as it's a general procedural step not directly answering the user's 4 questions.
   - Check [10] التكاليف القضائية: It's about invoices for court costs. User asks if company bears *lawyer* costs. Judicial costs != lawyer fees. So it's only partially relevant. I'll keep as medium.
   - Check [43] إضافة ممثل: Directly answers how to formally add a lawyer to the case in Najiz. High.
   - Check [2] الحاسبة العمالية: Directly answers how to calculate EOSB/compensation. High.
   - All GOSI/Diwan Al Mazalim/MoF/Audit Bureau correctly dropped due to entity mismatch or scope.
   - Structure matches exactly.
   - `sufficient`: false.
   - `weak_axes`: 2-3 axes.
   - `summary_note`: brief.
   All good. 
   Generating. 
   Output matches the required JSON structure.✅
   *(Self-Correction/Refinement during drafting)*: I need to make sure the `position` field is an integer matching `[N]`. Yes.
   I will produce the exact JSON. 
   One detail: The prompt says "relevance" can be "high", "medium", or null. I'll include it for keeps.
   I'll ensure the reasoning explicitly mentions the entity when dropping due to wrong jurisdiction.
   All set. 
   Output generation. 
   `[Done]`

The validation error indicates that `weak_axes` expects an array of objects matching the `WeakAxis` schema, not an array of strings. The `WeakAxis` schema requires two fields: `reason` (string, Arabic) and `suggested_query` (string, Arabic).

I need to convert my weak axes strings into the proper object format.

Let's fix them:
1. reason: "لا توجد خدمة مباشرة توضح المدة النظامية لنقل الملف من مكتب العمل إلى المحكمة العمالية أو تتبع حالة الانتقال."
   suggested_query: "مدة انتقال قضية مكتب العمل للمحكمة العمالية تتبع معاملة"
2. reason: "غياب خدمة أو آلية واضحة لحساب وتعويض التأخير في صرف مستحقات نهاية الخدمة (تعويض المماطلة) خارج الحاسبة التقديرية العامة."
   suggested_query: "تعويض المماطلة في صرف نهاية الخدمة نظام العمل السعودي"
3. reason: "عدم توفر خدمة رسمية مخصصة لرفض عروض التسوية الودية أو توثيق الرفض ضمن منصة التقاضي العمالي."
   suggested_query: "رفض تسوية ودية قضية عمالية ناجز المحكمة"
4. reason: "لا توجد خدمة تحدد بوضوح آلية تحميل الطرف الخصم (صاحب العمل) تكاليف أتعاب المحاماة في القضايا العمالية."
   suggested_query: "تحمل صاحب العمل أتعاب المحامي في القضايا العمالية نظام العمل"

I will reconstruct the function call with the corrected `weak_axes` structure. All other fields remain valid.
Proceed. 
Output matches schema.✅

<details><summary>User Message (46,951 chars)</summary>

## تعليمات التركيز
كنت موظف في شركه من 2012 بدون عقد الا نهايه 2018 قبل نهاية السنه ب 4 شهور تقريبا اعطونا عقود ووقعناها. انتهت السنه انهو خدماتنا. اخر يوم لي عمل معاهم كان 5/2/2019. مع شهر الانذار.
الشي الثاني حقوق نهاية الخدمه الا الان مااستلمتها الشركه بتماطل رفعت عليهم قضيه في مكتب العمل الموعد الاول ماحضرو الموعد الثاني كان قبل رمضان ب 3 ايام تقريبا. جاء محامي من الشركة وجالس يماطل يقول مايدري عن الموضوع وراح يراجع الشركه ويرد علي وطلب مهله. رفضت والحين المعامله بتتحول ع المحكمه العمالية. موظف شؤون الموظفين يقول حنأخرك الين قبل موعد المحكمه او الجلسه الاولي كذا بس عشان اشتكيت.
السؤال؟
1/كم تأخذ القضيه عشان تنتقل من مكتب العمل الي المحكمه؟
2/اقدر اطالب بتعويض عن فترة المماطله. 5 شهور تقريبا وكيف احدد قيمة التعويض؟
3/اذا كلموني قبل موعد المحكمه بشيء بسيط عشان استلم الحقوق من دون التعويض اقدر ارفض؟
4/اذا وكلت محامي الشركة تتحمل تكاليفه...

**تعليمات الحد الأقصى:** احتفظ بحد أقصى 10 خدمة عالية الصلة و5 خدمة متوسطة الصلة في مجموع النتائج.

---

## نتائج الخدمات الحكومية — 63 خدمة من 5 استعلام

### [1] خدمة: وزارة الموارد البشرية والتنمية الاجتماعية - نقل الخدمات [ref:24494]
**الجهة:** وزارة الموارد البشرية والتنمية الاجتماعية
**RRF:** 0.0131

خدمة نقل الخدمات - الانتقال الوظيفي - وزارة الموارد البشرية والتنمية الاجتماعية عبر منصة قوى. خدمة إلكترونية حكومية تتيح للمستفيدين من العاملين في القطاع الخاص نقل خدماتهم الوظيفية من صاحب عمل إلى آخر بشكل فوري ومجاني دون الحاجة إلى موافقة صاحب العمل الحالي أو انتظار فترة الإشعار. تندرج الخدمة ضمن فئة خدمات سوق العمل والعلاقات الوظيفية. مرتبطة بـ: منصة قوى، نقل كفالة، علاقة عمل جديدة، سجل العاملين، التنقل بين الوظائف، نظام العمل، بوابة الخدمات العمالية.

**الرابط:** https://sso.qiwa.sa/ar/sign-in
---

### [2] خدمة: وزارة العدل - الحاسبة العمالية [ref:48681]
**الجهة:** وزارة العدل
**RRF:** 0.0131

خدمة الحاسبة العمالية - حساب مستحقات نهاية الخدمة - وزارة العدل السعودية عبر منصة ناجز. خدمة إلكترونية حكومية تتيح للمستفيدين من أصحاب العمل والعمال في القطاع الخاص حساب الحقوق المالية المستحقة قانونياً مثل مكافأة نهاية الخدمة والتعويضات والإجازات بشكل فوري وآلي وفق نظام العمل. تندرج الخدمة ضمن فئة الخدمات العدلية والقانونية وحماية الحقوق العمالية. مرتبطة بـ: نهاية الخدمة، مكافأة نهاية الخدمة، نظام العمل السعودي، تعويض الفصل، إجازة سنوية، أجر أساسي، منصة ناجز، وزارة العدل.

**الرابط:** https://www.moj.gov.sa/ar/eServices/Pages/ServiceDetailsNew.aspx?itemId=299
---

### [3] خدمة: وزارة الموارد البشرية والتنمية الاجتماعية - انهاء العلاقة التعاقدية [ref:217663]
**الجهة:** وزارة الموارد البشرية والتنمية الاجتماعية
**RRF:** 0.0131

خدمة إنهاء العلاقة التعاقدية - فسخ العقد الوظيفي - وزارة الموارد البشرية والتنمية الاجتماعية عبر منصة قوى. خدمة إلكترونية حكومية تتيح للمستفيدين من الموظفين في القطاع الخاص إنهاء عقودهم الوظيفية المسجلة إلكترونياً مع أصحاب العمل بشكل فوري ومجاني عند انطباق الشروط، دون الحاجة لزيارة مقر العمل أو تقديم مستندات ورقية. تندرج الخدمة ضمن فئة علاقات العمل وإدارة العقود في القطاع الخاص. مرتبطة بـ: فسخ العقد، الاستقالة الإلكترونية، منصة قوى، نهاية الخدمة، مستحقات نهاية الخدمة، تسوية مالية، علاقة عمل، وزارة الموارد البشرية.

**الرابط:** https://hrsd.gov.sa/node/5573760
---

### [4] خدمة: المؤسسة العامة للتأمينات الاجتماعية - إعادة مبلغ المنفعة التأمينية المقطوعة [ref:237038]
**الجهة:** المؤسسة العامة للتأمينات الاجتماعية
**RRF:** 0.0131

خدمة إعادة مبلغ المنفعة التأمينية المقطوعة - استرداد مكافأة نهاية الخدمة السابقة - المؤسسة العامة للتأمينات الاجتماعية عبر بوابتها الإلكترونية. خدمة إلكترونية حكومية تتيح للمستفيدين من الموظفين الحكوميين السعوديين العائدين للعمل إعادة احتساب خدمتهم السابقة التي تعوضوا عنها، واسترداد مبلغ المكافأة المقطوعة لصالح حسابهم التقاعدي الجديد. تندرج الخدمة ضمن فئة الخدمات التأمينية والمعاشات التقاعدية. مرتبطة بـ: التقاعد المدني، التقاعد العسكري، مكافأة نهاية الخدمة، دمج المدد، اشتراكات التقاعد، عودة موظف حكومي، بوابة التأمينات الاجتماعية.

**الرابط:** https://www.gosi.gov.sa/ar/IndividualsServices/ServiceDetails/GovernmentServiceCombine
---

### [5] خدمة: المؤسسة العامة للتأمينات الاجتماعية - طلب تعويض تكاليف العلاج [ref:23909]
**الجهة:** المؤسسة العامة للتأمينات الاجتماعية
**RRF:** 0.0131

خدمة طلب تعويض تكاليف العلاج - مطالبة تعويض علاج إصابة عمل - المؤسسة العامة للتأمينات الاجتماعية عبر بوابتها الإلكترونية. خدمة إلكترونية حكومية تتيح للمستفيدين من أصحاب العمل المسجلين في التأمينات الاجتماعية تقديم مطالبة إلكترونية لاسترداد تكاليف علاج مشترك تعرض لإصابة عمل معتمدة، دون الحاجة للمراجعة الشخصية. تندرج الخدمة ضمن فئة خدمات التأمينات الاجتماعية وإصابات العمل. مرتبطة بـ: تعويض علاج، إصابة عمل، خطأ مهني، صاحب العمل، المنشأة، المشترك، فواتير العلاج، المستشفيات المعتمدة، نظام التأمينات.

**الرابط:** https://www.gosi.gov.sa/ar/BusinessServices/ServiceDetails/requesting_compensation_for_treatment_costs
---

### [6] خدمة: وزارة العدل - التحقق من إخلاء طرف [ref:252061]
**الجهة:** وزارة العدل
**RRF:** 0.0129

خدمة التحقق من إخلاء طرف - التأكد من صحة وثيقة إنهاء علاقة عمل - وزارة العدل عبر منصة ناجز. خدمة إلكترونية حكومية تتيح للمستفيدين من أصحاب العمل أو الجهات المعنية التحقق من صحة وثائق إخلاء الطرف الصادرة للموظفين السابقين بشكل فوري ومجاني لضمان موثوقية المعلومات. تندرج الخدمة ضمن فئة الخدمات العدلية والتحقق من الوثائق الرسمية. مرتبطة بـ: ناجز، إنهاء عقد العمل، شهادة الخدمة، توكلنا، علاقة عمل سابقة، التحقق الإلكتروني، وثيقة رسمية، وزارة الموارد البشرية والتنمية الاجتماعية.

**الرابط:** https://www.moj.gov.sa/ar/eServices/Pages/ServiceDetailsNew.aspx?itemId=301
---

### [7] خدمة: وزارة الموارد البشرية والتنمية الاجتماعية - طلب مكافئة نهاية الاحتضان [ref:20054]
**الجهة:** وزارة الموارد البشرية والتنمية الاجتماعية
**RRF:** 0.0129

خدمة طلب مكافأة نهاية الاحتضان - الحصول على مكافأة نهاية الرعاية - وزارة الموارد البشرية والتنمية الاجتماعية عبر بوابتها الإلكترونية. خدمة إلكترونية حكومية تتيح للمستفيدين من الأسر الحاضنة المسجلة لدى الوزارة تقديم طلب للحصول على مكافأة مالية لمرة واحدة بعد انتهاء فترة رعايتها لليتيم بشكل إلكتروني دون زيارة الفروع. تندرج الخدمة ضمن فئة الرعاية الاجتماعية وبرامج الدعم للأسر. مرتبطة بـ: الأسر الحاضنة، كفالة اليتيم، برنامج الأسر الحاضنة، الرعاية اللاحقة، الشؤون الاجتماعية، الدعم المالي للأيتام، انتهاء الحضانة.

**الرابط:** https://www.hrsd.gov.sa/ministry-services/services/515280
---

### [8] خدمة: وزارة المالية - خدمة تعويض المنشآت عن الزيادة في المقابل المالي [ref:23972]
**الجهة:** وزارة المالية
**RRF:** 0.0129

خدمة تعويض المنشآت عن الزيادة في المقابل المالي - طلب تعويض رسوم العمالة - وزارة المالية عبر منصة اعتماد. خدمة إلكترونية حكومية تتيح للمستفيدين من المنشآت المتعاقدة مع جهات حكومية التقدم بطلب تعويض مالي عن الزيادة في رسوم العمالة الوافدة الناتجة عن تطبيق برنامج التوازن المالي، دون الحاجة لمراجعة مقر الوزارة. تندرج الخدمة ضمن فئة الدعم المالي والتمويلي للقطاع الخاص والمقاولين. مرتبطة بـ: برنامج التوازن المالي، رسوم العمالة الوافدة، المقابل المالي، العقود الحكومية، منصة اعتماد، النفاذ الوطني الموحد، وزارة المالية السعودية، تعويض المقاولين.

**الرابط:** https://www.mof.gov.sa/eservices/Pages/ServiceDetails.aspx?ItemID=30
---

### [9] خدمة: وزارة العدل - طلب انتقال محامٍ متدرب من محامٍ إلى آخر [ref:19232]
**الجهة:** وزارة العدل
**RRF:** 0.0127

خدمة طلب انتقال محامٍ متدرب من محامٍ إلى آخر - نقل ترخيص التدريب - وزارة العدل عبر منصة ناجز. خدمة إلكترونية حكومية تتيح للمستفيدين من المحامين المتدربين المسجلين تقديم طلب للانتقال من مكتب محامٍ مرخص إلى مكتب آخر إلكترونياً، مما يسهل إجراءات تغيير جهة التدريب دون زيارة الإدارة العامة للمحاماة. تندرج الخدمة ضمن فئة التراخيص المهنية والخدمات القانونية. مرتبطة بـ: التدريب العملي للمحاماة، الإدارة العامة للمحاماة، عقد التدريب، إخلاء طرف، مزاولة مهنة المحاماة، الترخيص المؤقت، ناجز، النفاذ الوطني الموحد.

**الرابط:** https://www.moj.gov.sa/ar/eServices/Pages/ServiceDetailsNew.aspx?itemId=353
---

### [10] خدمة: وزارة العدل - التكاليف القضائية [ref:19691]
**الجهة:** وزارة العدل
**RRF:** 0.0127

خدمة التكاليف القضائية - الاستعلام عن الفواتير والسندات القضائية - وزارة العدل السعودية عبر بوابة ناجز وتطبيق توكلنا. خدمة إلكترونية حكومية تتيح للمستفيدين من أطراف الدعوى القضائية الاطلاع على سندات وفواتير التكاليف القضائية سواء المسددة أو غير المسددة وتقديم اعتراض إلكتروني عليها. تندرج الخدمة ضمن فئة الخدمات القضائية والإجراءات القانونية. مرتبطة بـ: نفاذ، ناجز، توكلنا، فاتورة قضائية، سند قبض، اعتراض على تكاليف، دعوى، محكمة، نظام التكاليف القضائية.

**الرابط:** https://www.moj.gov.sa/ar/eServices/Pages/ServiceDetailsNew.aspx?itemId=372
---

### [11] خدمة: المؤسسة العامة للتأمينات الاجتماعية - صرف مستحقات العجز المهني [ref:25865]
**الجهة:** المؤسسة العامة للتأمينات الاجتماعية
**RRF:** 0.0125

خدمة صرف مستحقات العجز المهني - تعويض العجز الوظيفي - المؤسسة العامة للتأمينات الاجتماعية عبر بوابتها الإلكترونية. خدمة إلكترونية حكومية تتيح للمستفيدين من المشتركين المصابين بعجز مهني الحصول على مستحقاتهم المالية تلقائياً واستباقياً بعد ثبوت الإصابة، دون الحاجة لتقديم طلب. تندرج الخدمة ضمن فئة المزايا التأمينية والتعويضات للمشتركين. مرتبطة بـ: تعويض إصابة عمل، قرار اللجنة الطبية، العجز الكلي أو الجزئي، المعاش التأميني، الخدمات الاستباقية، بوابة التأمينات الاجتماعية، نظام التأمينات.

**الرابط:** https://www.gosi.gov.sa/ar/IndividualsServices/ServiceDetails/Occupational_Disability_Benefit
---

### [12] خدمة: وزارة الموارد البشرية والتنمية الاجتماعية - الحصول على مكافأة نهاية الحضانة [ref:18500]
**الجهة:** وزارة الموارد البشرية والتنمية الاجتماعية
**RRF:** 0.0125

خدمة الحصول على مكافأة نهاية الحضانة - طلب مكافأة انتهاء كفالة - وزارة الموارد البشرية والتنمية الاجتماعية عبر بوابتها الإلكترونية. خدمة إلكترونية حكومية تتيح للمستفيدين من الأسر الحاضنة المسجلة لدى الوزارة التقديم إلكترونياً للحصول على الدعم المالي المخصص عند انتهاء فترة حضانة ورعاية اليتيم. تندرج الخدمة ضمن فئة الرعاية الاجتماعية والدعم الأسري. مرتبطة بـ: رعاية الأيتام، الأسرة الحاضنة، كفالة اليتيم، الدعم المالي الاجتماعي، شروط الحضانة، البحث الاجتماعي، بوابة وزارة الموارد البشرية.

**الرابط:** https://www.hrsd.gov.sa/ministry-services/services/%D9%85%D9%83%D8%A7%D9%81%D8%A3%D8%A9-%D9%86%D9%87%D8%A7%D9%8A%D8%A9-%D8%A7%D9%84%D8%AD%D8%B6%D8%A7%D9%86%D8%A9-%D9%84%D9%84%D8%A3%D8%B3%D8%B1-%D8%A7%D9%84%D8%AD%D8%A7%D8%B6%D9%86%D8%A9
---

### [13] خدمة: المؤسسة العامة للتأمينات الاجتماعية - صرف مستحقات التعطل عن العمل [ref:23597]
**الجهة:** المؤسسة العامة للتأمينات الاجتماعية
**RRF:** 0.0125

خدمة صرف مستحقات التعطل عن العمل - منفعة ساند - المؤسسة العامة للتأمينات الاجتماعية عبر بوابتها الإلكترونية وتطبيق تأميناتي. خدمة إلكترونية حكومية تتيح للمستفيدين من المواطنين السعوديين المشتركين في التأمينات الاجتماعية والذين فقدوا عملهم تقديم طلب للحصول على دعم مالي مؤقت (منفعة التعطل) بشكل إلكتروني دون زيارة المكاتب. تندرج الخدمة ضمن فئة المنافع التأمينية والدعم الاجتماعي. مرتبطة بـ: تأمين التعطل، برنامج ساند، صندوق الموارد البشرية، البحث عن عمل، فقدان العمل، شروط استحقاق ساند، الإعانة المالية، الاشتراكات التأمينية.

**الرابط:** https://www.gosi.gov.sa/ar/IndividualsServices/ServiceDetails/Request_Unemployment_Insurance_Benefits
---

### [14] خدمة: وزارة العدل - طلب الخبرة [ref:18473]
**الجهة:** وزارة العدل
**RRF:** 0.0125

خدمة طلب الخبرة - ندب خبير قضائي - وزارة العدل السعودية عبر منصة خبرة. خدمة إلكترونية حكومية تتيح للمستفيدين من أطراف الدعوى الاطلاع على عروض الخبراء ومراجعة التقارير النهائية للخبرة في القضايا التي قررت الدائرة القضائية ندب خبير فيها، مما يضمن الشفافية ويسرع الإجراءات. تندرج الخدمة ضمن فئة الخدمات القضائية والإجراءات القانونية. مرتبطة بـ: منصة تقاضي، الخبير المعتمد، تقرير الخبرة، الدائرة القضائية، أتعاب الخبير، إثبات الدعوى، البينات، نظام المرافعات.

**الرابط:** https://www.moj.gov.sa/ar/eServices/Pages/ServiceDetailsNew.aspx?itemId=466
---

### [15] خدمة: وزارة الموارد البشرية والتنمية الاجتماعية - متابعة وتوثيق الإجراءات و الوقوعات الوظيفية آلياً... [ref:20063]
**الجهة:** وزارة الموارد البشرية والتنمية الاجتماعية
**RRF:** 0.0123

خدمة متابعة وتوثيق الإجراءات والوقوعات الوظيفية آلياً - تسجيل الحوادث الوظيفية - وزارة الموارد البشرية والتنمية الاجتماعية عبر بوابتها الإلكترونية. خدمة إلكترونية حكومية تتيح للمستفيدين من منسقي الجهات الحكومية توثيق ومتابعة الحوادث والإجراءات المتعلقة بموظفي القطاع الحكومي (مثل الإصابات والإجازات الخاصة) إلكترونياً بشكل مركزي. تندرج الخدمة ضمن فئة إدارة الموارد البشرية والأنظمة الوظيفية في القطاع العام. مرتبطة بـ: توثيق وقوعات، إجراءات وظيفية، منسق جهة حكومية، سجل موظف، إصابة عمل، إجازة طارئة، نظام الموارد البشرية، البوابة الموحدة.

**الرابط:** https://www.hrsd.gov.sa/ministry-services/services/549594
---

### [16] خدمة: وزارة الموارد البشرية والتنمية الاجتماعية - طلب شهادة وظيفية [ref:19310]
**الجهة:** وزارة الموارد البشرية والتنمية الاجتماعية
**RRF:** 0.0123

خدمة طلب شهادة وظيفية - استخراج شهادة مهنية - وزارة الموارد البشرية والتنمية الاجتماعية عبر منصة قوى أفراد. خدمة إلكترونية حكومية تتيح للمستفيدين من العاملين في القطاع الخاص طلب ومراجعة شهاداتهم المهنية المتعلقة بالراتب ومدة الخدمة إلكترونياً وبشكل فوري ومجاني. تندرج الخدمة ضمن فئة التوثيق الوظيفي وخدمات العاملين. مرتبطة بـ: شهادة راتب، شهادة خدمة، مسيرة مهنية، منصة قوى، التوظيف في القطاع الخاص، سجل العامل، وزارة الموارد البشرية.

**الرابط:** https://sso.qiwa.sa/ar/sign-in
---

### [17] خدمة: المؤسسة العامة للتأمينات الاجتماعية - إعادة مبلغ المنفعة التأمينية المقطوعة [ref:23363]
**الجهة:** المؤسسة العامة للتأمينات الاجتماعية
**RRF:** 0.0123

خدمة إعادة مبلغ المنفعة التأمينية المقطوعة - استرداد مبلغ التعويض - المؤسسة العامة للتأمينات الاجتماعية عبر بوابتها الإلكترونية وتطبيق تأميناتي. خدمة إلكترونية حكومية تتيح للمستفيدين من الموظفين الحكوميين السعوديين العائدين للعمل تقديم طلب لضم خدمتهم السابقة واسترداد مبلغ التعويض الذي سبق صرفه لهم، وذلك لضمان استمرارية مدة اشتراكهم في نظام التقاعد. تندرج الخدمة ضمن فئة الخدمات التأمينية والمعاشات التقاعدية. مرتبطة بـ: ضم الخدمات، نظام التقاعد المدني، نظام التقاعد العسكري، تعويض نهاية الخدمة، استحقاق المعاش، مدة الاشتراك، بوابة التأمينات، تطبيق توكلنا.

**الرابط:** https://www.gosi.gov.sa/en/IndividualsServices/ServiceDetails/GovernmentServiceCombine
---

### [18] خدمة: إصدار بيان خدمة للتقاعد [ref:18512]
**الجهة:** وزارة الموارد البشرية والتنمية الاجتماعية
**RRF:** 0.0123

خدمة إصدار بيان خدمة للتقاعد - استخراج كشف الخدمة المحتسبة للتقاعد - وزارة الموارد البشرية والتنمية الاجتماعية عبر بوابتها الإلكترونية. خدمة إلكترونية حكومية تتيح للمستفيدين من الموظفين الحكوميين السابقين (كبار السن) الذين انتهت خدماتهم التحقق من بياناتهم وإصدار بيان الخدمة المحتسبة وإرساله إلكترونياً إلى المؤسسة العامة للتقاعد لاستكمال صرف مستحقاتهم التقاعدية. تندرج الخدمة ضمن فئة المعاشات والتقاعد والمستحقات المالية للموظفين. مرتبطة بـ: المؤسسة العامة للتقاعد، نهاية الخدمة، مستحقات تقاعدية، كشف الخدمة، الموظفون الحكوميون السابقون، المعاش التقاعدي، شروط صرف التقاعد.

**الرابط:** https://www.hrsd.gov.sa/ministry-services/services/549090
---

### [19] خدمة: وزارة الموارد البشرية والتنمية الاجتماعية - تسوية المخالفة الأولى مقابل الالتزام [ref:18482]
**الجهة:** وزارة الموارد البشرية والتنمية الاجتماعية
**RRF:** 0.0123

خدمة تسوية المخالفة الأولى مقابل الالتزام - إعفاء من غرامة العمل - وزارة الموارد البشرية والتنمية الاجتماعية عبر بوابتها الإلكترونية. خدمة إلكترونية حكومية تتيح للمستفيدين من أصحاب المنشآت في القطاع الخاص تقديم طلب لتخفيض قيمة مخالفات العمل المرصودة في الزيارة الأولى بعد إزالة المخالفة وتصحيح الوضع، دون الحاجة لمراجعة المكاتب. تندرج الخدمة ضمن فئة الامتثال التنظيمي والعلاقات العمالية. مرتبطة بـ: مخالفات العمل، نطاق المنشأة الأخضر، القرار الإداري، غرامات العمل، تصحيح المخالفة، الاعتراض على مخالفة، جدول مخالفات نظام العمل.

**الرابط:** https://www.hrsd.gov.sa/ministry-services/services/833337
---

### [20] خدمة: وزارة الموارد البشرية والتنمية الاجتماعية - نقل خدمات العمالة المنزلية من منشأة إلى فرد [ref:20036]
**الجهة:** وزارة الموارد البشرية والتنمية الاجتماعية
**RRF:** 0.0121

خدمة نقل خدمات العمالة المنزلية من منشأة إلى فرد - تحويل عامل منزلي - وزارة الموارد البشرية والتنمية الاجتماعية عبر منصة مساند. خدمة إلكترونية حكومية تتيح للمستفيدين من مكاتب الاستقدام المؤهلة نقل خدمات العاملين المنزليين (كالسائقين والخادمات) من منشأة إلى صاحب عمل فرد جديد بشكل إلكتروني، مما يسهل عملية النقل ويقلل الإجراءات الورقية. تندرج الخدمة ضمن فئة خدمات العمالة المنزلية وإدارة القوى العاملة. مرتبطة بـ: استقدام، عامل منزلي، مكتب استقدام، إقامة عامل، منصة مساند، وزارة الموارد البشرية، نقل كفالة، خدمات القطاع المنزلي، شروط الاستقدام.

**الرابط:** https://dll.musaned.com.sa/
---

### [21] خدمة: المؤسسة العامة للتأمينات الاجتماعية - صرف مستحقات الدفعة الواحدة [ref:23654]
**الجهة:** المؤسسة العامة للتأمينات الاجتماعية
**RRF:** 0.0121

خدمة صرف مستحقات الدفعة الواحدة - استحقاق مبلغ التقاعد لمرة واحدة - المؤسسة العامة للتأمينات الاجتماعية عبر بوابتها الإلكترونية. خدمة إلكترونية حكومية تتيح للمستفيدين من المشتركين السعوديين أو ذويهم طلب صرف المبلغ الإجمالي المستحق مرة واحدة عند عدم استيفاء شروط الحصول على معاش تقاعدي شهري، بشكل إلكتروني دون زيارة المكاتب. تندرج الخدمة ضمن فئة المزايا والمعاشات التأمينية. مرتبطة بـ: نهاية الخدمة، استحقاق التقاعد، مكافأة نهاية الخدمة، نظام التأمينات الاجتماعية، المشتغلين بالأعمال الشاقة، سحب الاشتراكات، بوابة التأمينات.

**الرابط:** https://www.gosi.gov.sa/ar/IndividualsServices/ServiceDetails/LumpsumBbenefits_Payment
---

### [22] خدمة: المؤسسة العامة للتأمينات الاجتماعية - استئناف صرف مستحقات التعطل عن العمل [ref:23558]
**الجهة:** المؤسسة العامة للتأمينات الاجتماعية
**RRF:** 0.0121

خدمة استئناف صرف مستحقات التعطل عن العمل - إعادة تفعيل منفعة ساند - المؤسسة العامة للتأمينات الاجتماعية عبر بوابتها الإلكترونية. خدمة إلكترونية حكومية تتيح للمستفيدين من المشتركين في نظام التأمينات الاجتماعية الذين توقفت مستحقاتهم من برنامج ساند تقديم طلب إلكتروني لاستئناف صرف منفعة التعطل عن العمل وفق شروط محددة. تندرج الخدمة ضمن فئة المنافع التأمينية والدعم الاجتماعي. مرتبطة بـ: برنامج ساند، تعطل عن العمل، إعانة بطالة، شروط استحقاق ساند، بوابة التأمينات، المشتركين السعوديين، إعادة التقديم، دعم الدخل.

**الرابط:** https://gosi.gov.sa/ar/IndividualsServices/ServiceDetails/Resumption_of_unemployment_compensation_payments
---

### [23] خدمة: وزارة الموارد البشرية والتنمية الاجتماعية - طلب نقل العمالة الوافدة [ref:18503]
**الجهة:** وزارة الموارد البشرية والتنمية الاجتماعية
**RRF:** 0.0119

خدمة طلب نقل العمالة الوافدة - تحويل كفالة عامل - وزارة الموارد البشرية والتنمية الاجتماعية عبر بوابتها الإلكترونية. خدمة إلكترونية حكومية تتيح للمستفيدين من المنشآت في القطاع الخاص تقديم طلب لنقل خدمة عامل مقيم (نقل كفالته) إليها من صاحب عمل آخر أو بين فروعها المسجلة بنفس الرقم الموحد بشكل فوري ومجاني. تندرج الخدمة ضمن فئة خدمات العمالة والكفالات وإدارة القوى العاملة. مرتبطة بـ: نقل كفالة، العمالة الوافدة، تصريح العمل، الإقامة، السجل التجاري، رخصة العمل، حماية الأجور، نطاق المنشأة، الرقم الموحد، بوابة مساند.

**الرابط:** https://www.hrsd.gov.sa/ministry-services/services/employee-transfer
---

### [24] خدمة: وزارة العدل - المتدربون لدي (للمحامي): إخلاء طرف محامي متدرب [ref:23774]
**الجهة:** وزارة العدل
**RRF:** 0.0119

خدمة المتدربون لدي (للمحامي): إخلاء طرف محامي متدرب - إنهاء تدريب المحامي - وزارة العدل عبر منصة ناجز. خدمة إلكترونية حكومية تتيح للمستفيدين من المحامين المرخصين في المملكة إنهاء تسجيل وإخلاء طرف المحامي المتدرب المسجل في سجلاتهم بشكل إلكتروني وفوري دون مراجعة الإدارة العامة للمحاماة. تندرج الخدمة ضمن فئة التراخيص المهنية والخدمات القانونية. مرتبطة بـ: ترخيص المحاماة، التدريب العملي للمحامي، الإدارة العامة للمحاماة، سجل المحامين، ناجز، النفاذ الوطني الموحد، إنهاء عقد التدريب.

**الرابط:** https://www.moj.gov.sa/ar/eServices/Pages/ServiceDetailsNew.aspx?itemId=346
---

### [25] خدمة: وزارة العدل - طلب إصدار قرار استقطاع [ref:19532]
**الجهة:** وزارة العدل
**RRF:** 0.0119

خدمة طلب إصدار قرار استقطاع - خطاب استقطاع النفقة - وزارة العدل عبر منصة ناجز. خدمة إلكترونية حكومية تتيح للمستفيدين من أصحاب الأحكام (طالبي التنفيذ) في قضايا النفقة المستقبلية طلب إصدار قرار لاستقطاع المبلغ المستحق مباشرة من راتب المدين عليه لضمان تنفيذ السند التنفيذي. تندرج الخدمة ضمن فئة الخدمات القضائية والتنفيذية. مرتبطة بـ: نفقة مستقبلية، سند تنفيذي، محكمة التنفيذ، استقطاع من الراتب، المنفذ ضده، حكم النفقة، بوابة ناجز، النفاذ الوطني الموحد.

**الرابط:** https://www.moj.gov.sa/ar/eServices/Pages/ServiceDetailsNew.aspx?itemId=309
---

### [26] خدمة: وزارة الموارد البشرية والتنمية الاجتماعية - نقل خدمات العمالة المنزلية من فرد إلى فرد [ref:20039]
**الجهة:** وزارة الموارد البشرية والتنمية الاجتماعية
**RRF:** 0.0118

خدمة نقل خدمات العمالة المنزلية من فرد إلى فرد - تحويل عامل منزلي - وزارة الموارد البشرية والتنمية الاجتماعية عبر منصة مساند. خدمة إلكترونية حكومية تتيح للمستفيدين من أصحاب العمل الحاليين والجديدين (أفراد) نقل خدمات العامل المنزلي وإجراءات إقامته بينهم بشكل إلكتروني كامل دون مراجعة الفروع. تندرج الخدمة ضمن فئة خدمات العمالة المنزلية وإدارة الإقامات. مرتبطة بـ: عامل منزلي، كفالة فرد، نقل كفالة، إقامة عامل، منصة مساند، تأشيرة عمل، شروط الاستقدام، رسوم نقل الخدمة.

**الرابط:** https://musaned.com.sa/auth/login
---

### [27] خدمة: الهيئة العامة للمنافسة - تقديم طلب تسوية [ref:241466]
**الجهة:** الهيئة العامة للمنافسة
**RRF:** 0.0118

خدمة تقديم طلب تسوية - طلب المصالحة المالية - الهيئة العامة للمنافسة عبر بوابتها الإلكترونية. خدمة إلكترونية حكومية تتيح للمستفيدين من المنشآت المخالفة لنظام المنافسة تقديم طلب إلكتروني لقبول تسوية مالية مع الهيئة كبديل عن الإحالة إلى المسار القضائي. تندرج الخدمة ضمن فئة الامتثال التنظيمي وتسوية المنازعات التجارية. مرتبطة بـ: مخالفة المنافسة، نظام المنافسة، الغرامات المالية، الامتثال القانوني، المنشآت التجارية، النفاذ الوطني الموحد، المسار القضائي، الهيئة العامة للمنافسة.

**الرابط:** https://gac.gov.sa/#/page/settlement-services/details/4ed257e3-4750-4733-8dc7-925cad2a3dfd
---

### [28] خدمة: وزارة العدل - طلب إعادة قيد محامٍ [ref:100037]
**الجهة:** وزارة العدل
**RRF:** 0.0118

خدمة طلب إعادة قيد محامٍ - تجديد ترخيص المحاماة - وزارة العدل السعودية عبر منصة ناجز. خدمة إلكترونية حكومية تتيح للمستفيدين من المحامين السعوديين المشطوب قيدهم تقديم طلب إلكتروني لإعادة قيدهم في سجل المحامين واستعادة ترخيص ممارسة المهنة دون مراجعة الإدارة العامة للمحاماة. تندرج الخدمة ضمن فئة التراخيص المهنية والقانونية. مرتبطة بـ: ترخيص المحاماة، سجل المحامين، شطب القيد، ممارسة المحاماة، النفاذ الوطني الموحد، رسوم إعادة القيد، الإدارة العامة للمحاماة، مزاولة المهنة.

**الرابط:** https://www.moj.gov.sa/ar/eServices/Pages/ServiceDetailsNew.aspx?itemId=345
---

### [29] خدمة: وزارة المالية - صرف العوائد السنوية [ref:23846]
**الجهة:** وزارة المالية
**RRF:** 0.0118

خدمة صرف العوائد السنوية - استلام المكافآت المالية الدورية - وزارة المالية عبر بوابتها الإلكترونية وتطبيق توكلنا. خدمة إلكترونية حكومية تتيح للمستفيدين من المواطنين السعوديين المستحقين أو ورثتهم الشرعيين الاستعلام عن مستحقاتهم المالية السنوية وصرفها إلكترونياً بعد التحقق الآلي من الأهلية. تندرج الخدمة ضمن فئة الخدمات المالية والدعم الاجتماعي. مرتبطة بـ: العوائد المالية، المكافآت السنوية، الدعم المالي، كبار السن، الورثة الشرعيين، الاستحقاق المالي، بوابة وزارة المالية، التحقق من الأهلية، صرف المستحقات.

**الرابط:** https://eservices.mof.gov.sa/Anonymous/Pages/ReturnsInquiryUser.aspx
---

### [30] خدمة: وزارة الموارد البشرية والتنمية الاجتماعية - طلب إعتماد لائحة تنظيم عمل [ref:19730]
**الجهة:** وزارة الموارد البشرية والتنمية الاجتماعية
**RRF:** 0.0116

خدمة طلب اعتماد لائحة تنظيم عمل - الموافقة على نظام العمل الداخلي - وزارة الموارد البشرية والتنمية الاجتماعية عبر منصة قوى. خدمة إلكترونية حكومية تتيح للمستفيدين من المنشآت في القطاع الخاص تقديم طلب إلكتروني لاعتماد لائحة تنظيم العمل الخاصة بهم (نموذجية أو خاصة) بشكل فوري ومجاني دون مراجعة المكاتب. تندرج الخدمة ضمن فئة التوظيف وعلاقات العمل والامتثال التنظيمي للمنشآت. مرتبطة بـ: لائحة العمل، نظام العمل، منصة قوى، الامتثال العمالي، مكتب المحاماة المعتمد، الشروط الوظيفية، المنشآت الصغيرة، التوطين.

**الرابط:** https://www.hrsd.gov.sa/ministry-services/services/972477
---

### [31] خدمة: وزارة المالية - رفع مسوغات صرف العوائد السنوية [ref:23906]
**الجهة:** وزارة المالية
**RRF:** 0.0116

خدمة رفع مسوغات صرف العوائد السنوية - تحميل مستندات استحقاق المكافأة - وزارة المالية عبر بوابتها الإلكترونية وتطبيق توكلنا. خدمة إلكترونية حكومية تتيح للمستفيدين من المواطنين المستحقين (كبار السن) أو ورثتهم رفع المستندات المطلوبة إلكترونياً لاستكمال إجراءات صرف العوائد السنوية دون مراجعة المقر، مع تلقي إشعارات تلقائية قبل موعد الصرف. تندرج الخدمة ضمن فئة الخدمات المالية والمساعدات الاجتماعية. مرتبطة بـ: عوائد سنوية، مكافأة، صرف استحقاق، ورثة، صك حصر الورثة، إقرار سنوي (نموذج 21)، وكالة شرعية، حساب بنكي، وزارة المالية السعودية.

**الرابط:** https://www.mof.gov.sa/eservices/Pages/ServiceDetails.aspx?ItemID=35
---

### [32] خدمة: المؤسسة العامة للتأمينات الاجتماعية - نقل مشترك بين فروع المنشأة [ref:23585]
**الجهة:** المؤسسة العامة للتأمينات الاجتماعية
**RRF:** 0.0114

خدمة نقل مشترك بين فروع المنشأة - تحويل موظف داخلي - المؤسسة العامة للتأمينات الاجتماعية عبر بوابتها الإلكترونية. خدمة إلكترونية حكومية تتيح للمستفيدين من أصحاب العمل والمنشآت في القطاع الخاص نقل موظف مشترك في التأمينات من فرع إلى آخر ضمن مجموعة منشآتهم بسلاسة، مما يحافظ على استمرارية سجله التأميني وتاريخ خدمته دون انقطاع. تندرج الخدمة ضمن فئة خدمات أصحاب العمل في نظام التأمينات الاجتماعية. مرتبطة بـ: إدارة المشتركين، تعديل بيانات المنشأة، استمرارية الاشتراك، سجل الأجور، المنشأة الأم والفروع، بوابة التأمينات لصاحب العمل.

**الرابط:** https://www.gosi.gov.sa/ar/BusinessServices/ServiceDetails/Transfer_Contributor_to_Another_Branch_
---

### [33] خدمة: المؤسسة العامة للتأمينات الاجتماعية - طلب الاستثناء من المهلة النظامية للتقديم على... [ref:23447]
**الجهة:** المؤسسة العامة للتأمينات الاجتماعية
**RRF:** 0.0114

خدمة طلب الاستثناء من المهلة النظامية للتقديم على منفعة ساند - إعفاء من مهلة طلب تعطل - المؤسسة العامة للتأمينات الاجتماعية عبر بوابتها الإلكترونية. خدمة إلكترونية حكومية تتيح للمستفيدين من المشتركين في نظام التأمينات الاجتماعية والذين فاتتهم المهلة النظامية (90 يوماً) لتقديم طلب منفعة التعطل عن العمل (ساند) تقديم طلب استثناء من تلك المهلة بسبب ظروف طارئة مقبولة. تندرج الخدمة ضمن فئة المنافع التأمينية والتعويضات الاجتماعية. مرتبطة بـ: منفعة ساند، تعطل عن العمل، مهلة التقديم، استثناء، اعتراض، شروط الاستحقاق، تعويض البطالة، بوابة التأمينات الاجتماعية.

**الرابط:** https://www.gosi.gov.sa/ar/IndividualsServices/ServiceDetails/RequestExcemption
---

### [34] خدمة: وزارة العدل - خدمة طلب إنهاء طلب التنفيذ [ref:19637]
**الجهة:** وزارة العدل
**RRF:** 0.0114

خدمة طلب إنهاء طلب التنفيذ - إيقاف السند التنفيذي - وزارة العدل السعودية عبر منصة ناجز. خدمة إلكترونية حكومية تتيح للمستفيدين من طالبي التنفيذ تقديم طلب لإنهاء السند التنفيذي بسبب الصلح أو التنازل أو أي سبب آخر بشكل إلكتروني وفوري دون الحاجة لمراجعة المحكمة. تندرج الخدمة ضمن فئة الخدمات القضائية والتنفيذية. مرتبطة بـ: منصة ناجز، السند التنفيذي، طالب التنفيذ، مدين، الصلح، التنازل عن الحق، إجراءات التنفيذ، المحكمة التنفيذية، النظام العدلي.

**الرابط:** https://www.moj.gov.sa/ar/eServices/Pages/ServiceDetailsNew.aspx?itemId=322
---

### [35] خدمة: وزارة الموارد البشرية والتنمية الاجتماعية - اعتماد صرف مبالغ الخصومات [ref:19253]
**الجهة:** وزارة الموارد البشرية والتنمية الاجتماعية
**RRF:** 0.0114

خدمة اعتماد صرف مبالغ الخصومات - تسجيل وإدارة الغرامات المالية - وزارة الموارد البشرية والتنمية الاجتماعية عبر منصة قوى. خدمة إلكترونية حكومية تتيح للمستفيدين من أصحاب العمل في القطاع الخاص تسجيل وإدارة عمليات صرف الغرامات والخصومات المالية التي تفرض على موظفيهم بشكل مؤتمت بالكامل. تندرج الخدمة ضمن فئة خدمات التوظيف وإدارة علاقات العمل. مرتبطة بـ: منصة قوى، قوى أعمال، خصومات الموظفين، غرامات العمل، عقوبات تأديبية، نظام العمل السعودي، علاقات العمل، شؤون الموظفين.

**الرابط:** https://sso.qiwa.sa/ar/sign-in
---

### [36] خدمة: الديوان العام للمحاسبة - تصديق براءة الذمة المالية [ref:20099]
**الجهة:** الديوان العام للمحاسبة
**RRF:** 0.0114

خدمة تصديق براءة الذمة المالية - توثيق إخلاء الطرف المالي - الديوان العام للمحاسبة عبر منصة شمل. خدمة إلكترونية حكومية تتيح للمستفيدين من الجهات الحكومية المشمولة برقابة الديوان طلب توثيق إخلاء طرف موظفيها مالياً وإدارياً بشكل إلكتروني عبر قنوات رقمية دون مراجعة شخصية. تندرج الخدمة ضمن فئة الخدمات المالية والإدارية للموظفين الحكوميين. مرتبطة بـ: إخلاء طرف، نهاية الخدمة، التصفية المالية، لجنة الجرد، السلف المستديمة، بيان الخدمة، منصة شمل، الربط التقني GSN.

**الرابط:** https://e-audit.shamel.gov.sa/start-acquittance-request
---

### [37] خدمة: وزارة الموارد البشرية والتنمية الاجتماعية - إدارة العقود [ref:19451]
**الجهة:** وزارة الموارد البشرية والتنمية الاجتماعية
**RRF:** 0.0113

خدمة إدارة العقود - توثيق عقود العمل - وزارة الموارد البشرية والتنمية الاجتماعية عبر منصة قوى. خدمة إلكترونية حكومية تتيح للمستفيدين من المنشآت في القطاع الخاص إنشاء وتوثيق وإنهاء عقود موظفيها إلكترونياً، مع إتاحة الفرصة للموظف للمراجعة والقبول عبر حسابه الشخصي. تندرج الخدمة ضمن فئة التوظيف وعلاقات العمل في القطاع الخاص. مرتبطة بـ: عقد عمل إلكتروني، منصة قوى، علاقة عمل، سجل التوظيف، نهاية الخدمة، تعديل العقد، حقوق العامل، التزامات صاحب العمل.

**الرابط:** https://sso.qiwa.sa/ar/sign-in
---

### [38] خدمة: المؤسسة العامة للتأمينات الاجتماعية - احتساب مدة إعارة [ref:248245]
**الجهة:** المؤسسة العامة للتأمينات الاجتماعية
**RRF:** 0.0113

خدمة احتساب مدة إعارة - طلب احتساب فترة الإعارة - المؤسسة العامة للتأمينات الاجتماعية عبر بوابتها الإلكترونية. خدمة إلكترونية حكومية تتيح للمستفيدين من المشتركين المواطنين في نظام التأمينات الاطلاع على طلب الإعارة المقدم من جهة عملهم والموافقة عليه أو طلب احتساب مدته أو الاعتراض عليه إلكترونياً. تندرج الخدمة ضمن فئة الخدمات التأمينية وإدارة علاقات العمل. مرتبطة بـ: الإعارة الوظيفية، جهة العمل الأصلية، جهة العمل المعار لها، مدة الخدمة المحتسبة، المعاش التقاعدي، النفاذ الوطني الموحد، طلبات المشترك.

**الرابط:** https://www.gosi.gov.sa/ar/IndividualsServices/ServiceDetails/SecondmentRequests
---

### [39] خدمة: وزارة الموارد البشرية والتنمية الاجتماعية - اصدار تاشيرة بديلة تعويضية [ref:20030]
**الجهة:** وزارة الموارد البشرية والتنمية الاجتماعية
**RRF:** 0.0113

خدمة إصدار تأشيرة بديلة تعويضية - استبدال تأشيرة عامل - وزارة الموارد البشرية والتنمية الاجتماعية عبر بوابة مساند. خدمة إلكترونية حكومية تتيح للمستفيدين من أصحاب العمل المؤهلين إصدار تأشيرة بديلة لعامل بدون رسوم حكومية في حال خروجه النهائي من المملكة خلال تسعين يوماً من وصوله، مما يحفظ حصتهم من العمالة. تندرج الخدمة ضمن فئة خدمات العمالة والاستقدام وإدارة التأشيرات. مرتبطة بـ: تأشيرة عمل، عقد عمل، خروج نهائي، ضوابط الاستقدام، بوابة مساند، صاحب عمل، عامل وافد، مهنة مسموح بها.

**الرابط:** https://musaned.com.sa/auth/login
---

### [40] خدمة: المؤسسة العامة للتأمينات الاجتماعية - صرف مستحقات نفقة ترحيل الجثمان [ref:21785]
**الجهة:** المؤسسة العامة للتأمينات الاجتماعية
**RRF:** 0.0111

خدمة صرف مستحقات نفقة ترحيل الجثمان - تعويض نقل رفات المتوفى - المؤسسة العامة للتأمينات الاجتماعية عبر بوابتها الإلكترونية. خدمة إلكترونية حكومية تتيح للمستفيدين من أصحاب العمل المسجلين في التأمينات الاجتماعية طلب صرف المستحقات المالية المخصصة لنفقات ترحيل جثمان مشترك توفي بسبب إصابة عمل بشكل إلكتروني. تندرج الخدمة ضمن فئة التعويضات والتأمينات ضد إصابات العمل والوفيات المهنية. مرتبطة بـ: تعويض إصابة عمل، وفاة مهنية، تأمين صحي مهني، بوليصة شحن، فاتورة تحنيط، مخاطر مهنية، منشأة مسجلة.

**الرابط:** https://www.gosi.gov.sa/ar/BusinessServices/ServiceDetails/The_Deportation_Expenses_of_the_Remains_of_the_Deceased_
---

### [41] خدمة: وزارة العدل - طلب تحديث بيانات محامي [ref:40614]
**الجهة:** وزارة العدل
**RRF:** 0.0111

خدمة طلب تحديث بيانات محامي - تعديل معلومات المحامي المسجل - وزارة العدل السعودية عبر منصة ناجز. خدمة إلكترونية حكومية تتيح للمستفيدين من المحامين المسجلين في المملكة تحديث بياناتهم الشخصية والمهنية المسجلة لدى الوزارة إلكترونياً لضمان دقة السجلات دون الحاجة لمراجعة الإدارة العامة للمحاماة. تندرج الخدمة ضمن فئة التراخيص المهنية والخدمات القانونية. مرتبطة بـ: ترخيص المحاماة، السجل المهني، النفاذ الوطني الموحد، الإدارة العامة للمحاماة، بيانات العنوان الوطني، التجديد المهني، شهادة المحاماة.

**الرابط:** https://www.moj.gov.sa/ar/eServices/Pages/ServiceDetailsNew.aspx?itemId=357
---

### [42] خدمة: وزارة الموارد البشرية والتنمية الاجتماعية - تغيير المهنة [ref:19445]
**الجهة:** وزارة الموارد البشرية والتنمية الاجتماعية
**RRF:** 0.0110

خدمة تغيير المهنة - تحديث وتصحيح مهنة العامل - وزارة الموارد البشرية والتنمية الاجتماعية عبر منصة قوى. خدمة إلكترونية حكومية تتيح للمستفيدين من المنشآت في القطاع الخاص تقديم طلب إلكتروني لتعديل مهنة الموظف المقيم لديها في سجلات الوزارة لتتوافق مع طبيعة عمله الفعلية وفق الدليل السعودي للمهن. تندرج الخدمة ضمن فئة خدمات العمالة والمنشآت وإدارة علاقات العمل. مرتبطة بـ: منصة قوى، تصحيح المهن، الدليل السعودي الموحد للمهن، تصنيف النطاق البلاتيني والأخضر، نقل الخدمة، تأشيرة الخروج النهائي، شروط مزاولة المهن الهندسية والبيطرية والمحاسبية.

**الرابط:** https://www.hrsd.gov.sa/en/ministry-services/services/70102
---

### [43] خدمة: وزارة العدل - إضافة ممثل [ref:19676]
**الجهة:** وزارة العدل
**RRF:** 0.0110

خدمة إضافة ممثل - تعيين وكيل أو محامي - وزارة العدل عبر منصة ناجز. خدمة إلكترونية حكومية تتيح للمستفيدين من أطراف القضايا (مدعين أو مدعى عليهم) تعيين وكلاء أو ممثلين قانونيين في قضيتهم بشكل إلكتروني وفوري دون الحاجة لزيارة المحكمة. تندرج الخدمة ضمن فئة الخدمات القضائية والإجراءات القانونية. مرتبطة بـ: ناجز، تمثيل قانوني، وكالة سارية، قضية، محكمة، المحامي، النفاذ الوطني الموحد، إجراءات التقاضي.

**الرابط:** https://www.moj.gov.sa/ar/eServices/Pages/ServiceDetailsNew.aspx?itemId=230
---

### [44] خدمة: المؤسسة العامة للتأمينات الاجتماعية - الاستعلام عن مستحقات تأمينية [ref:21647]
**الجهة:** المؤسسة العامة للتأمينات الاجتماعية
**RRF:** 0.0110

خدمة الاستعلام عن مستحقات تأمينية - التحقق من المبالغ المستحقة - المؤسسة العامة للتأمينات الاجتماعية عبر بوابتها الإلكترونية. خدمة إلكترونية حكومية تتيح للمستفيدين من الورثة وأصحاب الأخطار المهنية والمتعطلين عن العمل التحقق بشكل فوري ومجاني من وجود مبالغ تأمينية مستحقة لهم أو الاطلاع على قائمة المستحقين. تندرج الخدمة ضمن فئة الخدمات التأمينية والاستعلامات المالية. مرتبطة بـ: تعويضات التأمينات، مستحقات الورثة، تعويض إصابة عمل، تعويض تعطل، رقم حفيظة النفوس، التحقق السريع، بوابة التأمينات الاجتماعية.

**الرابط:** https://www.gosi.gov.sa/GOSIOnline/Check_Insurance_Dues?locale=ar_SA
---

### [45] خدمة: وزارة المالية - استعلام مراجعي الإدارة العامة للمصروفات العامة... [ref:23867]
**الجهة:** وزارة المالية
**RRF:** 0.0108

خدمة استعلام مراجعي المصروفات العامة والخاصة - متابعة طلبات المساعدات والتعويضات - وزارة المالية عبر بوابتها الإلكترونية. خدمة إلكترونية حكومية تتيح للمستفيدين من الأفراد والشركات الذين تقدموا بطلبات مساعدات أو تعويضات الاستعلام عن حالة طلباتهم بشكل فوري ومجاني دون الحاجة لمراجعة مقر الوزارة. تندرج الخدمة ضمن فئة الخدمات المالية والاستعلامات الحكومية. مرتبطة بـ: الإدارة العامة للمصروفات، طلب مساعدة مالية، تعويض أضرار، متابعة معاملة، بوابة وزارة المالية، النفاذ الوطني الموحد، الخدمات المالية للأفراد.

**الرابط:** https://www.mof.gov.sa/eservices/Pages/ServiceDetails.aspx?ItemID=31
---

### [46] خدمة: ديوان المظالم - طلب اذن بداية ونهاية الدوام [ref:25319]
**الجهة:** ديوان المظالم
**RRF:** 0.0108

خدمة طلب إذن بداية ونهاية الدوام - تقديم طلب استئذان إلكتروني - ديوان المظالم عبر بوابته الداخلية وتطبيق خدماتي ديوان المظالم. خدمة إلكترونية حكومية تتيح للمستفيدين من موظفي ديوان المظالم تقديم طلب إذن رسمي للغياب عن بداية أو وسط أو نهاية الدوام الرسمي بشكل إلكتروني وفوري. تندرج الخدمة ضمن فئة الخدمات الإدارية والموارد البشرية للموظفين. مرتبطة بـ: استئذان موظف، الغياب، الحضور والانصراف، البوابة الداخلية، نظام الموارد البشرية، الخدمات الذاتية للموظفين، تطبيق توكلنا.

**الرابط:** http://erpservices.bog.gov.sa/EportalUI/excuseRequest
---

### [47] خدمة: وزارة الموارد البشرية والتنمية الاجتماعية - مخالفات المنشأة [ref:25373]
**الجهة:** وزارة الموارد البشرية والتنمية الاجتماعية
**RRF:** 0.0108

خدمة مخالفات المنشأة - الاستعلام عن مخالفات العمل - وزارة الموارد البشرية والتنمية الاجتماعية عبر منصة قوى. خدمة إلكترونية حكومية تتيح للمستفيدين من أصحاب العمل في القطاع الخاص الاستعلام عن المخالفات العمالية المسجلة على منشآتهم ومراجعتها وتقديم اعتراض إلكتروني عليها. تندرج الخدمة ضمن فئة الامتثال والرقابة على سوق العمل. مرتبطة بـ: نظام العمل، لوائح العمل، غرامات عمالية، منصة قوى أعمال، التفتيش العمالي، التزام المنشأة، تسوية المخالفات، وزارة الموارد البشرية.

**الرابط:** https://sso.qiwa.sa/ar/sign-in
---

### [48] خدمة: المؤسسة العامة للتأمينات الاجتماعية - طلب استبعاد من منشأة [ref:23441]
**الجهة:** المؤسسة العامة للتأمينات الاجتماعية
**RRF:** 0.0107

خدمة طلب استبعاد من منشأة - إنهاء علاقة عمل تأمينية - المؤسسة العامة للتأمينات الاجتماعية عبر بوابتها الإلكترونية. خدمة إلكترونية حكومية تتيح للمستفيدين من المشتركين المواطنين تقديم طلب لإنهاء علاقتهم العملية والتأمينية مع منشأة مسجلة بشكل إلكتروني عبر حسابهم الشخصي دون الحاجة لمراجعة المكاتب. تندرج الخدمة ضمن فئة إدارة العلاقات التأمينية والتوظيفية للمشتركين. مرتبطة بـ: إنهاء الاشتراك، تاريخ الاستبعاد، سبب الاستبعاد، المنشأة المسجلة، المدة النشطة، رقم الطلب، متابعة المعاملة، بوابة التأمينات الاجتماعية.

**الرابط:** https://www.gosi.gov.sa/ar/IndividualsServices/ServiceDetails/Termination_engagement
---

### [49] خدمة: ديوان المظالم - طلب استلام نسخة الحكم النهائية/ التنفيذية [ref:25115]
**الجهة:** ديوان المظالم
**RRF:** 0.0105

خدمة طلب استلام نسخة الحكم النهائية/ التنفيذية - الحصول على صورة حكم قضائي - ديوان المظالم عبر منصة معين. خدمة إلكترونية حكومية تتيح للمستفيدين من الموظفين المختصين في المحاكم الاطلاع على الطلبات المقدمة للحصول على نسخ من الأحكام واتخاذ الإجراء النظامي حيالها إلكترونياً. تندرج الخدمة ضمن فئة الخدمات القضائية والإجراءات النظامية الداخلية. مرتبطة بـ: منصة معين، الأحكام القضائية، النسخ التنفيذية، ديوان المظالم، إجراءات المحكمة، الموظف المختص، النظام القضائي السعودي.

**الرابط:** http://moan.bog.gov.sa/EcourtServices
---

### [50] خدمة: المؤسسة العامة للتأمينات الاجتماعية - اضافة مدة اشتراك بأثر رجعي [ref:24995]
**الجهة:** المؤسسة العامة للتأمينات الاجتماعية
**RRF:** 0.0105

خدمة إضافة مدة اشتراك بأثر رجعي - تسجيل اشتراك سابق - المؤسسة العامة للتأمينات الاجتماعية عبر بوابتها الإلكترونية. خدمة إلكترونية حكومية تتيح للمستفيدين من المشتركين في أنظمة التقاعد المدني أو العسكري تقديم طلب لتسجيل فترة اشتراك سابقة لم تُسجل في سجلاتهم التأمينية، لضمان اكتمال مدة خدمتهم وحقوقهم التقاعدية. تندرج الخدمة ضمن فئة المعاشات والتأمينات الاجتماعية وإدارة سجلات المشتركين. مرتبطة بـ: التقاعد المدني، التقاعد العسكري، نهاية الخدمة، مدة الاشتراك، استحقاق المعاش، تصحيح السجل التأميني، نموذج عمليات المشترك، بوابة التأمينات.

**الرابط:** https://www.gosi.gov.sa/ar/IndividualsServices/ServiceDetails/adding_backdate_Engagement
---

### [51] خدمة: المؤسسة العامة للتأمينات الاجتماعية - إعادة مبلغ المنفعة التأمينية المقطوعة [ref:182569]
**الجهة:** المؤسسة العامة للتأمينات الاجتماعية
**RRF:** 0.0104

خدمة إعادة مبلغ المنفعة التأمينية المقطوعة - ضم الخدمة الحكومية السابقة - المؤسسة العامة للتأمينات الاجتماعية عبر بوابتها الإلكترونية وتطبيق تأميناتي. خدمة إلكترونية حكومية تتيح للمستفيدين من الموظفين الحكوميين السعوديين العائدين للعمل ضم خدمتهم السابقة التي تم تعويضهم عنها وإعادة مبلغ التعويض لاحتسابها ضمن مدة اشتراكهم التأميني. تندرج الخدمة ضمن فئة المعاشات والتقاعد ودمج المدد التأمينية. مرتبطة بـ: ضم الخدمات، تعويض نهاية الخدمة، نظام التقاعد المدني، نظام التقاعد العسكري، إعادة المبالغ المقطوعة، حساب مدة الاستحقاق، بوابة التأمينات الاجتماعية.

**الرابط:** https://www.gosi.gov.sa/ar/IndividualsServices/ServiceDetails/GovernmentServiceCombine
---

### [52] خدمة: ديوان المظالم - القبول النهائي [ref:25352]
**الجهة:** ديوان المظالم
**RRF:** 0.0104

خدمة القبول النهائي للتدريب التعاوني - إتمام قبول المتدربين - ديوان المظالم عبر البوابة الوطنية للتدريب (منصة خبير). خدمة إلكترونية حكومية تتيح للمستفيدين من مختصي إدارة التدريب في الجهات الحكومية إجراء القبول النهائي وإتمام إجراءات طلبات التدريب التعاوني المقدمة من المتدربين بشكل فوري وإلكتروني. تندرج الخدمة ضمن فئة التدريب والتطوير الوظيفي في القطاع الحكومي. مرتبطة بـ: التدريب التعاوني، منصة خبير، البوابة الوطنية للتدريب، مختص التدريب، القبول المبدئي، المتدربين، البرامج التدريبية، التوظيف.

**الرابط:** https://khabeer.bog.gov.sa/CoTraining/Admin/Default.aspx
---

### [53] خدمة: ديوان المظالم - إضافة ممثل على دعوى [ref:20717]
**الجهة:** ديوان المظالم
**RRF:** 0.0104

خدمة إضافة ممثل على دعوى - تفويض وكيل قضائي - ديوان المظالم عبر بوابته الإلكترونية ومنصة معين. خدمة إلكترونية حكومية تتيح للمستفيدين من أطراف الدعاوى القضائية تقديم طلب إضافة ممثل قانوني أو وكيل للنيابة عنهم في إجراءات الدعوى بشكل إلكتروني دون الحاجة لمراجعة المقر. تندرج الخدمة ضمن فئة الخدمات القضائية والإجراءات القانونية. مرتبطة بـ: منصة معين، التوكيل القضائي، الممثل القانوني، رقم الدعوى، المحكمة الإدارية، الخدمات القضائية للمسجلين، بوابة ديوان المظالم.

**الرابط:** http://moan.bog.gov.sa/JSPortal#/Login
---

### [54] خدمة: المؤسسة العامة للتأمينات الاجتماعية - بيانات الخدمة [ref:237521]
**الجهة:** المؤسسة العامة للتأمينات الاجتماعية
**RRF:** 0.0104

خدمة بيانات الخدمة - استعلام بيانات المعاش التقاعدي - المؤسسة العامة للتأمينات الاجتماعية عبر بوابتها الإلكترونية. خدمة إلكترونية حكومية تتيح للمستفيدين من المتقاعدين والمشتركين ذوي الخدمة المضمومة والمستفيدين من المعاش الاطلاع على بياناتهم المخصصة لنظام المعاش مثل مدة الخدمة والمستحقات بشكل فوري ومجاني. تندرج الخدمة ضمن فئة الخدمات التأمينية والاجتماعية للمتقاعدين والمستفيدين. مرتبطة بـ: المعاش التقاعدي، مدة الاشتراك، المستحقات المالية، الخدمة المضمومة، قائم بالصرف، بوابة التأمينات الاجتماعية، استعلام المعاش.

**الرابط:** https://www.gosi.gov.sa/ar/IndividualsServices/ServiceDetails/ServiceData
---

### [55] خدمة: وزارة الموارد البشرية والتنمية الاجتماعية - رصد بلاغات مخالفات نظام العمل [ref:182775]
**الجهة:** وزارة الموارد البشرية والتنمية الاجتماعية
**RRF:** 0.0103

خدمة رصد بلاغات مخالفات نظام العمل - الإبلاغ عن انتهاكات العمل - وزارة الموارد البشرية والتنمية الاجتماعية عبر بوابتها الإلكترونية. خدمة إلكترونية حكومية تتيح للمستفيدين من جميع الأفراد في المملكة التبليغ عن مخالفات نظام العمل في القطاع الخاص ومتابعة حالة البلاغ إلكترونياً للمساهمة في خلق بيئة عمل عادلة. تندرج الخدمة ضمن فئة حماية حقوق العمال ومراقبة سوق العمل. مرتبطة بـ: شكوى عمل، حقوق العامل، التفتيش الميداني، قانون العمل، عقد العمل، منشأة مخالفة، منصة أبشر، وزارة الموارد البشرية.

**الرابط:** https://www.hrsd.gov.sa/ministry-services/services/1155111
---

### [56] خدمة: المؤسسة العامة للتأمينات الاجتماعية - إعادة مبلغ المنفعة التأمينية المقطوعة [ref:23456]
**الجهة:** المؤسسة العامة للتأمينات الاجتماعية
**RRF:** 0.0103

خدمة إعادة مبلغ المنفعة التأمينية المقطوعة - استرداد دفعة المعاش - المؤسسة العامة للتأمينات الاجتماعية عبر بوابتها الإلكترونية. خدمة إلكترونية حكومية تتيح للمستفيدين من المتقاعدين والحاصلين على منفعة مقطوعة إعادة المبلغ الذي تم صرفه لهم بعد التحاقهم بعمل جديد، وذلك بشكل إلكتروني مجاني دون زيارة المكاتب. تندرج الخدمة ضمن فئة المعاشات والمنافع التأمينية وإدارة المدفوعات. مرتبطة بـ: نظام التأمينات الجديد، معاش تقاعدي، منفعة مقطوعة، الالتحاق بعمل جديد، التزامات المشترك، بوابة التأمينات الاجتماعية، خدمة الأفراد.

**الرابط:** https://www.gosi.gov.sa/ar/IndividualsServices/ServiceDetails/Return_Lumpsum_Benefit
---

### [57] خدمة: ديوان المظالم - خدمة طلب تكليف [ref:25349]
**الجهة:** ديوان المظالم
**RRF:** 0.0101

خدمة طلب تكليف - تسجيل تكليف موظف - ديوان المراقبة العامة عبر بوابته الداخلية. خدمة إلكترونية حكومية تتيح للمستفيدين من موظفي ديوان المراقبة العامة تقديم طلب إلكتروني لتكليف زملائهم للعمل خارج أوقات الدوام الرسمي، مما يسهل إجراءات الموارد البشرية الداخلية. تندرج الخدمة ضمن فئة الخدمات الإدارية والموارد البشرية للموظفين الحكوميين. مرتبطة بـ: تكليف خارج الدوام، العمل الإضافي، البوابة الداخلية للموظفين، نظام الموارد البشرية، ديوان المراقبة العامة، إجراءات داخلية.

**الرابط:** https://visitor.bog.gov.sa/
---

### [58] خدمة: المؤسسة العامة للتأمينات الاجتماعية - احتساب مدة إعارة [ref:182598]
**الجهة:** المؤسسة العامة للتأمينات الاجتماعية
**RRF:** 0.0101

خدمة احتساب مدة إعارة - طلب احتساب فترة الإعارة - المؤسسة العامة للتأمينات الاجتماعية عبر بوابتها الإلكترونية. خدمة إلكترونية حكومية تتيح للمستفيدين من المشتركين السعوديين في نظام التأمينات الاطلاع على طلب إعارة مقدم من جهة عملهم واتخاذ إجراء بشأنه كالموافقة أو طلب احتساب مدته أو الاعتراض عليه إلكترونياً. تندرج الخدمة ضمن فئة إدارة علاقات العمل والخدمات التأمينية للمشتركين. مرتبطة بـ: الإعارة الوظيفية، جهة العمل، مدة الاشتراك، المعاش التقاعدي، النفاذ الوطني الموحد، بوابة التأمينات، طلبات المشترك.

**الرابط:** https://www.gosi.gov.sa/ar/IndividualsServices/ServiceDetails/SecondmentRequests
---

### [59] خدمة: وزارة العدل - استكمال طلب قيد المستشار القانوني غير السعودي [ref:19496]
**الجهة:** وزارة العدل
**RRF:** 0.0101

خدمة استكمال طلب قيد المستشار القانوني غير السعودي - تسجيل المحامي الأجنبي - وزارة العدل السعودية عبر منصة ناجز. خدمة إلكترونية حكومية تتيح للمستفيدين من المستشارين القانونيين غير السعوديين أو المنشآت القانونية استكمال إجراءات قيد المستشار في السجل الخاص بهم إلكترونياً بعد تقديم الطلب المبدئي من المنشأة. تندرج الخدمة ضمن فئة التراخيص المهنية والخدمات القضائية. مرتبطة بـ: ترخيص ممارسة المحاماة، السجل المهني، المنشأة القانونية، عقد العمل، النفاذ الوطني الموحد، ناجز أفراد، باقة التراخيص، وزارة العدل.

**الرابط:** https://www.moj.gov.sa/ar/eServices/Pages/ServiceDetailsNew.aspx?itemId=447
---

### [60] خدمة: الديوان العام للمحاسبة - إجازة الصرف بموجب صورة [ref:20102]
**الجهة:** الديوان العام للمحاسبة
**RRF:** 0.0101

خدمة إجازة الصرف بموجب صورة - طلب إذن صرف مالي - الديوان العام للمحاسبة عبر منصة شمل. خدمة إلكترونية حكومية تتيح للمستفيدين من الموظفين المصرح لهم في الجهات الحكومية المشمولة برقابة الديوان العام للمحاسبة تقديم طلب للحصول على إذن صرف مالي استناداً إلى صورة من المستند الأصلي المفقود بشكل إلكتروني. تندرج الخدمة ضمن فئة الخدمات المالية والرقابية للجهات الحكومية. مرتبطة بـ: الرقابة المالية، المستندات المالية، المنصات الحكومية الموحدة، إجراءات الصرف، الديوان العام للمحاسبة، نظام المراسلات الحكومية، المنافذ المالية الموحدة.

**الرابط:** https://e-audit.shamel.gov.sa/start-exchange-permit-request
---

### [61] خدمة: وزارة الموارد البشرية والتنمية الاجتماعية - تجديد ترخيص شركة أو مكتب استقدام [ref:24044]
**الجهة:** وزارة الموارد البشرية والتنمية الاجتماعية
**RRF:** 0.0100

خدمة تجديد ترخيص شركة أو مكتب استقدام - تجديد رخصة مكتب توظيف - وزارة الموارد البشرية والتنمية الاجتماعية عبر بوابتها الإلكترونية. خدمة إلكترونية حكومية تتيح للمستفيدين من ممثلي المنشآت المرخصة في قطاع الاستقدام والتوظيف تقديم طلب تجديد ترخيصهم إلكترونياً لضمان استمرارية نشاطهم القانوني وتجنب إيقاف الخدمات. تندرج الخدمة ضمن فئة تراخيص وخدمات قطاع العمل والتنمية الاجتماعية. مرتبطة بـ: ترخيص استقدام، مكتب توظيف، منشأة مرخصة، قطاع العمل، شروط تجديد الترخيص، بوابة مساند، نظام العمل، العمالة الوافدة.

**الرابط:** https://www.hrsd.gov.sa/ministry-services/services/167378
---

### [62] خدمة: وزارة الموارد البشرية والتنمية الاجتماعية - الاعتراضات والتسويات على مخالفات العمل [ref:19397]
**الجهة:** وزارة الموارد البشرية والتنمية الاجتماعية
**RRF:** 0.0100

خدمة الاعتراضات والتسويات على مخالفات العمل - تقديم تظلم على مخالفات العمل - وزارة الموارد البشرية والتنمية الاجتماعية عبر بوابتها الإلكترونية. خدمة إلكترونية حكومية تتيح للمستفيدين من المنشآت العاملة في المملكة تقديم اعتراض إلكتروني على قرارات المخالفات الصادرة بحقهم من مركز الاعتماد، مما يتيح مراجعة وتصويب تلك القرارات دون زيارة مكاتب الوزارة. تندرج الخدمة ضمن فئة الخدمات العمالية والامتثال التنظيمي للمنشآت. مرتبطة بـ: مخالفات نظام العمل، لجنة الاعتراضات، تسوية المخالفات، غرامات العمل، التفتيش العمالي، مركز الاعتماد، الامتثال لأنظمة العمل.

**الرابط:** https://www.hrsd.gov.sa/ministry-services/services/70085
---

### [63] خدمة: المؤسسة العامة للتأمينات الاجتماعية - حاسبة المعاش التقاعدي [ref:23477]
**الجهة:** المؤسسة العامة للتأمينات الاجتماعية
**RRF:** 0.0100

خدمة حاسبة المعاش التقاعدي - تقدير قيمة المعاش - المؤسسة العامة للتأمينات الاجتماعية عبر بوابتها الإلكترونية. خدمة إلكترونية حكومية تتيح للمستفيدين من المشتركين المواطنين في نظام التأمينات الاجتماعية تقدير القيمة التقريبية لمعاشهم التقاعدي المستقبلي بناءً على مدد اشتراكهم وأجورهم، مما يساعد في التخطيط المالي. تندرج الخدمة ضمن فئة الخدمات التأمينية والاجتماعية والتخطيط للمستقبل. مرتبطة بـ: حساب المعاش، نهاية الخدمة، التقاعد، متوسط الأجور، مدة الاشتراك، صافي المعاش، التخطيط المالي للتقاعد، بوابة التأمينات.

**الرابط:** https://www.gosi.gov.sa/ar/IndividualsServices/ServiceDetails/Pension_Calculator
---

</details>
