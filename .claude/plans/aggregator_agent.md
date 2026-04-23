# Aggregator / Synthesizer Agent — Initial Plan

**Status:** DRAFT — awaiting sign-off before build
**Location:** `agents/deep_search_v3/aggregator/` (shared across reg_search, future case_search, compliance)
**Invoked after:** reranker stage (per sub-query)
**Produces:** a frontend **artifact** — structured Arabic markdown with numbered inline citations and a reference list

---

## 1. Scope & Rationale

The first aggregator (`deep_search_v3/reg_search/aggregator_prompts.py`) was built before the reranker existed. It tried to do four things at once: rank, deduplicate, synthesize, and identify gaps. With the reranker now shipping, **ranking and deduplication are fully owned upstream**. The new aggregator narrows to three responsibilities:

| Responsibility | Source of truth |
|---|---|
| **Final synthesis** — fuse all kept reranked results into a single Arabic answer to the ORIGINAL user query | this agent |
| **Numbered citations** — inline `(1)`, `(1,3)` + end-of-doc reference list | this agent |
| **Artifact emission** — structured object for the frontend artifact panel | this agent |
| ~~Relevance ordering~~ | reranker |
| ~~Dedup / cross-round merging~~ | reranker |
| ~~Sufficiency per sub-query~~ | reranker |
| ~~Weak-axis suggestion~~ | reranker (already emits `sufficient` + `summary_note`; aggregator just surfaces gaps, doesn't re-plan) |

**Why a new agent, not a refactor:** the current aggregator mixes input schemas (raw `SearchResult` and `RerankerQueryResult`), has a monolithic prompt that re-does reranker work, and doesn't produce artifacts. Cleaner to ship a dedicated `agents/deep_search_v3/aggregator/` package that treats reranker output as the only contract.

---

## 2. Input Contract

**Primary source:** list of reranker outputs, one per sub-query, as already produced to `logs/query_N/TIMESTAMP/reranker/round_1_qX_*.md`.

```python
class AggregatorInput(BaseModel):
    original_query: str                     # from run.md "## Focus"
    sub_queries: list[RerankerQueryResult]  # one entry per q1..qN
    domain: Literal["regulations", "cases", "compliance"] = "regulations"
    session_id: str                         # for artifact + logs
```

Each `RerankerQueryResult` already carries: `query`, `sufficient`, `results[]` (each with `source_type`, `title`, `content`, `article_num`, `regulation_title`, `section_title`, `relevance`, `reasoning`), `summary_note`.

**Future expansion:** `AggregatorInput` gains optional `case_search_results` and `compliance_results` fields with the same shape. Prompt variants 4 & 5 (below) are designed with this expansion in mind.

---

## 3. Output Contract

```python
class AggregatorOutput(BaseModel):
    synthesis_md: str                 # Arabic markdown with inline (N) citations
    references: list[Reference]        # ordered — index in list = citation number
    confidence: Literal["high", "medium", "low"]
    gaps: list[str]                    # short Arabic notes on unanswered aspects
    disclaimer_ar: str                 # standard legal disclaimer appended at render time
    artifact: Artifact                 # frontend-ready artifact object

class Reference(BaseModel):
    n: int                             # 1-based citation number
    regulation_title: str              # e.g. "نظام الأحوال الشخصية"
    article_num: Optional[str]         # "51"
    section_title: Optional[str]       # for section-type refs
    snippet: str                       # short Arabic excerpt used
    source_type: Literal["article", "section", "regulation"]
```

**Citation rendering in `synthesis_md`:**
- Inline: `... يستحق الزوجة النفقة على زوجها (1,3).`
- End of doc: auto-appended reference list
  ```
  ## المراجع
  1. نظام الأحوال الشخصية — مادة 51
  2. نظام الأحوال الشخصية — مادة 65
  3. نظام الأحوال الشخصية — الباب الثاني، الفصل الأول
  ```

**Artifact:** produced via existing artifact infra (`backend/app/api/artifacts.py`). `kind: "legal_synthesis"`, `title` derived from original query, `content` = `synthesis_md` + reference list.

---

## 4. Architecture

```
reranker outputs (q1..qN)
        │
        ▼
┌───────────────────────┐
│  Pre-processor (code) │  — dedupes citations across sub-queries by
│                       │    (regulation_title, article_num) tuple,
│                       │    assigns stable N numbers BEFORE prompt
└───────┬───────────────┘
        │
        ▼
┌───────────────────────┐
│  LLM: Synthesis       │  — receives pre-numbered references,
│  (CoT, single call)   │    writes Arabic markdown citing (N)
└───────┬───────────────┘
        │
        ▼
┌───────────────────────┐
│  Post-validator (code)│  — regex-extracts all (N) in synthesis;
│                       │    asserts each N ∈ references list;
│                       │    flags unused refs; computes confidence
└───────┬───────────────┘
        │
        ▼
┌───────────────────────┐
│  Artifact builder     │
└───────────────────────┘
```

**Key design decision: citation numbering happens in code, not in the LLM.**
The Obsidian vault's research and the legal-domain literature (Stanford: 17-33% hallucination even with RAG) both converge on the same conclusion — LLMs are unreliable at assigning stable citation IDs. We pre-assign numbers deterministically, then ask the LLM to *pick which number(s) apply to each claim*. Dramatically cuts hallucinated-citation risk.

**Chain-of-thought:**
- **Visible CoT in a `<thinking>` block** inside the prompt, stripped from final artifact by the post-validator.
- Four CoT steps: (1) restate user query in own words, (2) group references by legal theme, (3) outline the answer structure, (4) draft. The user asked for "CoT on all" — interpreting this as: every prompt variant uses visible structured CoT, with the thinking block later stripped from the artifact but preserved in logs.

**Models:**
- **Primary — Qwen 3.6 Plus** (`qwen3-plus` family). Strong Arabic, cost-effective, suitable for routine synthesis at temperature 0.2.
- **Fallback — Gemini Flash 3** (`gemini-2.5-flash` or `gemini-flash-latest`, confirm exact ID at wire-time). Triggered on Qwen failure: timeout, rate-limit, malformed output, or post-validator rejection after 1 retry.
- Same prompt text for both models (no per-model variants) — keeps behavior predictable. If output quality diverges in shadow runs, split the prompt dict by model key.
- Draft-Critique-Rewrite variant (Prompt 3) uses Qwen for all 3 calls; no Gemini fallback mid-chain — if any stage fails, the whole chain falls back to single-shot Gemini.
- Exact model IDs, timeouts, and retry policy live in `deps.py` and are overridable via env for A/B runs.

---

## 5. Prompt Variants (4 prompts)

All four share: XML-structured input (instruction / references / query blocks), pre-assigned citation numbers, mandatory disclaimer, Arabic-only output, strict "no facts beyond references" grounding rule.

### Prompt 1 — **CRAC Direct** (default for chat)

**Shape:** Conclusion → Rule → Application → Conclusion
**Use case:** direct user question in the chat pane; leads with the answer, evidence follows.
**Why first:** matches lawyer intuition ("bottom-line up front"), best UX for Arabic chat.

Sections emitted:
1. `## الخلاصة` — 1-2 sentence direct answer
2. `## الأساس النظامي` — cited rules
3. `## التطبيق على السؤال` — how the rules apply
4. `## الخلاصة النهائية` — qualifications, caveats
5. auto-appended references + disclaimer

### Prompt 2 — **IRAC Formal** (audit mode)

**Shape:** Issue → Rule → Application → Conclusion
**Use case:** when the user toggles "formal legal opinion"; output suitable for embedding in a memo.
**Why second:** builds reasoning from ground up; shows the legal question being answered so a reviewing lawyer can verify scope.

### Prompt 3 — **Draft → Critique → Rewrite** (high-stakes)

**Shape:** 3 LLM calls chained.
**Use case:** compliance agent output, court-adjacent questions, or when reranker marked `sufficient=False` on ≥2 sub-queries.
**Why third:** the Obsidian vault's strongest anti-hallucination pattern. Call 1 drafts, call 2 critiques the draft against the references, call 3 rewrites keeping only supported claims. 3× latency, ~3× token cost — opt-in only.

### Prompt 4 — **Thematic Multi-Source** (future case_search + compliance)

**Shape:** organizes answer by theme (e.g., "شروط الاستحقاق" / "الإجراءات" / "الآثار") rather than by rule, and signals conflicts across sources.
**Use case:** once case_search and compliance agents feed in, a single question may pull from three different result pools with different authority levels (regulation > ministerial decision > case precedent). This prompt makes source hierarchy explicit.
**Why fourth:** designed now to keep the output schema stable when new input streams arrive.

Emits per theme:
- `### [Theme]`
- **إجماع:** points all sources agree on (w/ citations)
- **تعارض:** where sources disagree (w/ citations to each)
- **فجوات:** aspects not covered

---

## 6. Package Layout

```
agents/deep_search_v3/aggregator/
├── __init__.py           # exports build_aggregator, AggregatorInput/Output
├── agent.py              # Pydantic AI agent assembly
├── deps.py               # AggregatorDeps dataclass + builder
├── runner.py             # handle_aggregator_turn + streaming
├── preprocessor.py       # reference dedup + N-assignment (code, not LLM)
├── postvalidator.py      # citation validation, confidence scoring
├── artifact_builder.py   # builds frontend Artifact object
├── prompts.py            # 4 variants in AGGREGATOR_PROMPTS dict
├── models.py             # AggregatorInput, AggregatorOutput, Reference
├── logger.py             # writes logs/query_N/TIMESTAMP/aggregator/*.md
└── tests/
    ├── test_preprocessor.py   # dedup logic
    ├── test_postvalidator.py  # citation-number validation
    └── test_agent.py          # TestModel/FunctionModel end-to-end
```

---

## 7. Validation Corpus — Replay From Existing Logs

We already have **~30 fully-logged reg_search runs** in `deep_search_v3/reg_search/logs/query_{1..35}/TIMESTAMP/`. Each carries: original query (`run.md`), expander output, raw search hits, reranker decisions, and (for runs where old aggregator ran) the final synthesis. These are the test corpus — no need to re-run search/reranker, no API cost for retrieval stages.

### 7.1 Replay harness

`agents/deep_search_v3/aggregator/tests/replay.py`

- Scans `logs/query_*/` directories, loads each as an `AggregatorInput` by parsing:
  - `run.md` → `original_query`
  - `reranker/round_*.md` → list of `RerankerQueryResult`
  - `reranker/summary.json` → cross-check sufficiency flags
- Runs the new aggregator against each, writes output to `logs/query_N/TIMESTAMP/aggregator_v2/synthesis.md` + `references.json` + `validation.json`
- **Does not touch upstream logs** — only appends to a new `aggregator_v2/` folder per run.

### 7.2 Automated validators (pure code, no LLM)

Run on every replay, fail the test if any rule breaks:

| Check | Rule |
|---|---|
| **Citation integrity** | every `(N)` in `synthesis_md` maps to a `references[N]`; no dangling numbers |
| **Reference grounding** | every `reference.snippet` appears verbatim (or near-verbatim via normalized Arabic diacritic match) in at least one reranked result's `content` |
| **No fabricated articles** | each `reference.article_num` + `regulation_title` tuple exists in the input reranker results |
| **Coverage** | at least 1 reference drawn from ≥80% of sub-queries flagged `sufficient=True` by reranker (otherwise the synthesis is ignoring upstream signal) |
| **Query anchoring** | first 500 chars of `synthesis_md` contain ≥2 content words from the original query (lightweight relevance smoke test) |
| **Arabic-only** | body contains no Latin-script sentences (model headers/labels ok) |
| **Structure** | required sections present for the chosen prompt variant (CRAC: 4 headings; IRAC: 4 headings; Thematic: ≥1 `### ` theme block) |
| **Gap honesty** | if reranker marked any sub-query `sufficient=False`, `gaps[]` must be non-empty |

### 7.3 Held-out review set — 5 queries

Pick 5 diverse queries as the gold set for manual review (spouse support, criminal procedure, commercial, labor, admin):
`query_5`, `query_12`, `query_14`, `query_19`, `query_27` — **exact picks confirm with user before freezing**.

For each: a human-written reference answer (Arabic, ~200 words, with citations) is stored in `tests/golden/query_N.md`. The replay harness computes:
- **Citation overlap** — Jaccard of cited (regulation, article) tuples between new output and golden
- **ROUGE-L on synthesis_md** against golden (sanity check, not a gate)
- **Manual review notes** (paste verbatim into `tests/golden/query_N_review.md`)

These 5 must pass manual review before removing the `USE_NEW_AGGREGATOR` flag in step 9 of the build sequence.

### 7.4 A/B comparison vs. old aggregator

For the 15+ queries where the old aggregator already produced output (`aggregator/synthesis_md` in existing logs):
- Side-by-side report: `tests/ab_report.md`
- Columns: query, old-citation-count, new-citation-count, old-hallucinated-refs (if any), new-hallucinated-refs, reviewer verdict
- Expected win conditions for new aggregator: **0 hallucinated citations** (old has a known non-zero rate), same or better coverage, cleaner structure.

### 7.5 TestModel/FunctionModel unit tests

Standard Pydantic AI test patterns in `tests/test_agent.py`:
- `TestModel` to stub Qwen and assert the user-message builder renders references with correct N numbering
- `FunctionModel` to simulate a response that cites `(5)` when only 3 refs exist → post-validator must reject
- Fallback path: primary raises `ModelHTTPError` → Gemini Flash 3 is called with identical prompt
- Draft-Critique-Rewrite chain: mock all 3 calls, assert draft is passed into critique, critique into rewrite

### 7.6 Run modes

```
python -m agents.deep_search_v3.aggregator.tests.replay --all
python -m agents.deep_search_v3.aggregator.tests.replay --query 19
python -m agents.deep_search_v3.aggregator.tests.replay --golden-only
python -m agents.deep_search_v3.aggregator.tests.replay --prompt prompt_2 --query 19
python -m agents.deep_search_v3.aggregator.tests.replay --ab              # old vs new
```

A `/test-aggregator` slash command wraps these for quick runs from Claude Code, mirroring the existing `/test-search` command.

---

## 8. Known Trade-offs

- **Pre-assigned numbers vs LLM-assigned:** we lose the LLM's ability to cluster semantically-related citations under one number. Acceptable — a numbered list is fine; semantic clustering is a nice-to-have.
- **Single synthesis call vs map-reduce across sub-queries:** reg_search tops out around 8 sub-queries × ~10 kept results = ~80 results. Claude 4.7 / GPT-5 context easily fits. Stick with "stuff" strategy until we see real overflows.
- **Visible CoT stripped from artifact:** user sees a clean answer, logs keep reasoning for debugging. Trade-off: can't show "thinking" animation in UI without a second streaming channel.
- **No re-planning:** this agent does not suggest new sub-queries when coverage is weak — it surfaces gaps in `gaps[]` and lets the orchestrator decide. Keeps responsibilities clean.

---

## 9. Open Questions (please confirm before build)

1. **Prompt count** — 4 variants proposed (CRAC, IRAC, Draft-Critique, Thematic). Add a 5th? Drop one?
2. **Default prompt** — CRAC (Prompt 1) as default?
3. **Artifact scope** — does the artifact carry only `synthesis_md`, or also the raw references JSON so the frontend can render an interactive citation panel (hover → see snippet)?
4. **Disclaimer** — use existing Luna legal disclaimer text or draft a new one for synthesized answers?
5. **Case_search + compliance integration** — build Prompt 4 now as the default once those agents ship, or leave it behind a feature flag until they're real?
6. **Draft-Critique-Rewrite trigger** — automatic (when reranker flags ≥2 sub-queries as insufficient) or user-toggled only?
7. **Exact model IDs** — confirm Qwen 3.6 Plus model string (`qwen3-plus`? `qwen3-max`? via DashScope or OpenRouter?) and Gemini Flash 3 string (`gemini-2.5-flash`? `gemini-flash-latest`?).
8. **Golden query picks** — 5 suggested (`query_5, 12, 14, 19, 27`). Swap any? Happy with coverage (family, criminal, commercial, labor, admin)?

---

## 10. Build Sequencing

Once above is signed off:

1. Scaffold package + models (`models.py`, `deps.py`) — small, no LLM
2. Pre-processor + tests (pure code — dedup/N-assignment)
3. Post-validator + tests (pure code — regex + reference check)
4. Prompt 1 (CRAC) + `agent.py` + `runner.py` — single variant end-to-end
5. Wire into orchestrator behind `USE_NEW_AGGREGATOR=True` flag
6. Shadow-run against 5 logged queries from `logs/query_{5,12,14,19,...}/`, diff outputs, adjust prompt
7. Add Prompts 2, 3, 4
8. Artifact builder + frontend integration
9. Remove `USE_NEW_AGGREGATOR` flag, delete old `deep_search_v3/reg_search/aggregator_prompts.py`

Estimated: 3 agents invoked sequentially — `@pydantic-ai-prompt-engineer` (4 prompts), `@pydantic-ai-dependency-manager` (package assembly), `@pydantic-ai-validator` (tests).
