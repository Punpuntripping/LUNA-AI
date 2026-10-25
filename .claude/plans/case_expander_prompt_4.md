# Case expander — prompt_4 (rulings, not statutes)

**Status:** BUILT 2026-10-25, uncommitted — 261 tests green (case_search + shared + deep_search_v4/tests).
A/B replay (§5) running; NOT deployed. Worked examples from 3 real prod questions in
`agents_reports/case_expander_assumptions/` (calibration for the owner, NOT prompt material — see D6).

**Scope:** `agents/deep_search_v4/case_search/` expander prompt + one schema description, and the
≥2-channel rescue in `agents/deep_search_v4/shared/query_cap.py`. No change to search, reranker,
aggregator, planner, or the reg expander.

---

## 1. Problem

Two owner-reported defects, both caused by the live system prompt (`EXPANDER_PROMPTS["prompt_3"]`
in `case_search/prompts.py`), not by the model.

### 1.1 It writes regulation queries into a rulings index

Owner example: «نظام المرافعات الشرعية المواد المتعلقة بميعاد الاستئناف وإجراءات رفع صحيفة الاستئناف».

`prompt_3` still describes the pre-2026-07-30 `case_sections` engine: each ruling "split into three
vector spaces" of raw text, with `basis` = «اسانيد + the laws used» and the drafting rule *"a query that
names a law, an article, or a procedural rule"*. Its own example «مواد نظام العمل المتعلقة بإنهاء العقد
قبل انتهاء مدته» is the exact template the owner's query copies.

The live engine (`search_case_topics` RPC, since `case_topics_loop.md`) matches against short atomic
**topics** distilled from each ruling. Measured 2026-10-25 on prod `case_topics` (293,873 rows):

| kind | rows | what a topic is | names a law | cites an article |
|---|---|---|---|---|
| `basis` | 116,463 | **a ground a party raised** — «الدفع بعدم قبول استئناف … لعدم تقديم مذكرة استئناف مسببة خلال المدة النظامية», «الاستناد إلى…», «طلب…», «المطالبة بـ…» + attrs `الطرف` / `موقف المحكمة` | 10.8% | 7.4% |
| `principle` | 64,554 | a principle the court laid down + `النوع` (موضوعي/شكلي) | 40.8% | 35.4% |
| `fact` | 112,856 | a short fact statement | 8.4% | 2.3% |

What the expander actually emitted (last 55 prod runs, 2026-10-12 → 10-24, 389 queries):

| channel | queries | names a law | statute-lookup shape («المواد المتعلقة», «مواد نظام», «تطبيق المادة») |
|---|---|---|---|
| basis | 133 | **86%** | 22% |
| principle | 157 | 5% | 0% |
| facts | 99 | 3% | 0% |

The case reranker (`RERANKER_PROMPTS["prompt_2"]`) was already updated: it defines `basis` as
"the grounds the parties relied on". **Only the expander still carries the old definition.**

### 1.2 It invents laws and article numbers

`prompt_3` has no no-invention rule (the reg expander has one), and its principle section actively
routes citations to basis («تطبيق المادة 99 من نظام المعاملات المدنية» → "goes to basis").

Smoking gun — trace `01a1552ebc14db90eb2c7516651342ee` (2026-10-19): input «ما هي شروط وقف السير في
دعوى عقارية لحين الفصل في دعوى أخرى…» names no law. The thinking reads *"basis: نظام المرافعات
الشرعية المادة 117 … Let's be careful…"* — an article number recalled from memory — and emits
«تطبيق المادة 117 من نظام المرافعات الشرعية على تعليق الدعوى لارتباطها».

Heuristic count: **61 of 116** law mentions in basis queries do not appear in the expander input
(first-token match, input truncated at 2,500 chars — approximate). The planner is not the source:
its restatement already carries a zero-bias rule (`planner/prompts.py:101`).

Wrong laws also mis-steer retrieval: principle topics cite «المحاكم التجارية» 7,656× vs «المرافعات
الشرعية» 2,844× — a commercial dispute tagged with the wrong procedural law is pulled toward the wrong
rulings.

### 1.3 One issue, three channels

All 55 runs used all three channels (the prompt mandates ≥2 and its distribution table prescribes
"principle + facts + basis"). Mean ~7 queries/run. The same issue is routinely re-dressed per channel —
e.g. principle «تعويض العامل عن الفصل التعسفي بأجر المدة الباقية…» / facts «عامل بعقد محدد المدة
أُنهي عقده… فطالب بالتعويض عن المدة الباقية» / basis «تطبيق المادة 77 من نظام العمل…». A ruling usually
carries both a principle and a basis topic on the same issue, so one query in the right channel already
reaches it; the copies cost a search + a reranker call each for mostly the same rulings.

### 1.4 It does not reason before writing

