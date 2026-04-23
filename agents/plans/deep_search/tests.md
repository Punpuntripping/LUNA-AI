# Deep Search Planner — Test Plan

## Test Fixtures

### Mock Dependencies

```python
from dataclasses import dataclass
from unittest.mock import MagicMock, AsyncMock
from agents.deep_search.agent import SearchDeps, planner_agent, PlannerResult

# Mock Supabase
mock_supabase = MagicMock()
mock_supabase.table.return_value.select.return_value.eq.return_value.is_.return_value.order.return_value.limit.return_value.execute.return_value.data = []
mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [{"artifact_id": "art-001"}]
mock_supabase.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = {
    "content_md": "# Previous Report\n\nExisting content..."
}

# Mock embedding
async def mock_embed(text: str) -> list[float]:
    return [0.0] * 1536

def make_test_deps(case_memory: str | None = None, case_id: str | None = None) -> SearchDeps:
    return SearchDeps(
        supabase=mock_supabase,
        embedding_fn=mock_embed,
        user_id="user-001",
        conversation_id="conv-001",
        case_id=case_id,
        case_memory=case_memory,
    )
```

### Mock Executor Responses

Since we're planning only the planner (not real executors), the delegation tools return mock markdown:

```python
MOCK_REGULATION_RESULT = """## نتائج البحث في الأنظمة
**الجودة: قوية**

### المادة 77 — إنهاء العقد بتعويض
> يحق لأي من طرفي عقد العمل المحدد المدة أو غير المحدد إنهاء العقد لسبب مشروع بموجب إشعار خطي.

### المادة 80 — حالات الفصل المشروع
> لا يجوز لصاحب العمل فسخ العقد دون مكافأة أو إشعار إلا في الحالات التالية...

### المادة 83 — التعويض عن الفصل التعسفي
> إذا أنهي العقد لسبب غير مشروع كان للطرف المتضرر الحق في تعويض.

**مصادر**: chunk_ref:labor_law_art_77, chunk_ref:labor_law_art_80, chunk_ref:labor_law_art_83
"""

MOCK_CASES_RESULT = """## نتائج الأحكام القضائية
**الجودة: متوسطة**

### حكم محكمة الاستئناف — 1445/3/15
> فصل العامل بعد 5 سنوات دون إنذار مسبق. حكمت المحكمة بالتعويض وفقاً للمادة 77.

### حكم المحكمة العمالية — 1444/11/20
> إنهاء عقد محدد المدة قبل انتهائه. المحكمة أيدت حق العامل في التعويض.

**النمط**: 3 من 4 أحكام أيدت حق التعويض
**مصادر**: case_ref:appeal_1445_315, case_ref:labor_1444_1120
"""

MOCK_COMPLIANCE_RESULT = """## نتائج الخدمات الحكومية
**الجودة: ضعيفة**

### تقديم شكوى عمالية — وزارة الموارد البشرية
يمكن تقديم شكوى عبر منصة مسار أو تطبيق الوزارة.

**مصادر**: service_ref:musaned_complaint
"""

MOCK_WEAK_RESULT = """## نتائج البحث في الأنظمة
**الجودة: ضعيفة**

لم يُعثر على نتائج مطابقة للاستعلام.
"""
```

### FunctionModel Fixtures

