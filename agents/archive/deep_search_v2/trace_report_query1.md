# Workflow Trace Report — Query #1: Threat, Stalking & Blackmail

**Log ID:** `20260402_070225_888318`  
**Duration:** 426.9s (7.1 min)  
**Status:** SUCCESS  
**PlanAgent tokens:** 8,548 (5,304 input + 3,244 output)  
**PlanAgent requests:** 2 LLM calls  
**Search loops:** 2 (running in parallel)

---

## 1. User Input

```
في شخص كنت بعلاقة معه، والآن أنا حابة أنهي العلاقة تمامًا. بس المشكلة إنه رافض ويضغط علي،
وأحيانًا يتصرف بشكل مريب مثل المراقبة أو التهديد بكلام غير مباشر، وطلب مني أسوي له أشياء ضد رغبتي.
أنا ما بلّغت ولا اشتكيت، بس حابة أعرف:
1. هل فيه ضرر قانوني ممكن يرجع علي لو تكلم عن علاقتنا أو ادعى أشياء؟
2. هل فيه طريقة أنهي الموضوع بدون ما أروح للشرطة أو تتأثر سمعتي؟
3. ولو وصلت الأمور للتهديد أو الملاحقة، إيش الخطوات القانونية اللي أقدر أسويها بدون ضرر لي؟
```

---

## 2. PlanAgent — Request 1 (Gemini 3.1 Pro)

**Input:** User query (above)  
**Decision:** Complex query with 2 distinct legal axes → invoke `invoke_search_loop` twice **in parallel**

### Tool Call 1: Loop A — Victim liability & privacy

```
invoke_search_loop(
  sub_question = "المسؤولية القانونية للضحية في حال وجود علاقة سابقة، وضمانات السرية
                   وحماية سمعة الضحية عند التبليغ عن التهديد أو الابتزاز في النظام السعودي.",
  context      = "المستخدمة تسأل عن تصرفات شخص ترفض إكمال العلاقة معه، يقوم بالمراقبة
                   والتهديد المبطن والطلب بأشياء ضد رغبتها. تسأل عن تضررها القانوني إذا
                   كشف العلاقة، طرق الإنهاء بدون شرطة، والإجراءات القانونية للتهديد
                   والملاحقة بضمان السرية وبدون تضررها."
)
```

### Tool Call 2: Loop B — Penalties & reporting procedures

```
invoke_search_loop(
  sub_question = "الإجراءات القانونية والعقوبات في النظام السعودي لجرائم التهديد، الابتزاز،
                   المراقبة، والملاحقة (التحرش)، وكيفية تقديم بلاغات سرية (مثل تطبيق كلنا أمن).",
  context      = "المستخدمة تسأل حول خيارات التعامل مع المراقبة، التهديد غير المباشر،
                   ومحاولات الإكراه من شخص كانت في علاقة معه."
)
```

---

## 3. Loop B — Penalties & Procedures (2 rounds)

### Round 1

#### Expander Agent (MiniMax M2.7) — Output

```json
{
  "queries": [
    { "tool": "regulations", "query": "عقوبة التهديد والإكراه في النظام السعودي وأحكام المادة ذات الصلة" },
    { "tool": "regulations", "query": "جرائم الابتزاز الإلكتروني والتهديد عبر وسائل التقنية في نظام مكافحة الجرائم المعلوماتية" },
    { "tool": "regulations", "query": "نظام مكافحة التحرش وآلية الإبلاغ عن الملاحقة والمراقبة وضمانات حماية المجني عليها" },
    { "tool": "compliance", "query": "آلية تقديم بلاغات سرية وتطبيق كلنا أمن للتهديد والملاحقة والخطوات الإجرائية" }
  ],
  "status_message": "جاري البحث عن الإجراءات القانونية والعقوبات..."
}
```

#### SearchNode — Execution (4 queries concurrent)

