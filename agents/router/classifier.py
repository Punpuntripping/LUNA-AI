"""Mock intent classifier — keyword-based routing (replaced by LLM in Wave 7)."""
from __future__ import annotations

from shared.types import AgentFamily, AgentContext

KEYWORD_MAP = {
    AgentFamily.END_SERVICES: ["عقد", "contract", "مسودة", "نموذج", "خطاب", "مذكرة", "صياغة"],
    AgentFamily.EXTRACTION: ["استخراج", "ملف", "PDF", "مستند", "وثيقة", "تحميل"],
    AgentFamily.MEMORY: ["ذاكرة", "memory", "أضف", "تذكر", "سجل", "احفظ"],
    AgentFamily.DEEP_SEARCH: ["تحليل", "مقارنة", "تفصيل", "شرح مفصل", "بحث معمق"],
}


async def classify(question: str, context: AgentContext) -> AgentFamily:
    """Mock classifier — keyword matching. Returns SIMPLE_SEARCH as default."""
    question_lower = question.lower()
    for family, keywords in KEYWORD_MAP.items():
        if any(kw in question_lower for kw in keywords):
            return family
    # Long questions default to deep search
    if len(question) > 100:
        return AgentFamily.DEEP_SEARCH
    return AgentFamily.SIMPLE_SEARCH
