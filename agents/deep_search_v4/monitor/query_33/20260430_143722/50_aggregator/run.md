# aggregator — query_0/

| | |
|---|---|
| **Duration** | 108.1s |
| **Prompt** | prompt_1 |
| **Model** | qwen3.6-plus |
| **References** | 8 |
| **Cited** | 8 |
| **Validation** | PASS |
| **Confidence** | low |

## Original Query
> سوابق قضائية في الفصل التعسفي

## Sub-queries
1. ضوابط الفصل التعسفي وشروط تحققه في عقود العمل
2. مبدأ إساءة استعمال الحق في إنهاء العقود
3. أسس تقدير التعويضات المالية عن الفصل غير المشروع
4. عبء الإثبات في المنازعات العمالية الخاصة بالفصل
5. اختصاص المحاكم العمالية وإجراءات الطعن في أحكام الفصل
6. سوابق قضائية في الفصل التعسفي

## Validation
- passed: true
- dangling_citations: []
- unused_references: []
- ungrounded_snippets: []
- arabic_only_ok: true
- structure_ok: true
- gap_honesty_ok: true
- sub_query_coverage: 1.00

## Gaps
1. غياب النصوص النظامية المباشرة من نظام العمل التي تُعرف الفصل التعسفي وتحدد عقوبته وتعويضاته.
2. عدم توفر أي سوابق قضائية فعلية أو مبادئ مستقرة من أحكام المحاكم حول الفصل التعسفي.
3. افتقاد التفاصيل الإجرائية الدقيقة للطعن في أحكام الفصل التعسفي أمام محكمة الاستئناف العمالية.
4. لم تُغطَّ بشكل كافٍ: [regulations] النوع: مباشر | الزاوية: استهداف النصوص النظامية المحددة التي تعرّف الفصل التعسفي وتبين حكمه وشروط تحققه مباشرة.
5. لم تُغطَّ بشكل كافٍ: [regulations] النوع: تجريدي | الزاوية: الارتقاء للمبدأ القانوني العام (إساءة استعمال الحق) الحاكم لفسخ العقود، لاستجلاب القواعد التأسيسية في الأبواب المعنية.
6. لم تُغطَّ بشكل كافٍ: [regulations] النوع: تفكيكي | الزاوية: تفكيك الجانب المالي المستقل لبحث المعايير والأنظمة الحاكمة لحساب نسبة ومقدار التعويض.
7. لم تُغطَّ بشكل كافٍ: [cases] [principle] Fallback: sectioned expander failed

## Files
- [synthesis.md](synthesis.md)
- [references.json](references.json)
- [validation.json](validation.json)
- [thinking.md](thinking.md)
- [prompt_single.md](prompt_single.md)
- [llm_raw_single.txt](llm_raw_single.txt)