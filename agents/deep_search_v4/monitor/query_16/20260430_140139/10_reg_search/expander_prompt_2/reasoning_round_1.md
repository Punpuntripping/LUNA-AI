# Reasoning — Expander Round 1
**Prompt key:** `prompt_2`

The user is an employee in Saudi Arabia asking about labor law issues.
Here's a breakdown of the user's situation:
1.  Worked from 2012 to 2019 without a contract, then signed a contract for the last few months of 2018. Services terminated end of 2019 (last day 5/2/2019).
2.  End of service benefits not received. Company is delaying.
3.  Filed a case at the Labor Office (first hearing: company absent, second hearing: lawyer delayed/asked for time).
4.  Case is transferring to the Labor Court. Employee affairs staff says they will delay until the court hearing date as retaliation for the complaint.
5.  Questions:
    *   Timeframe for case transfer from Labor Office to Labor Court.
    *   Can I claim compensation for delay (approx. 5 months)? How to determine value?
    *   Can I reject a settlement offer before the court date if it excludes compensation?
    *   If I hire a lawyer, does the company pay for it?

The user specifically requested a limit of 5 sub-queries.
The sector is pre-determined as "العمل والتوظيف" (Labor and Employment).

I need to generate queries based on the methodology:
1.  **Direct (نوع 1)**: Targeting specific regulations about End of Service Benefits (EOSB), contract duration, and case transfer procedures.
2.  **Abstract (Step-back) (نوع 2)**: Targeting general principles of compensation for delay or procedural abuse in labor disputes, or the legal status of unwritten contracts vs written ones.
3.  **Deconstructive (نوع 3)**: Targeting the right to legal fees or settlement rejection procedures.

Potential queries:
1.  (Direct): إجراءات ومدة تحويل النزاعات العمالية من مكتب العمل إلى المحكمة (Procedure and duration for transferring labor disputes from labor office to court).
2.  (Direct): أحكام تعويض المدة المتبقية من العقد أو التعويض عن إنهاء الخدمة بدون سبب مشروع (Compensation for remaining contract term or termination without valid cause - though the user says services ended, often if fired without cause, compensation is due. However, the user specifically asks about delay compensation). Let's stick to the user's explicit question about delay compensation if possible, or general delay claims in labor.
    *   Better Direct: المطالبة بالتعويض عن التأخير في صرف مستحقات نهاية الخدمة (Claiming compensation for delay in paying EOSB).
