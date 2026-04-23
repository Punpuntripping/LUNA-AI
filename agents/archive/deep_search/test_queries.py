"""Integration test: run 5 real Saudi legal queries through the deep_search planner.

All executor tools are mocked with domain-specific Arabic results.
The planner LLM is real (calls get_agent_model("deep_search_planner")).

Usage:
    python -m agents.deep_search.test_queries           # run all 5
    python -m agents.deep_search.test_queries 1         # run query #1 only
    python -m agents.deep_search.test_queries 2 4       # run queries #2 and #4
"""
from __future__ import annotations

import asyncio
import sys
import time
import logging
from unittest.mock import MagicMock, AsyncMock, patch

from agents.deep_search.deps import SearchDeps
from agents.deep_search.runner import handle_deep_search_turn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

# Fix Windows console encoding for Arabic output
import io, os
if os.name == "nt":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Test queries from test_queries.md
# ---------------------------------------------------------------------------

QUERIES = {
    1: {
        "name": "Foreign Investment & Real Estate Partnership",
        "query": (
            "لدي استفسار بشأن ضوابط مزاولة المقيم للأنشطة التجارية العقارية "
            "والتسويق والبيع والشراء والاستثمار\n"
            "وكيف أعقد شراكة مع مستثمر أجنبي وانا سعودية\n"
            "احتاج مشورتكم"
        ),
    },
    2: {
        "name": "Employment Rights & Final Settlement",
        "query": (
            "عندي إستفسار( كنت موظفة في قطاع خاص لمدة ٧ سنوات وما أخذت فيها "
            "إجازاتي السنوية وانهيت العقد بناءاً على مادة ٨٧ واستلمت فيها حقوقي "
            "ووقعت على ورقة المخالصة على أساس إن الحقوق كاملة ودحين اكتشفت إن "
            "منقصيني في الحقوق ١٥٠٠٠ هل أقدر أرفع دعوى والا خلاص ضاع حقي؟"
        ),
    },
    3: {
        "name": "Saudization Compliance",
        "query": (
            "سوالنا بالنسبة للسعودة في نشاطنا\n"
            "عندنا المحل في مركز تجاري بمدينة جدة.\n"
            "عندنا ٤ سعوديين ولكن ليس لهم وجود جسدي في المحل\n"
            "واحدة تعمل عن بعد والباقي يعملون عمل التسويق حسب مهنهم.\n"
            "نشاهد كثير بان فريق المراقبة من وزارة العمل يجي المحلات ويفرض الغرامات "
            "لعدم وجود جسدي للموظف السعودي.\n"
            "السوال:\n"
            "هل يلزم وجود جسدي للموظف السعودي في المحل او المكتب في مركز التجاري؟\n"
            "هل حكم المكتب مختلف عن حكم المحل؟\n"
            "هل كون المحل او المكتب في مركز تجاري يوثر في اختلاف القانون؟"
        ),
    },
    4: {
        "name": "Fraudulent Promissory Note",
        "query": (
            "استفسار بسيط شخص وقع على ورقه فاضيه وبعد فتره حصل نفسه مديون ب ١٦٠٠٠ "
            "الف ريال وتم رفع قضيه عليه اش الطريقه فيها هل فيه حل علماً ان المده "
            "تقريباً قبل ٥ سنوات ولم يحضر الجلسات ولم يكن عنده اي خلفيه عن ماحصل"
        ),
    },
    5: {
        "name": "Khul' (Divorce) Outside Saudi Arabia",
        "query": "انا سعودية ابغى اخلع زوجي وانا خارج المملكة",
    },
}

# ---------------------------------------------------------------------------
# Domain-specific mock executor results
# ---------------------------------------------------------------------------