```python
from pydantic_ai.models.function import FunctionModel, AgentInfo
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart

def make_model_that_searches_then_reports():
    """Model that: (1) calls respond_to_user + search tools, (2) calls create_report, (3) returns PlannerResult."""
    call_count = 0

    def model_fn(messages, info: AgentInfo):
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            # First call: status update + search
            return ModelResponse(parts=[
                ToolCallPart(tool_name="respond_to_user", args={"message": "أبحث في الأنظمة..."}),
                ToolCallPart(tool_name="search_regulations", args={"query": "الفصل التعسفي نظام العمل"}),
                ToolCallPart(tool_name="search_cases_courts", args={"query": "أحكام الفصل التعسفي"}),
            ])
        elif call_count == 2:
            # Second call: create report with citations
            return ModelResponse(parts=[
                ToolCallPart(tool_name="respond_to_user", args={"message": "وجدت نتائج، أعد التقرير..."}),
                ToolCallPart(tool_name="create_report", args={
                    "title": "أحكام الفصل التعسفي",
                    "content_md": "# أحكام الفصل التعسفي\n\n## الإطار النظامي\n...",
                    "citations": [
                        {"source_type": "article", "ref": "art_77_ref", "title": "المادة 77",
                         "content_snippet": "يحق لأي من طرفي العقد...", "regulation_title": "نظام العمل",
                         "article_num": "77", "court": None, "relevance": "تحدد تعويض إنهاء العقد"},
                    ],
                }),
            ])
        else:
            # Third call: return final result
            return ModelResponse(parts=[TextPart(text='{"task_done": true, "end_reason": "done", "answer_ar": "تم إعداد التقرير", "search_summary": "بحث الفصل التعسفي: 3 مواد، 2 حكم", "artifact_md": "# أحكام الفصل التعسفي..."}')])

    return FunctionModel(model_fn)


def make_model_that_asks_user():
    """Model that asks a clarifying question first."""
    call_count = 0

    def model_fn(messages, info: AgentInfo):
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            return ModelResponse(parts=[
                ToolCallPart(tool_name="ask_user", args={"question": "هل تقصد فصل العامل أم فصل الشركاء؟"}),
            ])
        elif call_count == 2:
            return ModelResponse(parts=[
                ToolCallPart(tool_name="search_regulations", args={"query": "الفصل التعسفي نظام العمل"}),
            ])
        else:
            return ModelResponse(parts=[TextPart(text='{"task_done": true, "end_reason": "done", "answer_ar": "تم البحث", "search_summary": "...", "artifact_md": "..."}')])

    return FunctionModel(model_fn)


def make_model_that_detects_out_of_scope():
    """Model that immediately returns out-of-scope."""
    def model_fn(messages, info: AgentInfo):
        return ModelResponse(parts=[TextPart(text='{"task_done": true, "end_reason": "out_of_scope", "answer_ar": "هذا خارج نطاق البحث", "search_summary": "Task ended — out of scope", "artifact_md": ""}')])

    return FunctionModel(model_fn)


def make_model_that_re_searches():
    """Model that: (1) searches, (2) evaluates as weak, (3) re-searches with different query, (4) reports."""
    call_count = 0

    def model_fn(messages, info: AgentInfo):
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            # Round 1: search cases
            return ModelResponse(parts=[
                ToolCallPart(tool_name="respond_to_user", args={"message": "أبحث عن الأحكام..."}),
                ToolCallPart(tool_name="search_cases_courts", args={"query": "أحكام الفصل التعسفي"}),
            ])
        elif call_count == 2:
            # Evaluate round 1 as weak, re-search
            return ModelResponse(parts=[
                ToolCallPart(tool_name="respond_to_user", args={"message": "نتائج الأحكام ضعيفة، أبحث بصياغة مختلفة..."}),
                ToolCallPart(tool_name="search_cases_courts", args={"query": "إنهاء عقد العمل تعويض محكمة عمالية"}),
            ])
        elif call_count == 3:
            # Round 2 results sufficient, create report
            return ModelResponse(parts=[
                ToolCallPart(tool_name="create_report", args={
                    "title": "أحكام الفصل التعسفي",
                    "content_md": "# report content...",
                    "citations": [],
                }),
            ])
        else:
            return ModelResponse(parts=[TextPart(text='{"task_done": true, "end_reason": "done", "answer_ar": "تم إعداد التقرير", "search_summary": "...", "artifact_md": "..."}')])

    return FunctionModel(model_fn)


def make_model_that_edits_report():
    """Model that: (1) loads previous report, (2) searches for new content, (3) creates merged report."""
    call_count = 0

    def model_fn(messages, info: AgentInfo):
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            # Load existing report first
            return ModelResponse(parts=[
                ToolCallPart(tool_name="get_previous_report", args={"artifact_id": "rpt_001"}),
            ])
        elif call_count == 2:
            # Search for the new content to add
            return ModelResponse(parts=[
                ToolCallPart(tool_name="search_regulations", args={"query": "المادة 77 نظام العمل"}),
            ])
        elif call_count == 3:
            # Create updated report with merged content and cumulative citations
            return ModelResponse(parts=[
                ToolCallPart(tool_name="create_report", args={
                    "title": "أحكام الفصل التعسفي",
                    "content_md": "# Updated report with المادة 77 added...",
                    "citations": [
                        {"source_type": "article", "ref": "art_80_ref", "title": "المادة 80",
                         "content_snippet": "...", "regulation_title": "نظام العمل",
                         "article_num": "80", "court": None, "relevance": "existing citation"},
                        {"source_type": "article", "ref": "art_77_ref", "title": "المادة 77",
                         "content_snippet": "...", "regulation_title": "نظام العمل",
                         "article_num": "77", "court": None, "relevance": "new citation"},
                    ],
                }),
            ])
        else:
            return ModelResponse(parts=[TextPart(text='{"task_done": true, "end_reason": "done", "answer_ar": "تم تحديث التقرير", "search_summary": "...", "artifact_md": "..."}')])

    return FunctionModel(model_fn)
```

## Test Scenarios

### 1. Happy Path — Search + Report

- **Input**: Briefing: "المستخدم يسأل عن أحكام الفصل التعسفي في نظام العمل السعودي"
- **Expected Output**: `PlannerResult(task_done=True, end_reason="done")`
- **Key Assertions**:
  - At least one delegation tool was called
  - `create_report` was called with non-empty `content_md` and `citations` list
  - `respond_to_user` was called at least once (status update)
  - `answer_ar` is non-empty Arabic string
  - `artifact_md` contains report markdown
- **UsageLimits**: Default (10K response, 20 requests, 25 tool calls)

