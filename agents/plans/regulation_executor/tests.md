# Regulation Executor -- Test Plan

## Test Fixtures

### Mock Dependencies

```python
from dataclasses import dataclass, field
from unittest.mock import MagicMock, AsyncMock, patch
import httpx
import json

from agents.deep_search.executors.regulation_executor import (
    RegulationSearchDeps,
    RegulationSearchResult,
    regulation_executor,
    run_regulation_search,
)


# ── Mock Embedding ──

async def mock_embed_768(text: str) -> list[float]:
    """Mock 768-dim Gemini embedding."""
    return [0.01] * 768


# ── Mock Supabase with vector search results ──

def make_mock_supabase(
    article_results: list[dict] | None = None,
    section_results: list[dict] | None = None,
    regulation_results: list[dict] | None = None,
    unfold_article: dict | None = None,
    unfold_regulation: dict | None = None,
    unfold_siblings: list[dict] | None = None,
) -> MagicMock:
    """Create a mock Supabase client with configurable search results."""
    mock = MagicMock()

    # Default article results
    if article_results is None:
        article_results = [
            {
                "id": "art-001",
                "chunk_ref": "labor_law_art_77",
                "title": "المادة السابعة والسبعون",
                "article_num": 77,
                "identifier_number": "77",
                "content_preview": "يحق لأي من طرفي عقد العمل المحدد المدة أو غير المحدد إنهاء العقد لسبب مشروع...",
                "regulation_id": "reg-001",
                "similarity": 0.85,
            },
            {
                "id": "art-002",
                "chunk_ref": "labor_law_art_80",
                "title": "المادة الثمانون",
                "article_num": 80,
                "identifier_number": "80",
                "content_preview": "لا يجوز لصاحب العمل فسخ العقد دون مكافأة أو إشعار إلا في الحالات التالية...",
                "regulation_id": "reg-001",
                "similarity": 0.78,
            },
            {
                "id": "art-003",
                "chunk_ref": "labor_law_art_83",
                "title": "المادة الثالثة والثمانون",
                "article_num": 83,
                "identifier_number": "83",
                "content_preview": "إذا أنهي العقد لسبب غير مشروع كان للطرف المتضرر الحق في تعويض.",
                "regulation_id": "reg-001",
                "similarity": 0.72,
            },
        ]

    if section_results is None:
        section_results = [
            {
                "id": "sec-001",
                "chunk_ref": "labor_law_ch5",
                "title": "الباب الخامس: علاقات العمل",
                "content_preview": "تنظم هذه المواد العلاقة بين صاحب العمل والعامل...",
                "regulation_id": "reg-001",
                "similarity": 0.65,
            },
        ]

    if regulation_results is None:
        regulation_results = [
            {
                "id": "reg-001",
                "regulation_ref": "labor_law_main",
                "title": "نظام العمل",
                "type": "نظام",
                "main_category": "regulation",
                "authority_level": "binding_law",
                "authority_score": 9,
                "summary_preview": "ينظم نظام العمل العلاقة بين أصحاب العمل والعمال...",
                "similarity": 0.60,
            },
        ]

    # Configure RPC calls for vector search
    # The actual implementation will use supabase.rpc() for vector queries
    mock.rpc.return_value.execute.return_value.data = article_results

    # Configure unfold lookups
    if unfold_article is None:
        unfold_article = {
            "content": "يحق لأي من طرفي عقد العمل المحدد المدة أو غير المحدد إنهاء العقد لسبب مشروع بموجب إشعار خطي يوجه إلى الطرف الآخر...",
            "article_context": "تحدد هذه المادة حقوق إنهاء عقد العمل والتعويضات المستحقة",
        }

    if unfold_regulation is None:
        unfold_regulation = {
            "title": "نظام العمل",
            "type": "نظام",
            "authority_level": "binding_law",
            "authority_score": 9,
            "main_category": "regulation",
            "regulation_ref": "labor_law_main",
        }

    if unfold_siblings is None:
        unfold_siblings = [
            {"title": "المادة 78", "article_num": 78, "content_preview": "يجب أن يكون الإشعار كتابياً..."},
            {"title": "المادة 79", "article_num": 79, "content_preview": "يستحق الطرف المتضرر..."},
        ]

    return mock


# ── Mock Jina Reranker ──

def make_mock_jina_response(
    num_results: int = 3,
    top_score: float = 0.92,
) -> dict:
    """Create a mock Jina reranker API response."""
    results = []
    for i in range(num_results):
        score = top_score - (i * 0.1)
        results.append({
            "index": i,
            "relevance_score": max(score, 0.1),
            "document": {"text": f"Result {i} text..."},
        })
    return {"results": results}


# ── Test Deps Factory ──

def make_test_deps(
    supabase: MagicMock | None = None,
    jina_key: str = "test-jina-key",
) -> RegulationSearchDeps:
    """Create test RegulationSearchDeps."""
    return RegulationSearchDeps(
        supabase=supabase or make_mock_supabase(),
        embedding_fn=mock_embed_768,
        jina_api_key=jina_key,
        http_client=httpx.AsyncClient(),
    )
```