| # | Tool | Query | Pipeline | Results |
|---|------|-------|----------|---------|
| 1 | regulations | عقوبة التهديد والإكراه... | embed → 3x RPC → Jina rerank → unfold | 10 results |
| 2 | regulations | جرائم الابتزاز الإلكتروني... | embed → 3x RPC → Jina rerank → unfold | 30 results |
| 3 | regulations | نظام مكافحة التحرش... | embed → 3x RPC → Jina rerank → unfold | 22 results |
| 4 | compliance | آلية تقديم بلاغات سرية... | embed → search_services RPC → Jina rerank | 3 results |
| | | | **Total** | **65 results** |

#### Aggregator Agent (MiniMax M2.7) — Output

```
sufficient: false
coverage_assessment: "نتائج الأنظمة قوية في جانب التحرش والابتزاز الإلكتروني،
                       لكن نتائج السوابق القضائية غير كافية"
weak_axes: [{ tool: "regulations", reason: "تفاصيل إضافية مطلوبة عن عقوبات محددة" }]
strong_results_summary: "تغطية جيدة لنظام مكافحة الجرائم المعلوماتية ونظام مكافحة التحرش والخدمات الحكومية"
synthesis_md: (8,586 chars — legal analysis covering penalties from both systems)
citations: 10
```

**Decision: Loop back → Round 2** (weak regulations axis)

---

### Round 2

#### Expander Agent — FAILED

```
ERROR: Exceeded maximum retries (2) for output validation
```

MiniMax M2.7 could not produce valid `ExpanderOutput` structured JSON. **Fallback activated:** system used the original sub-question as a single regulations query.

#### SearchNode — Execution (1 fallback query)

| # | Tool | Query | Results |
|---|------|-------|---------|
| 1 | regulations | الإجراءات القانونية والعقوبات في النظام السعودي لجرائم التهديد، الابتزاز، المراقبة... | 28 results |

#### Aggregator Agent — FAILED

```
ERROR: Exceeded the output_tokens_limit of 6000 (output_tokens=6531)
```

The synthesis exceeded the 6,000 token cap. **Fallback activated:** ReportNode used the Round 1 aggregator synthesis.

#### ReportNode — Output

```
report_md: 10,176 chars (built from Round 1 synthesis)
artifact_id: None (DB insert failed — 'citations' column missing in artifacts table)
```

### Loop B Summary

```
rounds_used: 2
report_length: 10,176 chars
citations: 10
errors: Expander validation failure (round 2), Aggregator token limit exceeded (round 2), DB insert failed
```

---

## 4. Loop A — Victim Liability & Privacy (3 rounds)

### Round 1

#### Expander Agent (MiniMax M2.7) — Output

```json
{
  "queries": [
    { "tool": "regulations", "query": "حماية الضحية من التهديد والابتزاز ضمانات السرية الهوية في النظام السعودي" },
    { "tool": "regulations", "query": "المسؤولية القانونية للضحية في الجرائم المرتكبة ضدها وجود علاقة سابقة" },
    { "tool": "regulations", "query": "الإجراءات القانونية للإبلاغ عن التهديد والملاحقة بدون الكشف عن هوية المبلغ" },
    { "tool": "cases",       "query": "أحكام حماية الضحية من التهديد المبطن والمراقبة في العلاقات السابقة" }
  ],
  "status_message": "جاري البحث عن الأحكام والنصوص القانونية..."
}
```

#### SearchNode — Execution (4 queries concurrent)

| # | Tool | Query | Results |
|---|------|-------|---------|
| 1 | regulations | حماية الضحية من التهديد... | 30 results |
| 2 | regulations | المسؤولية القانونية للضحية... | 28 results |
| 3 | regulations | الإجراءات القانونية للإبلاغ... | 34 results |
| 4 | cases | أحكام حماية الضحية... | 10 results (embed → search_cases RPC → Jina rerank) |
| | | **Total** | **102 results** |

#### Aggregator Agent — Output

```
sufficient: false
weak_axes: [{ tool: "cases", reason: "لم يتم العثور على سوابق قضائية كافية تخص مسؤولية الضحية" }]
strong_results_summary: "نتائج الأنظمة تغطي حماية المبلغين والضحايا بشكل جيد"
synthesis_md: (3,518 chars)
citations: 9
```

**Decision: Loop back → Round 2** (weak cases axis)

---

### Round 2

#### Expander Agent — Output (cases-only re-search)