3.  (Abstract): التعويض عن الضرر الناتج عن المماطلة في الفصل في النزاعات العمالية (Compensation for damage resulting from delay in labor dispute resolution).
4.  (Deconstructive): التزام صاحب العمل بتحمل أتعاب المحاماة في القضايا العمالية (Employer's obligation to bear attorney fees in labor cases).
5.  (Deconstructive): إجراءات التصالح والصلح في المنازعات العمالية قبل الجلسة القضائية (Reconciliation and settlement procedures in labor disputes before court hearing).

Let's refine them to be precise and within the 5 query limit.

Query 1 (Direct): "إجراءات ومدة تحويل ملف النزاع العمالي من مكتب العمل إلى المحكمة" (Targeting the specific procedural question #1).
Query 2 (Abstract/Deconstructive - Focus on delay/compensation): "التعويض عن المماطلة في سداد مستحقات نهاية الخدمة" (Targeting question #2).
Query 3 (Deconstructive - Focus on fees): "قواعد تحميل أتعاب المحاماة في الدعاوي العمالية" (Targeting question #4).
Query 4 (Direct/Deconstructive - Focus on settlement): "أهلية الموظف رفض عرض الصلح الجزئي في القضايا العمالية" (Targeting question #3).
Query 5 (Abstract - Broad principle): "حجية العقد غير المكتوب في إثبات مدة الخدمة" (Addressing the 2012-2018 period without contract, which is crucial context for calculating benefits, though the user didn't explicitly ask "how much money", the context implies calculating rights based on 7 years, not just the contract period). *Correction*: The user asks about delay, transfer time, and lawyer fees. The unwritten contract part is background. However, it's legally significant. If I have to stick to the *questions asked*, I should focus on those. The user asks: 1. Transfer time. 2. Compensation for delay. 3. Rejecting settlement. 4. Lawyer fees.
So I will prioritize queries answering these specific questions.

Refined list:
1.  "المدة النظامية لتحويل قضية العمل من التسوية الودية إلى المحكمة" (Direct - Answers Q1).
2.  "المطالبة بالتعويض عن التأخير في صرف مستحقات نهاية الخدمة" (Direct/Abstract - Answers Q2).
3.  "حق العامل في رفض الصلح الجزئي قبل موعد الجلسة" (Deconstructive - Answers Q3).
4.  "قواعد تحميل الطرف الخاسر أتعاب المحاماة في القضايا العمالية" (Deconstructive - Answers Q4).
5.  "إثبات مدة الخدمة السابقة دون عقد عمل مكتوب" (Abstract - Important context for the "2012-2018" period which might affect the base amount, even if not a specific question, it's a "hidden" sub-issue essential for the full answer). Or maybe "المماطلة في حضور جلسات التسوية العمالية وعقوبتها" (Abstract - Addresses the behavior of the company). Let's stick to the "2012-2018" issue because the user mentioned "worked without contract" which usually implies a need to prove that period to get the full EOSB, otherwise they only get EOSB for the contract period.
Actually, the user's specific questions are about the *process* (delay, transfer time, fees). The context implies the underlying rights are established, but the company is delaying.
I will stick to the questions asked + the general principle of delay/abuse.

Let's select:
1.  "الإجراءات والمدة النظامية لنقل النزاع العمالي من مكتب العمل للمحكمة" (Direct - Q1).
2.  "حق العامل في المطالبة بالتعويض عن المماطلة في صرف المستحقات" (Abstract - Q2).
3.  "قواعد تحميل أتعاب المحاماة في القضايا العمالية" (Deconstructive - Q4).
4.  "حكم رفض عرض الصلح في النزاعات العمالية قبل الجلسة القضائية" (Deconstructive - Q3).
5.  "آلية المطالبة بتعويض الضرر من المماطلة في التقاضي" (Step-back/Abstract - General principle of procedural delay damages).

Wait, the user asked "how to determine the value of compensation?". This is tricky because Saudi law might not explicitly have a fixed table for "delay compensation" in labor offices like a penalty clause. It might fall under general civil liability or discretionary compensation for harm.
So, an abstract query about "Compensation for harm caused by unjustified delay in labor cases" is good.

Let's finalize the 5 queries.
1. Direct: "المدة النظامية لتحويل ملف التسوية من مكتب العمل إلى المحكمة العمالية".
2. Abstract: "التعويض عن الضرر الناشئ عن المماطلة في سداد مستحقات نهاية الخدمة".
3. Deconstructive: "قواعد وأحكام تحميل أتعاب المحاماة في الدعاوي العمالية".
4. Deconstructive: "حق العامل في رفض تسوية جزئية وعدم التنازل عن الحقوق النظامية".
5. Direct (or Abstract): "إثبات العلاقة التعاقدية السابقة لكتابة العقد الرسمي" (Addressing the 2012-2018 period context which is vital for calculating the base EOSB, as the user worked 2012-2018 without contract, then signed. If the company claims EOSB only from 2018, the user loses money. Even though not explicitly asked as a question, it is a necessary sub-issue). *Self-correction*: The user asked 4 specific questions. I should probably answer those. The "2012-2018" is context. I will include a query about proving that period as it's a common trap.

Let's stick to the 4 questions + the "proof of period" as it's critical.

1. "المدة النظامية لتحويل النزاع العمالي من التسوية الودية إلى القضاء" (Answers Q1).
2. "التعويض عن المماطلة في سداد مستحقات نهاية الخدمة" (Answers Q2).
3. "قواعد تحميل الطرف الخاسر أتعاب المحاماة في القضايا العمالية" (Answers Q4).
4. "حق العامل في رفض الصلح الجزئي قبل الجلسة" (Answers Q3).
5. "إثبات مدة الخدمة الفعلية في غياب عقد عمل مكتوب" (Addresses the 2012-2018 context).

This looks solid.

Rationales:
1. Direct: Answers the specific question about the timeframe for transfer.
2. Abstract/Step-back: Looks for the legal principle regarding compensation for administrative/judicial delay by the employer.
3. Deconstructive: Addresses the lawyer fees question.
4. Deconstructive: Addresses the right to reject settlement.
5. Deconstructive: Addresses the hidden issue of proving the pre-contract period.

I need to make sure I don't use names of laws if not mentioned, but describing the behavior is key.

One adjustment: The user asks "how to determine the value of compensation?". The abstract query should capture the basis for this.
Query 2: "أسس تقدير التعويض عن التأخير في الوفاء بالالتزامات العمالية". (Basis for estimating compensation for delay in fulfilling labor obligations). This is better than just "compensation for delay".

Let's refine the Arabic phrasing for the tool output.
1. المدة النظامية لتحويل النزاع العمالي من مكتب العمل إلى المحكمة
2. أسس تقدير التعويض عن المماطلة في سداد مستحقات نهاية الخدمة
3. قواعد تحميل أتعاب المحاماة في الدعاوي العمالية
4. إجراءات رفض الصلح الجزئي والتمسك بالحقوق النظامية
5. إثبات العلاقة العمالية المستمرة قبل توثيق العقد

Sectors: العمل والتوظيف (Pre-determined).

Final check on constraints:
- 5 queries max. (I have exactly 5).
- Sector: العمل والتوظيف.
- Types: Direct, Abstract, Deconstructive.

Query 1 is Direct.
Query 2 is Abstract (Step-back to principle of estimation for delay).
Query 3 is Deconstructive (Procedural issue).
Query 4 is Deconstructive (Procedural/Right issue).
Query 5 is Abstract/Deconstructive (Evidentiary issue).

Looks good.
