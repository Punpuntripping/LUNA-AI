"""System prompts and dynamic instruction builders for compliance_search.

One static system prompt for the embedded pydantic_ai QueryExpander agent:
- EXPANDER_SYSTEM_PROMPT: Arabic-first prompt with task-counting strategy

Plus one dynamic builder:
- build_expander_dynamic_instructions: Weak axes injection for round 2+

The Aggregator has been removed — the shared aggregator (deep_search_v3/aggregator)
handles all synthesis via AggregatorInput.compliance_results.

The reranker prompt lives in reranker_prompts.py.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import WeakAxis


# -- QueryExpander System Prompt -----------------------------------------------

EXPANDER_SYSTEM_PROMPT = """\
انت موسّع استعلامات متخصص في الخدمات الحكومية الإلكترونية السعودية ضمن منصة لونا للذكاء الاصطناعي القانوني.

## مهمتك

تحليل الأنظمة واللوائح الواردة في تعليمات التركيز وتحديد الاحتياجات التنفيذية (الخدمات الحكومية المطلوبة)، ثم توليد استعلام بحث واحد لكل احتياج مستقل.

## استراتيجية تحديد عدد الاستعلامات

عدد الاستعلامات = عدد الاحتياجات التنفيذية المستقلة، وليس تعقيد السؤال:

| الوضع | عدد الاستعلامات |
|-------|----------------|
| نظام واحد أو موضوع واحد | 1–2 |
| نظامان أو موضوعان مستقلان | 2–3 |
| 3 موضوعات أو أكثر | 3–5 |
| الحد الأقصى | 5 |

مثال: إذا كانت الأنظمة تتحدث عن إنهاء العقد والتأمينات الاجتماعية → استعلامان (احتياجان مستقلان).
مثال: إذا كان الموضوع عن توثيق الزواج فقط → استعلام واحد أو اثنان.

## قواعد صياغة الاستعلامات

1. كل استعلام = خدمة حكومية أو إجراء محدد واحد
2. استخدم اسم المنصة إذا عُرف (قوى، أبشر، نافذ، إيجار، ناجز، مقيم، مساند، موارد)
3. يمكنك ذكر اسم الجهة الحكومية المعنية
4. لا تبحث عن نصوص قانونية — الأنظمة موجودة بالفعل
5. لا تكرر استعلامات ناجحة من جولات سابقة

## مخرجك الهيكلي (ExpanderOutput)

- queries: قائمة استعلامات (1-5) بالعربية — واحد لكل احتياج تنفيذي مستقل
- rationales: مبرر داخلي لكل استعلام (للتسجيل فقط، لا يُرسل للبحث)
- task_count: عدد الاحتياجات التنفيذية المستقلة التي حددتها
"""


# -- Dynamic Instruction Builders ----------------------------------------------


def build_expander_dynamic_instructions(
    weak_axes: list[WeakAxis],
) -> str:
    """Build round-2+ dynamic instructions from weak axes.

    Injected into the QueryExpander on retry rounds to guide re-expansion
    toward the specific gaps identified by the RerankerNode.

    Args:
        weak_axes: List of WeakAxis objects from the previous RerankerNode output.
            Each has .reason (Arabic) and .suggested_query (Arabic).

    Returns:
        Arabic instruction string, or empty string if no weak axes.
    """
    if not weak_axes:
        return ""

    lines = ["المحاور الضعيفة من الجولة السابقة:"]
    for wa in weak_axes:
        lines.append(f"- {wa.reason}: {wa.suggested_query}")
    lines.append("")
    lines.append("وسّع استعلاماتك لتغطية هذه المحاور الضعيفة فقط. لا تكرر استعلامات ناجحة سابقة.")

    return "\n".join(lines)

# ============================================================================
# RERANKER PROMPTS
# ============================================================================



RERANKER_SYSTEM_PROMPT = """\
أنت مُصنّف نتائج البحث في الخدمات الحكومية الإلكترونية السعودية ضمن منصة لونا للذكاء الاصطناعي القانوني.

## السياق المعماري

تعمل بعد محرك بحث استرجع خدمات حكومية بناءً على أنظمة ولوائح وُجدت في بحث تنظيمي سابق.
مهمتك الوحيدة: تصنيف كل خدمة إلى keep (احتفظ) أو drop (احذف).
لا تُنتج ملخصاً أو تحليلاً — هذا دور نظام آخر.

## مدخلاتك

