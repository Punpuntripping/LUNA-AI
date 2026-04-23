# Router Agent — Test Plan

## Test Fixtures

### Mock Dependencies

```python
from dataclasses import dataclass
from unittest.mock import MagicMock
from agents.router.router import RouterDeps, router_agent
from agents.models import ChatResponse, OpenTask

# Mock Supabase — artifact reads
mock_supabase = MagicMock()
mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.is_.return_value.maybe_single.return_value.execute.return_value.data = {
    "title": "تقرير الفصل التعسفي",
    "content_md": "# تقرير\n\n## المادة 77\nنص المادة..."
}


def make_test_deps(
    case_memory_md: str | None = None,
    case_metadata: dict | None = None,
    case_id: str | None = None,
    user_preferences: dict | None = None,
) -> RouterDeps:
    return RouterDeps(
        supabase=mock_supabase,
        user_id="user-001",
        conversation_id="conv-001",
        case_id=case_id,
        case_memory_md=case_memory_md,
        case_metadata=case_metadata,
        user_preferences=user_preferences,
    )
```

### FunctionModel Fixtures

```python
from pydantic_ai.models.function import FunctionModel, AgentInfo
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart


def make_model_chat_response():
    """Model that returns a ChatResponse directly."""
    def model_fn(messages, info: AgentInfo):
        return ModelResponse(parts=[TextPart(
            text='{"type": "chat", "message": "مرحباً! كيف يمكنني مساعدتك اليوم؟"}'
        )])
    return FunctionModel(model_fn)


def make_model_deep_search_task():
    """Model that returns an OpenTask for deep_search."""
    def model_fn(messages, info: AgentInfo):
        return ModelResponse(parts=[TextPart(
            text='{"type": "task", "task_type": "deep_search", "briefing": "المستخدم يسأل عن أحكام الفصل التعسفي في نظام العمل السعودي. يريد معرفة: (1) الحالات التي يعتبر فيها الفصل تعسفياً، (2) حقوق العامل المفصول تعسفياً، (3) التعويضات المستحقة.", "artifact_id": null}'
        )])
    return FunctionModel(model_fn)


def make_model_end_services_task():
    """Model that returns an OpenTask for end_services."""
    def model_fn(messages, info: AgentInfo):
        return ModelResponse(parts=[TextPart(
            text='{"type": "task", "task_type": "end_services", "briefing": "المستخدم يطلب صياغة عقد عمل محدد المدة لموظف سعودي. المدة: سنة واحدة. الراتب: 8000 ريال. يتضمن فترة تجربة 3 أشهر.", "artifact_id": null}'
        )])
    return FunctionModel(model_fn)


def make_model_extraction_task():
    """Model that returns an OpenTask for extraction."""
    def model_fn(messages, info: AgentInfo):
        return ModelResponse(parts=[TextPart(
            text='{"type": "task", "task_type": "extraction", "briefing": "المستخدم رفع ملف PDF ويريد استخراج النصوص والبيانات الأساسية منه.", "artifact_id": null}'
        )])
    return FunctionModel(model_fn)


def make_model_artifact_question():
    """Model that calls get_artifact then returns ChatResponse."""
    call_count = 0

    def model_fn(messages, info: AgentInfo):
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            # First call: read the artifact
            return ModelResponse(parts=[
                ToolCallPart(tool_name="get_artifact", args={"artifact_id": "art-001"}),
            ])
        else:
            # Second call: answer from artifact content
            return ModelResponse(parts=[TextPart(
                text='{"type": "chat", "message": "بحسب التقرير السابق، المادة 77 من نظام العمل تنص على أنه يحق لأي من طرفي العقد إنهاؤه لسبب مشروع بموجب إشعار كتابي."}'
            )])

    return FunctionModel(model_fn)


def make_model_edit_artifact():
    """Model that returns OpenTask with artifact_id for editing."""
    def model_fn(messages, info: AgentInfo):
        return ModelResponse(parts=[TextPart(
            text='{"type": "task", "task_type": "deep_search", "briefing": "المستخدم يريد تعديل التقرير السابق وإضافة معلومات عن المادة 81 من نظام العمل.", "artifact_id": "art-001"}'
        )])
    return FunctionModel(model_fn)


def make_model_clarification():
    """Model that asks for clarification via ChatResponse."""
    def model_fn(messages, info: AgentInfo):
        return ModelResponse(parts=[TextPart(
            text='{"type": "chat", "message": "هل تقصد البحث في أحكام الفصل في نظام العمل أم في نظام الشركات؟ يرجى التوضيح حتى أتمكن من مساعدتك بشكل أفضل."}'
        )])
    return FunctionModel(model_fn)
```

## Test Scenarios

### 1. Greeting — Direct ChatResponse

- **Input**: "مرحبا"
- **Expected Output**: `ChatResponse` with a greeting message
- **Key Assertions**:
  - `isinstance(result, ChatResponse)`
  - `result.type == "chat"`
  - `result.message` is non-empty Arabic string
  - No tools were called

### 2. Legal Research — OpenTask(deep_search)

- **Input**: "ما أحكام الفصل التعسفي في نظام العمل؟"
- **Expected Output**: `OpenTask(task_type="deep_search")`
- **Key Assertions**:
  - `isinstance(result, OpenTask)`
  - `result.type == "task"`
  - `result.task_type == "deep_search"`
  - `result.briefing` is non-empty, >= 50 characters
  - `result.briefing` contains reference to the user's question
  - `result.artifact_id is None` (new task, not editing)