```json
{
  "queries": [
    { "tool": "cases", "query": "سوابق قضائية: مسؤولية الضحية القانونية عند وجود علاقة سابقة مع الجاني في جرائم التهديد" },
    { "tool": "cases", "query": "أحكام قضائية: الحماية من التهديد والابتزاز في العلاقات الشخصية وضمانات سرية البلاغ" },
    { "tool": "cases", "query": "مبادئ قضائية: عدم تأثير سلوك المجني عليه السابق على مسؤوليتهن في الجرائم المرتكبة" }
  ],
  "status_message": "جاري البحث عن سوابق قضائية..."
}
```

Note: Expander correctly generated queries **only for the weak tool (cases)**, following the aggregator's guidance.

#### SearchNode — Execution (3 queries concurrent)

| # | Tool | Query | Results |
|---|------|-------|---------|
| 1 | cases | سوابق قضائية: مسؤولية الضحية... | 10 results |
| 2 | cases | أحكام قضائية: الحماية من التهديد... | 10 results |
| 3 | cases | مبادئ قضائية: عدم تأثير... | 10 results |
| | | **Total** | **30 results** |

#### Aggregator Agent — Output

```
sufficient: false
weak_axes: [
  { tool: "regulations", reason: "تفاصيل إضافية مطلوبة" },
  { tool: "cases",       reason: "السوابق لم تعالج المسألة مباشرة" }
]
synthesis_md: (6,848 chars — expanded analysis incorporating court cases)
```

**Decision: Loop back → Round 3** (still insufficient)

---

### Round 3 (final)

#### Expander Agent — Output (broader re-search)

```json
{
  "queries": [
    { "tool": "regulations", "query": "تأثير العلاقة السابقة بين الطرفين على المسؤولية القانونية في جرائم التهديد والابتزاز" },
    { "tool": "regulations", "query": "الإجراءات القانونية للتهديد والابتزاز في النظام السعودي وضمانات سرية هوية المجني عليه" },
    { "tool": "compliance",  "query": "التبليغ عن التهديد والابتزاز مع الحفاظ على سرية الهوية الجهات المختصة الإجراءات" },
    { "tool": "cases",       "query": "أحكام قضائية سعودية في جرائم التهديد والابتزاز وحماية الضحية من كشف العلاقة" }
  ],
  "status_message": "جاري البحث عن المسؤولية القانونية..."
}
```

#### SearchNode — Execution (4 queries concurrent)

| # | Tool | Query | Results | Notes |
|---|------|-------|---------|-------|
| 1 | regulations | تأثير العلاقة السابقة... | 10 results | |
| 2 | regulations | الإجراءات القانونية... | 46 results | |
| 3 | compliance | التبليغ عن التهديد... | 3 results | Jina 429 → cosine fallback |
| 4 | cases | أحكام قضائية سعودية... | 10 results | |
| | | **Total** | **69 results** | |

#### Aggregator Agent — max rounds reached, proceeded to ReportNode

#### ReportNode — Output

```
report_md: 8,918 chars
artifact_id: None (DB insert failed — 'citations' column missing)
```

### Loop A Summary

```
rounds_used: 3
report_length: 8,918 chars
citations: 9
errors: Jina 429 (round 3, cosine fallback), DB insert failed
```

---

## 5. PlanAgent — Request 2 (Final Response)

### Input (tool returns from both loops)

**Loop A return:**
```
تم البحث عن: المسؤولية القانونية للضحية في حال وجود علاقة سابقة...
عدد الجولات: 3
الملخص: في النظام القانوني السعودي، وجود علاقة سابقة بين الجاني والضحية في
جرائم التهديد أو الابتزاز لا يُسقط المسؤولية الجنائية للجاني ولا يُخفف عقوبته،
إذ لا يعتد النظام برضا المجني عليه في الجرائم المماثلة. ويتمتع الضحية بإطار
حماية شامل عبر نظام حماية المبلغين والشهود والخبراء والضحايا، الذي يضمن سرية
هويته وإخفاء بياناته في جميع الوثائق الرسمية، مع إمكانية استخدام الاتصال المرئي
وتغيير الصوت وإخفاء الملامح عند الإدلاء بشهادته، فضلاً عن 13 نوعاً من الحماية
الأمنية والاجتماعية والمالية. كما تحظر المادة السادسة عشرة الكشف عن أي معلومات
تُعرّف بهوية الضحية، وتُقرر عقوبات بالسجن وغرامة ضد من يكشف هذه المعلومات.
ويمكن الإبلاغ عن التهديد أو الابتزاز مع الحفاظ على السرية عبر بوابة هيئة الرقابة
ومكافحة الفساد أو ديوان المظالم.
عدد الاستشهادات: 9
```