### FunctionModel Fixtures

```python
from pydantic_ai.models.function import FunctionModel, AgentInfo
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart


def make_model_happy_path():
    """Model that: (1) embed_and_search, (2) rerank, (3) unfold, (4) return result."""
    call_count = 0

    def model_fn(messages, info: AgentInfo):
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            # Step 1: Vector search
            return ModelResponse(parts=[
                ToolCallPart(
                    tool_name="embed_and_search",
                    args={"query": "الفصل التعسفي نظام العمل"},
                ),
            ])
        elif call_count == 2:
            # Step 2: Rerank
            return ModelResponse(parts=[
                ToolCallPart(
                    tool_name="rerank_results",
                    args={
                        "query": "الفصل التعسفي نظام العمل",
                        "candidate_ids": ["art-001", "art-002", "art-003", "sec-001"],
                    },
                ),
            ])
        elif call_count == 3:
            # Step 3: Unfold top results
            return ModelResponse(parts=[
                ToolCallPart(
                    tool_name="unfold_context",
                    args={"top_result_ids": ["art-001", "art-002", "art-003"]},
                ),
            ])
        else:
            # Step 4: Return structured result
            result = {
                "quality": "strong",
                "result_count": 3,
                "results_md": "## نظام العمل\n\n### المادة 77\n> يحق لأي من طرفي العقد...\n\n### المادة 80\n> لا يجوز لصاحب العمل...\n\n### المادة 83\n> إذا أنهي العقد لسبب غير مشروع...",
                "citations": [
                    {
                        "source_type": "article",
                        "ref": "labor_law_art_77",
                        "title": "المادة 77",
                        "content_snippet": "يحق لأي من طرفي عقد العمل...",
                        "regulation_title": "نظام العمل",
                        "article_num": "77",
                        "relevance": "تحدد حقوق إنهاء العقد",
                    },
                    {
                        "source_type": "article",
                        "ref": "labor_law_art_80",
                        "title": "المادة 80",
                        "content_snippet": "لا يجوز لصاحب العمل فسخ العقد...",
                        "regulation_title": "نظام العمل",
                        "article_num": "80",
                        "relevance": "حالات الفصل المشروع",
                    },
                ],
                "top_score": 0.92,
            }
            return ModelResponse(parts=[
                TextPart(text=json.dumps(result, ensure_ascii=False)),
            ])

    return FunctionModel(model_fn)


def make_model_with_text_fallback():
    """Model that gets weak vector results, uses text fallback, then reranks."""
    call_count = 0

    def model_fn(messages, info: AgentInfo):
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            return ModelResponse(parts=[
                ToolCallPart(
                    tool_name="embed_and_search",
                    args={"query": "شروط الرهن التجاري"},
                ),
            ])
        elif call_count == 2:
            # Vector results are weak, call text fallback
            return ModelResponse(parts=[
                ToolCallPart(
                    tool_name="text_search_fallback",
                    args={"query": "شروط الرهن التجاري"},
                ),
            ])
        elif call_count == 3:
            return ModelResponse(parts=[
                ToolCallPart(
                    tool_name="rerank_results",
                    args={
                        "query": "شروط الرهن التجاري",
                        "candidate_ids": ["art-010", "art-011"],
                    },
                ),
            ])
        elif call_count == 4:
            return ModelResponse(parts=[
                ToolCallPart(
                    tool_name="unfold_context",
                    args={"top_result_ids": ["art-010"]},
                ),
            ])
        else:
            result = {
                "quality": "moderate",
                "result_count": 1,
                "results_md": "## نظام الرهن التجاري\n\n### المادة 1\n> ...",
                "citations": [],
                "top_score": 0.55,
            }
            return ModelResponse(parts=[
                TextPart(text=json.dumps(result, ensure_ascii=False)),
            ])

    return FunctionModel(model_fn)


def make_model_no_results():
    """Model that finds nothing and returns weak quality."""
    call_count = 0

    def model_fn(messages, info: AgentInfo):
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            return ModelResponse(parts=[
                ToolCallPart(
                    tool_name="embed_and_search",
                    args={"query": "قانون الفضاء الخارجي"},
                ),
            ])
        elif call_count == 2:
            return ModelResponse(parts=[
                ToolCallPart(
                    tool_name="text_search_fallback",
                    args={"query": "قانون الفضاء الخارجي"},
                ),
            ])
        else:
            result = {
                "quality": "weak",
                "result_count": 0,
                "results_md": "لم يُعثر على أنظمة أو مواد مطابقة للاستعلام.",
                "citations": [],
                "top_score": 0.0,
            }
            return ModelResponse(parts=[
                TextPart(text=json.dumps(result, ensure_ascii=False)),
            ])

    return FunctionModel(model_fn)


def make_model_jina_failure():
    """Model where Jina reranker fails -- should fall back to similarity sort."""
    call_count = 0

    def model_fn(messages, info: AgentInfo):
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            return ModelResponse(parts=[
                ToolCallPart(
                    tool_name="embed_and_search",
                    args={"query": "الفصل التعسفي"},
                ),
            ])
        elif call_count == 2:
            # Rerank will fail (mocked to return fallback)
            return ModelResponse(parts=[
                ToolCallPart(
                    tool_name="rerank_results",
                    args={
                        "query": "الفصل التعسفي",
                        "candidate_ids": ["art-001", "art-002"],
                    },
                ),
            ])
        elif call_count == 3:
            return ModelResponse(parts=[
                ToolCallPart(
                    tool_name="unfold_context",
                    args={"top_result_ids": ["art-001", "art-002"]},
                ),
            ])
        else:
            result = {
                "quality": "moderate",
                "result_count": 2,
                "results_md": "## نتائج (بدون إعادة ترتيب)\n\n...",
                "citations": [],
                "top_score": 0.85,
            }
            return ModelResponse(parts=[
                TextPart(text=json.dumps(result, ensure_ascii=False)),
            ])

    return FunctionModel(model_fn)
```

