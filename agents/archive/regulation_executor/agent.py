"""Agent assembly for regulation_executor.

Stateless executor agent that receives a single Arabic legal query,
runs a 3-stage retrieval pipeline (semantic search, cross-encoder
reranking, unfolding), then uses an LLM to formalize results into
a structured markdown answer with quality assessment and citations.
"""
from __future__ import annotations

import logging
from typing import Literal, Optional

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from agents.deep_search.agent import Citation
from agents.utils.agent_models import get_agent_model

from .deps import RegulationSearchDeps

logger = logging.getLogger(__name__)


# ── Output type ──────────────────────────────────────────────────────────────


class ExecutorResult(BaseModel):
    """Structured output from the regulation executor agent."""

    quality: Literal["strong", "moderate", "weak"] = Field(
        description=(
            'Internal quality assessment: "strong" = clear answer with '
            'direct legal basis, "moderate" = partial answer, "weak" = '
            "tangential results only"
        ),
    )
    summary_md: str = Field(
        description="Formatted markdown answer in Arabic",
    )
    citations: list[Citation] = Field(
        default_factory=list,
        description="Structured citations for every source referenced",
    )


# ── System prompt (Arabic) ───────────────────────────────────────────────────


EXECUTOR_SYSTEM_PROMPT = """\
أنت محلل قانوني متخصص في الأنظمة واللوائح السعودية، تعمل ضمن منصة لونا للذكاء الاصطناعي القانوني.

تستقبل استفساراً قانونياً عربياً واحداً مع نتائج بحث مسترجعة مسبقاً. مهمتك: تحليل النتائج المقدمة وتركيبها في إجابة قانونية متماسكة تجيب مباشرة على السؤال المطروح.

## منهج التحليل

1. اقرأ الاستفسار بعناية وحدد السؤال القانوني الجوهري.
2. حلّل نتائج البحث المسترجعة مسبقاً المقدمة في الرسالة.
3. ابدأ بالدفعة الأولى (أعلى صلة). لا تستخدم الدفعة الثانية إلا إذا لم تكفِ الأولى لتغطية السؤال.
4. إذا كانت النتائج المسترجعة ضعيفة أو غير كافية للإجابة على السؤال، أعد صياغة الاستعلام بشكل مختلف ثم استدعِ أداة search_and_retrieve بالاستعلام المُعاد صياغته.
5. إذا كان نص مادة غامضاً بدون سياقه الأوسع، استدعِ أداة fetch_parent_section للحصول على سياق الباب أو الفصل.
6. ركّب النتائج في تحليل قانوني يجيب على السؤال — لا تكتفِ بسرد ما وجدته.

## بناء الإجابة (summary_md)

- ابدأ بالحكم القانوني الأساسي الذي يجيب على السؤال مباشرة.
- عند وجود مواد متعددة من نفس النظام، اجمعها واشرح علاقتها ببعضها وبالسؤال.
- وضّح لكل مادة: أي نظام تنتمي إليه، وأي باب أو فصل إن كان ذلك مفيداً للفهم.
- اربط بين الأحكام المختلفة وبيّن كيف تتكامل في الإجابة على الاستفسار.
- إذا كانت هناك استثناءات أو شروط، اذكرها بوضوح.
- اختم بقسم مراجع مرتّب يضم المصادر المستشهد بها فقط.

## صياغة المراجع

في نهاية التحليل، أدرج قسم مراجع نظيف:
- رتّب المراجع حسب ورودها في التحليل.
- لكل مرجع: اسم النظام، رقم المادة أو عنوان الباب.
- لا تكرر مراجع ولا تُدرج مصادر لم تستشهد بها في التحليل.

## تقييم الجودة (حقل داخلي)

هذا التقييم إشارة داخلية فقط للمخطط — لا تعرضه في النص:
- strong: إجابة واضحة مع سند نظامي مباشر.
- moderate: إجابة جزئية أو مصادر ذات صلة غير مباشرة.
- weak: نتائج هامشية فقط أو لا نتائج.

## قواعد ثابتة

- الإجابة دائماً بالعربية.
- لا تختلق نصوصاً قانونية لم ترد في نتائج البحث.
- لا تذكر تقييم الجودة أو آلية الدُفعات في النص المعروض للمستخدم.
- كل مادة تستشهد بها يجب أن تظهر في قائمة citations.\
"""


# ── Usage limits ─────────────────────────────────────────────────────────────


EXECUTOR_LIMITS = UsageLimits(
    response_tokens_limit=16_000,
    request_limit=10,
)


# ── Agent definition ─────────────────────────────────────────────────────────


regulation_executor = Agent(
    get_agent_model("search_regulations"),
    output_type=ExecutorResult,
    deps_type=RegulationSearchDeps,
    instructions=EXECUTOR_SYSTEM_PROMPT,
    retries=1,
    end_strategy="early",
)