Unlike the reg expander (Phase 1: assumptions → intent → framing), the case expander goes straight to
queries. On thin questions it fills the gap with law names from memory instead of with the points a
court would actually decide.

---

## 2. Decisions

| # | Decision |
|---|---|
| D1 | New key **`prompt_4`** (sectioned), added to `SECTIONED_EXPANDER_PROMPTS`; `DEFAULT_EXPANDER_PROMPT` → `prompt_4`. `prompt_3` stays byte-identical as the A/B baseline and the one-line rollback. (Not an in-place rewrite — the A/B needs both.) |
| D2 | The prompt describes the **real corpus**: rulings distilled into short topics of three kinds; a hit returns a **ruling**, never an article; statute text is the regulation search's job. |
| D3 | **`basis` = a ground a party raised** (دفع / استناد / طلب / تمسك / مطالبة), matching the reranker's definition and the corpus. Every «مواد نظام X…» example is removed. |
| D4 | **No invention.** No law name, article number, authority or platform that is not in the input (focus instruction, user context, context blocks); never an article number recalled from memory. If the input names one, at most **one** query may carry it, copied exactly — principle topics cite articles 35% of the time, so that query can reach rulings that applied it. Assumptions never introduce a law (D5). |
| D5 | **Reason first** (in thinking, never in a query): (a) what the input states — facts from context blocks before any assumption; (b) reasonable defaults for what is missing — the user's side, the stage, the kind of dispute; (c) the **decision points** a court would rule on. Assumptions steer, they are not stated as facts. When two readings would lead to different rulings, each reading gets its own query rather than a silent pick. Never stack assumed specifics (lesson from `prompt_1`: stacked user facts → query_28 q4, 0/10 kept). The planner already asks the user when the question is truly ambiguous, so these are small defaults, not reinterpretation. |
| D6 | **Diversity = distinct decision points or distinct readings — never the same point in another channel.** One issue → one query → one channel, chosen by where the issue lives in a ruling (the court's principle / a party's ground / a fact pattern). No channel quota, no distribution table. At most one facts query for the dispute as a whole, unless it holds genuinely separate fact patterns. |
| D7 | **Examples are minimal.** Only short single-query ❌→✅ contrast pairs, drawn from different domains, placed inside the rule they illustrate. No multi-query worked example (owner, 2026-10-25: a worked example biases the model toward producing near-identical queries). The 3 agent reports are for owner calibration only. |
| D8 | Remove the ≥2-channel rescue from `clamp_queries` (owner OK 2026-10-25). The wire floor `cap ≥ 2` stays — it is shared with the reg path and out of scope. |
| D9 | `ExpanderOutputV2.queries` field description ("usually 3-5, one per channel per angle") is sent to the model in the output schema; rewrite it to match D6. |
| D10 | **Keep the user's own legal term and the courts' vocabulary — never a textbook or foreign synonym.** Verified on prod `case_topics` 2026-10-25: «وقف السير» 86 rows vs «تعليق الدعوى» 4 (while «تعليق المطالبات» = 146 bankruptcy rows); «القطعية» 337 vs «حكم بات» 9; «تخارج» 600 rows of which ~21 concern estates (the rest: partners exiting companies). A synonym swap drifts the query into another domain without inventing anything, so D4 alone does not catch it. |

### Refinements to D5 from the worked examples (`agents_reports/case_expander_assumptions/`)

