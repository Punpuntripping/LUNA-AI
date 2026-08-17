# `manual_search` — the `simple_search` Searcher's Fallback Lookup — Design Plan

The deterministic-resolution escape hatch for the new lightweight **`simple_search`**
family. When the searcher cannot pin the object the user means by exact keys, it
reaches for `manual_search`: one call, one ranked candidate set, one decision —
*which document did they mean* — and an `AMBIGUOUS:` pause when the answer is not
knowable from the corpus.

> **Scope:** the manual-search tool ONLY. Not the `searcher` agent, not the
> `synthesizer`, not case B (library object attached) or case C (`WI-ref` join).
>
> **Status: DESIGN ONLY — not built.** `agents/simple_search/` does not exist yet
> (verified: no such directory on `master`).
>
> **Live-verified 2026-08-15** against project `dwgghvxogtwyaxmbgjod`. Every
> constant below carries a measured justification from that session; the numbers
> are reproducible with the queries quoted inline.
>
> **Correction to the brief.** The request asked for something "inspired by the
> deprecated rerankers of deep_search v3." A `agents/deep_search_v3/` package did
> exist and was deleted in commit `3645534` — it is recoverable from git and was
> read for this plan (§4). Its most relevant mechanism, the coarse-to-fine
> `unfold(regulation_detailed)` drill-down, is the one idea worth borrowing.
> Most of the rest is actively wrong for this task, for a reason §4.3 makes precise.

---

## 1. Why this tool exists

`simple_search`'s premise is *fetch ONE legal object and talk about it, cheaply.*
That only works if the searcher can turn a user's phrasing into an object id.
Today the repo has exactly one identity resolver — `agents/tool_repository/fetch_article.py`
— and it covers a single corner of the problem:

| the user names… | deterministic resolver today | verdict |
|---|---|---|
| a **regulation** by exact title | `resolve_regulation_id()` — ILIKE + Python rank + `_MIN_MATCH_SCORE=0.40` floor | works |
| an **article** by number in a named regulation | `_fetch_article_content()` — exact `(regulation_id, article_number)` text match | works |
| a **judgment** | — | **nothing** |
| a **service** (إجراء حكومي) | — | **nothing** |
| a **circular** (تعميم) | — | **nothing** |
| any of the above **loosely** | — | **nothing** |

So `manual_search` is not merely a fallback. It is:

- a **fallback** for `article` and `regs` (fires when `fetch_article`'s resolver
  misses, floors out, or returns `AMBIGUOUS:`), and
- the **primary and only** resolution path for `judgments`, `services`, and
  `circulars`.

That asymmetry drives the whole design and should not be flattened.

### 1.1 Exact trigger conditions from the searcher

`manual_search` fires when, and only when:

| # | Condition | Detected by |
|---|---|---|
| T1 | `data_type ∈ {judgments, services, circulars}` and the object is named in prose | no deterministic resolver exists — call it first, always |
| T2 | `fetch_article`'s resolver returned **no match** (best candidate below `_MIN_MATCH_SCORE=0.40`) | `fetch_article` returned `"المادة N غير موجودة في <title>"` where `<title>` is the **user's own** string |
| T3 | `fetch_article` returned `AMBIGUOUS:` and the searcher would rather widen than ask | tool return prefix |
| T4 | The regulation resolved but the article number is absent from `articles_v2` | `"المادة N غير موجودة في <resolved reg name>"` |
| T5 | The user names an object too loosely for a title key («نظام الشركات الجديد», «حكم المحكمة التجارية في نزاع التوريد») | searcher judgement |

It must **not** fire when the searcher has an id in hand (case B) or a
`workspace_item_references` row to join (case C). Those paths skip retrieval entirely.

---

## 2. Live-verified starting state

### 2.1 The three retrieval mechanisms that actually exist

