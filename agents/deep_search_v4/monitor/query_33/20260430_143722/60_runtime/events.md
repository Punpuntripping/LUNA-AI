# 60 -- SSE Events

Total events captured: 64

## By type

- aggregator_done: 1
- error: 15
- plan_ready: 1
- planner_done: 1
- planner_start: 1
- preprocess_done: 1
- source_views_attached: 1
- status: 43

## Tail (last 200)

```
[planner_start] {"event": "planner_start", "model": "qwen3.6-plus"}
[planner_done] {"event": "planner_done", "invoke": ["reg", "cases"], "focus": {"reg": "default", "cases": "high"}, "model": "qwen3.6-plus", "duration_s": 23.142}
[plan_ready] {"event": "plan_ready", "plan": {"invoke": ["reg", "cases"], "focus": {"reg": "default", "cases": "high"}, "sectors": ["العمل والتوظيف"], "rationale": "طلب صريح للسوابق القضائية في الفصل التعسفي (cases: high)، مدعوماً بالأساس النظامي (reg: ...
[status] {"type": "status", "text": "بحث [principle]: سوابق قضائية في الفصل التعسفي..."}
[status] {"type": "status", "text": "حدث خطأ أثناء توسيع الاستعلامات (sectioned)."}
[status] {"type": "status", "text": "جاري تنفيذ 1 استعلامات مُقنّنة على 1 قناة..."}
[status] {"type": "status", "text": "تم استرجاع 30 حكم فريد عبر القنوات — جاري الدمج..."}
[status] {"type": "status", "text": "تم الدمج: 15 حكم في القائمة الموحّدة، 10 مبدأ، 0 وقائع، 0 اسانيد"}
[status] {"type": "status", "text": "جاري تصنيف 15 حكم موزّعة على 1 استعلام (بالتوازي)..."}
[status] {"type": "status", "text": "اكتملت تصفية الأحكام (sectioned): 0 محتفظ به، 15 محذوف"}
[status] {"type": "status", "text": "جاري البحث في الأنظمة واللوائح: ضوابط الفصل التعسفي وشروط تحققه في عقود العمل..."}
[status] {"type": "status", "text": "جاري البحث في قاعدة بيانات الأنظمة..."}
[status] {"type": "status", "text": "جاري البحث في الأنظمة واللوائح: مبدأ إساءة استعمال الحق في إنهاء العقود..."}
[status] {"type": "status", "text": "جاري البحث في قاعدة بيانات الأنظمة..."}
[status] {"type": "status", "text": "جاري البحث في الأنظمة واللوائح: عبء الإثبات في المنازعات العمالية الخاصة بالفصل..."}
[status] {"type": "status", "text": "جاري البحث في قاعدة بيانات الأنظمة..."}
[status] {"type": "status", "text": "جاري البحث في الأنظمة واللوائح: أسس تقدير التعويضات المالية عن الفصل غير المشروع..."}
[status] {"type": "status", "text": "جاري البحث في قاعدة بيانات الأنظمة..."}
[status] {"type": "status", "text": "جاري البحث في الأنظمة واللوائح: اختصاص المحاكم العمالية وإجراءات الطعن في أحكام الفصل..."}
[status] {"type": "status", "text": "جاري البحث في قاعدة بيانات الأنظمة..."}
[error] {"type": "error", "text": "hybrid_search_sections: [WinError 10035] A non-blocking socket operation could not be completed immediately"}
[error] {"type": "error", "text": "hybrid_search_regulations: [WinError 10035] A non-blocking socket operation could not be completed immediately"}
[error] {"type": "error", "text": "hybrid_search_articles: [WinError 10035] A non-blocking socket operation could not be completed immediately"}
[status] {"type": "status", "text": "لم تُعطِ تصفية القطاعات نتائج — جاري البحث بدون تصفية..."}
[error] {"type": "error", "text": "hybrid_search_sections: [WinError 10035] A non-blocking socket operation could not be completed immediately"}
[error] {"type": "error", "text": "hybrid_search_articles: [WinError 10035] A non-blocking socket operation could not be completed immediately"}
[error] {"type": "error", "text": "hybrid_search_regulations: [WinError 10035] A non-blocking socket operation could not be completed immediately"}
[status] {"type": "status", "text": "لم تُعطِ تصفية القطاعات نتائج — جاري البحث بدون تصفية..."}
[error] {"type": "error", "text": "hybrid_search_articles: [WinError 10035] A non-blocking socket operation could not be completed immediately"}
[error] {"type": "error", "text": "hybrid_search_sections: [WinError 10035] A non-blocking socket operation could not be completed immediately"}
[error] {"type": "error", "text": "hybrid_search_regulations: [WinError 10035] A non-blocking socket operation could not be completed immediately"}
[status] {"type": "status", "text": "لم تُعطِ تصفية القطاعات نتائج — جاري البحث بدون تصفية..."}
[error] {"type": "error", "text": "hybrid_search_articles: [WinError 10035] A non-blocking socket operation could not be completed immediately"}
[error] {"type": "error", "text": "hybrid_search_sections: [WinError 10035] A non-blocking socket operation could not be completed immediately"}
[error] {"type": "error", "text": "hybrid_search_regulations: [WinError 10035] A non-blocking socket operation could not be completed immediately"}
[status] {"type": "status", "text": "لم تُعطِ تصفية القطاعات نتائج — جاري البحث بدون تصفية..."}
[error] {"type": "error", "text": "hybrid_search_sections: [WinError 10035] A non-blocking socket operation could not be completed immediately"}
[error] {"type": "error", "text": "hybrid_search_regulations: [WinError 10035] A non-blocking socket operation could not be completed immediately"}
[error] {"type": "error", "text": "hybrid_search_articles: [WinError 10035] A non-blocking socket operation could not be completed immediately"}
[status] {"type": "status", "text": "لم تُعطِ تصفية القطاعات نتائج — جاري البحث بدون تصفية..."}
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
[status] {"type": "status", "text": "تم تصفية النتائج: 12 نتيجة محتفظ بها، 92 محذوفة"}
[preprocess_done] {"event": "preprocess_done", "ref_count": 8}
[source_views_attached] {"event": "source_views_attached", "count": 8}
[aggregator_done] {"event": "aggregator_done", "duration_s": 108.08389199990779, "passed": true, "model": "qwen3.6-plus", "ref_count": 8, "ref_count_pre_filter": 8, "cited": 8}
```