**Loop B return:**
```
تم البحث عن: الإجراءات القانونية والعقوبات في النظام السعودي لجرائم التهديد...
عدد الجولات: 2
الملخص: حدد النظام السعودي عقوبات متدرجة لجرائم التهديد والابتزاز الإلكتروني
تصل إلى 10 سنوات سجن و5 ملايين ريال غرامة في أخطر الحالات (المادة 7 من نظام
مكافحة جرائم المعلوماتية)، في حين تتراوح عقوبات التحرش بين سنتين و5 سنوات سجن
حسب ظروف التشديد كاستهداف الأطفال أو ذوي الإعاقة أو استغلال السلطة (المادة 6
من نظام مكافحة جريمة التحرش). ويحق لأي شخص الإبلاغ عن هذه الجرائم مع الحفاظ على
سرية هوية المجني عليه، مع جواز الإعفاء من العقوبة للمبتدئ بالإبلاغ قبل وقوع الضرر.
عدد الاستشهادات: 10
```

### Output — `final_result`

**task_done:** true  
**end_reason:** completed

**answer_ar** (1,458 chars — conversational response):
```
أهلاً بك. وضعك مفهوم تماماً، والنظام السعودي صُمم لحماية الضحايا في مثل هذه الحالات
بصرامة وبسرية تامة.

1. هل يقع عليك ضرر قانوني بسبب العلاقة السابقة؟
لا، النظام لا يعتد بوجود علاقة سابقة كمبرر أو مخفف لجريمة التهديد أو الابتزاز
والملاحقة. الجاني هو من يتحمل المسؤولية الجنائية الكاملة، والنظام يركز على حمايتك
أنتِ كضحية للتهديد ولن تُعاقبي بسبب هذه العلاقة.

2. طرق الإنهاء بسرية وبدون الإضرار بالسمعة:
يمكنك تقديم بلاغ بسرية تامة دون الحاجة للمراجعة الحضورية. الأنظمة السعودية (مثل نظام
حماية المبلغين والشهود والضحايا) تضمن إخفاء هويتك بالكامل. يمكنك استخدام تطبيق
(كلنا أمن) لتقديم بلاغ إلكتروني سري.

3. الخطوات القانونية عند تطور الأمر لتهديد وملاحقة:
- تسجيل الأدلة: احتفظي بأي رسائل، مكالمات، محاولات تواصل
- الإبلاغ السري: قدمي بلاغاً عبر تطبيق "كلنا أمن"
- الحماية: يوفر النظام 13 نوعاً من الحماية، وعقوبات تصل لسجن 2-10 سنوات وغرامات ضخمة

لقد أعددت لكِ تقريراً قانونياً مفصلاً يوضح العقوبات والإجراءات ونصوص الحماية النظامية.
```

**artifact_md** (3,692 chars — full legal report):
```markdown
# التقرير القانوني: الحماية النظامية لضحايا التهديد والملاحقة والابتزاز

## أولاً: الموقف القانوني للضحية في حال وجود علاقة سابقة
- لا تسقط المسؤولية الجنائية عن الجاني
- حماية الضحية هي الأولوية

## ثانياً: ضمانات السرية وحماية سمعة الضحية
1. نظام حماية المبلغين والشهود والخبراء والضحايا
2. سرية الإدلاء بالأقوال (تقنيات مرئية، تغيير الصوت، إخفاء الملامح)
3. تجريم إفشاء هوية الضحية (المادة السادسة عشرة)
4. 13 نوعاً متدرجاً من الحماية

## ثالثاً: الخطوات القانونية وطرق التبليغ السرية
1. توثيق الأدلة
2. التبليغ عبر تطبيق (كلنا أمن) أو النيابة العامة

## رابعاً: العقوبات النظامية الرادعة للجاني
1. نظام مكافحة جرائم المعلوماتية: سجن حتى سنة + 500K ريال (عادي) / 10 سنوات + 5M ريال (مشدد)
2. نظام مكافحة جريمة التحرش: سنتين + 100K ريال (أساسي) / 5 سنوات + 300K ريال (مشدد)
```