| mechanism | what it is | where | can it resolve an *identity*? |
|---|---|---|---|
| **BM25** | `search_index` table + `bm25_terms` + `bm25_corpus_stats` + `bm25_search()` RPC | migrations `111`/`112`, fronted by `/api/v1/search` | **Yes — best tool for it.** Has an exact-title pin (§3.2) |
| **Semantic** | `search_topics` table + `search_topics()` RPC, one 1024-d Alibaba `text-embedding-v4` space | `deep_search_v4/reg_compliance_search/search.py` | No — topical recall only |
| **PostgREST ILIKE** | exact-char substring on `title`/`clean_title` etc. | `fetch_article._fetch_reg_candidates` | Partially — exact-char, brittle, but full-corpus |

`pg_trgm 1.6` **is installed**, but there is **no trigram index on any corpus
table**. All six live GIN trgm indexes are on user/chat tables
(`lawyer_cases.case_name`, `conversations.title_ar` ×2, `messages.content`,
`case_documents.document_name`, `case_memories.content_ar`). A trigram fallback
over `regulations_v2` / `cases` / `circulars` / `services` **does not exist and
would need a new migration.** Do not plan around it.

### 2.2 Corpus coverage — measured, and badly uneven

```sql
select corpus, count(*), count(slug) from public.search_index group by corpus;
```

| type filter | BM25 `search_index` | source table | **BM25 coverage** | `search_topics` docs | **semantic coverage** |
|---|---|---|---|---|---|
| `regs` | 1,686 | `regulations_v2` 3,951 | **42.7 %** | 3,951 (`regulation`) + 1,184 (`appendix`) | **100 %** |
| `circulars` | 1,843 | `circulars` 1,843 | **100 %** | 1,843 | **100 %** |
| `services` | **100** | `services` 4,746 | **2.1 %** | 4,746 | **100 %** |
| `judgments` | 10,000 | `cases` 30,531 | **32.8 %** | **0 — absent** | **0 %** |
| `article` | **0 — no `article` corpus** | `articles_v2` 51,792 | **0 %** | 0 (chunks, not articles) | **0 %** |

Live corpora in `search_index`: `blog, circular, judgment, regulation, service,
template`. There is no `article` corpus and no `case` corpus beyond `judgment`.

**Three structural facts fall straight out of that table and they are not
negotiable:**

- **D6 holds — BM25 can never find a مادة.** Only parent أنظمة are indexed.
- **Services must not lead with BM25.** 2.1 % coverage. `search_topics` has all
  4,746.
- **Judgments must not lead with semantic.** `search_topics` has zero judgments;
  the semantic path for `cases` is the separate `hybrid_search_cases()` RPC.

### 2.3 The two normalizers disagree — 91.7 % of the time