### 2. Ambiguous Query — ask_user

- **Input**: Briefing: "المستخدم يسأل عن حكم الفصل. لم يحدد أي نوع."
- **Expected Output**: `ask_user` tool called before any search tool
- **Key Assertions**:
  - `ask_user` called with Arabic question
  - After user reply, search proceeds normally
  - Final result is `task_done=True`

### 3. Out of Scope Detection (first turn)

- **Input**: Briefing from router: "اكتب لي عقد عمل" (contract drafting, not search)
- **Expected Output**: `PlannerResult(task_done=True, end_reason="out_of_scope")`
- **Key Assertions**:
  - No delegation tools called
  - `answer_ar` explains out-of-scope
  - `search_summary` mentions out of scope for router context

### 4. Out of Scope During Pinned Task

- **Context**: Deep search task is already pinned, user sends out-of-scope follow-up
- **Input**: Follow-up message: "اكتب لي عقد عمل" (while task is pinned for search)
- **Expected Output**: `PlannerResult(task_done=True, end_reason="out_of_scope")`
- **Key Assertions**:
  - `answer_ar` explains this is outside deep search scope (e.g., "هذا الطلب خارج نطاق البحث. سأحولك للمحادثة الرئيسية.")
  - `search_summary` includes both what was researched so far AND that user requested out-of-scope action
  - `artifact_md` contains whatever report state existed before the out-of-scope message
  - No new delegation tools called for the contract request
- **Post-condition (orchestrator-level)**: After TaskEnd with reason="out_of_scope", orchestrator should re-feed the message to the router for correct routing to end_services

### 5. Editing Existing Report

- **Input**: Briefing with artifact_id: "أضف المادة 77 للتقرير السابق"
- **Expected Output**: `get_previous_report` called first, then search, then `create_report` with merged content
- **Key Assertions**:
  - `get_previous_report` called with correct artifact_id
  - `create_report` content includes both old and new content
  - Citations are cumulative (existing + new merged)
  - `create_report.citations` list has entries from both old and new sources

### 6. Weak Results — Re-search with Different Query

- **Input**: Briefing about a topic where first search returns weak results
- **Expected Output**: First search returns weak results, planner re-searches with a different query to the same executor
- **Key Assertions**:
  - `respond_to_user` called explaining weak results before re-search
  - Same delegation tool called again with a **different** query string
  - Total search rounds <= 3
  - If round 3 still weak, returns partial results with a note (does not loop forever)

### 7. Case Context — Memory Injection

- **Input**: Briefing + `case_memory` with labor law context
- **Expected Output**: Planner uses case context to focus search queries
- **Key Assertions**:
  - Dynamic instruction injected case memory text
  - Search queries are informed by case context (labor law domain)

### 8. Budget Enforcement — UsageLimits

- **Input**: Briefing that could trigger infinite search loops
- **Expected Output**: Agent stops within UsageLimits
- **Key Assertions**:
  - Total tool calls <= 25
  - Total requests <= 20
  - Agent returns partial results with a note rather than crashing

### 9. Task Completion Lifecycle (orchestrator integration)

- **Purpose**: Verify the orchestrator correctly handles PlannerResult wrapping
- **Key Assertions**:
  - `PlannerResult(task_done=True, end_reason="done")` wraps to `TaskEnd(type="end", reason="done")`
  - `PlannerResult(task_done=False)` wraps to `TaskContinue(type="continue")`
  - `TaskEnd.summary` comes from `PlannerResult.search_summary`
  - `TaskEnd.last_response` comes from `PlannerResult.answer_ar`
  - `TaskContinue.response` comes from `PlannerResult.answer_ar`
  - `TaskContinue.artifact` comes from `PlannerResult.artifact_md` (FULL, not diff)
  - On TaskEnd: orchestrator unpins task, clears task_history, injects summary into router_history

## Arabic Test Queries

```python
ARABIC_TEST_QUERIES = [
    # Clear, specific
    "ما حكم الفصل التعسفي في نظام العمل السعودي؟",
    "ما هي حقوق المرأة العاملة في نظام العمل؟",
    "كيف يتم حساب مكافأة نهاية الخدمة؟",

    # Ambiguous (should trigger ask_user)
    "ما حكم الفصل؟",
    "أريد معرفة حقوقي",

    # With specific legal references
    "اشرح لي المادة 77 من نظام العمل",
    "ما الفرق بين المادة 80 والمادة 81؟",

    # Case-context queries
    "هل ينطبق نظام العمل على هذه القضية؟",
    "ما هي الأنظمة المتعلقة بقضيتي؟",

    # Out of scope
    "اكتب لي عقد عمل",
    "استخرج البيانات من هذا الملف",
]
```

## Production Limits

```python
from pydantic_ai import UsageLimits

PLANNER_LIMITS = UsageLimits(
    response_tokens_limit=10_000,
    request_limit=20,
    tool_calls_limit=25,
)
```