- **Split only on a fork that changes the rulings.** Inheritance waiver: *with vs without consideration* decides the outcome (هبة لا تلزم إلا بالقبض vs صلح لازم) → two queries. Suspension: *user requests vs resists* the stay needs the same rulings → no split (two queries differing by a negation fetch the same set).
- **Don't spend a query on a reading the corpus can't answer** (waiver during the testator's lifetime: no rulings) — reason about it, then drop it.
- **On a thin question, build from who must prove what and from the other side's likely defence**, not from a remembered law. Supplier breach: «إثبات المشتري دفع الثمن وعجز المورد عن إثبات التوريد…», «الدفع بأن إخلال المورد سببه عدم سداد المشتري…».
- **A facts query must say who claims against whom** when the corpus is lopsided: ~5,503 fact topics have the supplier suing vs ~53 with the buyer suing (agent keyword probe).
- Evidence the invented article mis-steers: «المادة 117» appears in 10 `case_topics` rows — sick-leave pay (نظام العمل), signature denial (المحاكم التجارية), expert reports (الإثبات), officers' discipline — none about staying a case.

Reasoning effort stays `_FLASH_MEDIUM` on the `case_search_expander` slot — the reg expander runs the
same reason-first method at medium.

---

## 3. prompt_4 — section outline

English instructions, Arabic queries (house language policy). Target length ≈ prompt_3.

1. **Identity** — searches Saudi court **rulings** to find how courts decided disputes like this one.
   Not a statute search; the regulations search runs separately.
2. **Output language** — Arabic only (unchanged).
3. **How the engine works** — each ruling distilled into short topics; a query is matched by meaning
   against one kind; the hit is the ruling. Topic shapes, one line each, with a real-corpus example:
   - `principle` — a principle the court laid down («الاستئناف المقدم خلال الأجل مقبول شكلاً…» shape).
   - `basis` — a ground a party raised («الدفع بـ…», «الاستناد إلى…», «طلب…»).
   - `facts` — a short fact statement.
   Statement: a query shaped like a statute lookup («المواد المتعلقة بـ…», «أحكام … في نظام …») matches
   almost nothing — basis topics name a law only ~1 time in 10.
4. **Method — reason first** (D5): stated vs missing → reasonable defaults → decision points.
5. **One issue, one query, one channel** (D6) + how to pick the channel.
6. **Per-channel drafting style** — length bands kept from prompt_3 (principle 5-12 words, facts 8-18,
   basis 6-14); principle forms («من المقرر أن…», «عدم قبول…», «أثر…») kept; basis forms new.
7. **No invention** (D4) — one ❌→✅ pair (statute lookup → ruling query, single query).
8. **Rare-details pruning** — kept from prompt_3 (amounts, names, cities, dialect).
9. **Number of queries** — smallest set that covers the decision points: simple 2-3, medium 3-5,
   complex 5-8, ceiling 10.
10. **Output** — JSON shape unchanged (`text` / `channel` / `rationale`); rationale names the decision
    point (and the reading, when D5 split one).
11. **Self-check before sending** — no law/article/authority absent from the input; no two queries on
    the same decision point; every basis query is a party's ground, not a statute lookup; the user's
    own legal term kept, no textbook/foreign synonym (D10).
12. **Context blocks** — paragraph kept from prompt_3, plus: a `statute_articles` block means the user
    cited that article (D4 allows one query to carry it).

---

## 4. File manifest

| File | Change |
|---|---|
| `agents/deep_search_v4/case_search/prompts.py` | Add `EXPANDER_PROMPTS["prompt_4"]` + its header comment; `SECTIONED_EXPANDER_PROMPTS = {"prompt_3", "prompt_4"}`; `DEFAULT_EXPANDER_PROMPT = "prompt_4"`; module docstring lists prompt_3 (superseded, A/B baseline) and prompt_4; prompt_3 header comment marked superseded (text untouched); `build_expander_user_message` docstring drops the «at least two channels» paragraph. |
| `agents/deep_search_v4/case_search/models.py` | `ExpanderOutputV2.queries` description (D9); `LoopState.expander_query_cap` comment drops the channel-aware clamp sentence. |
| `agents/deep_search_v4/orchestrator.py:103` | ⚠ **`FullLoopDeps.case_expander_prompt_key = "prompt_4"`** — production reads THIS pin, not `DEFAULT_EXPANDER_PROMPT`; flipping only the module default would ship nothing. A test now asserts the pin. |
| `agents/deep_search_v4/case_search/loop.py` | Sectioned clamp call (~L606-623): drop `channel_of=…` and the rescue comment. |
| `agents/deep_search_v4/case_search/tests/test_loop.py` | Sectioned clamp tests now assert a plain head-slice; prompt key `prompt_4`. |
| `agents/deep_search_v4/case_search/search.py` | Docstring reference to the pinned key. |
| docs | `planning/MODE_PROFILES.md`, `.claude/plans/blog_post_api_protocol.md`, `.claude/plans/expander_query_cap.md` — notes that the ≥2-channel rescue is gone, floor unchanged. |
| `agents/deep_search_v4/shared/query_cap.py` | Remove the `channel_of` parameter, the rescue block (L114-131) and its docstring paragraphs (module docstring L36-38, function docstring L98-105). |
| `agents/deep_search_v4/shared/tests/test_query_cap.py` | Delete the "sectioned ≥2-channel rescue" tests (L130-~232); flat-path tests unchanged. |
| `agents/deep_search_v4/case_search/tests/test_expander.py` | Default is `prompt_4`; `prompt_4` is sectioned → `ExpanderOutputV2`; guard that prompt_4 no longer carries the old basis drafting rule ("names a law, an article"). |
| `scripts/case_expander_ab.py` | New — A/B replay (§5). |

Prompt-cache note: the system prompt changes once (new cache key once); the user message builder is
untouched, so `cap=None` output stays byte-identical.

---

## 5. Validation — A/B replay before flipping the default

`scripts/case_expander_ab.py` — replays real prod expander inputs through `prompt_3` and `prompt_4`.

- **Inputs:** the 55 prod expander user messages (2026-10-12 → 10-24), pulled from Logfire
  (`pydantic_ai.all_messages[0]`), stored OUTSIDE git (they carry client names) — scratchpad or
  `agents_reports/` (gitignored).
- **Phase A — expander + search (cheap):** per run and prompt — query count, channels used, % queries
  naming a law/article absent from the input, same-issue pairs across channels (embedding cosine
  ≥ 0.85 between a run's own queries, same embedding call as `search_case_section`), distinct rulings
  retrieved via `search_case_topics_rpc`, overlap of the two ruling sets.
- **Phase B — reranker keeps (subset of 15 runs):** rulings kept per run and kept-set overlap — the
  number that decides whether fewer queries lost real rulings or only duplicates.
- **Pass bar:** invented law/article mentions → ~0; same-issue cross-channel pairs sharply down;
  Phase B kept rulings not lower than prompt_3 on the median run.
- Keys: the expander slot head is Baseten (`project_baseten_deepseek_flash`); local `.env` is stale for
  Alibaba (rotated 2026-10-24) — confirm the chain resolves before the run.

### 5.1 Results — run 1 (2026-10-25, `agents_reports/case_expander_ab/full/report.md`)

| metric (55 runs) | prompt_3 | prompt_4 |
|---|---|---|
| queries/run | 7.25 | 7.53 |
| invented law/article (heuristic) | 56 | 3 — all 3 false positives («المادة المخدرة») |
| statute-lookup shaped | 34 | 1 — «وفق نظام العمل», which the input named |
| channel mix p/b/f | 161/139/99 | 184/175/55 |
| runs using all 3 channels | 55 | 55 |
| retrieval redundancy (hits ÷ distinct rulings) | 1.15 | 1.16 |
| Phase B (15 runs): distinct rulings kept/run | 27.0 | 27.6 — sets overlap only ~5–30% |

Read: defects 1.1 and 1.2 are fixed. **Count did not drop** (thin no-context questions went UP,
6.35 → 7.0): the reason-first step finds more decision points and the "smallest set" guidance does not
bite; some points are still written twice in opposite polarity (جواز… / عدم قبول…). **§1.3's premise
was wrong at the retrieval level** — cross-channel restatements retrieve mostly DIFFERENT rulings
(redundancy ≈1.15 on both prompts), so the case for D6 is cost and focus, not duplicate rulings.
Kept volume is equal but the sets differ, so counts cannot rank them — quality needs a judge.

Embedding trap found on the way: DashScope text-embedding-v4 rejects batches > 10, while
`agents/utils/embeddings.py:MAX_EMBED_BATCH = 25` — an 11+ query expander output would fail the whole
search step in prod (not hit in the 55 runs; owner to decide).

⚠ Fidelity bug in run 1's Phase B: the script reranked each query's WHOLE grouped list, while the loop
reranks only the head (`SectionedRerankerNode._TOP_N_PER_QUERY = 15`). Same for both arms, but the
absolute "kept/run" numbers are not prod numbers. Fixed in `scripts/case_expander_ab.py` (2026-10-25).

### 5.2 Probe — every query searched in all 3 kinds (`agents_reports/case_expander_ab/all_channels/`)

Owner question 2026-10-25: search each query across principle + fact + basis instead of its one
channel. Same 110 runs' queries, re-embedded, top-15 cases per kind; no LLM calls.

| metric | prompt_3 (399 q) | prompt_4 (414 q) |
|---|---|---|
| another kind's top-15 already in the own-channel top-15 | 11% | 11% |
| queries where another kind has a better top-1 than the chosen channel | 50% | 35% |
| top-1 cosine median principle / fact / basis | .629 / .600 / .613 | .623 / .619 / .644 |
| RRF-fused top-15 (3 kinds) that is still own-channel | 43% | 43% |
| distinct rulings shown to the reranker per run — today → fused top-15 | 99.5 → 95.0, 49 new | 102.5 → 95.5, 48 new |

Read: the kinds are near-disjoint neighbourhoods and similarly calibrated, so the channel the expander
picks decides which half of the matching rulings the reranker ever sees — and for 1 query in 3 it
picks the weaker one. Fusing keeps the reranker bucket at 15 (reranker cost unchanged) and replaces
about half of it. Not yet known: whether the swapped-in rulings are better — needs the reranker +
a blind judge (today vs fused, same queries).

## 6. Rollout / rollback

Build → unit tests → A/B (§5) → owner review of the A/B report → flip default (already in the build,
ships with the deploy) → `/ship`. Rollback = `case_expander_prompt_key = "prompt_3"` in
`orchestrator.py:103` (+ `DEFAULT_EXPANDER_PROMPT` for CLI parity); the clamp change is independent
and safe either way.

## 7. Out of scope

- Planner focus instructions, reranker, aggregator, reg expander.
- The `cap ≥ 2` wire floor.
- Legacy `prompt_1` / `prompt_2` (non-sectioned hybrid path).
