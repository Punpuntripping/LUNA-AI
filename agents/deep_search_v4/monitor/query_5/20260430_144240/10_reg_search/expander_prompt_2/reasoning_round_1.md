# Reasoning — Expander Round 1
**Prompt key:** `prompt_2`

Here's a thinking process:

1.  **Analyze User Input:**
   - **Scenario:** An employee is currently on leave. The employer wants to terminate/non-renew the contract while maintaining communication. They ask if they can send a "notice of non-renewal" letter because the contract is near expiry, and they need to give one month's notice.
   - **Key Legal Concepts:**
     - Contract non-renewal during leave
     - Notice period for non-renewal/termination (1 month)
     - Legal validity of sending notice while employee is on leave
     - Procedures for ending employment contracts in Saudi labor law
   - **Constraints:**
     - Exactly 7 sub-queries (as per prompt: "اقتصر على عدد 7 من الاستعلامات الفرعية، ولا تتجاوز هذا الحد.")
     - Sector is pre-determined: العمل والتوظيف (Labor and Employment)
     - Must include 3 types: Direct, Abstract (Step-back), Deconstructive
     - Each query = 1 legal concept
     - Arabic queries
     - Provide rationales for each query (type + targeted angle)

   - **Mandatory Structure:**
     - Direct Query: Targets specific article/rule about non-renewal notice during leave.
     - Abstract Query: Steps back to general principle (e.g., validity of notices during leave, rules for contract expiration/renewal).
     - Deconstructive Query: Breaks into independent sub-issues (e.g., calculation of notice period, legal effect of leave on contract termination procedures, compensation/entitlements upon non-renewal).

