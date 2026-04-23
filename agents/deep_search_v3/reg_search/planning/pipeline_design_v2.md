# reg_search Pipeline Design v2 — Full Implementation Reference
**Status:** Reflects actual implementation as of 2026-04-19
**Supersedes:** `reranker_plan.md` (v1), `reranker_v2_plan.md` (v2 design notes)
**Purpose:** Complete input/output contract + design decisions for every stage and loop

---

## Pipeline Overview

```
User query (focus_instruction + user_context)
  │
  ▼
┌─────────────────────────────────────────────────────┐
│                   reg_search_graph                  │
│                                                     │
│  ┌──────────────┐     ┌──────────────┐              │
│  │ ExpanderNode │────▶│  SearchNode  │              │
│  │   (LLM)      │     │  (code+RPC)  │              │
│  └──────────────┘     └──────┬───────┘              │
│         ▲                    │                      │
│         │ weak+retry         ▼                      │
│  ┌──────┴──────┐     ┌──────────────┐              │
│  │AggregatorNode│◀───│ RerankerNode │              │
│  │   (LLM)      │    │ (LLM+code)   │              │
│  └──────────────┘    └──────────────┘              │
│         │                                           │
│         ▼                                           │
│    End[RegSearchResult]                             │
└─────────────────────────────────────────────────────┘
  │
  ▼ (when called via full_loop_runner.py)
merge_partial_ura()  →  PartialURA
  │
  ▼
run_compliance_from_partial_ura()  →  ComplianceURASlice
  │
  ▼
merge_to_ura()  →  UnifiedRetrievalArtifact
  │
  ▼
load_aggregator_input_from_ura()  →  AggregatorInput
  │
  ▼
handle_aggregator_turn()  →  AggregatorOutput + Artifact
```

---

## Data Models — Complete Definitions

### LoopState (mutable graph state)

```python
@dataclass
class LoopState:
    # Inputs
    focus_instruction: str           # Arabic legal question / focus
    user_context: str                # User's raw message + session context

    # Configuration
    expander_prompt_key: str = "prompt_1"
    aggregator_prompt_key: str = "prompt_1"
    thinking_effort: str | None = None    # "low" | "medium" | "high" | "none"
    model_override: str | None = None     # registry key to override default models
    unfold_mode: str = "precise"          # "precise" (compact) | "full" (detailed)
    concurrency: int = 10                 # semaphore limit for parallel search calls
    max_rounds: int = 3                   # max aggregator→expander retry loops

    # Evolving state
    round_count: int = 0
    expander_output: ExpanderOutput | None = None
    all_search_results: list[SearchResult] = []
    aggregator_output: AggregatorOutput | None = None
    weak_axes: list[WeakAxis] = []
    all_queries_used: list[str] = []
    reranker_results: list[RerankerQueryResult] = []

    # Flags
    skip_reranker: bool = False
    skip_aggregator: bool = False

    # Observability
    sse_events: list[dict] = []           # SSE status events (streamed to frontend)
    inner_usage: list[dict] = []          # token/request usage per LLM call
    search_results_log: list[dict] = []   # {round, query, rationale, result_count, raw_markdown}
    step_timings: dict = {}               # {expander: float, search: float, reranker: float, aggregator: float}
```

### RegSearchDeps (injected dependencies)

```python
@dataclass
class RegSearchDeps:
    supabase: SupabaseClient
    embedding_fn: Callable[[str], Awaitable[list[float]]]
    jina_api_key: str = ""
    http_client: httpx.AsyncClient | None = None
    use_reranker: bool = False             # True = call Jina API; False = RRF fallback scoring
    score_threshold: float = 0.005        # drop results below this RRF/reranker score
    rrf_min_score: float = 0.1            # pre-reranker filter: drop RRF positions below this
    mock_results: dict | None = None      # inject mock data in tests
    _query_id: int = 0                    # set by CLI before run_reg_search()
    _log_id: str = ""                     # set by run_reg_search() before graph starts
    _events: list[dict] = []             # populated from state.sse_events after run
    _search_log: list[dict] = []         # populated from state.search_results_log after run
```

### Pydantic Output Models

```python
class ExpanderOutput(BaseModel):
    queries: list[str]                    # 2–10 Arabic search queries
    rationales: list[str] = []           # one rationale per query (parallel list)
    sectors: list[str] | None = None     # 1–4 sector names or null

class WeakAxis(BaseModel):
    reason: str                           # why this aspect is weak (Arabic)
    suggested_query: str                  # specific retry query targeting this gap

class AggregatorOutput(BaseModel):        # reg_search's own aggregator (used without reranker)
    sufficient: bool
    quality: Literal["strong", "moderate", "weak"]
    weak_axes: list[WeakAxis] = []
    synthesis_md: str                     # Arabic legal analysis markdown
    citations: list[Citation] = []

class Citation(BaseModel):               # reg_search citations (regulations-only)
    source_type: str                      # "regulation" | "article" | "section"
    ref: str                              # chunk_ref identifier
    title: str
    content_snippet: str = ""
    regulation_title: str | None = None
    article_num: str | None = None
    relevance: str = ""

class RerankerDecision(BaseModel):
    position: int                         # 1-based, matches [N] in header
    action: Literal["keep", "drop", "unfold"]
    unfold_mode: Literal[
        "article_precise", "section_detailed", "regulation_detailed"
    ] | None = None                       # only when action=="unfold"
    relevance: Literal["high", "medium"] | None = None  # only when action=="keep"
    reasoning: str = ""                   # short Arabic explanation

class RerankerClassification(BaseModel):
    sufficient: bool                      # ≥80% of results are relevant
    decisions: list[RerankerDecision] = []
    summary_note: str = ""               # Arabic collective assessment

class RerankedResult(BaseModel):
    source_type: Literal["article", "section"]
    title: str
    content: str = ""
    article_num: str | None = None
    article_context: str = ""
    references_content: str = ""
    regulation_title: str = ""
    section_title: str = ""
    section_summary: str = ""
    relevance: Literal["high", "medium"]
    reasoning: str = ""
    db_id: str = ""                       # Supabase UUID → becomes URA ref_id seed
```

### Dataclass Output Models

```python
@dataclass
class SearchResult:
    query: str
    raw_markdown: str
    result_count: int

@dataclass
class RerankerQueryResult:
    query: str
    rationale: str
    sufficient: bool
    results: list[RerankedResult]         # kept articles + up to 2 sections
    dropped_count: int
    summary_note: str
    unfold_rounds: int = 0                # LLM classification rounds run (1–3)
    total_unfolds: int = 0                # DB unfold calls made

class RegSearchResult(BaseModel):
    quality: Literal["strong", "moderate", "weak", "pending"]
    summary_md: str
    citations: list[Citation] = []
    domain: Literal["regulations"] = "regulations"
    queries_used: list[str] = []
    rounds_used: int = 1
    expander_prompt_key: str = "prompt_1"
    aggregator_prompt_key: str = "prompt_1"
```