`bm25_search`'s exact-title pin compares `luna_normalize_ar(query) =
luna_normalize_ar(title)`. That function folds **hamza-carrying alef only**
(`أإآٱ → ا`) plus tatweel/harakat — deliberately, per `bm25_navigation_search.md`
§3.1 bug 2. `fetch_article._normalize_title` folds much more: `ة→ه`, `ى→ي`,
`ؤ→و`, `ئ→ي`, **and drops a leading «ال»**.

Measured over `search_index`:

| corpus | rows | titles where the two normalizers disagree | strict-normalized collisions |
|---|---|---|---|
| `regulation` | 1,686 | 1,546 (**91.7 %**) | 10 groups |
| `circular` | 1,843 | 1,552 (**84.2 %**) | 44 groups |
| `service` | 100 | 100 (**100 %**) | 0 groups |

Confirmed end-to-end: `luna_normalize_ar('النظام') <> luna_normalize_ar('نظام')`,
and the query «النظام العمل» scores **3.14** on نظام العمل — the 1000-point pin
never fires — while «نظام العمل» scores **1003.14**.

**Consequence (§3.2 step 2):** the tool must run its own strict-normalized exact
probe in Python over the candidates BM25 returns. Collision counts are low enough
that the stricter fold is safe.

---

## 3. Retrieval strategy

### 3.1 Per-type ladder

The searcher passes `data_type` with every request; it selects the ladder. Each
rung runs only if the one above returned nothing usable.

| `data_type` | ① primary | ② secondary | ③ tertiary | rationale (measured) |
|---|---|---|---|---|
| `regs` | **BM25** `['regulation']` | **ILIKE** on `regulations_v2.title` + `clean_title` (reuse `fetch_article._fetch_reg_candidates`) | `search_topics(p_types:=['regulation','appendix'])` | BM25 owns the exact-title pin, but covers only 42.7 %. ILIKE reaches all 3,951 rows; measured «نظام العمل» → 9 hits |
| `circulars` | **BM25** `['circular']` | `search_topics(p_types:=['circular'])` | — | 100 % BM25 coverage; no reason for a third rung |
| `services` | **`search_topics`** `p_types:=['service']` | **ILIKE** on `services.service_name_ar` (4,746 rows, avg 55 chars — cheap) | BM25 `['service']` | BM25 is at 2.1 %. Measured: ILIKE «سجل تجاري» → 4 hits over the full table vs 2 inside BM25 |
| `judgments` | **BM25** `['judgment']` | `hybrid_search_cases()` RPC | — | `search_topics` has no judgments at all. BM25 covers 32.8 % |
| `article` | **two-stage** (§3.3) | — | — | no article corpus anywhere |

Rungs ② and ③ are *recall* rungs. They do not carry an exact-title pin, so a hit
found there enters ranking at `medium` confidence at best.

### 3.2 Ranking and winner selection — a three-gate ladder

Run in order; the first gate that decides, decides.

#### Gate 1 — the exact-title pin (deterministic, zero LLM)

`bm25_search` adds `p_exact_bonus` (default **1000.0**) when the normalized query
equals the normalized title. Measured separation:

| query | rank-1 | score | rank-2 score | ratio |
|---|---|---|---|---|
| «نظام العمل» | نظام العمل | **1003.14** | 3.08 | **325 ×** |
| «نظام المرور» | نظام المرور | **1006.41** | 5.96 | **169 ×** |

A score ≥ `_EXACT_PIN_SCORE` is therefore equivalent to normalized-title equality,
and the separation is not a thumb on the scale — it is absolute. **Unique pin ⇒
resolved, confidence `high`, no LLM, no ask.**

If **more than one** row clears the pin (possible — 10 regulation / 44 circular /
176 judgment duplicate-normalized-title groups exist), fall to Gate 3 with those
rows only.

#### Gate 1b — the strict-normalized Python probe

Because of §2.3, re-run the exact test app-side over the returned candidates using
`fetch_article._normalize_title`. This recovers the pin on the 91.7 % of titles
where SQL normalization is too shallow — «نظام العمل السعودي» and «النظام العمل»
both resolve here rather than falling through to a coin-flip. Same outcome as
Gate 1: unique ⇒ resolved `high`.

#### Gate 2 — the deterministic floor: **title-term coverage, NOT score**

This is the single most important calibration in the plan, and it inverts the
obvious approach. Measured, with `q_terms` = normalized lexemes of the query and
`covered` = how many appear in the winner's title:

| query | winner | BM25 score | q_terms | covered | **coverage** | correct? |
|---|---|---|---|---|---|---|
| «نظام العمل» | نظام العمل | 1003.14 | 2 | 2 | **1.00** | ✅ |
| «النظام العمل» | نظام العمل | 3.14 | 2 | 2 | **1.00** | ✅ |
| «نظام العمل السعودي» | نظام العمل | 3.51 | 3 | 2 | **0.67** | ✅ |
| «اصدار سجل تجاري» | وزارة التجارة - قيد سجل تجاري… | 5.46 | 3 | 2 | **0.67** | ✅ |
| «نظام الفساد المالي والإداري» *(absent)* | نظام هيئة الرقابة ومكافحة الفساد | **14.79** | 4 | 2 | **0.50** | ❌ |
| «تعميم التسجيل العقاري» | تعميم رقم 13/ت/8409 - تنظيم | 8.37 | 3 | 1 | **0.33** | ❌ |
| «نظام حماية الفضاء السيبراني الوطني» *(absent)* | نظام الملكية الفكرية | **12.52** | 5 | 1 | **0.20** | ❌ |

Correct resolutions cluster at **0.67–1.00**; wrong ones at **0.20–0.50**.
`_MIN_TITLE_COVERAGE = 0.60` sits in the widest gap.

Note what the **score** column does: the two *wrong* answers scored 14.79 and
12.52 — **higher than every correct non-exact answer** (3.14, 3.51, 5.46, 8.37).
A `_MIN_MATCH_SCORE`-style absolute floor on the BM25 score would not merely fail,
it would invert. See trap #1.

Gate 2 decides only when coverage ≥ floor **and** the candidate is alone above it.
Otherwise → Gate 3.

#### Gate 3 — the identity decision, made by the searcher itself

Everything that survives to Gate 3 is a genuine "which one did they mean" case.
Measured, these are decided by margins deterministic scoring cannot see:

- «نظام العمل السعودي» → 3.51 vs 3.45 — a **1.7 %** gap between the right answer
  and نظام التأمين ضد التعطل عن العمل.
- «النظام العمل» → 3.14 vs 3.08 — **1.9 %**.

A language model answers both instantly. Deterministic scoring cannot.

**So does an LLM reranking pass earn its call? Yes — but not as a separate agent.**

`manual_search` should **return the ranked candidate table as its string return
value** and let the **searcher** pick. Three reasons, all load-bearing:

1. **The searcher already holds the only context that matters.** Identity
   resolution needs the user's verbatim request. Both shipped rerankers are
   *deliberately blinded* to it — v3 `reg_search/prompts.py:333`
   («لا تستقبل السؤال الأصلي — ركز على الاستعلام الفرعي فقط») and v4
   `prompts.py:430` say so explicitly. A separate reranker agent would have to
   *undo* the house reranker contract to be useful here.
2. **It costs zero extra LLM calls.** The searcher is already mid-run with a tool
   loop open. A dedicated `tier_2` rerank call would add one round-trip per
   attempt, up to 3 per turn, to a family whose entire premise is *cheap*.
3. **The searcher already owns `ask_user`.** Gate-3 candidates and the ambiguity
   escalation then live in one place instead of two.

A dedicated reranker agent stays available as a **Phase 2 option** if measurement
shows the searcher picking badly — see open question 3.

### 3.3 `article` — the coarse-to-fine two-stage (the one real v3 borrow)

No index anywhere reaches a مادة. So:

1. **Stage 1 — resolve the parent نظام** through the full `regs` ladder (§3.1)
   plus gates 1–3. This is `manual_search` calling its own regulation path.
2. **Stage 2 — fetch the article** from `articles_v2` by exact
   `(regulation_id, article_number)` text equality — literally
   `fetch_article._fetch_article_content()`, reused unchanged.

Coverage check: `articles_v2` holds 51,792 rows across **1,806** distinct
`regulation_id`s — i.e. only **45.7 %** of the 3,951 أنظمة have article rows at
all. A resolved نظام with no articles is a *normal* outcome, not an error, and
must produce a distinct message (§5) so the searcher does not retry forever.

This is v3's `unfold(target_id, mode="regulation_detailed")` — point at a whole
نظام, have code expand it one level down, then decide — rebuilt as two
deterministic steps instead of an LLM drill-down loop.

---

## 4. What to borrow from the deprecated v3 rerankers

Recovered from `3645534^`. Two design docs existed:
`reg_search/planning/reranker_plan.md` (v1, tool-calling) and
`reranker_v2_plan.md` (the version that shipped).

### 4.1 Worth borrowing

| v3 mechanism | v3 form | how it lands here |
|---|---|---|
| **Coarse-to-fine unfold** | `unfold_mode ∈ {article_precise, section_detailed, regulation_detailed}` — point at a نظام, expand to sections, re-classify | §3.3's two-stage doc→article. Deterministic, not an LLM loop |
| **Deterministic pre-filter before the LLM** | `rrf_min_score = 0.1` — "drop RRF positions below this before reranker (saves tokens)" (`reg_search/models.py:318`) | Gate 2's `_MIN_TITLE_COVERAGE = 0.60` — same discipline, correct quantity |
| **Classify EVERY candidate, with a reason** | `decisions[]` over all positions, `action: keep\|drop`, Arabic `reasoning` | Gate 3 shows the searcher **all** candidates, not a keep-subset. For a one-of-N pick, the rejected ones are the evidence |
| **A collective sufficiency gate** | `sufficient: bool` (the 80 % rule) | becomes a binary `resolved` — did we identify the object, yes or no |
| **Feedback that re-runs retrieval** | `weak_axes: list[WeakAxis]` → loop back to `ExpanderNode` (`compliance_search/loop.py:373-380`) | the shape of the synthesizer→searcher reject path; the searcher's next `manual_search` call carries the narrowed query |

### 4.2 Overkill — do not build

| v3/v4 mechanism | why it is wrong here |
|---|---|
| Multi-round loop (`MAX_RERANKER_ROUNDS = 3`) | one object, one decision. The 3-cycle budget already lives in the searcher |
| v4 `query_axes` / `satisfies_axes` | axes decompose a *question*. An identity lookup has no axes |
| `relevance: Literal["high","medium"]` per keep | we need one winner plus ordered alternates, not a graded set |
| Keep caps (`MAX_KEEP_PER_SUBQUERY = 7`, `reranker_max_keep = 8`) | the output is 1 object or an ask |
| v4's scope gate ("does this نظام's scope govern the matter?") | that is relevance. `simple_search` fetches what was *asked for* |
| Jina cross-encoder (`JINA_MODEL = "jina-reranker-v3"`) | dead in v4 — `_rerank()` is called from nowhere. Do not revive |
| v3 `_enrich_kept_blocks()` post-keep DB re-fetch | we fetch exactly one object; enrichment is the fetch |

### 4.3 The inversion that matters

Every reranker in both generations answers *"does this passage serve this
sub-query"* over a machine-generated sub-query, blinded to the user. Neither v3
nor v4 has ever had a *"which ONE document did the user mean"* rerank — the only
identity resolver in the tree is `fetch_article`, and it is deterministic with an
`ask_user` escalation. `manual_search` is therefore a **new kind of component**,
not a port. Borrow v3's *plumbing discipline*; do not borrow its *criteria*.

### 4.4 Mechanics worth keeping from v4

Not semantics — machinery. `manual_search`'s candidate rendering should reuse:

- **Stable labels `C1…Cn`, minted by code, never renumbered, never a UUID**
  (`reg_compliance_search/reranker.py:477` also normalizes `"[C7]"` / `" C7 "`).
- **`TextOutput(make_json_salvager(...))`** — `agents/utils/structured_output.py:123`
  — only if a structured output is introduced at all (it is not, under §3.2 Gate 3).
- **Forensic drop records** — log every candidate with its score, coverage, and
  the gate that eliminated it. The reranker-forensics tooling already expects this shape.
- **`_FLAT_CONTENT_SNIPPET = 1_000`** as the per-candidate body budget.

---

## 5. Tool contract

Mirrors `fetch_article` exactly: plain-string return, no `ModelRetry`, registered
via a `register_*` entry point, deps satisfied structurally.

```python
@agent.tool
async def manual_search(  # noqa: RUF029 — supabase client is sync by design
    ctx: RunContext[HasSupabase],
    query: str,                 # the object as the user named it, VERBATIM
    data_type: Literal["regs", "judgments", "services", "circulars", "article"],
    article_number: str = "",   # required iff data_type == "article"
) -> str:
```

**`query` must be passed verbatim.** Per `router_no_describe_query`, a paraphrase
destroys the exact-title pin — the whole Gate-1 win depends on the user's own
string reaching `luna_normalize_ar`.

### 5.1 Return shapes

| outcome | return |
|---|---|
| **Resolved** (Gate 1/1b, or Gate 2 unique) | `## <resolved title>\n\n<body ≤ _SNIPPET_CHARS>` — plus the `fetch_article` «(ثقة متوسطة …)» note when confidence is `medium` |
| **Candidates** (Gate 3) | `المرشحون:` + a numbered `C1…Cn` table of title · type · one-line lead. The searcher picks and re-calls, or asks |
| **Ambiguous** (≥2 candidates, none above floor, top-2 within margin) | `AMBIGUOUS: تعذّر تحديد … المرشحون المحتملون: …` — mirrors `_build_ambiguous()` verbatim, `_AMBIGUOUS_LIST_N = 3` |
| **Not found** | `لم يتم العثور على <نوع> بهذا الاسم: «<query>»` — names the **user's own** string, never a resolved one |
| **Article-specific not-found** | `المادة N غير موجودة في <reg>` — reuses `fetch_article`'s wording |
| **Regulation has no articles at all** | `<reg> غير مفهرس على مستوى المواد` — distinct, so the searcher stops retrying |
| **Error** | caught, logged `# noqa: BLE001`, degrades to the not-found string |

