# aggregator — query_0/

| | |
|---|---|
| **Duration** | 98.4s |
| **Prompt** | prompt_reg_only |
| **Model** | qwen3.6-plus |
| **References** | 12 |
| **Cited** | 11 |
| **Validation** | PASS |
| **Confidence** | medium |

## Original Query
> هل اقدر اقول لزوجي أنا رافضه ارجع، وبعد انقضاء العده هل اعتبر طالق منه؟

## Sub-queries
1. إجراءات طلب الخلع أو التطليق القضائي بطلب من الزوجة
2. ضوابط انقضاء العدة وإنهاء الرابطة الزوجية
3. إجراءات توثيق الطلاق أو الخلع لدى المحكمة المختصة
4. الأثر القانوني لرفض الزوجة معاشرت زوجها دون إجراءات نظامية
5. حكم عدة المرأة في حالة الخلع أو التطليق

## Validation
- passed: true
- dangling_citations: []
- unused_references: [8]
- ungrounded_snippets: []
- arabic_only_ok: true
- structure_ok: true
- gap_honesty_ok: true
- sub_query_coverage: 1.00
- notes:
  - unused references: [8]

## Gaps
1. النصوص لا توضح المدة الزمنية الدقيقة لعدة الخلع أو الطلاق (ثلاث حيضات أو غير ذلك)، بل اكتفت بذكر بداية احتسابها.
2. الأثر القانوني الدقيق لرفض الزوجة يقتصر على سقوط النفقة، دون تفصيل العقوبات الأخرى أو الإجراءات التأديبية المحتملة للامتناع دون حكم.
3. تفاصيل الإجراءات التنفيذية لطلب الفسخ أو الخلع عبر المنصات الإلكترونية لم ترد في المراجع.
4. لم تُغطَّ بشكل كافٍ: [regulations] مباشر: يستهدف الحكم المباشر لقول الزوجة بالرفض دون رفع دعوى أو إجراء رسمي، ويحدد ما إذا كان له أثر في إنهاء الزواج أو اعتبارها طالقة.
5. لم تُغطَّ بشكل كافٍ: [regulations] تفكيكي: يتناول المسألة الفرعية الخاصة بمدة ونوع العدة المترتبة على الطلاق أو الخلع بطلب من الزوجة، وهل تنقضي بانتهاء المدة تلقائياً.

## Files
- [synthesis.md](synthesis.md)
- [references.json](references.json)
- [validation.json](validation.json)
- [thinking.md](thinking.md)
- [prompt_single.md](prompt_single.md)
- [llm_raw_single.txt](llm_raw_single.txt)