MOCK_REGULATIONS = {
    "foreign_investment": """\
## نتائج البحث في الأنظمة - الاستثمار الأجنبي والعقارات
**الجودة: قوية**

### المادة 1 - نظام الاستثمار الأجنبي (المرسوم الملكي رقم م/1 لعام 1421هـ)
> يجوز للمستثمر الأجنبي الحصول على ترخيص للاستثمار في المملكة العربية السعودية في جميع القطاعات المسموح بها، سواء بشكل كلي أو بالمشاركة مع مستثمر سعودي.

### المادة 5 - نظام الاستثمار الأجنبي
> يتمتع المشروع المرخص له بموجب هذا النظام بجميع المزايا والحوافز والضمانات التي يتمتع بها المشروع الوطني.

### المادة 3 - نظام تملك غير السعوديين للعقار واستثماره
> يجوز للمستثمر الأجنبي المرخص له تملك العقار اللازم لمزاولة النشاط المرخص، بما في ذلك سكن العاملين.

### المادة 7 - نظام الشركات (الشراكة مع أجنبي)
> يجوز تأسيس شركة ذات مسؤولية محدودة بين طرف سعودي وطرف أجنبي، ويكون نصيب الشريك الأجنبي وفقاً للترخيص الصادر من وزارة الاستثمار.

**المصادر:**
- chunk_ref:foreign_invest_art1 | نظام الاستثمار الأجنبي - المادة 1
- chunk_ref:foreign_invest_art5 | نظام الاستثمار الأجنبي - المادة 5
- chunk_ref:real_estate_art3 | نظام تملك غير السعوديين للعقار - المادة 3
- chunk_ref:companies_art7 | نظام الشركات - المادة 7
""",
    "labor_settlement": """\
## نتائج البحث في الأنظمة - حقوق العمال والمخالصة
**الجودة: قوية**

### المادة 87 - نظام العمل السعودي
> إذا انتهت خدمة العامل وجب على صاحب العمل أن يدفع له مكافأة عن مدة خدمته تحسب على أساس أجر نصف شهر عن كل سنة من السنوات الخمس الأولى، وأجر شهر عن كل سنة من السنوات التالية.

### المادة 111 - نظام العمل (الإجازات السنوية)
> يستحق العامل عن كل سنة إجازة سنوية لا تقل مدتها عن واحد وعشرين يوماً، تُزاد إلى مدة لا تقل عن ثلاثين يوماً إذا أمضى العامل في خدمة صاحب العمل خمس سنوات متصلة.

### المادة 113 - نظام العمل (البدل النقدي للإجازات)
> يجب على صاحب العمل أن يدفع للعامل أجراً عن أيام الإجازة المستحقة إذا ترك العمل قبل استعمالها.

### المادة 215 - نظام العمل (التقادم)
> لا تقبل أمام هيئات تسوية الخلافات العمالية أي دعوى تتعلق بالمطالبة بحق من حقوق العامل بعد مضي اثني عشر شهراً من تاريخ انتهاء علاقة العمل.

### فقه العمل - المخالصة المشوبة بالغلط
> المخالصة التي يوقعها العامل لا تمنعه من المطالبة بحقوقه الثابتة نظاماً إذا ثبت أنها كانت مبنية على غلط أو تدليس أو إكراه، أو إذا تنازل فيها عن حقوق مقررة بموجب النظام.

**المصادر:**
- chunk_ref:labor_art87 | نظام العمل - المادة 87
- chunk_ref:labor_art111 | نظام العمل - المادة 111
- chunk_ref:labor_art113 | نظام العمل - المادة 113
- chunk_ref:labor_art215 | نظام العمل - المادة 215
""",
    "saudization": """\
## نتائج البحث في الأنظمة - السعودة والتوطين
**الجودة: قوية**

### المادة 26 - نظام العمل السعودي
> يجب ألا يقل عدد العمال السعوديين في أي منشأة عن (75%) من إجمالي عدد العمال، ويجوز لوزير الموارد البشرية خفض هذه النسبة مؤقتاً في حالات معينة.

### قرار وزاري رقم 4983 لعام 1441هـ - برنامج نطاقات المطوّر
> يصنف البرنامج المنشآت وفقاً لنسب التوطين إلى نطاقات (بلاتيني، أخضر مرتفع، أخضر متوسط، أخضر منخفض، أحمر) وتترتب على كل نطاق مزايا وقيود مختلفة.

### قرار وزاري بشأن التواجد الفعلي
> يشترط التواجد الجسدي للموظف السعودي في مقر العمل خلال أوقات العمل الرسمية لاحتسابه ضمن نسب التوطين، باستثناء الموظفين المعتمدين رسمياً للعمل عن بُعد وفق ضوابط العمل المرن.

### ضوابط العمل عن بُعد (قرار وزاري رقم 5765 لعام 1442هـ)
> يجوز احتساب العامل السعودي الذي يعمل عن بُعد ضمن نسب التوطين بشرط استيفاء الشروط التالية: أن يكون العقد موثقاً في منصة قوى، وأن يكون العمل عن بُعد بدوام كامل أو جزئي وفق الضوابط.

### المادة 229 - نظام العمل (المخالفات والغرامات)
> يعاقب كل من يخالف أحكام هذا النظام بغرامة لا تقل عن (2,000) ريال ولا تزيد على (10,000) ريال. وتتعدد الغرامة بتعدد العمال.

**المصادر:**
- chunk_ref:labor_art26 | نظام العمل - المادة 26
- chunk_ref:nitaqat_4983 | برنامج نطاقات المطوّر
- chunk_ref:physical_presence | قرار التواجد الفعلي
- chunk_ref:remote_work_5765 | ضوابط العمل عن بُعد
- chunk_ref:labor_art229 | نظام العمل - المادة 229
""",
    "promissory_note": """\
## نتائج البحث في الأنظمة - السندات لأمر والتزوير
**الجودة: قوية**

### المادة 468 - نظام المعاملات المدنية
> يجوز إبطال العقد أو التصرف القانوني إذا شابه غلط جوهري أو تدليس أو إكراه، ويقع عبء الإثبات على من يدعي البطلان.

### المادة 3 - نظام الأوراق التجارية
> السند لأمر يجب أن يتضمن: التعهد بدفع مبلغ معين، تاريخ الاستحقاق، اسم المستفيد، توقيع المحرر. وكل سند خلا من أحد البيانات الإلزامية لا يعتد به كسند لأمر.

### المادة 5 - نظام مكافحة التزوير
> يعاقب بالسجن من سنة إلى خمس سنوات وبغرامة لا تزيد على خمسمائة ألف ريال أو بإحداهما كل من زور محرراً عرفياً أو استعمله مع علمه بتزويره.

### المادة 78 - نظام المرافعات الشرعية (الطعن في الأحكام الغيابية)
> يجوز لمن صدر ضده حكم غيابي أن يعترض عليه بطلب التماس إعادة النظر خلال ثلاثين يوماً من تاريخ إبلاغه بالحكم.

### المادة 200 - نظام المرافعات الشرعية (التماس إعادة النظر)
> يحق لأي من الخصوم أن يلتمس إعادة النظر في الأحكام النهائية في الأحوال التالية: إذا كان الحكم قد بُني على أوراق ظهر بعد الحكم تزويرها.

**المصادر:**
- chunk_ref:civil_trans_art468 | نظام المعاملات المدنية - المادة 468
- chunk_ref:commercial_papers_art3 | نظام الأوراق التجارية - المادة 3
- chunk_ref:forgery_art5 | نظام مكافحة التزوير - المادة 5
- chunk_ref:litigation_art78 | نظام المرافعات الشرعية - المادة 78
- chunk_ref:litigation_art200 | نظام المرافعات الشرعية - المادة 200
""",
    "khul_divorce": """\
## نتائج البحث في الأنظمة - الخلع وأحكام الأحوال الشخصية
**الجودة: قوية**

### المادة 107 - نظام الأحوال الشخصية السعودي
> للزوجة أن تطلب الخلع بأن تفتدي نفسها من زوجها بعوض تتفق عليه معه، فإن لم يتفقا على العوض يقرره القاضي بما لا يزيد على المهر وتوابعه.

### المادة 108 - نظام الأحوال الشخصية
> لا يشترط في الخلع رضا الزوج إذا حكم به القاضي بعد تعذر الصلح بين الزوجين وثبوت تضرر الزوجة.

### المادة 132 - نظام الأحوال الشخصية (الاختصاص القضائي)
> تختص محاكم المملكة بالنظر في دعاوى الأحوال الشخصية التي يكون أحد أطرافها سعودي الجنسية، حتى لو كان مقيماً خارج المملكة.

### المادة 220 - نظام المرافعات الشرعية (الرفع الإلكتروني)
> يجوز رفع الدعاوى إلكترونياً عبر بوابة ناجز دون الحاجة للحضور الشخصي، بما في ذلك دعاوى الأحوال الشخصية.

**المصادر:**
- chunk_ref:personal_status_art107 | نظام الأحوال الشخصية - المادة 107
- chunk_ref:personal_status_art108 | نظام الأحوال الشخصية - المادة 108
- chunk_ref:personal_status_art132 | نظام الأحوال الشخصية - المادة 132
- chunk_ref:litigation_art220 | نظام المرافعات الشرعية - المادة 220
""",
}