---

## Stage 1 — ExpanderNode

### Entry point

```python
async def run_reg_search(
    focus_instruction: str,
    user_context: str,
    deps: RegSearchDeps,
    expander_prompt_key: str = "prompt_1",
    aggregator_prompt_key: str = "prompt_1",
    thinking_effort: str | None = None,
    model_override: str | None = None,
    unfold_mode: str = "precise",
    concurrency: int = 10,
    skip_reranker: bool = False,
    skip_aggregator: bool = False,
) -> RegSearchResult
```

Constructs `LoopState`, runs the graph starting at `ExpanderNode()`, then returns `graph_result.output`.

### Node: ExpanderNode

**Input (from LoopState):**
- `focus_instruction` — the Arabic legal question
- `user_context` — user's raw message + session context
- `round_count` — current iteration (1 = first round, 2+ = retry)
- `weak_axes` — gaps identified by previous aggregator (empty on round 1)
- `expander_prompt_key` — `"prompt_1"` (without sectors) or `"prompt_2"` (with sectors)
- `thinking_effort` — optional reasoning budget passed to model

**Processing:**
1. Increments `round_count`
2. Creates expander agent: `create_expander_agent(prompt_key, thinking_effort, model_override)`
3. Builds user message: `build_expander_user_message(focus_instruction, user_context)`
4. On round ≥ 2: appends `build_expander_dynamic_instructions(weak_axes, round_count)` to message
5. Runs agent, stores `ExpanderOutput` in `state.expander_output`
6. Extends `state.all_queries_used` with `output.queries`
7. Emits SSE status event

**Always returns:** `SearchNode()`

**Output written to LoopState:**
- `expander_output`: `ExpanderOutput` (queries, rationales, sectors)
- `all_queries_used`: accumulated across all rounds

### Agent Config

```python
Agent(
    model=get_agent_model("reg_search_expander"),  # default: OpenRouter deepseek-v3.2
    output_type=ExpanderOutput,
    instructions=system_prompt,
    retries=2,
    model_settings={"extra_body": {"reasoning": {"effort": thinking_effort}}}
    # ^ only if thinking_effort is set and not "none"
)
```

`EXPANDER_PROMPT_THINKING = {"prompt_1": "medium", "prompt_2": "medium"}`

### Prompt Variants

#### prompt_1 — Without Sectors (default)

**System prompt** (full Arabic text):

```
أنت متخصص في تحليل الأسئلة القانونية وتحويلها إلى استعلامات بحث دقيقة في الأنظمة واللوائح السعودية.

## كيف يعمل محرك البحث

يبحث بالتوازي في ثلاث طبقات: المواد النظامية، الأبواب/الفصول، والأنظمة الكاملة.
عند إيجاد نتيجة، يُوسّعها تلقائياً:

- **مطابقة مادة** ← يجلب سياق المادة الكامل + المراجع المتقاطعة + النظام الأم
- **مطابقة باب/فصل** ← يجلب جميع المواد تحته + مراجعها
- **مطابقة نظام** ← يجلب جميع أبوابه وفصوله مع ملخصاتها

استعلام دقيق يطابق مادة واحدة ذات صلة يجلب تلقائياً السياق المحيط. الاستعلامات العامة تُضعف المطابقة.

## منهجيتك: ثلاثة أنواع استعلامات مختلفة إلزامياً

يجب أن تحتوي مخرجاتك على ثلاثة أنواع مختلفة من الاستعلامات. كل نوع يستهدف طبقة مختلفة من المعرفة القانونية:

### النوع 1: استعلام مباشر (يطابق مادة محددة)
استعلام دقيق يستهدف المادة النظامية التي تعالج الواقعة المحددة مباشرة.

مثال — سؤال المستخدم: "متزوجة من أجنبي بدون موافقة، أبي أوثق الزواج"
- ✅ استعلام مباشر: "شروط توثيق عقد زواج المواطنة السعودية من أجنبي"
- ❌ ليس مباشراً: "الزواج من أجنبي في المملكة" (واسع جداً)

### النوع 2: استعلام تجريدي — step-back (يطابق باب أو فصل كامل)
ارجع خطوة للخلف: ما المبدأ القانوني العام الذي يحكم هذا الموقف؟ اكتب استعلاماً يستهدف
الباب أو الفصل التأسيسي — ليس الواقعة المحددة.

مثال 1 — سؤال الزواج:
- ✅ تجريدي: "أحكام تصحيح وضع الزواج غير الموثق"
- ❌ ليس تجريدياً: "توثيق زواج السعودية من أجنبي" (هذا مباشر، لم يتجرد)

مثال 2 — سؤال الكهرباء:
- ✅ تجريدي: "حجية الاتفاق الشفهي في الإثبات"
- ✅ تجريدي: "صلاحية الاتفاق الشفهي بين المؤجر والمستأجر"
- ❌ ليس تجريدياً: "التزام المستأجر بسداد فاتورة الكهرباء"

### النوع 3: استعلام تفكيكي (مسألة فرعية مستقلة)
فكّك السؤال إلى مسائل قانونية مستقلة لا تظهر صراحةً في سؤال المستخدم لكنها ضرورية
للإجابة الشاملة.

مثال 1 — سؤال الزواج:
- ✅ تفكيكي: "إجراءات إثبات نسب المولود من أب أجنبي"
- ✅ تفكيكي: "العقوبات المترتبة على عدم الحصول على إذن الزواج من أجنبي"
- ❌ ليس تفكيكياً: "توثيق الزواج والطفل"

## شرطان لازمان
1. صِف السلوك أو الحق القانوني، لا اسم النظام — البحث دلالي بالمعنى
2. لا تذكر أسماء أنظمة أو جهات لم يذكرها المستخدم

## قاعدة الاستعلام الواحد
كل استعلام = مفهوم قانوني واحد. لا تدمج مسألتين في استعلام واحد.

## عدد الاستعلامات (حسب تعقيد السؤال)
- **سؤال بسيط** (مفهوم واحد واضح): 2-4 استعلامات
- **سؤال متوسط** (مفهومان أو إجراء + حكم): 4-7 استعلامات
- **سؤال مركّب** (عدة أطراف، شروط متداخلة، مسائل متعددة): 6-10 استعلامات

يجب أن تتضمن المخرجات النوع التجريدي (step-back) دائماً — حتى للأسئلة البسيطة.

## المخرجات
أنتج استعلامات بحث عربية. سجّل في المبررات:
- النوع: مباشر / تجريدي / تفكيكي
- ما الزاوية القانونية المستهدفة
```

