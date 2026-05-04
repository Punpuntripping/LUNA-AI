# 60 -- SSE Events

Total events captured: 37

## By type

- aggregator_done: 1
- plan_ready: 1
- planner_done: 1
- planner_start: 1
- preprocess_done: 1
- source_views_attached: 1
- status: 31

## Tail (last 200)

```
[planner_start] {"event": "planner_start", "model": "qwen3.6-plus"}
[planner_done] {"event": "planner_done", "invoke": ["reg"], "focus": {"reg": "default"}, "model": "qwen3.6-plus", "duration_s": 24.367}
[plan_ready] {"event": "plan_ready", "plan": {"invoke": ["reg"], "focus": {"reg": "default"}, "sectors": null, "rationale": "سؤال يقتصر على الحكم النظامي للرجعة وانقضاء العدة في نظام الأحوال الشخصية؛ لا حاجة لإجراء أو سابقة. لا قطاع مطابق في القاموس فأع...
[status] {"type": "status", "text": "جاري البحث في الأنظمة واللوائح: الأثر القانوني لرفض الزوجة معاشرت زوجها دون إجراءات نظامية..."}
[status] {"type": "status", "text": "جاري البحث في قاعدة بيانات الأنظمة..."}
[status] {"type": "status", "text": "جاري البحث في الأنظمة واللوائح: ضوابط انقضاء العدة وإنهاء الرابطة الزوجية..."}
[status] {"type": "status", "text": "جاري البحث في قاعدة بيانات الأنظمة..."}
[status] {"type": "status", "text": "جاري البحث في الأنظمة واللوائح: إجراءات طلب الخلع أو التطليق القضائي بطلب من الزوجة..."}
[status] {"type": "status", "text": "جاري البحث في قاعدة بيانات الأنظمة..."}
[status] {"type": "status", "text": "جاري البحث في الأنظمة واللوائح: حكم عدة المرأة في حالة الخلع أو التطليق..."}
[status] {"type": "status", "text": "جاري البحث في قاعدة بيانات الأنظمة..."}
[status] {"type": "status", "text": "جاري البحث في الأنظمة واللوائح: إجراءات توثيق الطلاق أو الخلع لدى المحكمة المختصة..."}
[status] {"type": "status", "text": "جاري البحث في قاعدة بيانات الأنظمة..."}
[status] {"type": "status", "text": "تم اختيار 21 نتيجة (عتبة: 0.005)"}
[status] {"type": "status", "text": "جاري استخراج التفاصيل لأفضل 21 نتيجة..."}
[status] {"type": "status", "text": "تم اختيار 21 نتيجة (عتبة: 0.005)"}
[status] {"type": "status", "text": "جاري استخراج التفاصيل لأفضل 21 نتيجة..."}
[status] {"type": "status", "text": "تم اختيار 21 نتيجة (عتبة: 0.005)"}
[status] {"type": "status", "text": "جاري استخراج التفاصيل لأفضل 21 نتيجة..."}
[status] {"type": "status", "text": "تم اختيار 21 نتيجة (عتبة: 0.005)"}
[status] {"type": "status", "text": "جاري استخراج التفاصيل لأفضل 21 نتيجة..."}
[status] {"type": "status", "text": "تم اختيار 21 نتيجة (عتبة: 0.005)"}
[status] {"type": "status", "text": "جاري استخراج التفاصيل لأفضل 21 نتيجة..."}
[status] {"type": "status", "text": "تم استرجاع 21 نتيجة من الأنظمة واللوائح."}
[status] {"type": "status", "text": "تم استرجاع 21 نتيجة من الأنظمة واللوائح."}
[status] {"type": "status", "text": "تم استرجاع 21 نتيجة من الأنظمة واللوائح."}
[status] {"type": "status", "text": "تم استرجاع 21 نتيجة من الأنظمة واللوائح."}
[status] {"type": "status", "text": "تم استرجاع 21 نتيجة من الأنظمة واللوائح."}
[status] {"type": "status", "text": "تم توليد 5 استعلامات بحث (الجولة 1)"}
[status] {"type": "status", "text": "تصفية حسب القطاعات: العدل والقضاء"}
[status] {"type": "status", "text": "جاري تنفيذ 5 استعلامات بحث..."}
[status] {"type": "status", "text": "تم استلام 105 نتيجة -- جاري التقييم والتحليل..."}
[status] {"type": "status", "text": "جاري إعادة ترتيب وتصفية النتائج (5 استعلام بالتوازي)..."}
[status] {"type": "status", "text": "تم تصفية النتائج: 38 نتيجة محتفظ بها، 79 محذوفة"}
[preprocess_done] {"event": "preprocess_done", "ref_count": 21}
[source_views_attached] {"event": "source_views_attached", "count": 21}
[aggregator_done] {"event": "aggregator_done", "duration_s": 98.38013490010053, "passed": true, "model": "qwen3.6-plus", "ref_count": 12, "ref_count_pre_filter": 21, "cited": 11}
```