MOCK_CASES = {
    "foreign_investment": """\
## نتائج البحث في السوابق القضائية - الاستثمار الأجنبي
**الجودة: متوسطة**

### حكم المحكمة التجارية - القضية 1445/2/5010
**المحكمة:** المحكمة التجارية بالرياض
**الملخص:** قضت المحكمة ببطلان عقد الشراكة بين مستثمر سعودي وأجنبي لعدم حصول الأخير على ترخيص استثماري ساري من وزارة الاستثمار.
**المبدأ:** لا يجوز للأجنبي مزاولة أي نشاط تجاري دون ترخيص استثماري ساري المفعول.

**المصادر:**
- CASE-1445-2-5010 | بطلان شراكة أجنبية | المحكمة التجارية بالرياض
""",
    "labor_settlement": """\
## نتائج البحث في السوابق القضائية - المخالصة وحقوق العمال
**الجودة: قوية**

### حكم محكمة الاستئناف العمالية - القضية 1444/7/3200
**المحكمة:** محكمة الاستئناف العمالية بالرياض
**الملخص:** ألغت محكمة الاستئناف حجية المخالصة الموقعة من العاملة لثبوت وجود فارق في المستحقات بمبلغ 18,000 ريال (إجازات سنوية غير مستهلكة).
**المبدأ:** المخالصة لا تمنع العامل من المطالبة بالفرق إذا أثبت وجود خطأ حسابي أو حقوق نظامية لم تُصرف.

### حكم المحكمة العمالية - القضية 1445/1/1800
**المحكمة:** المحكمة العمالية بجدة
**الملخص:** قبلت المحكمة دعوى عامل طالب ببدل إجازات سنوية رغم توقيعه على مخالصة، لأن المخالصة لا تسقط الحقوق المقررة نظاماً.
**المبدأ:** التنازل عن الحقوق النظامية المقررة في نظام العمل باطل حتى لو وقع العامل على مخالصة.

**المصادر:**
- CASE-1444-7-3200 | إلغاء مخالصة | محكمة الاستئناف العمالية بالرياض
- CASE-1445-1-1800 | بدل إجازات رغم مخالصة | المحكمة العمالية بجدة
""",
    "saudization": """\
## نتائج البحث في السوابق القضائية - مخالفات السعودة
**الجودة: متوسطة**

### قرار لجنة النظر في مخالفات نظام العمل - 1444/9/420
**الجهة:** وزارة الموارد البشرية والتنمية الاجتماعية
**الملخص:** فرضت غرامة 20,000 ريال على منشأة في مركز تجاري بجدة لعدم وجود جسدي لأي موظف سعودي وقت الزيارة التفتيشية رغم تسجيل 3 سعوديين.
**المبدأ:** التسجيل الشكلي للموظفين السعوديين دون تواجد فعلي يُعد تحايلاً على نظام التوطين.

**المصادر:**
- CASE-1444-9-420 | غرامة عدم تواجد جسدي | لجنة مخالفات نظام العمل
""",
    "promissory_note": """\
## نتائج البحث في السوابق القضائية - السند لأمر والتزوير
**الجودة: قوية**

### حكم محكمة الاستئناف - القضية 1443/4/2100
**المحكمة:** محكمة الاستئناف بالرياض
**الملخص:** قضت المحكمة بإبطال سند لأمر مزور بعد إثبات أن المدعى عليه وقع على ورقة بيضاء تم تعبئتها لاحقاً بمبلغ مختلف، وتمت إحالة الموضوع للنيابة العامة للتحقيق في جريمة التزوير.
**المبدأ:** التوقيع على ورقة بيضاء يُعد تفويضاً محدوداً ويمكن الطعن فيه إذا ثبت سوء استخدام التفويض.

### حكم المحكمة الجزائية - القضية 1444/6/890
**المحكمة:** المحكمة الجزائية بمكة المكرمة
**الملخص:** أدانت المحكمة المتهم بتهمة التزوير في محرر عرفي لتعبئته ورقة موقعة على بياض بمبلغ مالي دون علم الموقع.

**المصادر:**
- CASE-1443-4-2100 | إبطال سند مزور | محكمة الاستئناف بالرياض
- CASE-1444-6-890 | إدانة تزوير | المحكمة الجزائية بمكة المكرمة
""",
    "khul_divorce": """\
## نتائج البحث في السوابق القضائية - الخلع
**الجودة: متوسطة**

### حكم محكمة الأحوال الشخصية - القضية 1445/5/6700
**المحكمة:** محكمة الأحوال الشخصية بالرياض
**الملخص:** قبلت المحكمة دعوى خلع مقدمة إلكترونياً عبر ناجز من زوجة سعودية مقيمة في الخارج، وحكمت بالخلع مقابل إعادة المهر.
**المبدأ:** يجوز رفع دعوى الخلع إلكترونياً ولا يشترط الحضور الجسدي للزوجة.

**المصادر:**
- CASE-1445-5-6700 | خلع إلكتروني | محكمة الأحوال الشخصية بالرياض
""",
}