---

## Test Scenarios

### 1. Happy Path -- Vector Search + Rerank + Unfold

- **Input**: `query="الفصل التعسفي في نظام العمل السعودي"`
- **Expected**: `quality="strong"`, `result_count >= 3`, `top_score > 0.7`
- **Key Assertions**:
  - `embed_and_search` called with the query
  - `rerank_results` called with candidate IDs from vector search
  - `unfold_context` called with top reranked IDs
  - `results_md` contains article titles and content excerpts in Arabic
  - `citations` list has at least 2 entries with `source_type`, `ref`, `title`
  - Return string starts with `## نتائج البحث في الأنظمة`
  - Return string contains `**الجودة: قوية**`

### 2. Weak Vector Results -- Text Fallback Triggered

- **Input**: `query="شروط الرهن التجاري"` (with mocked weak vector results)
- **Expected**: `text_search_fallback` called after weak vector results
- **Key Assertions**:
  - `embed_and_search` called first
  - Agent evaluates results as weak (few results, low scores)
  - `text_search_fallback` called with same or refined query
  - Combined candidates sent to reranker
  - Final quality is "moderate" or better

### 3. No Results Found

- **Input**: `query="قانون الفضاء الخارجي"` (topic not in DB)
- **Expected**: `quality="weak"`, `result_count=0`
- **Key Assertions**:
  - Both `embed_and_search` and `text_search_fallback` called
  - Both return empty/minimal results
  - `results_md` contains a "no results" message in Arabic
  - `citations` is empty list
  - Return string contains `**الجودة: ضعيفة**`

### 4. Jina Reranker Failure -- Graceful Degradation

- **Input**: `query="الفصل التعسفي"` (with mocked Jina API failure)
- **Expected**: Results sorted by vector similarity instead of reranker score
- **Key Assertions**:
  - `rerank_results` tool handles the Jina error internally (does NOT raise)
  - Returns candidates sorted by original `similarity` score
  - `quality` is based on similarity scores (not reranker scores)
  - Agent still produces a valid `RegulationSearchResult`