### 3. Document Drafting — OpenTask(end_services)

- **Input**: "اكتب لي عقد عمل"
- **Expected Output**: `OpenTask(task_type="end_services")`
- **Key Assertions**:
  - `result.task_type == "end_services"`
  - `result.briefing` mentions contract/document drafting
  - `result.artifact_id is None`

### 4. File Extraction — OpenTask(extraction)

- **Input**: "استخرج البيانات من الملف المرفوع"
- **Expected Output**: `OpenTask(task_type="extraction")`
- **Key Assertions**:
  - `result.task_type == "extraction"`
  - `result.briefing` mentions file extraction

### 5. Artifact Question — get_artifact + ChatResponse

- **Input**: "ماذا قال التقرير السابق عن المادة 77؟"
- **Conversation history**: Contains task summary with `Artifact: art-001`
- **Expected Output**: `ChatResponse` answering the question from artifact content
- **Key Assertions**:
  - `get_artifact` tool was called with `artifact_id="art-001"`
  - `isinstance(result, ChatResponse)`
  - `result.message` references content from the artifact

### 6. Artifact Edit Request — OpenTask with artifact_id

- **Input**: "عدّل التقرير وأضف المادة 81"
- **Conversation history**: Contains task summary with `Artifact: art-001`
- **Expected Output**: `OpenTask` with `artifact_id="art-001"`
- **Key Assertions**:
  - `isinstance(result, OpenTask)`
  - `result.artifact_id == "art-001"`
  - `result.briefing` mentions editing and adding Article 81

### 7. Ambiguous Message — Clarification ChatResponse

- **Input**: "ما حكم الفصل؟"
- **Expected Output**: `ChatResponse` asking for clarification
- **Key Assertions**:
  - `isinstance(result, ChatResponse)`
  - `result.message` contains a question mark or clarification request
  - No OpenTask returned (router asks before dispatching)

### 8. Case Context — Briefing Includes Case Info

- **Input**: "ابحث عن الأنظمة المتعلقة بقضيتي"
- **Deps**: `case_memory_md="### معلومات القضية\nعقد عمل - فصل تعسفي"`, `case_id="case-001"`
- **Expected Output**: `OpenTask(task_type="deep_search")`
- **Key Assertions**:
  - Dynamic instruction injected case context
  - `result.briefing` references labor law / unfair dismissal from case context

### 9. Error Fallback — ChatResponse on Exception

- **Input**: Any message that causes the model to throw
- **Expected Output**: `ChatResponse` with Arabic error message
- **Key Assertions**:
  - `isinstance(result, ChatResponse)`
  - `result.message` contains error text in Arabic
  - No exception propagated to caller

### 10. English Message — English Response

- **Input**: "What are the labor law provisions for termination?"
- **Expected Output**: Either `ChatResponse` in English or `OpenTask` with English briefing
- **Key Assertions**:
  - If ChatResponse: message is in English
  - If OpenTask: briefing captures the English query

### 11. Meta Question — Direct ChatResponse

- **Input**: "ما هي لونا؟"
- **Expected Output**: `ChatResponse` explaining Luna
- **Key Assertions**:
  - `isinstance(result, ChatResponse)`
  - No tools called
  - No OpenTask returned

### 12. Usage Limits Enforcement

- **Input**: Message that might cause loops
- **Expected Output**: Agent respects UsageLimits
- **Key Assertions**:
  - Total requests <= 5
  - Total tool calls <= 3
  - Response tokens <= 2000
  - Agent returns a result (not crash) even at limit

## Arabic Test Queries

```python
ROUTER_TEST_QUERIES = {
    # Should produce ChatResponse
    "greeting": [
        "مرحبا",
        "السلام عليكم",
        "كيف حالك؟",
        "شكراً",
    ],
    "meta": [
        "ما هي لونا؟",
        "كيف تعمل؟",
        "ما الخدمات المتاحة؟",
    ],
    "clarification_needed": [
        "ما حكم الفصل؟",
        "أريد معرفة حقوقي",
        "ساعدني",
    ],

    # Should produce OpenTask(deep_search)
    "deep_search": [
        "ما أحكام الفصل التعسفي في نظام العمل؟",
        "ابحث لي عن المادة 77 من نظام العمل",
        "حلل لي الفرق بين المادة 80 والمادة 81",
        "ما حقوق المرأة العاملة في نظام العمل السعودي؟",
        "اشرح لي بالتفصيل أحكام مكافأة نهاية الخدمة",
    ],

    # Should produce OpenTask(end_services)
    "end_services": [
        "اكتب لي عقد عمل",
        "أريد صياغة مذكرة دفاع",
        "ساعدني في كتابة خطاب إنذار",
        "أحتاج مسودة رأي قانوني",
    ],

    # Should produce OpenTask(extraction)
    "extraction": [
        "استخرج البيانات من هذا الملف",
        "لخص لي هذه الوثيقة",
    ],
}
```

## Production Limits

```python
from pydantic_ai.usage import UsageLimits

ROUTER_LIMITS = UsageLimits(
    response_tokens_limit=2000,
    request_limit=5,
    tool_calls_limit=3,
)
```