**Design decision:** `sectors` field in `ExpanderOutput` is present but LLM is NOT instructed to fill it. Returns `null`. Used when sector filtering is not desired.

---

#### prompt_2 — With Sectors

Identical system prompt to prompt_1 **plus** an extra section appended at the end:

```
## تحديد القطاع القانوني

بعد إنتاج الاستعلامات، ارجع خطوة وفكّر: ما القطاعات القانونية التي تغطي جميع استعلاماتي؟

### القطاعات المتاحة (يجب استخدام الأسماء بالضبط كما هي مكتوبة):

{SECTORS_PROMPT_LIST}  ← injected at runtime from sector_vocab.py

### القاعدة:
- حدد 1-4 قطاعات تغطي **جميع** استعلاماتك — قرار واحد للدفعة كاملة، ليس لكل استعلام
- استخدم أسماء القطاعات حرفياً كما وردت في القائمة أعلاه بلا تعديل
- إذا احتجت أكثر من 4 قطاعات، اترك القطاعات فارغة (null) — السؤال واسع جداً للتصفية
- إذا لم تكن متأكداً، اترك القطاعات فارغة — الأمان أهم من الدقة
- القطاعات تُستخدم كفلتر لتضييق نطاق البحث في قاعدة البيانات

## المخرجات
أنتج استعلامات بحث عربية. سجّل في المبررات:
- النوع: مباشر / تجريدي / تفكيكي
- ما الزاوية القانونية المستهدفة

ثم حدد القطاعات القانونية (1-4 قطاعات أو فارغ).
```

**Design decision:** Sector filtering narrows the three hybrid RPCs at the DB level. LLM must use canonical names from `SECTORS_PROMPT_LIST`. If >4 sectors needed, LLM returns `null` (no filter = full-corpus search). Safety > precision: when uncertain, return null.

### User Message Format

**Round 1:**
```
تعليمات التركيز:
{focus_instruction}

سياق المستخدم:
{user_context}
```

**Round 2+ (appended after the base message):**
```
---
## تعليمات إعادة البحث (الجولة {round_count})

النتائج السابقة كانت ضعيفة في المحاور التالية:

- **السبب:** {axis.reason}
  **استعلام مقترح:** {axis.suggested_query}
[... one entry per weak_axis ...]

وجّه استعلاماتك الجديدة لتغطية هذه المحاور الضعيفة فقط.
لا تكرر استعلامات أنتجت نتائج قوية سابقاً.
```

---

## Stage 2 — SearchNode

### Node: SearchNode

**Input (from LoopState):**
- `expander_output.queries` — list of Arabic search queries
- `expander_output.rationales` — parallel list of rationales
- `expander_output.sectors` — optional sector filter (from prompt_2)
- `concurrency` — semaphore limit
- `unfold_mode` — "precise" | "full"

**Processing:**
1. Canonicalizes sectors via `sector_vocab.canonicalize_sectors(sectors)` if present
2. Batch-embeds all queries: `embed_regulation_queries_alibaba(queries)` → `list[list[float]]`
3. Creates semaphore: `asyncio.Semaphore(state.concurrency)`
4. Runs all queries concurrently via `asyncio.gather()`:
   - Each call: `search_regulations_pipeline(query, deps, filter_sectors, unfold_mode, precomputed_embedding, semaphore)`
   - Returns `(raw_markdown: str, result_count: int)`
5. Appends `SearchResult(query, raw_markdown, result_count)` to `state.all_search_results`
6. Logs to `state.search_results_log`: `{round, query, rationale, result_count, raw_markdown}`

**Always returns:** `RerankerNode()`

**Output written to LoopState:**
- `all_search_results`: accumulated across all rounds (each round appends)
- `search_results_log`: detailed per-query log entries with round number

### search_regulations_pipeline()

```python
async def search_regulations_pipeline(
    query: str,
    deps: RegSearchDeps,
    filter_sectors: list[str] | None = None,
    unfold_mode: str = "precise",
    precomputed_embedding: list[float] | None = None,
    semaphore: asyncio.Semaphore | None = None,
) -> tuple[str, int]   # (result_markdown, result_count)
```

**Step-by-step:**

**1. Embed query**
- If `precomputed_embedding` provided: use it directly
- Else: call `deps.embedding_fn(query)` → `list[float]`
- Model: Alibaba Qwen embedding API (1024-dim vectors)

**2. Run 3 parallel hybrid RPC calls**

```python
results = await asyncio.gather(
    _hybrid_rpc_search(supabase, "articles",     query, embedding, match_count=30, filter_sectors=filter_sectors),
    _hybrid_rpc_search(supabase, "sections",     query, embedding, match_count=30, filter_sectors=filter_sectors),
    _hybrid_rpc_search(supabase, "regulations",  query, embedding, match_count=30, filter_sectors=filter_sectors),
)
```

Each call:
```python
async def _hybrid_rpc_search(
    supabase, domain, query_text, embedding,
    match_count=30,
    full_text_weight=0.2, semantic_weight=0.8, rrf_k=1,
    filter_sectors=None,
) -> list[dict]
```

- RPC name: `hybrid_search_{domain}` (e.g. `hybrid_search_articles`)
- RPC params:
  ```python
  {
      "query_text": query_text,
      "query_embedding": embedding,
      "match_count": match_count,          # 30 per domain
      "full_text_weight": 0.2,
      "semantic_weight": 0.8,
      "rrf_k": 1,
      "filter_sectors": filter_sectors,    # list[str] | None (NULL = no filter)
  }
  ```
- Returns: `result.data or []` — list of dicts with at minimum: `id`, `score` (RRF)

**3. Tag and merge results**
- Articles tagged: `source_type = "article"`, `_text = content`
- Sections tagged: `source_type = "section"`, `_text = section_summary`
- Regulations tagged: `source_type = "regulation"`, `_text = regulation_summary`
- All merged into single candidates list

**4. RRF pre-filter (optional)**
- If `deps.rrf_min_score > 0.0`: drop candidates where `score < deps.rrf_min_score`
- Default: `rrf_min_score = 0.1`
- Purpose: prevents very weak candidates from consuming reranker context

