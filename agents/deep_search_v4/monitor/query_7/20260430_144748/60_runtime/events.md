# 60 -- SSE Events

Total events captured: 35

## By type

- error: 1
- plan_ready: 1
- planner_done: 1
- planner_start: 1
- status: 31

## Tail (last 200)

```
[planner_start] {"event": "planner_start", "model": "qwen3.6-plus"}
[planner_done] {"event": "planner_done", "invoke": ["reg"], "focus": {"reg": "default"}, "model": "qwen3.6-plus", "duration_s": 12.931}
[plan_ready] {"event": "plan_ready", "plan": {"invoke": ["reg"], "focus": {"reg": "default"}, "sectors": null, "rationale": "سؤال يقتصر على الحكم النظامي للرجعة وانقضاء العدة في نظام الأحوال الشخصية؛ لا حاجة لإجراء أو سابقة. لا قطاع مطابق في القاموس فأع...
[status] {"type": "status", "text": "جاري البحث في الأنظمة واللوائح: أثر قول الزوجة لزوجها برفض العلاقة الزوجية في إثبات الطلاق..."}
[status] {"type": "status", "text": "جاري البحث في قاعدة بيانات الأنظمة..."}
[status] {"type": "status", "text": "جاري البحث في الأنظمة واللوائح: الحكم على اعتبار الزوجة طالقة تلقائياً بعد انقضاء العدة دون حكم قضائي..."}
[status] {"type": "status", "text": "جاري البحث في قاعدة بيانات الأنظمة..."}
[status] {"type": "status", "text": "جاري البحث في الأنظمة واللوائح: أحكام الخلع والتفريق القضائي بطلب من الزوجة..."}
[status] {"type": "status", "text": "جاري البحث في قاعدة بيانات الأنظمة..."}
[status] {"type": "status", "text": "جاري البحث في الأنظمة واللوائح: الضوابط النظامية لانحلال عقد الزواج بمبادرة من أحد الطرفين..."}
[status] {"type": "status", "text": "جاري البحث في قاعدة بيانات الأنظمة..."}
[status] {"type": "status", "text": "جاري البحث في الأنظمة واللوائح: الإجراءات القضائية لمطالبة الزوجة بإنهاء العلاقة الزوجية أو الخلع..."}
[status] {"type": "status", "text": "جاري البحث في قاعدة بيانات الأنظمة..."}
[status] {"type": "status", "text": "تم اختيار 21 نتيجة (عتبة: 0.005)"}
[status] {"type": "status", "text": "جاري استخراج التفاصيل لأفضل 21 نتيجة..."}
[status] {"type": "status", "text": "تم اختيار 21 نتيجة (عتبة: 0.005)"}
[status] {"type": "status", "text": "جاري استخراج التفاصيل لأفضل 21 نتيجة..."}
[error] {"type": "error", "text": "hybrid_search_sections: <ConnectionTerminated error_code:1, last_stream_id:29, additional_data:None>"}
[status] {"type": "status", "text": "تم اختيار 14 نتيجة (عتبة: 0.005)"}
[status] {"type": "status", "text": "جاري استخراج التفاصيل لأفضل 14 نتيجة..."}
[status] {"type": "status", "text": "تم اختيار 21 نتيجة (عتبة: 0.005)"}
[status] {"type": "status", "text": "جاري استخراج التفاصيل لأفضل 21 نتيجة..."}
[status] {"type": "status", "text": "تم استرجاع 21 نتيجة من الأنظمة واللوائح."}
[status] {"type": "status", "text": "تم استرجاع 14 نتيجة من الأنظمة واللوائح."}
[status] {"type": "status", "text": "تم استرجاع 21 نتيجة من الأنظمة واللوائح."}
[status] {"type": "status", "text": "تم اختيار 21 نتيجة (عتبة: 0.005)"}
[status] {"type": "status", "text": "جاري استخراج التفاصيل لأفضل 21 نتيجة..."}
[status] {"type": "status", "text": "تم استرجاع 21 نتيجة من الأنظمة واللوائح."}
[status] {"type": "status", "text": "تم استرجاع 21 نتيجة من الأنظمة واللوائح."}
[status] {"type": "status", "text": "تم توليد 5 استعلامات بحث (الجولة 1)"}
[status] {"type": "status", "text": "تصفية حسب القطاعات: الأحوال المدنية والجوازات | العدل والقضاء"}
[status] {"type": "status", "text": "جاري تنفيذ 5 استعلامات بحث..."}
[status] {"type": "status", "text": "تم استلام 98 نتيجة -- جاري التقييم والتحليل..."}
[status] {"type": "status", "text": "جاري إعادة ترتيب وتصفية النتائج (5 استعلام بالتوازي)..."}
[status] {"type": "status", "text": "تم تصفية النتائج: 41 نتيجة محتفظ بها، 165 محذوفة"}
```