MOCK_COMPLIANCE_DOMAIN = {
    "foreign_investment": """\
## نتائج البحث في الخدمات الحكومية - الاستثمار الأجنبي
**الجودة: قوية**

### خدمة إصدار ترخيص استثمار أجنبي - وزارة الاستثمار
**الجهة:** وزارة الاستثمار (MISA)
**المنصة:** investsaudi.sa
**المتطلبات:** سجل تجاري، قوائم مالية مدققة، خطة عمل، إثبات هوية المستثمر الأجنبي.
**الرسوم:** 2,000 ريال سنوياً.

### خدمة تأسيس شركة مشتركة - وزارة التجارة
**المنصة:** mc.gov.sa
**المتطلبات:** ترخيص استثمار أجنبي ساري، عقد تأسيس موثق، نظام أساسي.

**المصادر:**
- SVC-MISA-001 | ترخيص استثمار أجنبي | وزارة الاستثمار
- SVC-MC-002 | تأسيس شركة مشتركة | وزارة التجارة
""",
    "labor_settlement": """\
## نتائج البحث في الخدمات الحكومية - تسوية الخلافات العمالية
**الجودة: قوية**

### خدمة رفع دعوى عمالية - منصة ناجز
**الجهة:** وزارة العدل
**المنصة:** najiz.sa
**الوصف:** رفع دعوى عمالية إلكترونياً للمطالبة بالحقوق المالية.
**الشرط المسبق:** صدور محضر عدم صلح من خدمة ودّي.

### خدمة التسوية الودية - ودّي
**الجهة:** وزارة الموارد البشرية
**المنصة:** mol.gov.sa
**المدة:** 21 يوم عمل.
**ملاحظة:** إلزامية قبل رفع الدعوى العمالية.

**المصادر:**
- SVC-NAJIZ-001 | رفع دعوى عمالية | منصة ناجز
- SVC-WADDI-001 | التسوية الودية | منصة ودّي
""",
    "saudization": """\
## نتائج البحث في الخدمات الحكومية - التوطين ونطاقات
**الجودة: قوية**

### خدمة الاستعلام عن نطاق المنشأة - منصة قوى
**الجهة:** وزارة الموارد البشرية
**المنصة:** qiwa.sa
**الوصف:** معرفة تصنيف المنشأة في نطاقات ونسب التوطين المطلوبة والفعلية.

### خدمة توثيق عقد العمل عن بُعد - منصة قوى
**المنصة:** qiwa.sa
**الوصف:** توثيق عقود العمل عن بُعد لاحتساب الموظف في نسب التوطين.
**الشرط:** تسجيل العامل في التأمينات الاجتماعية.

**المصادر:**
- SVC-QIWA-002 | الاستعلام عن نطاقات | منصة قوى
- SVC-QIWA-003 | عقد العمل عن بُعد | منصة قوى
""",
    "promissory_note": """\
## نتائج البحث في الخدمات الحكومية - الطعن في أحكام تنفيذية
**الجودة: متوسطة**

### خدمة تقديم طلب التماس إعادة نظر - منصة ناجز
**الجهة:** وزارة العدل
**المنصة:** najiz.sa
**الوصف:** تقديم طلب التماس إعادة نظر في حكم نهائي بسبب تزوير أو غش.
**المدة:** خلال 30 يوماً من العلم بسبب الالتماس.

### خدمة تقديم بلاغ تزوير - النيابة العامة
**الجهة:** النيابة العامة
**المنصة:** pp.gov.sa
**الوصف:** تقديم بلاغ جنائي بتهمة التزوير في محرر عرفي.

**المصادر:**
- SVC-NAJIZ-002 | التماس إعادة نظر | منصة ناجز
- SVC-PP-001 | بلاغ تزوير | النيابة العامة
""",
    "khul_divorce": """\
## نتائج البحث في الخدمات الحكومية - الخلع والأحوال الشخصية
**الجودة: قوية**

### خدمة رفع دعوى خلع إلكترونياً - منصة ناجز
**الجهة:** وزارة العدل
**المنصة:** najiz.sa
**الوصف:** رفع دعوى فسخ عقد النكاح (خلع) إلكترونياً من داخل أو خارج المملكة.
**المتطلبات:** حساب مفعّل في أبشر، صورة عقد النكاح، إثبات هوية.
**ملاحظة:** لا يشترط الحضور الشخصي، يمكن التوكيل إلكترونياً.

**المصادر:**
- SVC-NAJIZ-003 | رفع دعوى خلع | منصة ناجز
""",
}

