# Reasoning — Expander Round 1
**Prompt key:** `prompt_2`

Here's a thinking process:

1.  **Analyze User Input:**
   - **Topic:** سوابق قضائية في الفصل التعسفي (Judicial precedents on arbitrary dismissal)
   - **Constraint:** Exactly 5 sub-queries, do not exceed.
   - **Sector:** Pre-defined as "العمل والتوظيف" (Labor and Employment). No need to choose others.
   - **Role:** Legal query expansion specialist for Saudi systems/regulations.
   - **Output Format:** Must include 3 types of queries (Direct, Abstract/Step-back, Deconstructive), rationales for each, and the sector.

2.  **Deconstruct the Topic & Constraints:**
   - *Core Subject:* Arbitrary dismissal (الفصل التعسفي) in Saudi labor law.
   - *Key Aspect:* Judicial precedents/case law (سوابق قضائية) - though Saudi law is primarily statutory, the query should target how courts handle these cases, compensation, burden of proof, etc.
   - *Constraint Check:* Exactly 5 queries. Must include at least one of each type (Direct, Abstract, Deconstructive). Sector is fixed: العمل والتوظيف.
   - *Query Types Needed:*
     - Direct (مباشر): Targets specific articles/rules on arbitrary dismissal.
     - Abstract/Step-back (تجريدي): Steps back to general principles (e.g., burden of proof in termination, compensation principles, employer's right to terminate vs. abuse of right).
     - Deconstructive (تفكيكي): Breaks into independent legal issues (e.g., compensation calculation, burden of proof, procedural requirements for termination, appeal mechanisms).

3.  **Draft Queries (Mental Refinement):**
   *Direct (1-2):*
   - ضوابط وأحكام الفصل التعسفي في نظام العمل السعودي
   - معايير认定 الفصل التعسفي من قبل المحاكم العمالية
   Let's stick to one clear direct: "أحكام الفصل التعسفي وضوابطه في نظام العمل"
   *Abstract (1-2):*
   - مبدأ إساءة استعمال الحق في إنهاء عقود العمل
   - التعويضات المترتبة على إنهاء العقد دون سبب مشروع
   Let's use: "مبدأ إساءة استعمال الحق في إنهاء العلاقة التعاقدية" or "أحكام التعويض عن الضرر الناتج عن إنهاء العقد دون مسوغ نظامي"
   *Deconstructive (2-3):*
   - عبء الإثبات في دعاوى الفصل التعسفي
   - آلية حساب التعويض المالي في قضايا الفصل غير المشروع
   - الإجراءات النظامية للطعن في قرارات المحاكم العمالية
   Let's pick two to reach exactly 5 total:
   1. مباشر: أحكام وشروط الفصل التعسفي في نظام العمل
   2. تجريدي: مبدأ إساءة استعمال الحق في إنهاء العلاقة التعاقدية
   3. تفكيكي: عبء الإثبات في الدعاوى القضائية للفصل غير المبرر
   4. تفكيكي: معايير تقدير التعويض المالي في قضايا الفصل التعسفي
   5. تفكيكي: الإجراءات والطعون القضائية في أحكام المحاكم العمالية

   Wait, I need exactly 5. I have 1 Direct, 1 Abstract, 3 Deconstructive. That's fine. Let's ensure they align with the 3-type requirement (at least one of each). I'll adjust to make sure the types are clearly distributed and match the prompt's examples.
   Let's refine them to be precise and single-concept:
   1. مباشر: ضوابط الفصل التعسفي وشروط تحققه في عقود العمل
   2. تجريدي: مبدأ إساءة استعمال الحق في إنهاء العقود
   3. تفكيكي: عبء الإثبات في المنازعات العمالية الخاصة بالفصل
   4. تفكيكي: أسس تقدير التعويضات المالية عن الفصل غير المشروع
   5. تفكيكي: اختصاص المحاكم العمالية وإجراءات الطعن في أحكام الفصل

   Check constraints: Exactly 5. Sector: العمل والتوظيف. Types covered. Rationales needed. All in Arabic. Single legal concept per query.

4.  **Draft Rationales (Internal Logic for each):**
   - Query 1 (مباشر): يستهدف المواد النظامية التي تحدد تعريف الفصل التعسفي وحكمه مباشرة في نظام العمل.
   - Query 2 (تجريدي): يرتقي للمبدأ القانوني العام الحاكم وهو "إساءة استعمال الحق" عند إنهاء العقد، مما يجل أحكام الأبواب المتعلقة بالفسخ والتعويض العام.
   - Query 3 (تفكيكي): يعزل مسألة إجرائية جوهرية وهي توزيع عبء الإثبات بين العامل وصاحب العمل في هذه المنازعات.
   - Query 4 (تفكيكي): يفكك جانب التعويض المالي لبحث المعايير القضائية لنسبة ومقدار التعويض المستقل عن حكم التعسف نفسه.
   - Query 5 (تفكيكي): يستهدف المسألة الإجرائية الخاصة بالجهة القضائية المختصة ومسار الطعن، وهو شرط سابق لأي حكم قضائي.

5.  **Final Output Generation (Mental Validation):**
   - Format: JSON-like or structured text as per `final_result` tool.
   - Parameters: `queries` (array of 5 strings), `rationales` (array of 5 strings), `sectors` (array with one string: "العمل والتوظيف").
   - Check constraints: 5 queries exactly. Sector pre-defined. Types represented. Rationales explain type & angle. Single concept each. Arabic language.
   - All good. I will structure the output to match the tool schema exactly.

   Structure:
   queries: ["ضوابط الفصل التعسفي وشروط تحققه في عقود العمل", "مبدأ إساءة استعمال الحق في إنهاء العقود", "عبء الإثبات في المنازعات العمالية الخاصة بالفصل", "أسس تقدير التعويضات المالية عن الفصل غير المشروع", "اختصاص المحاكم العمالية وإجراءات الطعن في أحكام الفصل"]
   rationales: ["نوع مباشر: يستهدف المواد النظامية التي تحدد تعريف الفصل التعسفي وحكمه مباشرة.", "نوع تجريدي (step-back): يرتقي للمبدأ القانوني العام الحاكم وهو إساءة استعمال الحق عند الفسخ، مما يجلّ القواعد التأسيسية للأبواب المعنية.", "نوع تفكيكي: يعزل مسألة إثباتية جوهرية حول من يتحمل عبء إثبات سبب الفصل أو انتفائه.", "نوع تفكيكي: يفكك الجانب المالي المستقل لبحث المعايير القضائية لنسبة ومقدار التعويض.", "نوع تفكيكي: يستهدف المسألة الإجرائية الخاصة بالاختصاص القضائي ومسار الطعن النظامي."]
   sectors: ["العمل والتوظيف"]

   Matches all constraints. Proceeds.✅
   One minor adjustment: Ensure rationales explicitly state the type as requested: "سجّل في المبررات: النوع: مباشر / تجريدي / تفكيكي - ما الزاوية القانونية المستهدفة". I'll make them slightly more descriptive to match exactly.
   All set. Output matches tool call format. 
   Generating. 
   *Self-Correction/Verification during thought:*
   - Check query count: exactly 5. ✅
   - Check types: Direct (1), Abstract (1), Deconstructive (3). Covers all 3. ✅
   - Check sector: "العمل والتوظيف" (pre-defined). ✅
   - Check single concept per query: Yes. ✅
   - Language: Arabic. ✅
   - Tool schema matches. ✅
   Ready. 
   Output matches the function call.✅
   Proceeds. 
   [Output Generation] -> `final_result(queries=[...], rationales=[...], sectors=["العمل والتوظيف"])`✅
   *(Note: I will format the rationales clearly as requested: "النوع: ... | الزاوية: ...")* 
   All good. 
   *Output Generation* (matches the response)
   `final_result(queries=["ضوابط الفصل التعسفي وشروط تحققه في عقود العمل", "مبدأ إساءة استعمال الحق في إنهاء العقود", "عبء الإثبات في المنازعات العمالية الخاصة بالفصل", "أسس تقدير التعويضات المالية عن الفصل غير المشروع", "اختصاص المحاكم العمالية وإجراءات الطعن في أحكام الفصل"], rationales=["النوع: مباشر | الزاوية: استهداف النصوص النظامية المحددة التي تعرّف الفصل التعسفي وتبين حكمه وشروط تحققه مباشرة.", "النوع: تجريدي | الزاوية: الارتقاء للمبدأ القانوني العام (إساءة استعمال الحق) الحاكم لفسخ العقود، لاستجلاب القواعد التأسيسية في الأبواب المعنية.", "النوع: تفكيكي | الزاوية: عزل المسألة الإجرائية الجوهرية المتعلقة بتوزيع عبء الإثبات بين العامل وصاحب العمل.", "النوع: تفكيكي | الزاوية: تفكيك الجانب المالي المستقل لبحث المعايير والأنظمة الحاكمة لحساب نسبة ومقدار التعويض.", "النوع: تفكيكي | الزاوية: استهداف المسألة الإجرائية المتعلقة بالاختصاص القضائي ومسار الطعن النظامي ضد الأحكام."], sectors=["العمل والتوظيف"])` -> Correct.✅
   Proceeds. 
   [Output Generation] (matches the final response)
   *(Done.)*
