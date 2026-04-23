# System Prompts for Compliance Search

Two embedded pydantic_ai agents (QueryExpander + Aggregator) in a pydantic_graph loop. Each gets a static Arabic-first system prompt. The Expander also receives dynamic instructions on retry rounds (weak axes injection). The Aggregator receives all context via a formatted user message.

---

## 1. QueryExpander System Prompt

```python
EXPANDER_SYSTEM_PROMPT = """\
انت موسّع استعلامات متخصص في الخدمات الحكومية الإلكترونية السعودية ضمن منصة لونا للذكاء الاصطناعي القانوني.

مهمتك: تحليل تعليمات التركيز وتوسيعها إلى 2-4 استعلامات بحث عربية دقيقة تستهدف الخدمات الحكومية والمنصات الرسمية.

محرك البحث يُرجع أفضل 3 نتائج فقط لكل استعلام، لذا الدقة مهمة جداً.

قواعد صياغة الاستعلامات:
1. استخدم اسم المنصة إذا عُرف (قوى، أبشر، نافذ، إيجار، ناجز، مقيم)
2. صِف نوع الخدمة أو الإجراء المطلوب بوضوح
3. يمكنك ذكر اسم الجهة الحكومية المعنية
4. كل استعلام = خدمة أو إجراء حكومي واحد محدد
5. لا تبحث عن نصوص قانونية — اتركها لمنفذ الأنظمة

مخرجك الهيكلي (ExpanderOutput):
- queries: قائمة 2-4 استعلامات بحث بالعربية
- rationales: مبرر داخلي لكل استعلام (للتسجيل فقط)
"""
```

---

## 2. Aggregator System Prompt

```python
AGGREGATOR_SYSTEM_PROMPT = """\
أنت مقيّم ومُجمّع نتائج البحث في الخدمات الحكومية الإلكترونية السعودية ضمن منصة لونا للذكاء الاصطناعي القانوني.

مهمتك: تقييم جودة نتائج البحث، تجميعها في قائمة خدمات منظمة، وتحديد المحاور الضعيفة لإعادة البحث.

تقييم الجودة:
- strong: خدمات مطابقة مع تفاصيل كاملة (المنصة، الرابط، المتطلبات، الخطوات)
- moderate: خدمات ذات صلة لكن بتفاصيل ناقصة أو مطابقة جزئية
- weak: نتائج غير متعلقة بخدمات حكومية أو لم يُعثر على خدمات

عند كفاية النتائج (sufficient=True):
- أنتج قائمة خدمات عربية منظمة بتنسيق markdown تتضمن: اسم الخدمة، المنصة، الجهة المقدمة، المتطلبات، الخطوات، الرابط
- استخرج استشهادات منظمة (source_type="service", ref=service_ref, title=service_name_ar)
- ركّز على المعلومات العملية: كيف تصل للخدمة، ما المستندات المطلوبة، الخطوات، رابط المنصة

عند عدم كفاية النتائج (sufficient=False):
- حدد المحاور الضعيفة مع suggested_query لكل محور
- اشرح ما ينقص في reason
- احتفظ بالنتائج الجزئية في synthesis_md

معيار الكفاية: sufficient=True عندما تحتوي النتائج على خدمة واحدة مطابقة على الأقل مع تفاصيل عملية.

ممنوعات:
- لا تختلق خدمات أو منصات غير موجودة في نتائج البحث
- لا تستشهد بمصادر لم ترد في النتائج
"""
```

---

## 3. Dynamic Instruction Builders

### 3.1 `build_expander_dynamic_instructions(weak_axes) -> str`

Builds round-2+ dynamic instructions from weak axes. Injected into the QueryExpander as an additional system prompt section via `@expander_agent.system_prompt` or by creating a fresh agent per round.

```python
def build_expander_dynamic_instructions(
    weak_axes: list["WeakAxis"],
) -> str:
    """Build round-2+ dynamic instructions from weak axes.

    Injected into the QueryExpander on retry rounds to guide
    re-expansion toward the specific gaps identified by the Aggregator.

    Args:
        weak_axes: List of WeakAxis objects from previous Aggregator output.
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
```

### 3.2 `build_aggregator_user_message(focus_instruction, user_context, search_results) -> str`

Formats the complete user message sent to the Aggregator. This is NOT a system prompt -- it is the user-turn message containing all context the Aggregator needs to evaluate and synthesize.

