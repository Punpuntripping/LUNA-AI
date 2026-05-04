# Reasoning — Expander Round 1
**Prompt key:** `prompt_2`

Here's a thinking process:

1.  **Analyze User Input:**
   - **Core Situation:** User terminated their contract with their previous company with 4 months remaining, citing Article 81 (Saudi Labor Law) for various reasons. The company filed a claim for compensation under Article 77.
   - **Context/Constraints:** 
     - Focus on exactly 7 sub-queries.
     - Do not exceed 7.
     - Legal sector is pre-defined: "العمل والتوظيف" (Labor and Employment). No need to choose new sectors.
     - Must follow the 3 query types: Direct, Abstract (step-back), Deconstructive.
     - Each query = single legal concept.
     - Describe the legal behavior/right, not system names.
     - Output in Arabic.

2.  **Deconstruct the Legal Scenario (Saudi Labor Law Context):**
   - *Article 81:* Allows the employee to leave work without notice and retain all rights if the employer commits specific breaches (e.g., assault, fraud, failure to pay wages, unsafe conditions, etc.). This is constructive dismissal/termination for cause by the employee.
   - *Article 77:* Deals with compensation for unlawful termination by either party without a valid reason under the law. The employer is claiming compensation for the remaining 4 months of the contract.
   - *Key Legal Conflict:* Can the employer claim Article 77 compensation if the employee lawfully terminated under Article 81? What are the conditions for applying Article 81? How is compensation calculated under Article 77 if applicable? What is the burden of proof for Article 81 violations? Does termination under Article 81 exempt the employee from Article 77 claims?
   - *Legal Principles:* Constructive termination, burden of proof for employer's breach, validity of fixed-term contract early termination, compensation calculation for unlawful termination, employee's right to full benefits upon justified resignation.