All user-facing strings Arabic. **Never `ModelRetry`** — the `edit_artifact` house
rule (react on the next model turn; do not burn tool-retry budget).

---

## 6. Interaction with the searcher's 3-cycle budget

The searcher and synthesizer share **3 cycles per turn**. `manual_search` spends
that pool as follows:

| rule | value | reasoning |
|---|---|---|
| `manual_search` calls per cycle | **≤ 2** | one broad attempt + one narrowed retry. A third is a different question, not a retry |
| Ladder rungs per call | **all 3**, inside one call | rungs are DB round-trips, not cycles. The searcher must not be made to walk them |
| Gate-3 candidate returns per cycle | **≤ 1** | a second candidate table in the same cycle means the query is wrong, not the ranking |
| `AMBIGUOUS:` → `ask_user` | **does not consume a cycle** | the pause is a user turn; the resumed run continues the same cycle. Mirrors the planner's deferred-tool pause |

**"Failed" means:** the call returned not-found, *or* returned `AMBIGUOUS:` and the
searcher chose not to ask, *or* returned candidates the searcher could not pick from.
Two failed calls in one cycle end the cycle. Three failed cycles end the turn with an
honest Arabic "couldn't identify it" — never a guessed object.

---

## 7. Constants — each with its measurement

House convention: a constant plus a comment recording its calibration.