### 5. Timeout Handling

- **Input**: `query="any query"` (with mocked slow executor)
- **Expected**: `run_regulation_search` returns timeout error string
- **Key Assertions**:
  - Return value is a string containing "انتهت مهلة البحث"
  - No exception propagated to caller
  - Logged as warning

### 6. Embedding Dimension Correctness

- **Input**: Any query
- **Expected**: Embedding function produces exactly 768-dim vector
- **Key Assertions**:
  - `embed_regulation_query()` returns `list[float]` of length 768
  - NOT 1536 (which would be OpenAI's model)
  - Vector is passed correctly to Supabase vector search

### 7. Truncation -- Large Results

- **Input**: Query that returns articles with very long content (mocked 20K+ char content)
- **Expected**: Results truncated to budget
- **Key Assertions**:
  - Individual article content capped at 3,000 chars
  - Total results_md under 40,000 chars
  - Results are ordered by relevance (most relevant kept, least relevant dropped)
  - If results are dropped, a note about omitted results is included

### 8. Structured Output Validation

- **Input**: Various queries
- **Expected**: `RegulationSearchResult` validates correctly
- **Key Assertions**:
  - `quality` is one of: "strong", "moderate", "weak"
  - `result_count` is non-negative integer
  - `top_score` is float between 0.0 and 1.0
  - `citations` is a list of dicts (each with required fields)
  - `results_md` is non-empty string for strong/moderate quality

### 9. Candidate Cache Isolation

- **Input**: Two sequential calls to `run_regulation_search`
- **Expected**: Each call starts with empty `_candidate_cache`
- **Key Assertions**:
  - `deps._candidate_cache` is reset to `{}` at the start of each run
  - `deps._reranked_results` is reset to `[]` at the start of each run
  - Results from call 1 do not leak into call 2

### 10. Integration -- Planner Tool Wiring

- **Input**: Planner calls `search_regulations("الفصل التعسفي")` tool
- **Expected**: Tool constructs `RegulationSearchDeps`, calls `run_regulation_search`, returns string
- **Key Assertions**:
  - `RegulationSearchDeps.embedding_fn` is `embed_regulation_query` (768-dim)
  - `RegulationSearchDeps.jina_api_key` comes from settings
  - SSE status event appended to `ctx.deps._sse_events`
  - Return value is a string (not a structured object)
  - Planner model can parse quality from the returned markdown

---

## Arabic Test Queries

```python
REGULATION_TEST_QUERIES = [
    # Specific article reference
    "ما نص المادة 77 من نظام العمل؟",
    "أحكام المادة 80 في نظام العمل",

    # Topical search
    "ما هي شروط الفصل التعسفي في القانون السعودي؟",
    "حقوق المرأة العاملة في نظام العمل",
    "أحكام الرهن التجاري",

    # Regulation-specific
    "نظام المعاملات المدنية",
    "لائحة نظام العمل التنفيذية",

    # Broad domain
    "حماية المستهلك في المملكة العربية السعودية",
    "ضوابط التجارة الإلكترونية",

    # Edge cases
    "المادة الأولى من جميع الأنظمة",  # very broad
    "قانون الذكاء الاصطناعي",  # may not exist in DB
]
```

---

## Production Limits

```python
from pydantic_ai import UsageLimits

EXECUTOR_LIMITS = UsageLimits(
    response_tokens_limit=4_000,
    request_limit=5,
    tool_calls_limit=8,
)
```

---

## Environment Requirements for Tests

### Unit Tests (no external calls)
- Mock Supabase client
- Mock embedding function (returns `[0.01] * 768`)
- Mock HTTP client (for Jina API)
- FunctionModel (for agent behavior)

### Integration Tests (real DB, mock LLM)
- Real Supabase client with read access to `regulations`, `sections`, `articles`
- Real `embed_regulation_query()` (requires `GOOGLE_API_KEY`)
- Mock HTTP client for Jina (or real Jina with test key)
- FunctionModel for agent behavior

### End-to-End Tests (real everything)
- Real Supabase, real Gemini embeddings, real Jina reranker, real Gemini 3 Flash model
- Test with known queries that should return specific articles
- Verify quality assessment matches expected results
- Measure latency (should complete within 25s timeout)
