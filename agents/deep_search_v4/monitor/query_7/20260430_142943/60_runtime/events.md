# 60 -- SSE Events

Total events captured: 34

## By type

- plan_ready: 1
- planner_done: 1
- planner_start: 1
- status: 31

## Tail (last 200)

```
[planner_start] {"event": "planner_start", "model": "qwen3.6-plus"}
[planner_done] {"event": "planner_done", "invoke": ["reg"], "focus": {"reg": "default"}, "model": "qwen3.6-plus", "duration_s": 9.229}
[plan_ready] {"event": "plan_ready", "plan": {"invoke": ["reg"], "focus": {"reg": "default"}, "sectors": null, "rationale": "سؤال يقتصر على الحكم النظامي للرجعة وانقضاء العدة في نظام الأحوال الشخصية؛ لا حاجة لإجراء حكومي أو سابقة قضائية. لا يوجد قطاع «أ...
[status] {"type": "status", "text": "جاري البحث في الأنظمة واللوائح: شروط طلب الزوجة الطلاق أو الخلع من القاضي..."}
[status] {"type": "status", "text": "جاري البحث في قاعدة بيانات الأنظمة..."}
[status] {"type": "status", "text": "جاري البحث في الأنظمة واللوائح: أثر انقضاء العدة على بقاء عقد الزواج..."}
[status] {"type": "status", "text": "جاري البحث في قاعدة بيانات الأنظمة..."}
[status] {"type": "status", "text": "جاري البحث في الأنظمة واللوائح: الأحكام العامة لوقوع الطلاق وانحلال عقد النكاح..."}
[status] {"type": "status", "text": "جاري البحث في قاعدة بيانات الأنظمة..."}
[status] {"type": "status", "text": "جاري البحث في الأنظمة واللوائح: إجراءات التوثيق القضائي للطلاق بعد انتهاء العدة..."}
[status] {"type": "status", "text": "جاري البحث في قاعدة بيانات الأنظمة..."}
[status] {"type": "status", "text": "جاري البحث في الأنظمة واللوائح: المركز القانوني للزوجة بعد انتهاء العدة دون حكم طلاق..."}
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
[status] {"type": "status", "text": "تصفية حسب القطاعات: العدل والقضاء | الأحوال المدنية والجوازات"}
[status] {"type": "status", "text": "جاري تنفيذ 5 استعلامات بحث..."}
[status] {"type": "status", "text": "تم استلام 105 نتيجة -- جاري التقييم والتحليل..."}
[status] {"type": "status", "text": "جاري إعادة ترتيب وتصفية النتائج (5 استعلام بالتوازي)..."}
[status] {"type": "status", "text": "تم تصفية النتائج: 44 نتيجة محتفظ بها، 206 محذوفة"}
```