| constant | value | measured justification |
|---|---|---|
| `_EXACT_PIN_SCORE` | `1000.0` | equals `bm25_search`'s `p_exact_bonus` default. Measured: «نظام العمل» → 1003.14 vs rank-2 3.08 (325 ×); «نظام المرور» → 1006.41 vs 5.96 (169 ×). Score ≥ 1000 ⟺ normalized-title equality |
| `_MIN_TITLE_COVERAGE` | `0.60` | correct resolutions measured at 1.00 / 1.00 / 0.67 / 0.67; wrong ones at 0.50 / 0.33 / 0.20. Widest gap midpoint (§3.2 Gate 2) |
| `_BM25_CANDIDATES` | `100` | `p_candidates=500` → **1188 ms** («نظام العمل», regulation) and **555 ms** (judgment). `=100` → **349 ms** / **262 ms** — 3.4 × and 2.1 × faster. Exact pin verified to hold identically at cand = 20/50/100/500 |
| `_BM25_LIMIT` | `8` | candidates shown. v4 shows `_TOP_N_PER_QUERY = 15` for *relevance*; identity needs fewer. 8 leaves headroom over `_AMBIGUOUS_LIST_N = 3` |
| `_AMBIGUITY_MARGIN` | `0.1` | inherited from `fetch_article:70` — but applied to **coverage**, never to a raw BM25 score (trap #1) |
| `_AMBIGUOUS_LIST_N` | `3` | inherited from `fetch_article:72` — keeps the ask-the-user payload identical across both tools |
| `_SNIPPET_CHARS` | `1_000` | matches v4's `_FLAT_CONTENT_SNIPPET = 1_000` |
| `_MAX_CALLS_PER_CYCLE` | `2` | §6 |
| `_ILIKE_CANDIDATE_CAP` | `50` | measured ILIKE breadth is small: «نظام العمل» → 9 rows over 3,951 regs; «سجل تجاري» → 4 over 4,746 services. 50 is ~5 × the observed worst case |

Deliberately **absent**: any absolute floor on the raw BM25 score. See trap #1.

---

## 8. Files touched

| File | Change |
|---|---|
| `agents/tool_repository/manual_search.py` *(new)* | the tool + `register_manual_search(agent)` + pure layer: `_coverage()`, `_rank_candidates()`, `resolve_object()`, `manual_search_result()` |
| `agents/tool_repository/fetch_article.py` | **no change** — import and reuse `_normalize_title`, `_fetch_reg_candidates`, `_fetch_article_content`. All three are already in `__all__` |
| `agents/simple_search/searcher/agent.py` *(new, out of scope)* | calls `register_manual_search(agent)` |
| `agents/simple_search/searcher/prompts.py` *(new, out of scope)* | the §1.1 trigger table + "pass `query` verbatim" |
| `agents/tool_repository/tests/test_manual_search.py` *(new)* | §9 |

**No migration.** Every mechanism used is live today. **No backend import** — the
corpus is public, so the tool needs only `.supabase` (`HasSupabase`, not
`HasWorkspaceContext`). Note the house rule "`agents/` never imports `backend/`"
is violated elsewhere (`agent_search/publisher.py:23-29`,
`artifact_editor/agent.py:307`, and `fetch_article`'s lazy import at `:550`);
`manual_search` needs no exemption.

---

## 9. Tests

Pure layer, no live DB — the `fetch_article` test pattern (fake supabase).

- Gate 1: score ≥ 1000, unique → resolved `high`, **zero** LLM path taken.
- Gate 1: **two** rows ≥ 1000 → falls to Gate 3, does not pick arbitrarily.
- Gate 1b: «النظام العمل» and «نظام العمل السعودي» both resolve to نظام العمل via
  the strict Python probe (SQL pin absent).
- Gate 2: coverage 0.67 unique → resolved `medium` + confidence note.
- Gate 2 **inversion guard**: a candidate with score 14.79 and coverage 0.50 must
  **not** resolve. This is the absent-law regression and the most important test.
- **Singleton guard**: exactly one candidate, coverage below floor → not-found,
  **not** accept (trap #2).
- Ladder: `services` with an empty BM25 result still resolves via `search_topics`.
- Ladder: `judgments` never calls `search_topics` (it has none).
- `article`: نظام resolves, article present → body; article absent → «المادة N
  غير موجودة»; نظام has zero `articles_v2` rows → the distinct "not indexed" string.
- `AMBIGUOUS:` payload is byte-compatible with `fetch_article._build_ambiguous`.
- Every failure path returns a plain string; assert **no `ModelRetry` is ever raised**.
- Arabic-Indic digits / Arabic ordinals in `article_number` normalize before the
  exact-text key hits the DB.

---

## 10. Traps

1. **Raw BM25 score is not a cross-query confidence measure — do NOT port
   `_MIN_MATCH_SCORE` onto it.** BM25 is `Σ_t IDF(t)·TF-sat(t)`; magnitude tracks
   *query term rarity*, not match quality. Measured: the two **wrong** absent-law
   resolutions scored **14.79** and **12.52**, higher than every **correct**
   non-exact answer (3.14, 3.51, 5.46, 8.37). An absolute floor inverts the
   decision. Coverage is the transferable quantity; score is not.
2. **A singleton result is the most dangerous case, not the safest.** Both absent
   laws returned **exactly one row**. `fetch_article`'s rule "single candidate
   above the floor → accept it" (`:406-408`) would confidently return the wrong
   law here, and `n == 1` also makes any rank-1/rank-2 ratio undefined — reading
   as infinite confidence. Invert it: `n == 1` **and** coverage below floor ⇒
   not-found.
3. **BM25 coverage is uneven and in one case catastrophic** (§2.2): service
   **2.1 %**, judgment 32.8 %, regulation 42.7 %, circular 100 %. A services query
   that leads with BM25 will look broken and the cause will not be the ranker.
4. **BM25 can never find a مادة** — D6, verified live (no `article` corpus).
5. **`search_topics` contains no judgments** — verified live. The semantic path for
   `cases` is `hybrid_search_cases()`, a different RPC with a different signature.
6. **The two Arabic normalizers disagree on 91.7 % of regulation titles** (§2.3).
   Relying on the SQL pin alone silently loses most exact matches.
7. **The exact-title pin can tie** — 10 regulation, 44 circular, 176 judgment
   duplicate-normalized-title groups. "Score ≥ 1000" is not "unique".
8. **The candidate cut runs BEFORE the exact bonus is applied.** `bm25_search`
   narrows by `ts_rank_cd` to `p_candidates`, *then* rescores. A title with poor
   `ts_rank_cd` could be cut before its 1000-point bonus is ever computed.
   Verified to hold down to `cand=20` for «نظام العمل» — but that is **one query**,
   not a guarantee. Lowering `_BM25_CANDIDATES` below 100 needs its own measurement.
9. **No trigram index exists on any corpus table** (§2.1). pg_trgm is installed;
   the indexes are all on chat tables. Anyone reaching for a fuzzy fallback will
   find `%` and `similarity()` doing sequential scans.
10. **BM25 ranks published/slugged rows only.** Every hit has a public library URL
    — convenient — but the corpus is a subset by construction, and it grows when
    the slug backfill runs, with no code change and no warning.
11. **`query` must reach the tool verbatim.** A router or searcher paraphrase
    destroys the Gate-1 pin. Same failure mode as `router_no_describe_query`.
12. **Latency is real.** 262–1188 ms per `bm25_search` call, measured. Three ladder
    rungs × 2 calls × 3 cycles is a multi-second budget in a family whose selling
    point is being fast. `_BM25_CANDIDATES = 100` is load-bearing, not a nicety.

---

## 11. Open questions

1. **Should Gate 3 be the searcher, or a dedicated reranker agent?** §3.2
   recommends the **searcher** — zero extra LLM calls, and it is the only party
   holding the user's verbatim request (both shipped rerankers are deliberately
   blinded to it). Confirm, or greenlight a `simple_search_manual_rerank` slot at
   `tier_2` as Phase 2.

2. **Services: route to `search_topics` permanently, or wait for the slug
   backfill?** BM25 has 100 of 4,746 (2.1 %). `search_topics` has all 4,746 but
   costs a 1024-d embedding call per query. Permanent semantic-first for services,
   or BM25-first once the backfill lands?

3. **Does the searcher's deps object carry an `embedding_fn`?** Every
   `search_topics` rung needs one. If `simple_search` deps stay lean
   (`.supabase` only, like `HasSupabase`), rungs ②/③ for `services` and `regs` are
   **unbuildable as specified** and the ladders collapse to BM25 + ILIKE. This
   blocks §3.1 and needs answering before build.

4. **Is "which ONE judgment did they mean" a real user intent?** Measured, the
   judgment corpus gives a flat plateau — «نزاع تجاري توريد» → 5.20 / 5.06 / 5.05 /
   5.04, no discrimination. If users never name a specific حكم, `judgments` should
   arguably return a *set* and skip identity resolution entirely.

5. **Should a successful `manual_search` auto-pin a workspace item?**
   `fetch_article` accumulates and flushes one `statute_package` per search
   (`:564`). Cheap and consistent — or is that deep_search machinery that
   `simple_search` should deliberately not inherit?

6. **Does an `AMBIGUOUS:` → `ask_user` pause consume a cycle?** §6 proposes **no**
   (it is a user turn, and the planner's deferred pause behaves this way). Confirm
   — it changes the worst-case turn cost materially.

7. **Will the `judgment` (10,000 / 30,531) and `regulation` (1,686 / 3,951) BM25
   backfills complete?** Both ladders are designed around ILIKE/semantic rungs
   compensating for the gap. If the backfill is imminent, rung ② for `regs` is
   dead weight; if it is stalled like the `service` corpus, it is essential.

8. **Should `manual_search` be registered on the synthesizer too**, so a reject
   can re-retrieve without a searcher round-trip? Cheaper, but it breaks the
   stated split where the searcher owns the whole retrieval loop.