2.  **Brainstorming Queries (Aiming for 7 total, covering the 3 types):**
   *Type 1: Direct (Targeting specific rules/conditions)*
   1. إشعار عدم الرغبة في تجديد عقد العمل قبل انتهاء مدته
   2. إجراءات إنهاء عقد العمل أثناء إجازة الموظف

   *Type 2: Abstract/Step-back (General principles)*
   3. الأحكام العامة لانقضاء عقد العمل محدد المدة
   4. صحة التبليغات والخطابات الرسمية أثناء فترة الإجازة النظامية

   *Type 3: Deconstructive (Independent sub-issues)*
   5. احتساب مدة إشعار إنهاء العقد ضمن فترة الإجازة
   6. مستحقات العامل عند عدم تجديد عقد العمل محدد المدة
   7. الالتزامات الإجرائية لصاحب العمل عند انتهاء العقد

   Let's refine them to ensure they are precise, single-concept, and align with Saudi labor law search semantics.
   Check against constraints:
   - Exactly 7? Yes.
   - Covers 3 types? Yes.
   - Single concept each? Yes.
   - Arabic? Yes.
   - Sector: العمل والتوظيف (pre-assigned, so I'll just list it).
   - Rationales included? Will add.

   Let's tweak them for better legal precision in Saudi context:
   1. (مباشر) شروط إشعار عدم تجديد عقد العمل محدد المدة
   2. (مباشر) جواز إنهاء عقد العمل أثناء تمتع العامل بالإجازة
   3. (تجريدي) القواعد العامة لانقضاء عقود العمل بانتهاء المدة
   4. (تجريدي) حجية وسريان التبليغات الإدارية أثناء العطل والإجازات
   5. (تفكيكي) طريقة احتساب فترة الإشعار إذا تداخلت مع إجازة العامل
   6. (تفكيكي) مستحقات نهاية الخدمة عند عدم تجديد العقد
   7. (تفكيكي) الآثار النظامية لعدم الالتزام بمهلة الإشعار قبل انتهاء العقد

   Wait, the prompt says "اقتصر على عدد 7 من الاستعلامات الفرعية". I will output exactly 7.
   Let's verify the types:
   - مباشر: 1, 2
   - تجريدي: 3, 4
   - تفكيكي: 5, 6, 7
   Total: 7. Good.
   Rationales will specify type and angle.
   Sector: العمل والتوظيف (pre-assigned, so I will output it as requested, though the prompt says I don't need to choose new ones, I'll still include it in the sectors array as per the tool schema).

   Let's craft the final output carefully matching the JSON schema for `final_result`.
   Schema:
   {
     "queries": [string, string, ...],
     "rationales": [string, string, ...],
     "sectors": ["العمل والتوظيف"] (or null if >4, but here it's 1)
   }

   Check rationales format: "Internal rationale per query (logs only, not sent to LLM)" - I'll write them clearly indicating type and angle.
   I will make sure the queries are distinct and precisely target Saudi labor law concepts.
   Query 1: شروط إشعار عدم رغبة صاحب العمل في تجديد عقد العمل
   Query 2: نظام إنهاء عقد العامل أثناء فترة الإجازة السنوية
   Query 3: الأحكام العامة لانقضاء عقد العمل بانتهاء مدته
   Query 4: نفاذ الخطابات والإشعارات الرسمية أثناء العطل والإجازات
   Query 5: احتساب مدة إشعار الإنهاء إذا تزامنت مع إجازة العامل
   Query 6: مستحقات العامل المالية عند عدم تجديد عقد محدد المدة
   Query 7: التعويض عن عدم الالتزام بمهلة الإشعار النظامية

   Rationales:
   1. النوع: مباشر. الزاوية: يستهدف المادة التي تحدد شروط ومدة إشعار عدم التجديد قبل انتهاء العقد.
   2. النوع: مباشر. الزاوية: يحكم جواز إبلاغ العامل بالإنهاء أو عدم التجديد وهو في إجازة رسمية.
   3. النوع: تجريدي. الزاوية: يعود للمبدأ التأسيسي لانقضاء العقود محددة المدة وآثارها العامة.
   4. النوع: تجريدي. الزاوية: يبحث في القاعدة العامة لسريان التبليغات الإدارية والقانونية خلال فترات الغياب أو الإجازة.
   5. النوع: تفكيكي. الزاوية: يفكك إشكالية تداخل فترة الإشعار مع الإجازة وكيفية احتسابها نظامياً.
   6. النوع: تفكيكي. الزاوية: يستقل ببيان الحقوق المالية المترتبة على انتهاء العقد دون تجديد.
   7. النوع: تفكيكي. الزاوية: يتناول الجزاء أو التعويض النظامي في حال إرسال الإشعار متأخراً أو عدم الالتزام بالمهلة.

   Sectors: ["العمل والتوظيف"]

   All constraints met. I will generate the JSON output.✅
   Double check the prompt: "الصكاترات (المجالات القانونية) محددة مسبقاً من المُخطِّط: العمل والتوظيف. لا تحتاج لاختيار صكاترات جديدة." -> So I will just put ["العمل والتوظيف"].
   "اقتصر على عدد 7 من الاستعلامات الفرعية، ولا تتجاوز هذا الحد." -> Exactly 7.
   Ready. Output matches schema.✅
   One minor thing: The prompt says "الصكاترات" which is a typo for "القطاعات". I'll stick to "العمل والتوظيف".
   All good.✅
   Output generation. 
   Structure:
   ```json
   {
     "queries": [...],
     "rationales": [...],
     "sectors": ["العمل والتوظيف"]
   }
   ```
   Matches perfectly.✅
   Proceeds. 
   Self-Correction/Refinement during thought: 
   Let's ensure the queries are highly optimized for Saudi labor law semantic search. 
   "شروط إشعار عدم رغبة صاحب العمل في تجديد عقد العمل" -> Good.
   "نظام إنهاء عقد العامل أثناء فترة الإجازة السنوية" -> Good.
   "الأحكام العامة لانقضاء عقد العمل بانتهاء مدته" -> Good.
   "نفاذ الخطابات والإشعارات الرسمية أثناء العطل والإجازات" -> Good.
   "احتساب مدة إشعار الإنهاء إذا تزامنت مع إجازة العامل" -> Good.
   "مستحقات العامل المالية عند عدم تجديد عقد محدد المدة" -> Good.
   "التعويض عن عدم الالتزام بمهلة الإشعار النظامية" -> Good.
   Rationales align with types. All set.✅
   Output matches exactly. 
   Proceeds. 
   [Output Generation] -> *Self-Correction/Verification during generation prep*: 
   The prompt says "الصكاترات (المجالات القانونية) محددة مسبقاً من المُخطِّط: العمل والتوظيف. لا تحتاج لاختيار صكاترات جديدة." This means I should just output `["العمل والتوظيف"]` in the sectors array. 
   The tool schema expects `sectors` to be an array of strings or null. 
   I will produce the exact JSON. 
   All good. 
   Output matches. 
   [Done.] 
   *Self-Correction/Note during thought*: I should make sure the rationales explicitly state the type as requested: "النوع: مباشر / تجريدي / تفكيكي" and the angle. I'll format them concisely.
   Final check of the prompt: "سجّل في المبررات: - النوع: مباشر / تجريدي / تفكيكي - ما الزاوية القانونية المستهدفة"
   I will follow this exactly.
   Ready.✅
   Output Generation. 
   ```json
   {
     "queries": [
       "شروط إشعار عدم رغبة صاحب العمل في تجديد عقد العمل",
       "نظام إنهاء عقد العامل أثناء فترة الإجازة السنوية",
       "الأحكام العامة لانقضاء عقد العمل بانتهاء مدته",
       "نفاذ الخطابات والإشعارات الرسمية أثناء العطل والإجازات",
       "احتساب مدة إشعار الإنهاء إذا تزامنت مع إجازة العامل",
       "مستحقات العامل المالية عند عدم تجديد عقد محدد المدة",
       "التعويض عن عدم الالتزام بمهلة الإشعار النظامية"
     ],
     "rationales": [
       "النوع: مباشر. الزاوية: يستهدف المادة التي تنظم مهلة وشروط إبلاغ العامل بعدم رغبة صاحب العمل في التجديد قبل انتهاء العقد.",
       "النوع: مباشر. الزاوية: يحكم جواز نظامي لإبلاغ العامل بإنهاء أو عدم تجديد العقد وهو في إجازة رسمية.",
       "النوع: تجريدي. الزاوية: يعود للمبدأ التأسيسي لانقضاء عقود العمل بانتهاء المدة والأحكام العامة المترتبة عليه.",
       "النوع: تجريدي. الزاوية: يبحث في القاعدة العامة لسريان التبليغات والخطابات القانونية والإدارية خلال فترات الإجازة أو الغياب.",
       "النوع: تفكيكي. الزاوية: يفكك إشكالية تداخل فترة الإشعار مع الإجازة وكيفية احتسابها أو تمديدها نظامياً.",
       "النوع: تفكيكي. الزاوية: يستقل ببيان الحقوق والمستحقات المالية التي تستحق للعند عدم التجديد أو انتهاء العقد.",
       "النوع: تفكيكي. الزاوية: يتناول الجزاء أو التعويض النظامي المترتب على التقصير في مهلة الإشعار أو عدم الالتزام بها."
     ],
     "sectors": ["العمل والتوظيف"]
   }
   ```
   All constraints met. Output matches schema. Proceeds. 
   *Self-Correction/Verification during output gen*: The prompt says "الصكاترات" which is clearly a typo for القطاعات. I will output `["العمل والتوظيف"]`. The JSON structure is correct. I will output it now. 
   One minor thing: The prompt says "اقتصر على عدد 7 من الاستعلامات الفرعية، ولا تتجاوز هذا الحد." -> I have exactly 7.
   All good.✅
   Output matches. 
   [Final Check]
   - 3 types present? Yes (مباشر x2, تجريدي x2, تفكيكي x3)
   - Exactly 7? Yes.
   - Sector: العمل والتوظيف? Yes.
   - Rationales include type and angle? Yes.
   - Arabic? Yes.
   - Single concept per query? Yes.
   - No system/entity names mentioned unnecessarily? Yes.
   Ready.✅