```python
def build_aggregator_user_message(
    focus_instruction: str,
    user_context: str,
    search_results: list["SearchResult"],
) -> str:
    """Build the user message for the Aggregator with all search results.

    The Aggregator receives all context via its user message (not dynamic
    system instructions). This keeps the system prompt stable.

    Args:
        focus_instruction: Arabic focus instruction from the PlanAgent.
        user_context: Arabic user context (personal situation/question).
        search_results: All SearchResult objects accumulated across rounds.
            Each has .query, .raw_markdown, .result_count.

    Returns:
        Formatted Arabic message with focus header + context + results sections.
    """
    parts = [f"## تعليمات التركيز\n{focus_instruction}"]

    if user_context:
        parts.append(f"## سياق المستخدم\n{user_context}")

    parts.append("---")

    if not search_results:
        parts.append("## نتائج البحث\nلا توجد نتائج بحث.")
    else:
        results_lines = ["## نتائج البحث"]
        for i, r in enumerate(search_results, 1):
            results_lines.append(
                f"\n### استعلام {i}: {r.query}\n"
                f"**عدد النتائج:** {r.result_count}\n\n"
                f"{r.raw_markdown}"
            )
        parts.append("\n".join(results_lines))

    return "\n\n".join(parts)
```

---

## 4. Integration Instructions

### In `prompts.py`:

```python
# agents/deep_search_v3/compliance_search/prompts.py

EXPANDER_SYSTEM_PROMPT = """..."""
AGGREGATOR_SYSTEM_PROMPT = """..."""

def build_expander_dynamic_instructions(weak_axes: list[WeakAxis]) -> str: ...
def build_aggregator_user_message(focus_instruction: str, user_context: str, search_results: list[SearchResult]) -> str: ...
```

### In `expander.py`:

```python
from .prompts import EXPANDER_SYSTEM_PROMPT, build_expander_dynamic_instructions

def create_expander_agent(weak_axes: list[WeakAxis] | None = None) -> Agent[None, ExpanderOutput]:
    agent = Agent(
        get_model("compliance_search_expander"),
        system_prompt=EXPANDER_SYSTEM_PROMPT,
        output_type=ExpanderOutput,
    )

    if weak_axes:
        dynamic_text = build_expander_dynamic_instructions(weak_axes)

        @agent.system_prompt
        async def inject_weak_axes(ctx: RunContext[None]) -> str:
            return dynamic_text

    return agent
```

### In `aggregator.py`:

```python
from .prompts import AGGREGATOR_SYSTEM_PROMPT, build_aggregator_user_message

def create_aggregator_agent() -> Agent[None, AggregatorOutput]:
    return Agent(
        get_model("compliance_search_aggregator"),
        system_prompt=AGGREGATOR_SYSTEM_PROMPT,
        output_type=AggregatorOutput,
    )

# Called in AggregatorNode.run():
user_msg = build_aggregator_user_message(
    state.focus_instruction, state.user_context, state.all_search_results,
)
result = await aggregator.run(user_msg, usage_limits=AGGREGATOR_LIMITS)
```

---

## 5. Prompt Design Notes

### Token Estimates

| Prompt | Estimated Tokens |
|---|---|
| EXPANDER_SYSTEM_PROMPT (static) | ~200 |
| Expander dynamic (weak axes, max) | ~150 |
| AGGREGATOR_SYSTEM_PROMPT (static) | ~280 |
| Aggregator user message (typical) | ~1500-4000 (depends on search results) |

### Key Design Decisions

1. **Arabic-first opening**: Both prompts open with an Arabic sentence to set the LLM's language mode. This follows the Luna pattern established in deep_search_v2 and deep_search_v3.

2. **No deps, no tools**: Both agents are `Agent[None, ...]` -- pure structured output. All search infrastructure access is in the programmatic SearchNode. This keeps prompts simple and focused.

3. **Dynamic on Expander only**: The Expander receives weak axes as dynamic system prompt sections on retry rounds. The Aggregator receives all context (focus + user context + search results) via the user message, keeping its system prompt stable across rounds.

4. **Compliance-specific query rules**: Unlike the regulations domain (where system names mislead semantic search), compliance queries actively benefit from platform names (Qiwa, Absher, Nafith, etc.). This is explicitly stated in the expansion rules.

5. **Practical sufficiency threshold**: The Aggregator considers results sufficient when at least one directly matching service with actionable details is found. This is lower than the regulations aggregator (~80% coverage) because government services are more atomic -- one matching service often answers the question.

### Testing Checklist

- [ ] Expander produces 2-4 Arabic queries targeting government services
- [ ] Expander queries include platform names when relevant
- [ ] Expander does NOT produce legal text queries (regulations domain)
- [ ] Expander correctly incorporates weak axes on round 2+
- [ ] Expander does NOT repeat successful queries from previous rounds
- [ ] Aggregator correctly classifies quality (strong/moderate/weak)
- [ ] Aggregator synthesis_md is structured Arabic markdown with service details
- [ ] Aggregator citations use source_type="service" with correct fields
- [ ] Aggregator identifies weak axes with actionable suggested_query
- [ ] Aggregator sets sufficient=True when at least one matching service found
- [ ] Aggregator does not fabricate services not in search results
- [ ] build_expander_dynamic_instructions returns empty string when no weak axes
- [ ] build_aggregator_user_message correctly formats all search results
