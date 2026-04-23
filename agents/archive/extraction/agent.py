"""Mock extraction task agent."""
from __future__ import annotations

from agents.models import TaskContinue, TaskEnd

MOCK_SUMMARY = """# ملخص المستند

## معلومات المستند
- **النوع**: عقد تجاري
- **التاريخ**: 1445/06/15هـ
- **الأطراف**: شركة الأولى للتجارة / مؤسسة الثانية للمقاولات

## البنود الرئيسية
1. مدة العقد: سنتان من تاريخ التوقيع
2. القيمة الإجمالية: 500,000 ريال سعودي
3. شرط جزائي: 10% من قيمة العقد

## المخاطر المحتملة
- بند التحكيم غير محدد الجهة
- غياب آلية واضحة لتسوية النزاعات
- عدم تحديد آلية تعديل الأسعار

## التوصيات
- تحديد جهة التحكيم (المركز السعودي للتحكيم التجاري)
- إضافة بند لتعديل الأسعار وفقاً لمؤشر أسعار المستهلك
"""

MOCK_STREAM_TEXT = "قمت بتحليل المستند المرفق واستخراج المعلومات الأساسية. يتضمن الملخص البنود الرئيسية والمخاطر المحتملة والتوصيات."

OUT_OF_SCOPE_KEYWORDS = ["بحث", "تحليل", "مقارنة", "search", "research", "عقد", "مسودة", "صياغة", "contract", "draft"]


def mock_extraction(
    question: str,
    current_artifact: str,
    is_first_turn: bool,
) -> TaskContinue | TaskEnd:
    """Mock task agent — returns TaskContinue with summary or TaskEnd if out of scope."""
    q_lower = question.lower()

    if not is_first_turn and any(kw in q_lower for kw in OUT_OF_SCOPE_KEYWORDS):
        return TaskEnd(
            reason="out_of_scope",
            summary="تم تحليل المستند واستخراج المعلومات الأساسية.",
            artifact=current_artifact or MOCK_SUMMARY,
            last_response="يبدو أن طلبك خارج نطاق تحليل المستندات. سأحولك للخدمة المناسبة.",
        )

    artifact = current_artifact if current_artifact else MOCK_SUMMARY
    return TaskContinue(
        response=MOCK_STREAM_TEXT,
        artifact=artifact,
    )
