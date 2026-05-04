# 60 -- SSE Events

Total events captured: 62

## By type

- aggregator_done: 1
- plan_ready: 1
- planner_done: 1
- planner_start: 1
- preprocess_done: 1
- source_views_attached: 1
- status: 56

## Tail (last 200)

```
[planner_start] {"event": "planner_start", "model": "qwen3.6-plus"}
[planner_done] {"event": "planner_done", "invoke": ["reg", "compliance", "cases"], "focus": {"reg": "default", "compliance": "high", "cases": "high"}, "model": "qwen3.6-plus", "duration_s": 16.438}
[plan_ready] {"event": "plan_ready", "plan": {"invoke": ["reg", "compliance", "cases"], "focus": {"reg": "default", "compliance": "high", "cases": "high"}, "sectors": ["العمل والتوظيف"], "rationale": "سؤال عمالي متشعب يغطي إجراءات نقل القضية من مكتب الع...
[status] {"type": "status", "text": "جاري البحث في الأنظمة واللوائح: المدة النظامية لتحويل النزاع العمالي من مكتب العمل إلى المحكمة..."}
[status] {"type": "status", "text": "جاري البحث في قاعدة بيانات الأنظمة..."}
[status] {"type": "status", "text": "جاري البحث في الأنظمة واللوائح: أسس تقدير التعويض عن المماطلة في سداد مستحقات نهاية الخدمة..."}
[status] {"type": "status", "text": "جاري البحث في قاعدة بيانات الأنظمة..."}
[status] {"type": "status", "text": "جاري البحث في الأنظمة واللوائح: قواعد تحميل أتعاب المحاماة في الدعاوي العمالية..."}
[status] {"type": "status", "text": "جاري البحث في قاعدة بيانات الأنظمة..."}
[status] {"type": "status", "text": "جاري البحث في الأنظمة واللوائح: إجراءات رفض الصلح الجزئي والتمسك بالحقوق النظامية..."}
[status] {"type": "status", "text": "جاري البحث في قاعدة بيانات الأنظمة..."}
[status] {"type": "status", "text": "جاري البحث في الأنظمة واللوائح: إثبات العلاقة العمالية المستمرة قبل توثيق العقد..."}
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
[status] {"type": "status", "text": "تصفية حسب القطاعات: العمل والتوظيف"}
[status] {"type": "status", "text": "جاري تنفيذ 5 استعلامات بحث..."}
[status] {"type": "status", "text": "تم استلام 105 نتيجة -- جاري التقييم والتحليل..."}
[status] {"type": "status", "text": "جاري إعادة ترتيب وتصفية النتائج (5 استعلام بالتوازي)..."}
[status] {"type": "status", "text": "تم تصفية النتائج: 1 نتيجة محتفظ بها، 104 محذوفة"}
[status] {"type": "status", "text": "بحث [principle]: كنت موظف في شركه من 2012 بدون عقد الا نهايه 2018 قبل نهاية السنه ب 4 ش..."}
[status] {"type": "status", "text": "حدث خطأ أثناء توسيع الاستعلامات (sectioned)."}
[status] {"type": "status", "text": "جاري تنفيذ 1 استعلامات مُقنّنة على 1 قناة..."}
[status] {"type": "status", "text": "تم استرجاع 30 حكم فريد عبر القنوات — جاري الدمج..."}
[status] {"type": "status", "text": "تم الدمج: 15 حكم في القائمة الموحّدة، 10 مبدأ، 0 وقائع، 0 اسانيد"}
[status] {"type": "status", "text": "جاري تصنيف 15 حكم موزّعة على 1 استعلام (بالتوازي)..."}
[status] {"type": "status", "text": "اكتملت تصفية الأحكام (sectioned): 13 محتفظ به، 2 محذوف"}
[status] {"type": "status", "text": "تم توليد 5 استعلامات بحث (5 احتياج تنفيذي — الجولة 1)"}
[status] {"type": "status", "text": "جاري تنفيذ 5 استعلامات بحث..."}
[status] {"type": "status", "text": "تم استرجاع 63 خدمة حكومية فريدة."}
[status] {"type": "status", "text": "جاري تصنيف 63 خدمة..."}
[status] {"type": "status", "text": "تم الاحتفاظ بـ 3 خدمة ذات صلة — الجودة: غير كافية"}
[status] {"type": "status", "text": "جاري إعادة البحث (الجولة 2)..."}
[status] {"type": "status", "text": "تم توليد 5 استعلامات بحث (5 احتياج تنفيذي — الجولة 2)"}
[status] {"type": "status", "text": "جاري تنفيذ 5 استعلامات بحث..."}
[status] {"type": "status", "text": "تم استرجاع 94 خدمة حكومية فريدة."}
[status] {"type": "status", "text": "جاري تصنيف 94 خدمة..."}
[status] {"type": "status", "text": "تم الاحتفاظ بـ 4 خدمة ذات صلة — الجودة: غير كافية"}
[status] {"type": "status", "text": "جاري إعادة البحث (الجولة 3)..."}
[status] {"type": "status", "text": "تم توليد 2 استعلامات بحث (2 احتياج تنفيذي — الجولة 3)"}
[status] {"type": "status", "text": "جاري تنفيذ 2 استعلامات بحث..."}
[status] {"type": "status", "text": "تم استرجاع 103 خدمة حكومية فريدة."}
[status] {"type": "status", "text": "جاري تصنيف 103 خدمة..."}
[status] {"type": "status", "text": "تم الاحتفاظ بـ 6 خدمة ذات صلة — الجودة: كافية"}
[status] {"type": "status", "text": "اكتمل البحث — الجودة: moderate"}
[preprocess_done] {"event": "preprocess_done", "ref_count": 17}
[source_views_attached] {"event": "source_views_attached", "count": 17}
[aggregator_done] {"event": "aggregator_done", "duration_s": 112.86064700013958, "passed": true, "model": "qwen3.6-plus", "ref_count": 12, "ref_count_pre_filter": 17, "cited": 12}
```