# Map query number to domain key
QUERY_DOMAIN_MAP = {
    1: "foreign_investment",
    2: "labor_settlement",
    3: "saudization",
    4: "promissory_note",
    5: "khul_divorce",
}


# ---------------------------------------------------------------------------
# Mock setup
# ---------------------------------------------------------------------------

def make_mock_supabase() -> MagicMock:
    """Create a mock Supabase client with artifact CRUD support."""
    client = MagicMock()

    # INSERT chain (create_report new artifact)
    (
        client.table.return_value
        .insert.return_value
        .execute.return_value
    ).data = [{"artifact_id": "art-test-001"}]

    # UPDATE chain
    (
        client.table.return_value
        .update.return_value
        .eq.return_value
        .execute.return_value
    ).data = [{"artifact_id": "art-test-001"}]

    # SELECT chain (get_previous_report)
    (
        client.table.return_value
        .select.return_value
        .eq.return_value
        .eq.return_value
        .is_.return_value
        .maybe_single.return_value
        .execute.return_value
    ).data = None

    # Simpler select (_get_current_artifact)
    (
        client.table.return_value
        .select.return_value
        .eq.return_value
        .maybe_single.return_value
        .execute.return_value
    ).data = None

    return client


def make_deps(supabase: MagicMock) -> SearchDeps:
    """Create SearchDeps with mock dependencies."""
    async def mock_embed(text: str) -> list[float]:
        return [0.0] * 1536

    return SearchDeps(
        supabase=supabase,
        embedding_fn=mock_embed,
        user_id="test-user-001",
        conversation_id="test-conv-001",
        case_id=None,
        case_memory=None,
        artifact_id=None,
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def run_query(num: int) -> dict:
    """Run a single test query and return results."""
    q = QUERIES[num]
    domain = QUERY_DOMAIN_MAP[num]

    print(f"\n{'=' * 70}")
    print(f"  Query #{num}: {q['name']}")
    print(f"{'=' * 70}")
    print(f"  {q['query'][:120]}...")
    print()

    mock_sb = make_mock_supabase()
    deps = make_deps(mock_sb)

    # Patch all 3 executor tools to return domain-specific results
    reg_result = MOCK_REGULATIONS.get(domain, "لا توجد نتائج")
    cases_result = MOCK_CASES.get(domain, "لا توجد نتائج")
    compliance_result = MOCK_COMPLIANCE_DOMAIN.get(domain, "لا توجد نتائج")

    # Patch executors at source so local imports inside tools pick up mocks
    async def mock_regulation_search(query, reg_deps):
        return reg_result

    with (
        # Patch the real executor function at its source module
        patch("agents.deep_search.executors.run_regulation_search", side_effect=mock_regulation_search),
        patch("agents.deep_search.executors.RegulationSearchDeps", MagicMock()),
        patch("agents.utils.embeddings.embed_regulation_query", new_callable=AsyncMock, return_value=[0.0]*1024),
        # Patch mock constants for cases/compliance tools
        patch("agents.deep_search.tools.MOCK_CASES_RESULT", cases_result),
        patch("agents.deep_search.tools.MOCK_COMPLIANCE_RESULT", compliance_result),
    ):
        start = time.time()
        result, events = await handle_deep_search_turn(
            message=q["query"],
            deps=deps,
        )
        duration = time.time() - start

    # Display results
    result_type = type(result).__name__
    print(f"  Result: {result_type} (took {duration:.1f}s)")
    print()

    # SSE events
    if events:
        print(f"  SSE Events ({len(events)}):")
        for e in events:
            etype = e.get("type", "?")
            detail = e.get("text", e.get("artifact_id", e.get("question", "")))
            print(f"    [{etype}] {str(detail)[:100]}")
        print()

    # Response
    if hasattr(result, "response"):
        print(f"  Response (TaskContinue):")
        print(f"  {result.response[:300]}")
    elif hasattr(result, "last_response"):
        print(f"  Last Response (TaskEnd):")
        print(f"  {result.last_response[:300]}")
        print(f"\n  Reason: {result.reason}")
        print(f"  Summary: {result.summary[:200]}")

    # Artifact
    artifact = getattr(result, "artifact", "")
    if artifact:
        print(f"\n  {'~' * 50}")
        print(f"  Artifact ({len(artifact)} chars):")
        # Show first 600 chars
        for line in artifact[:600].split("\n"):
            print(f"    {line}")
        if len(artifact) > 600:
            print(f"    ... ({len(artifact) - 600} more chars)")

    print(f"\n  {'=' * 70}")

    return {
        "num": num,
        "name": q["name"],
        "result_type": result_type,
        "events_count": len(events),
        "artifact_len": len(artifact),
        "duration": duration,
        "has_artifact": bool(artifact),
    }


async def main():
    """Run selected or all test queries."""
    # Parse which queries to run
    if len(sys.argv) > 1:
        nums = [int(x) for x in sys.argv[1:] if x.isdigit()]
    else:
        nums = [1, 2, 3, 4, 5]

    print(f"\n{'#' * 70}")
    print(f"  Deep Search Planner - Integration Test ({len(nums)} queries)")
    print(f"  Executors: MOCKED (domain-specific Arabic results)")
    print(f"  Planner: REAL LLM (via model registry)")
    print(f"{'#' * 70}")

    results = []
    for num in nums:
        if num in QUERIES:
            r = await run_query(num)
            results.append(r)
        else:
            print(f"\n  [SKIP] Query #{num} not found (valid: 1-5)")

    # Summary table
    print(f"\n\n{'#' * 70}")
    print(f"  SUMMARY")
    print(f"{'#' * 70}")
    print(f"  {'#':<4} {'Name':<42} {'Result':<15} {'Events':<8} {'Artifact':<10} {'Time':<8}")
    print(f"  {'-'*4} {'-'*42} {'-'*15} {'-'*8} {'-'*10} {'-'*8}")
    for r in results:
        art = f"{r['artifact_len']} ch" if r["has_artifact"] else "none"
        print(
            f"  {r['num']:<4} {r['name']:<42} {r['result_type']:<15} "
            f"{r['events_count']:<8} {art:<10} {r['duration']:.1f}s"
        )
    print()

    total_time = sum(r["duration"] for r in results)
    passed = sum(1 for r in results if r["has_artifact"])
    print(f"  Total: {total_time:.1f}s | With artifact: {passed}/{len(results)}")
    print(f"\n  Logs: agents/logs/deep_search/")


if __name__ == "__main__":
    asyncio.run(main())