**search_summary:**
```
تم البحث في قواعد النظام السعودي حول المركز القانوني لضحية التهديد والابتزاز عند وجود
علاقة سابقة، وتبين أن العلاقة السابقة لا تعفي الجاني من المسؤولية ولا توقع ضرراً يعتد
به على مسار الشكوى بحق الضحية. كما تم توضيح آليات التبليغ السري الواسعة بموجب نظام
حماية المبلغين والشهود والضحايا، بالإضافة لتوضيح عقوبات الملاحقة والتحرش ونظام مكافحة
الجرائم المعلوماتية.
```

---

## 6. SSE Event Timeline (119 events)

| Time Window | Events | Phase |
|-------------|--------|-------|
| 0-1 | 2 | PlanAgent emits "searching for..." status for both loops |
| 2-3 | 2 | Loop B: Expander status + SearchNode starting 4 queries |
| 4-28 | 25 | Loop B Round 1: 4 search pipelines running (embed → RPC → rerank → unfold) |
| 29-30 | 2 | Loop B Round 1: 65 results received → Aggregator evaluating |
| 31 | 1 | Loop B Round 1: Aggregator says "insufficient" → looping back |
| 32 | 1 | Loop B Round 2: Expander FAILED (validation error) |
| 33-40 | 8 | Loop B Round 2: Fallback query → 28 results |
| 41-43 | 3 | Loop B Round 2: Aggregator evaluation → FAILED (token limit) |
| 44 | 1 | Loop B: Report built but DB insert failed |
| 45-71 | 27 | Loop A Round 1: Expander + 4 queries → 102 results |
| 72-74 | 3 | Loop A Round 1: Aggregator says "insufficient cases" → looping back |
| 75-89 | 15 | Loop A Round 2: 3 cases-only queries → 30 results |
| 90-91 | 2 | Loop A Round 2: Aggregator says "still insufficient" → Round 3 |
| 92-115 | 24 | Loop A Round 3: 4 queries (regs + compliance + cases) → 69 results |
| 116-117 | 2 | Loop A Round 3: Max rounds → Aggregator evaluation |
| 118 | 1 | Loop A: Report built but DB insert failed |

---

## 7. Error Summary

| Error | Agent | Location | Impact |
|-------|-------|----------|--------|
| `Exceeded maximum retries (2) for output validation` | Expander (MiniMax M2.7) | Loop B, Round 2 | Fallback to raw sub-question as single query — degraded query diversity |
| `Exceeded output_tokens_limit of 6000 (output_tokens=6531)` | Aggregator (MiniMax M2.7) | Loop B, Round 2 | Used Round 1 synthesis instead — lost deeper analysis from new results |
| `Could not find 'citations' column of 'artifacts'` | ReportNode → Supabase | Both loops | Reports not saved to DB — artifact_id is None in both loops |
| `Jina 429 Too Many Requests` | Jina Reranker | Loop A, Round 3 | Cosine distance fallback — slightly degraded ranking quality |

---

## 8. Performance Breakdown

| Component | Calls | Approx. Time |
|-----------|-------|-------------|
| PlanAgent (Gemini 3.1 Pro) | 2 LLM requests | ~20s + ~37s = ~57s |
| Expander (MiniMax M2.7) | 5 calls (1 failed) | ~13s each = ~65s |
| Aggregator (MiniMax M2.7) | 5 calls (1 failed) | ~40-60s each = ~250s |
| SearchNode (embed + RPC + rerank) | 20 query executions | ~10s each concurrent = ~50s wall |
| ReportNode (no LLM) | 2 calls | ~1s each |
| **Total wall time** | | **426.9s** |

Aggregator is the dominant time cost (~60% of total), followed by Expander retries and PlanAgent thinking.