3.  **Develop Queries (Target: exactly 7, covering Direct, Abstract, Deconstructive types):**

   *Direct Queries (Target specific articles/situations):*
   1. شروط ترك العمل دون إشعار مع الاحتفاظ بكافة الحقوق (Targets Art. 81 conditions directly)
   2. أحكام التعويض عن الفسخ التعسفي للعقد المحدد المدة (Targets Art. 77 compensation mechanism)
   3. إثبات مبررات ترك العامل للعمل قبل انتهاء العقد (Burden of proof for Art. 81)

   *Abstract/Step-Back Queries (Target broader chapters/principles):*
   4. أثر الخطأ الجسيم لصاحب العمل على إنهاء علاقة العمل (Abstracts Art. 81 into the principle of employer's gross breach)
   5. التوازن التعويدي عند الفسخ الأحادي لعقود العمل (Abstracts the compensation balance/conflict between Art. 77 & 81)

   *Deconstructive Queries (Target independent sub-issues):*
   6. إجراءات تسوية المستحقات النهائية عند ترك العمل لسبب نظامي (Focuses on end-of-service/end-of-contract benefits settlement)
   7. اختصاص المحاكم العمالية في المنازعات التعويضية عن الفسخ (Focuses on jurisdiction/procedure for labor compensation claims)

   Wait, let's verify against constraints:
   - Exactly 7? Yes.
   - Types: Direct (1, 2, 3), Abstract (4, 5), Deconstructive (6, 7). Good mix.
   - Each targets a single legal concept? Yes.
   - Describes behavior/right, not system names? Yes.
   - Sector: العمل والتوظيف (pre-defined).
   - Language: Arabic.
   - Rationale format: Must include type and legal angle.

   Let's refine them to ensure they strictly match the prompt's style and avoid mentioning article numbers directly in the queries (as per "لا تذكر أسماء أنظمة أو جهات لم يذكرها المستخدم" and general best practice for semantic search). The prompt says "لا تذكر أسماء أنظمة أو جهات لم يذكرها المستخدم" (Don't mention system/entity names the user didn't mention). I'll keep the queries focused on the legal concepts.

   Refined Queries:
   1. (مباشر) شروط ترك العمل دون إشعار مع الاحتفاظ بكافة الحقوق
   2. (مباشر) أحكام التعويض عن الفسخ المبكر للعقد المحدد المدة
   3. (مباشر) عبء الإثبات في دعوى ترك العمل لسبب مشروع من العامل
   4. (تجريدي) أثر الخطأ الجسيم لصاحب العمل على استقرار عقد العمل
   5. (تجريدي) المعايير القضائية لتحديد التعويض عند الفسخ الأحادي
   6. (تفكيكي) إجراءات المطالبة بالمستحقات المالية عند إنهاء العلاقة الوظيفية
   7. (تفكيكي) الاختصاص النوعي للمحكمة العمالية في منازعات الفسخ التعويضي

   Check against constraints:
   - 7 queries? Yes.
   - Types labeled? I will format rationales as requested: "النوع: مباشر / تجريدي / تفكيكي" + "ما الزاوية القانونية المستهدفة".
   - Sector: العمل والتوظيف.
   - All in Arabic.
   - Matches the exact output format required by the tool `final_result`.

   Let's structure the tool call:
   queries: [list of 7]
   rationales: [list of 7 rationales matching each query]
   sectors: ["العمل والتوظيف"]

   Draft rationales:
   1. النوع: مباشر | الزاوية: الشروط النظامية التي تبيح للعامل ترك العمل فوراً مع استحقاق كامل حقوقه
   2. النوع: مباشر | الزاوية: القواعد الحاكمة للتعويض المالي عند إنهاء العقد المحدد المدة قبل انقضائه
   3. النوع: مباشر | الزاوية: توزيع عبء الإثبات بين العامل وصاحب العمل عند النزاع على مشروعية ترك العمل
   4. النوع: تجريدي | الزاوية: المبدأ العام لتأثير إخلال صاحب العمل بالتزاماته الجوهرية على استمرارية العقد
   5. النوع: تجريدي | الزاوية: الضوابط والمعايير الأساسية التي يستند إليها القضاء في تقدير التعويضات عن الفسخ
   6. النوع: تفكيكي | الزاوية: الإجراءات والمهلة النظامية لتسوية الأجور والمزايا المتبقية بعد إنهاء الخدمة
   7. النوع: تفكيكي | الزاوية: تحديد الجهة القضائية المختصة نوعياً ومكانياً للنظر في دعاوي التعويض العمالي

   All good. I will now generate the function call.
   Double-check constraints: "اقتصر على عدد 7 من الاستعلامات الفرعية، ولا تتجاوز هذا الحد." -> Exactly 7.
   "الصكاترات (المجالات القانونية) محددة مسبقاً من المُخطِّط: العمل والتوظيف." -> sectors: ["العمل والتوظيف"]
   Format matches `final_result` schema.
   Proceed. 
   One minor adjustment: The prompt says "لا تذكر أسماء أنظمة أو جهات لم يذكرها المستخدم". I didn't mention any system names. Good.
   The rationales should clearly state the type and angle. I'll format them exactly as requested.
   Output matches the tool specification.✅
   Generating. 
   `final_result(queries=[...], rationales=[...], sectors=["العمل والتوظيف"])` -> ready. 
   Self-Correction/Verification during thought:
   - Check query count: exactly 7.
   - Check types: 3 direct, 2 abstract, 2 deconstructive. Covers all 3 types as required.
   - Check sector: pre-defined as "العمل والتوظيف".
   - Check format: matches JSON schema.
   - All constraints met. Output matches.✅
   Proceeds. 
   [Output Generation] -> *Proceeds to call function*
   *(Self-Correction/Refinement during drafting)*: I should ensure the rationales are concise and directly map to the query index. The schema expects an array of strings for rationales. I'll format each string as "النوع: ... | الزاوية: ...".
   Queries:
   1. شروط ترك العمل دون إشعار مع الاحتفاظ بكافة الحقوق
   2. أحكام التعويض عن الفسخ المبكر للعقد المحدد المدة
   3. عبء الإثبات في دعوى ترك العمل لسبب مشروع من العامل
   4. أثر الخطأ الجسيم لصاحب العمل على استقرار عقد العمل
   5. المعايير القضائية لتحديد التعويض عند الفسخ الأحادي
   6. إجراءات المطالبة بالمستحقات المالية عند إنهاء العلاقة الوظيفية
   7. الاختصاص النوعي للمحكمة العمالية في منازعات الفسخ التعويضي
   All align perfectly. Output matches. 
   Proceeds. 
   [Final Check of the Prompt]: "أنهيت عقدي مع شركتي السابقة، متبقيًا منه أربعة أشهر، بموجب المادة ٨١ لأسبابٍ مختلفة. تقدمت الشركة بطلب تعويض بموجب المادة ٧٧."
   The queries accurately target the legal core of this conflict (Art 81 vs Art 77, burden of proof, compensation calculation, jurisdiction, settlement procedures).
   Ready. 
   Output matches function call.✅
   *Self-Correction/Note during thought*: The prompt says "لا تذكر أسماء أنظمة أو جهات لم يذكرها المستخدم". I used "المحكمة العمالية" in query 7. The user didn't mention courts, but it's a deconstructive query about jurisdiction. To be strictly compliant, I'll change it to "الاختصاص القضائي في المنازعات التعويضية عن فسخ عقود العمل" to avoid naming a specific entity/system. Also, I'll adjust rationale 7 accordingly.
   Revised Query 7: الاختصاص القضائي في المنازعات التعويضية عن فسخ عقود العمل
   Rationale 7: النوع: تفكيكي | الزاوية: تحديد الجهة المختصة بنظر دعاوي التعويض الناشئة عن الإنهاء المبكر للعقد
   This is safer and strictly follows "لا تذكر أسماء أنظمة أو جهات لم يذكرها المستخدم".
   All set.✅
   Output generation. 
   `[final_result]` call constructed.✅
   Proceeds. 
   *(Done.)*