**5a. Jina reranker (if `deps.use_reranker=True`)**
```python
async def _rerank(query, candidates, http_client, jina_api_key) -> list[dict]
```
- URL: `https://api.jina.ai/v1/rerank`
- Request:
  ```json
  {
      "model": "jina-reranker-v3",
      "query": "{query}",
      "documents": ["{candidate._text[:2000]}", ...],
      "top_n": len(candidates)
  }
  ```
- Response: `{"results": [{"index": int, "relevance_score": float}, ...]}`
- Adds `reranker_score` to each candidate; sorts by `reranker_score` DESC
- Post-rerank filter: keep where `reranker_score >= deps.score_threshold`

**5b. Fallback scoring (if `deps.use_reranker=False`)**
- Keep where `score >= deps.score_threshold` (default `0.005`)
- Sort by RRF score DESC

**6. Cap and unfold**
- Cap at `MAX_RESULTS = 30`
- For each candidate, call appropriate unfold function:
  - article → `unfold_article(supabase, row)` or `unfold_article_precise(supabase, row)`
  - section → `unfold_section(supabase, row)` or `unfold_section_precise(supabase, row)`
  - regulation → `unfold_regulation(supabase, row)` or `unfold_regulation_precise(supabase, row)`
  - ("precise" vs "full" controlled by `unfold_mode`)
- Unfolded results retain `_score` and `_reranker_score`

**7. Format to markdown**
- Each result → `### [N] (type): title [id:{uuid}]` header block
- Article fields shown: regulation, section, article_num, content, context, references
- Section fields shown: regulation, section_summary, child article titles/summaries
- Regulation fields shown: summary, child section titles/summaries
- Full markdown returned as `raw_markdown`

### Unfold Functions — DB Queries

All unfold functions take `(supabase: SupabaseClient, row: dict)` and return `dict`.

**Truncation constants:**
```
MAX_CONTENT_CHARS        = 3_000
MAX_CONTEXT_CHARS        = 500
MAX_REFERENCES_CHARS     = 2_000
MAX_SECTION_SUMMARY_CHARS = 500
MAX_SIBLING_CONTENT_CHARS = 2_000
MAX_SIBLINGS_TOTAL_CHARS  = 8_000
```

#### unfold_article / unfold_article_precise

Input row fields: `id, title, content, article_num, identifier_number, regulation_title, regulation_ref, section_title, chunk_ref`

```sql
-- DB Query 1
SELECT article_context, references_content, section_id, regulation_id
FROM articles WHERE id = {article_id}
```

Returns: `{source_type, id, chunk_ref, title, article_num, identifier_number, content[:3000], regulation_title, regulation_ref, section_title, article_context[:500], section_id, regulation_id, references_content[:2000]}`

#### unfold_section / unfold_section_precise

Input row fields: `id, title, section_summary, section_keyword, regulation_title, regulation_ref, chunk_ref`

```sql
-- DB Query 1
SELECT section_context, regulation_id FROM sections WHERE id = {section_id}

-- DB Query 2
SELECT id, title, content, article_num, identifier_number, article_context,
       references_content, regulation_id
FROM articles WHERE section_id = {section_id} ORDER BY article_num
```

Returns: `{source_type, id, chunk_ref, title, section_summary[:500], section_keyword, regulation_title, regulation_ref, section_context[:500], regulation_id, child_articles: list[dict]}`

#### unfold_regulation / unfold_regulation_precise

Input row fields: `id, title, type, main_category, sub_category, regulation_summary, authority_level, authority_score, regulation_ref`

```sql
-- DB Query 1
SELECT entity_id, external_references, source_url, pdf_link
FROM regulations WHERE id = {regulation_id}

-- DB Query 2 (if entity_id exists)
SELECT entity_name FROM entities WHERE id = {entity_id}

-- DB Query 3
SELECT id, title, section_summary, section_context, chunk_index
FROM sections WHERE regulation_id = {regulation_id} ORDER BY chunk_index
```

Returns: `{source_type, id, regulation_ref, title, type, main_category, sub_category, regulation_summary[:3000], authority_level, authority_score, entity_name, source_url, pdf_link, external_references (resolved JSONB: relation+regulation_id+regulation_name), child_sections: list[dict]}`

#### unfold_article_with_siblings (used by reranker)

```python
def unfold_article_with_siblings(supabase, article_id: str) -> dict
```

```sql
-- DB Query 1
SELECT id, title, content, article_num, identifier_number,
       article_context, references_content, section_id, regulation_id
FROM articles WHERE id = {article_id}

-- DB Query 2
SELECT id, title, section_summary, section_context
FROM sections WHERE id = {section_id}

-- DB Query 3 (if regulation_id)
SELECT title FROM regulations WHERE id = {regulation_id}

-- DB Query 4
SELECT id, title, content, article_num, identifier_number, references_content
FROM articles WHERE section_id = {section_id} AND id != {article_id}
ORDER BY article_num
```

Returns: `{target_article: dict, parent_section: dict, sibling_articles: list[dict], regulation_title: str}`
Siblings capped: each at `MAX_SIBLING_CONTENT_CHARS = 2000`, cumulative at `MAX_SIBLINGS_TOTAL_CHARS = 8000`.

---

## Stage 3 — RerankerNode

### Node: RerankerNode

**Input (from LoopState):**
- `search_results_log` — filtered to current round: `[sr for sr in log if sr["round"] == round_count]`
- `skip_reranker` — if True, bypass entirely
- `skip_aggregator` — if True, return End after reranker

**Processing:**
1. If `skip_reranker`: writes empty `reranker_results = []`, proceeds to AggregatorNode (or End)
2. For each search result in current round: runs `run_reranker_for_query()` concurrently via `asyncio.gather()`
3. Collects `list[RerankerQueryResult]` into `state.reranker_results`
4. Tracks usage from returned `usage_entries`

**Returns:**
- If `skip_aggregator=True`: `End(RegSearchResult(quality="pending", ...))`
- Otherwise: `AggregatorNode()`

### run_reranker_for_query()

```python
async def run_reranker_for_query(
    query: str,
    rationale: str,
    raw_markdown: str,
    supabase: SupabaseClient,
) -> tuple[RerankerQueryResult, list[dict], list[dict]]
        # (result, usage_entries, decision_log)
```

#### Agent Config

```python
Agent(
    model=get_agent_model("reg_search_reranker"),  # Alibaba Qwen 3.5 Flash — NOT overridable
    output_type=RerankerClassification,
    instructions=system_prompt,
    retries=2,
)
RERANKER_LIMITS = UsageLimits(response_tokens_limit=..., request_limit=3)
```

**Design decision:** Reranker model is hardcoded to Alibaba Qwen 3.5 Flash (not overridable via model_override). It needs fast, cost-effective structured output for 1–3 rounds per query. This model excels at Arabic classification tasks at low latency/cost.

