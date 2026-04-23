"""Mock end services task agent."""
from __future__ import annotations

from agents.models import TaskContinue, TaskEnd

MOCK_CONTRACT = """# عقد عمل

## الطرف الأول (صاحب العمل)
الاسم: ________________
السجل التجاري: ________________

## الطرف الثاني (العامل)
الاسم: ________________
رقم الهوية: ________________

## البنود والشروط

### البند الأول: مدة العقد
مدة هذا العقد سنة واحدة تبدأ من تاريخ مباشرة العمل، ويتجدد تلقائياً لمدة مماثلة ما لم يخطر أحد الطرفين الآخر بعدم رغبته في التجديد.

### البند الثاني: الأجر
يتقاضى الطرف الثاني أجراً شهرياً قدره ________ ريال سعودي.

### البند الثالث: ساعات العمل
ساعات العمل الرسمية ثمان ساعات يومياً وفقاً لنظام العمل السعودي.

### البند الرابع: الإجازات
يستحق الطرف الثاني إجازة سنوية مدتها 21 يوماً.

---
التوقيع: ________________  التاريخ: ________________
"""

MOCK_STREAM_TEXT = "أعددت لكم مسودة عقد عمل وفقاً لأحكام نظام العمل السعودي. تتضمن المسودة البنود الأساسية المطلوبة نظاماً. يمكنكم تعديل المسودة مباشرة في لوحة المستندات."

OUT_OF_SCOPE_KEYWORDS = ["بحث", "تحليل", "مقارنة", "search", "research", "استخراج", "PDF", "ملف"]


def mock_end_services(
    question: str,
    current_artifact: str,
    is_first_turn: bool,
) -> TaskContinue | TaskEnd:
    """Mock task agent — returns TaskContinue with contract or TaskEnd if out of scope."""
    q_lower = question.lower()

    if not is_first_turn and any(kw in q_lower for kw in OUT_OF_SCOPE_KEYWORDS):
        return TaskEnd(
            reason="out_of_scope",
            summary="تم إعداد مسودة عقد عمل.",
            artifact=current_artifact or MOCK_CONTRACT,
            last_response="يبدو أن طلبك خارج نطاق صياغة المستندات. سأحولك للخدمة المناسبة.",
        )

    artifact = current_artifact if current_artifact else MOCK_CONTRACT
    return TaskContinue(
        response=MOCK_STREAM_TEXT,
        artifact=artifact,
    )