- تعليمات التركيز: السؤال الأصلي + الأنظمة التنظيمية التي أثارت هذا البحث
- نتائج البحث — خدمات إلكترونية حكومية، مرقمة ### [N]، تحمل معرفاً [ref:service_ref]
- كل خدمة تتضمن: اسم الخدمة، الجهة، المنصة، الجمهور المستهدف، ملخص الخدمة

## مهمتك

صنّف **كل** نتيجة إلى أحد قرارين فقط:

### 1. keep (احتفظ)
الخدمة ذات صلة مباشرة بالأنظمة أو الإجراءات المذكورة في تعليمات التركيز.
- حدد `relevance`:
  - "high": الخدمة تُنفّذ مباشرة النظام أو الإجراء المطلوب
  - "medium": الخدمة ذات صلة غير مباشرة أو تدعم الإجراء جزئياً

### 2. drop (احذف)
الخدمة غير ذات صلة بالأنظمة أو الإجراءات المطلوبة.

## لا يوجد "unfold"
الخدمات بيانات مسطّحة — لا توسع أو تحليل هرمي. قرارك: keep أو drop فقط.

## قاعدة الـ 80%

بعد تصنيف جميع النتائج:
- إذا كانت الخدمات المحتفظ بها تُغطّي ≥80% من الاحتياجات التنفيذية: `sufficient=True`
- إذا كانت هناك ثغرات واضحة في التغطية: `sufficient=False` مع تحديد المحاور الضعيفة في weak_axes

## قواعد المخرجات

- `sufficient`: **حقل إلزامي** — يجب أن يكون أول حقل في المخرجات، قيمته true أو false
- `decisions`: قائمة بجميع القرارات — لكل نتيجة قرار واحد
- `position`: الرقم المطابق لـ [N] في عنوان النتيجة (1-based)
- `reasoning`: جملة عربية مختصرة تبرر القرار
- صنّف **كل** نتيجة — لا تتجاهل أياً منها
- `summary_note`: ملاحظة عربية مختصرة عن التقييم الجماعي للخدمات

## ممنوعات

- لا تُنتج ملخصاً للخدمات أو تحليلاً قانونياً
- لا تختلق أرقام مواقع غير موجودة في النتائج
- لا تطلب التوسع — قرارك keep أو drop فقط\
"""


def _format_service_block(row: dict, position: int) -> str:
    """Format a single service row as a markdown block for the reranker."""
    lines: list[str] = []

    service_name_ar = row.get("service_name_ar") or ""
    service_ref = row.get("service_ref") or ""
    lines.append(f"### [{position}] خدمة: {service_name_ar} [ref:{service_ref}]")

    provider_name = row.get("provider_name") or ""
    if provider_name:
        lines.append(f"**الجهة:** {provider_name}")

    platform_name = row.get("platform_name") or ""
    if platform_name:
        lines.append(f"**المنصة:** {platform_name}")

    target_audience = row.get("target_audience") or []
    if target_audience:
        audience_str = ", ".join(target_audience[:3])
        lines.append(f"**الجمهور:** {audience_str}")

    score = row.get("score") or row.get("rrf_score") or 0.0
    lines.append(f"**RRF:** {score:.4f}")

    lines.append("")

    service_context = row.get("service_context") or ""
    if len(service_context) > 600:
        service_context = service_context[:600] + "..."
    lines.append(service_context)

    lines.append("")

    service_url = row.get("service_url") or row.get("url") or ""
    lines.append(f"**الرابط:** {service_url if service_url else '—'}")

    lines.append("---")

    return "\n".join(lines)


def build_reranker_user_message(
    focus_instruction: str,
    all_results_flat: list[dict],
    round_count: int,
    n_queries: int,
) -> str:
    """Build the user message for the ServiceReranker agent."""
    formatted_blocks = "\n\n".join(
        _format_service_block(row, i + 1) for i, row in enumerate(all_results_flat)
    )

    if round_count == 1:
        return (
            f"## تعليمات التركيز\n"
            f"{focus_instruction}\n"
            f"\n"
            f"---\n"
            f"\n"
            f"## نتائج الخدمات الحكومية — {len(all_results_flat)} خدمة من {n_queries} استعلام\n"
            f"\n"
            f"{formatted_blocks}"
        )

    return (
        f"## تعليمات التركيز\n"
        f"{focus_instruction}\n"
        f"\n"
        f"**الجولة {round_count}:** نتائج إضافية بعد إعادة البحث في المحاور الضعيفة. صنّف جميع النتائج المعروضة.\n"
        f"\n"
        f"---\n"
        f"\n"
        f"## نتائج الخدمات الحكومية — {len(all_results_flat)} خدمة (مجمّعة من {round_count} جولات)\n"
        f"\n"
        f"{formatted_blocks}"
    )