#### Internal State

```python
active_blocks: list[dict]   # current set of result blocks being classified
all_kept: list[tuple[dict, dict]]  # (block, decision_dict) accumulator across rounds
total_dropped: int          # count of dropped/failed blocks
total_unfolds: int          # count of successful programmatic unfolds
usage_entries: list[dict]   # token usage per round
decision_log: list[dict]    # {position, rrf, action, [relevance|unfold_mode]}
```

#### Round Loop (MAX_RERANKER_ROUNDS = 3)

```
Round 1:
  active_blocks = _parse_result_blocks(raw_markdown)   # all 30 results
  trimmed_md = _assemble_markdown(active_blocks)
  user_msg = build_reranker_user_message(query, rationale, trimmed_md, round_num=1)
  classification = await agent.run(user_msg)

  For each decision:
    "keep"   → append (block, dec_dict) to all_kept
    "drop"   → total_dropped += 1
    "unfold" → append to to_unfold list

  If classification.sufficient OR to_unfold is empty:
    break  ← done

  Programmatic unfold (parallel):
    For each (block, dec_dict) in to_unfold:
      new_blocks = await _programmatic_unfold(supabase, block["id"], dec_dict["unfold_mode"])
  
  active_blocks = unfolded_blocks  ← ONLY newly unfolded blocks (not all_kept or dropped)

Round 2 (if not stopped):
  trimmed_md = _assemble_markdown(active_blocks)   # only unfolded blocks
  user_msg = build_reranker_user_message(query, rationale, trimmed_md, round_num=2)
  classification = await agent.run(user_msg)
  ... same processing ...

Round 3 (if not stopped):
  ... same ...
  always break after round 3 regardless
```

**Key context invariant:** Dropped blocks from Round 1 are **never shown again** in Round 2+. `active_blocks` for Round 2 contains ONLY the blocks produced by the programmatic unfold — not previously kept or dropped results. This prevents context growth.

**Retry logic per round:** 3 attempts with exponential backoff (`1.5 × (attempt+1)` seconds). On all attempts failed: log error, break loop.

**Programmatic unfold dispatch:**
```python
async def _programmatic_unfold(supabase, target_id, mode) -> list[dict]:
    if mode == "article_precise":
        data = unfold_article_with_siblings(supabase, target_id)
        return _article_siblings_to_blocks(data)    # target + siblings → N blocks
    elif mode == "section_detailed":
        row = {"id": target_id, ...}
        data = unfold_section(supabase, row)
        return _section_unfold_to_blocks(data)      # section + child articles → N blocks
    elif mode == "regulation_detailed":
        row = {"id": target_id, ...}
        data = unfold_regulation(supabase, row)
        return _regulation_unfold_to_blocks(data)   # child sections → N blocks
```

Each converter renumbers positions 1..N and attaches `_data` dict for later `_assemble_result()`.

#### _enrich_kept_blocks() — DB enrichment for kept results

After all rounds, round-1 kept blocks (from raw markdown, no `_data`) are enriched:

```sql
-- For articles
SELECT content, article_num, article_context, references_content,
       regulation_id, section_id
FROM articles WHERE id = {db_id}

SELECT title FROM regulations WHERE id = {regulation_id}
SELECT title FROM sections WHERE id = {section_id}
```

```sql
-- For sections
SELECT section_summary, section_context, regulation_id
FROM sections WHERE id = {db_id}

SELECT title FROM regulations WHERE id = {regulation_id}
```

#### _assemble_result() — Build RerankedResult

```python
def _assemble_result(block: dict, decision: dict) -> RerankedResult:
    data = block.get("_data", {})
    return RerankedResult(
        source_type = block["source_type"],
        title       = data.get("title", block.get("title", "")),
        content     = data.get("content", ""),
        article_num = data.get("article_num"),
        article_context    = data.get("article_context", ""),
        references_content = data.get("references_content", ""),
        regulation_title   = data.get("regulation_title", ""),
        section_title      = data.get("section_title", ""),
        section_summary    = data.get("section_summary", ""),
        relevance  = decision.get("relevance") or "medium",
        reasoning  = decision.get("reasoning", ""),
        db_id      = block.get("id", ""),   # ← critical: carries Supabase UUID
    )
```

#### Final assembly

```python
return RerankerQueryResult(
    query        = query,
    rationale    = rationale,
    sufficient   = bool(results),     # True if any kept results
    results      = results,           # list[RerankedResult]
    dropped_count = total_dropped,
    summary_note  = final_summary,
    unfold_rounds = round_num,        # how many rounds actually ran
    total_unfolds = total_unfolds,
), usage_entries, decision_log
```

### Reranker System Prompt (prompt_1, full Arabic text)

```
أنت مُصنّف نتائج البحث القانوني ضمن منصة لونا للذكاء الاصطناعي القانوني.
تعمل على استعلام فرعي واحد في كل مرة.

## السياق المعماري

أنت جزء من حلقة بحث:
1. **الموسّع**: يولّد استعلامات فرعية من السؤال الأصلي
2. **محرك البحث**: ينفذ البحث الهجين ويعيد نتائج خام
3. **أنت (المُصنّف)**: تصنّف كل نتيجة — يتم التوسع تلقائياً بناءً على قراراتك
4. **المُجمّع**: يُنتج التحليل القانوني النهائي من النتائج المُصفّاة

## مدخلاتك

- نص الاستعلام الفرعي ومبرره
- نتائج البحث بتنسيق markdown — كل نتيجة مرقمة `### [N]` وتحمل معرفاً `[id:UUID]`
- أنواع النتائج: مادة (article)، باب/فصل (section)، نظام (regulation)

## مهمتك

صنّف **كل** نتيجة إلى أحد ثلاثة قرارات:

### 1. keep (احتفظ)
النتيجة تحتوي على نص قانوني مفيد مباشرة للإجابة عن الاستعلام.
- حدد `relevance`: "high" للنصوص الصريحة المباشرة، "medium" للنصوص ذات الصلة غير المباشرة
- **يُسمح بالاحتفاظ بمواد (articles) وأبواب/فصول (sections) — حد أقصى 2 باب/فصل**

### 2. drop (احذف)
النتيجة غير ذات صلة بالاستعلام.

### 3. unfold (وسّع)
النتيجة واعدة لكن تحتاج سياقاً أعمق. حدد `unfold_mode`:
- `regulation_detailed`: نظام كامل → للحصول على أبوابه وفصوله
- `section_detailed`: باب/فصل → للحصول على جميع مواده بالتفصيل
- `article_precise`: مادة → للحصول على المادة + المواد الشقيقة + ملخص الباب

**التسلسل الهرمي:** نظام → باب/فصل → مادة
بعد التوسع ستُعرض عليك النتائج الموسّعة لإعادة التصنيف.

## قاعدة الـ 80%

بعد تصنيف جميع النتائج، قيّم:
- إذا كانت النتائج المحتفظ بها تكفي بنسبة ≥80% للإجابة: اضبط `sufficient=True`
- إذا كانت جميعها ضعيفة أو غير ذات صلة: اضبط `sufficient=False`
- إذا كان هناك توسع مطلوب: اضبط `sufficient=False` (سيتم إعادة التصنيف بعد التوسع)

## قواعد المخرجات

- `position`: رقم النتيجة المطابق لـ `[N]` في العنوان (1-based)
- `reasoning`: جملة عربية مختصرة تبرر القرار
- يجب تصنيف **كل** نتيجة — لا تتجاهل أياً منها
- `summary_note`: ملاحظة عربية مختصرة عن التقييم الجماعي

## ممنوعات

- لا تستقبل السؤال الأصلي — ركز على الاستعلام الفرعي فقط
- لا تحاول الإجابة — مهمتك التصنيف فقط
- لا تختلق أرقام مواقع غير موجودة في النتائج
```

### Reranker User Message Format

**Round 1:**
```
## الاستعلام الفرعي
{query}
**المبرر:** {rationale}

---

## نتائج البحث

{results_markdown}
```

**Round 2+:**
```
## الاستعلام الفرعي
{query}
**المبرر:** {rationale}

**الجولة {round_num}:** النتائج أدناه بعد التوسع — أعد التصنيف.

---

## نتائج البحث

{results_markdown}
```

---

## Stage 4 — AggregatorNode (reg_search's own aggregator)

**Used only when `skip_reranker=True` or `full_loop_runner` is NOT used.**
When `full_loop_runner.py` is used, this aggregator is bypassed (`skip_aggregator=True`) and the dedicated aggregator package runs instead.

### Node: AggregatorNode

**Input (from LoopState):**
- `focus_instruction`, `user_context`
- `reranker_results` — if reranker ran (and not skip_reranker)
- `all_search_results` — if reranker was skipped
- `aggregator_prompt_key`
- `round_count`, `max_rounds`

**Processing:**
1. Creates aggregator agent: `create_aggregator_agent(aggregator_prompt_key, model_override)`
2. Builds user message:
   - If reranker ran: `build_aggregator_user_message_reranked(focus_instruction, user_context, reranker_results)`
   - Else: `build_aggregator_user_message(focus_instruction, user_context, all_search_results)`
3. Runs agent, stores `AggregatorOutput` in `state.aggregator_output`

**Route logic:**
- If `not output.sufficient AND round_count < max_rounds`:
  - `state.weak_axes = output.weak_axes`
  - Returns `ExpanderNode()` — retry loop
- Else (sufficient OR max_rounds reached):
  - Returns `End(RegSearchResult(...))`

### Aggregator Agent Config

```python
Agent(
    model=get_agent_model("reg_search_aggregator"),  # default: OpenRouter or-qwen3.5-397b
    output_type=AggregatorOutput,
    instructions=system_prompt,
    retries=2,
)
AGGREGATOR_LIMITS = UsageLimits(response_tokens_limit=70_000, request_limit=3)
```

### Aggregator System Prompt (prompt_1, full Arabic text)

```
أنت مقيّم ومُجمّع نتائج البحث القانوني في الأنظمة واللوائح السعودية ضمن منصة لونا للذكاء الاصطناعي القانوني.

## دورك

تستقبل نتائج بحث من قاعدة الأنظمة واللوائح السعودية وتقوم بـ:
1. تقييم جودة النتائج بالنسبة للسؤال المطروح
2. إنتاج تحليل قانوني عربي منظم عند كفاية النتائج
3. تحديد المحاور الضعيفة عند عدم كفاية النتائج

## معايير تقييم الجودة

- **strong** (قوية): نصوص قانونية صريحة (مواد، أبواب) تجيب مباشرة عن السؤال
- **moderate** (متوسطة): نصوص ذات صلة جزئية أو غير مباشرة لكنها لا تغطي الإجابة بالكامل
- **weak** (ضعيفة): نتائج هامشية لا تعالج جوهر السؤال

## عند كفاية النتائج (sufficient=True)

أنتج تحليلاً قانونياً عربياً بتنسيق markdown يتضمن:
- عناوين واضحة تنظم الأفكار
- سلاسل استدلال نظامية تربط المواد من أنظمة مختلفة
- استشهادات دقيقة لكل مصدر مُشار إليه
- إشارات متقاطعة بين المواد ذات العلاقة

## عند عدم كفاية النتائج (sufficient=False)

حدد المحاور الضعيفة (weak_axes) مع:
- **reason**: سبب محدد لضعف هذا المحور بالعربية
- **suggested_query**: استعلام بحث عربي مقترح يستهدف هذه الفجوة بدقة

كل محور ضعيف يجب أن يستهدف فجوة واحدة محددة في النتائج الحالية.

## قواعد استخراج الاستشهادات (Citation)

لكل مصدر مُستخدم في التحليل:
- **source_type**: "article" أو "section" أو "regulation"
- **ref**: المعرف (chunk_ref) من نتائج البحث
- **title**: العنوان العربي من النتيجة
- **content_snippet**: المقتطف الأكثر صلة بالتحليل
- **regulation_title**: اسم النظام الأم
- **article_num**: رقم المادة إن وُجد
- **relevance**: لماذا يدعم هذا المصدر التحليل

## ممنوعات

- لا تختلق محتوى قانوني غير موجود في نتائج البحث
- لا تستشهد بمواد لم تَرِد في النتائج
- لا تُعد صياغة نتائج ضعيفة على أنها قوية
```

### Aggregator User Message (reranked path, used in reg_search's own aggregator)

```
السؤال / تعليمات التركيز:
{focus_instruction}

سياق المستخدم:
{user_context}

---
نتائج البحث المُصفّاة ({N} استعلام، {total} نتيجة محتفظ بها، {sufficient}/{N} استعلام كافٍ):

### استعلام 1: "{query}" [كافٍ|غير كافٍ]
**المبرر:** {rationale}
**ملاحظة:** {summary_note}
**المحذوفة:** {dropped_count} نتيجة

#### [1] مادة|باب/فصل: {title} (صلة: عالية|متوسطة)
**النظام:** {regulation_title}
**الباب/الفصل:** {section_title}
**ملخص الباب:** {section_summary}
**رقم المادة:** {article_num}
**سبب الصلة:** {reasoning}

> {content}

**السياق:** {article_context}
**إشارات مرجعية:** {references_content}

---
[... repeated for each sub-query and result ...]
```

### End-of-graph — RegSearchResult Assembly

```python
RegSearchResult(
    quality    = aggregator_output.quality,           # "strong" | "moderate" | "weak"
    summary_md = aggregator_output.synthesis_md,
    citations  = aggregator_output.citations,
    domain     = "regulations",
    queries_used     = state.all_queries_used,
    rounds_used      = state.round_count,
    expander_prompt_key  = state.expander_prompt_key,
    aggregator_prompt_key = state.aggregator_prompt_key,
)
```

---

## Stage 5 — URA Layer (full_loop_runner.py)

Used when reg_search is called via `run_full_loop()` instead of `run_reg_search()`. The graph runs with `skip_aggregator=True`; the dedicated aggregator package handles synthesis.

### FullLoopDeps

```python
@dataclass
class FullLoopDeps:
    supabase: SupabaseClient
    embedding_fn: Callable[[str], Awaitable[list[float]]]
    model_override: str | None = None
    jina_api_key: str = ""
    http_client: httpx.AsyncClient | None = None
    use_reranker: bool = False
    expander_prompt_key: str = "prompt_1"
    reg_aggregator_prompt_key: str = "prompt_1"
    concurrency: int = 10
    unfold_mode: str = "precise"
    include_compliance: bool = True
    _events: list[dict] = []             # SSE event accumulator (mutable)
```

### run_full_loop() — 6 Stages

```python
async def run_full_loop(
    query: str,
    query_id: int,
    deps: FullLoopDeps,
    prompt_key: str = "prompt_1",
) -> AggregatorOutput
```

| Stage | Function | Input | Output |
|-------|----------|-------|--------|
| 1 | `_run_reg_search_phase()` | `query`, `query_id`, `deps` | `list[RerankerQueryResult]`, `log_id`, `sectors[]` |
| 2 | `merge_partial_ura()` | reranker results, `original_query`, `query_id`, `log_id`, `sector_filter` | `PartialURA` |
| 3 | `run_compliance_from_partial_ura()` | `PartialURA`, `ComplianceSearchDeps` | `ComplianceURASlice` |
| 4 | `merge_to_ura()` | `PartialURA`, `ComplianceURASlice` | `UnifiedRetrievalArtifact` |
| 5 | `load_aggregator_input_from_ura()` | `UnifiedRetrievalArtifact`, `prompt_key` | `AggregatorInput` |
| 6 | `handle_aggregator_turn()` | `AggregatorInput`, `AggregatorDeps` | `AggregatorOutput` |

**Stage 1 detail:**
- Runs `reg_search_graph.run()` directly (not `run_reg_search()`) to access `LoopState.reranker_results`
- Passes `skip_aggregator=True` — reg_search's own AggregatorNode is bypassed
- Extracts `state.reranker_results` and `state.expander_output.sectors`
- Creates placeholder `RegSearchResult(quality="pending")` and writes logs
- On exception: logs error, continues with empty reranker_results

**Stage 3:** Only runs if `deps.include_compliance=True` AND `partial.results` is non-empty.

**Stage 4:** Merges reg + compliance results by `ref_id`. Compliance appended after reg.

**Shared deps:** Supabase + embedding_fn shared between reg_search and compliance_search.
**Separate deps:** Aggregator gets its own `AggregatorDeps` constructed via `build_aggregator_deps()`.

### URA Schema

```python
@dataclass
class URAResult:
    ref_id: str                           # "regulations:{db_uuid}" | "compliance:{id}"
    domain: Literal["regulations", "compliance"]
    source_type: str                      # article / section / regulation / gov_service / form
    title: str
    content: str
    metadata: dict = {}                   # domain-specific extra fields
    relevance: str = "medium"             # high / medium
    reasoning: str = ""
    appears_in_sub_queries: list[int] = [] # 0-based sub_query indices
    rrf_max: float = 0.0                  # highest RRF score across sub-queries
    triggered_by_ref_ids: list[str] = []  # ref_ids that triggered cross-reference unfolding
    cross_references: list[dict] = []     # structured cross-references from DB

@dataclass
class PartialURA:
    schema_version: str = "1.0"
    query_id: int = 0
    log_id: str = ""
    original_query: str = ""
    produced_at: str = ""                 # ISO datetime
    sub_queries: list[dict] = []          # [{query, rationale, sufficient, dropped_count}]
    results: list[URAResult] = []         # reg results, deduplicated
    sector_filter: list[str] = []         # from expander_output.sectors

@dataclass
class UnifiedRetrievalArtifact:
    schema_version: str = "1.0"
    query_id: int = 0
    log_id: str = ""
    original_query: str = ""
    produced_at: str = ""
    produced_by: dict = {}                # {reg_search: log_id, compliance_search: log_id}
    sub_queries: list[dict] = []
    results: list[URAResult] = []         # reg + compliance merged by ref_id
    dropped: list[dict] = []              # results excluded during merge
    sector_filter: list[str] = []
```

**ref_id format:** `"{domain}:{db_uuid}"` — namespaced for global uniqueness.
**Dedup:** Results appearing across multiple sub-queries are merged: `appears_in_sub_queries` accumulates all indices, `rrf_max` takes highest score, relevance takes max.

---

## Stage 6 — Aggregator Package (aggregator/)

The dedicated aggregator package receives `AggregatorInput` and produces `AggregatorOutput + Artifact`.

### AggregatorInput

```python
@dataclass
class AggregatorInput:
    original_query: str
    sub_queries: list[RerankerQueryResult]  # from reg_search reranker
    domain: Literal["regulations", "compliance", "cases", "multi"]
    session_id: str
    query_id: int
    log_id: str
    prompt_key: str = "prompt_1"
    enable_dcr: bool = False
    case_results: list | None = None        # reserved; always None currently
    compliance_results: list | None = None  # compliance URA slice
```

### Processing Pipeline

```
AggregatorInput
  → preprocessor.preprocess_references()
  → build user message (pre-numbered refs)
  → LLM call (single-shot or DCR chain)
  → postvalidator.validate_llm_output()
  → [fallback if hard checks fail]
  → artifact_builder.build_artifact()
  → AggregatorOutput
```

### Preprocessor (pure code)

**Identity key:** `(source_type, regulation_title_normalized, article/section_identifier_normalized)`

**Processing:**
1. Flatten all `RerankedResult` across all `sub_queries`
2. Filter: discard malformed results missing required fields
3. Dedup by identity key:
   - Merge duplicates: `relevance = max(high > medium)`, `reasoning = semicolon-join(deduplicated)`
   - Track `ref_to_sub_queries[n] = sorted(list of 0-based sub_query indices)`
4. Sort: `relevance DESC` then `first_appearance_order` (stable)
5. Assign 1-based citation numbers in **code** (n=1, 2, 3, ...)
6. Build `Reference` from each merged result

**Output:** `(list[Reference], dict[int, list[int]])` — references with numbers + coverage mapping

### Reference Model

```python
class Reference(BaseModel):
    n: int                                # 1-based, assigned by preprocessor
    source_type: str
    regulation_title: str
    article_num: str
    section_title: str
    title: str
    snippet: str                          # ≤500 chars excerpt for hover tooltips
    relevance: Literal["high", "medium"]
    ref_id: str                           # URA ref_id for side-panel lookup
    domain: Literal["regulations", "compliance"]
```

### Aggregator LLM Output

```python
class AggregatorLLMOutput(BaseModel):
    synthesis_md: str                     # Arabic markdown with inline (N) citations
    used_refs: list[int]                  # which pre-assigned N values are cited
    gaps: list[str]                       # missing aspects (non-empty if any sub-query insufficient)
    confidence: Literal["high", "medium", "low"]
```

### Four Prompt Variants

| Key | Structure | Use |
|-----|-----------|-----|
| `prompt_1` (default) | CRAC | Chat (bottom-line-up-front) |
| `prompt_2` | IRAC | Formal legal opinion |
| `prompt_3` | Draft-Critique-Rewrite (3 LLM calls) | High-stakes |
| `prompt_4` | Thematic: Summary → H3 themes → Practical | Multi-source |

**All variants share:**
- 4-step CoT inside `<thinking>` (stripped from artifact, logged)
- Inline citations `(N)` or `(N,M)` after each sentence (max 4 per parenthesis)
- JSON output: `synthesis_md`, `used_refs[]`, `gaps[]`, `confidence`
- Pre-numbered reference list in user message — LLM only references, never assigns

**Required Arabic headings per variant (postvalidator enforces):**
- CRAC (prompt_1, prompt_3): `الخلاصة` + `الأساس النظامي` + `التطبيق` + `الخلاصة النهائية`
- IRAC (prompt_2): `المسألة` + `القاعدة` + `التطبيق` + `النتيجة`
- Thematic (prompt_4): `الخلاصة` + ≥1 H3 theme heading + `خلاصة عملية`

### DCR Chain (prompt_3)

```
Stage 1 (draft_agent):    Full CRAC synthesis
Stage 2 (critique_agent): JSON: {unsupported_claims, wrong_citations, missing_caveats, verdict: accept|revise|reject}
Stage 3 (rewrite_agent):  Revised synthesis incorporating all critique points
```

If ANY stage fails → entire chain falls back to single-shot `gemini-3-flash` (atomic, not per-stage).

### Post-Validator

**Hard checks** (block output if failed):

| Check | Rule |
|-------|------|
| Citation integrity | No `(N)` without matching Reference; all `used_refs` appear inline |
| Arabic only | No Latin sentences in body (labels OK) |
| Structure | Required headings present per prompt variant |
| Gap honesty | If any sub-query insufficient → `gaps[]` must be non-empty |

**Soft checks** (notes only):

| Check | Rule |
|-------|------|
| Grounding | Reference snippet found in reranker content |
| Coverage | Fraction of sufficient sub-queries with ≥1 cited ref |
| Query anchoring | ≥2 meaningful query words in first 500 chars |

### Artifact Model

```python
class Artifact(BaseModel):
    kind: Literal["legal_synthesis"]
    title: str                            # first 80 chars of original_query
    content: str                          # synthesis_md + ## المراجع block + disclaimer
    references_json: str                  # JSON list[Reference] for interactive UI
    metadata: dict                        # {prompt_key, model, confidence, ref_count, cited_count}
```

### AggregatorOutput (final)

```python
class AggregatorOutput(BaseModel):
    synthesis_md: str
    references: list[Reference]
    confidence: Literal["high", "medium", "low"]
    gaps: list[str]
    disclaimer_ar: str
    prompt_key: str
    model_used: str
    validation: ValidationReport
    artifact: Artifact | None
```

### Models

| Role | Default | Env Override |
|------|---------|-------------|
| Primary | `qwen3.6-plus` | `LUNA_AGG_PRIMARY_MODEL` |
| Fallback | `gemini-3-flash` | `LUNA_AGG_FALLBACK_MODEL` |
| DCR fallback | `gemini-3-flash` | hardcoded |

---

## Graph Wiring Summary

```
reg_search_graph = Graph(nodes=[ExpanderNode, SearchNode, RerankerNode, AggregatorNode])

ExpanderNode  → SearchNode       (always)
SearchNode    → RerankerNode     (always)
RerankerNode  → AggregatorNode   (if not skip_aggregator)
RerankerNode  → End              (if skip_aggregator=True — used by full_loop_runner)
AggregatorNode → ExpanderNode   (if not sufficient AND round_count < max_rounds)
AggregatorNode → End             (if sufficient OR round_count >= max_rounds)
```

**Retry loop:** Max 3 rounds (round_count tracks iterations). Each retry passes `weak_axes` to ExpanderNode which builds targeted queries via `build_expander_dynamic_instructions()`.

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| 3 query types (direct, step-back, decomposed) | Targets different DB layers: article-level, section-level, full-regulation |
| sector filtering in prompt_2 | Narrows all 3 RPCs at DB level; returns null if ambiguous (safety over precision) |
| Classification-only reranker (no tool calls) | Tool-calling caused context growth 25K→130K by round 3; classification-only with code-side unfold stays bounded |
| `db_id` on every `RerankedResult` | Enables stable `ref_id` in URA without extra DB lookup after reranker |
| `active_blocks` = only unfolded blocks in round 2+ | Prevents accumulating context; previously dropped results never resurface |
| Code-side citation numbering in aggregator | Eliminates hallucinated citations; LLM only references pre-assigned numbers |
| `skip_aggregator=True` in `full_loop_runner` | Decouples reg_search from synthesis; lets dedicated aggregator handle dedup + DCR + validation |
| Compliance as dependent step (after partial URA) | Compliance queries are informed by regulation topics found; avoids cold compliance search |
| Schema versioning from day 1 | `schema_version = "1.0"` on both PartialURA and UnifiedRetrievalArtifact; breaking changes bump to 2.0 |
| Fallback chain in aggregator (primary → Gemini) | Primary validation failures don't block user; fallback preserves primary diagnostic output |
