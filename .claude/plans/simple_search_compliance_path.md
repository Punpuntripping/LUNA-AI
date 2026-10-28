# simple_search — the compliance path + the deep_search fallback policy

**Premise.** «بطلع العنوان الوطني لمؤسستي من فين؟ وكيف اقدر اثبت ان العنوان الوطني للمؤسسة رقم (رقم السجل التجاري)» is a simple question, and the router was right to send it to simple_search. The family still failed on it twice (convo `93fafee7`, triage `agents_reports/error_triage/93fafee7_20261027-1808.md`):

- The **resume** ran 10 identical `manual_search` calls, hit `tool_calls_limit`, and showed «عذراً، حدث خطأ أثناء استئناف البحث».
- The **retry** used the title as `service_id`, ran 3 cycles, and saved «وضّح لي اسم النظام أو رقم المادة أو المحكمة» after 4.5 min.

The searcher is a **name resolver**. A service question doesn't *name* a service; it describes a need. That calls for *find by meaning → pick → read*, and the family has no such path. Separately, every way simple_search can fail, except an explicit abort, ends the turn with no answer.

This plan does three things, in one plan:

1. **The compliance path:** the searcher expands → `search_topics` over services → a new compliance agent picks, opens and writes the artifact → the responder.
2. **The deep_search fallback policy:** no simple_search turn ends in an error or a degraded line when deep_search could still answer it.
3. **The resolver fixes** from the 2026-09-05 plan (`agents_reports/fix_plan_simple_search_loop_2026-09-05.md`, never shipped), for the levels that keep the resolver.

Companions: `.claude/plans/simple_search_family.md`, `simple_search_responder.md`, `simple_search_manual_search.md`.

---

## §0 — Decisions (owner, 2026-10-27/28)

| # | Decision |
|---|---|
| D1 | **Extend the existing searcher**; no new family and no new router target. For a services question, the searcher's cycle becomes: expand → search → compliance agent, instead of resolve → synthesizer. |
| D2 | **Expansion = up to 3 queries** written by the searcher and run against `search_topics` restricted to `service`. |
| D3 | After merge + dedup, the compliance agent sees **5–15 candidates** (title + summary). It picks **1–6**: **up to 2 main services + up to 4 supporting ones** (prerequisites or follow-on steps). |
| D4 | **Opening** a service = its card + «الدليل الشامل» when it has one, otherwise the service's full markdown. Opens are **capped**. A guide longer than its share is sliced from the **beginning, middle and end**, never only from the beginning. |
| D5 | **The compliance agent writes the artifact.** It is **always** an artifact (no card decision). Its length follows the question: a small question gets a small answer. |
| D6 | The **responder** then writes the bubble around the artifact, as it does today. |
| D7 | The compliance agent **never hands off to deep_search itself**. When the results aren't sufficient, it **sends the turn back to the searcher** with a reason. The searcher can then re-search, **ask the user a clarifying question** (`ask_user`), or abort to deep_search. |
| D8 | **Scope:** only direct questions about a service whose answer is **1–2 main services**. Anything regulatory (obligations, penalties, rights, what the law requires) and broad multi-service compliance go to **deep_search**. |
| D9 | **Fallback covers all of simple_search**: budget exhaustion, crashes and "no confirmed source" hand off to deep_search instead of «عذراً، حدث خطأ» / `_DEGRADED_AR`. `_ALREADY_ANSWERED_AR` (resume-skip) stays. |

---

## §1 — The flow

```
router ──simple_search──▶ searcher (cycle n of MAX_CYCLES=3, one shared pool)
                            │
       ┌────────────────────┼───────────────────────────┬──────────────┐
  regs/article/judgment/   services: emits              ask_user       aborted
  circular: resolve →      service_queries (≤3)         (pause)        → deep_search
  synthesizers (as today)       │
                                ▼
                     service_search  (code, no LLM)
                     embed ≤3 queries (one batch) → search_topics(['service'])×3
                     → merge (RRF) → dedup → top 5–15 → enrich (intro, provider, has_guide)
                                │
                                ▼
                     compliance agent  (new, Layer 3)
                     sees [1]…[15] title+summary · open_service([n…]) ≤ 6 opens, budgeted
                                │
               ┌────────────────┴────────────────┐
         sufficient                         insufficient (reason)
         artifact_md + [n] cites                 │
         primary 1–2 · supporting ≤4            ▼
               │                     rejection_notes += reason → next searcher cycle
               ▼                     (re-query · ask_user · abort → deep_search)
     _finalise: responder (bubble) + forced card publish
```

A turn may also mix cases: an attached /compliance page (case B) or a service cited in a prior WI (case C) still resolves to a **C-handle** and goes through the existing L6 synthesizer. The compliance path is only for **service_queries**.

---

## §2 — Searcher changes (`agents/simple_search/searcher.py`, `prompts.py`)

1. **`SearcherDecision.service_queries: list[str]`** (max 3, `Field(max_length=3)`). Each is a short Arabic retrieval phrase.
   - The first query states the user's need in their own words. The others cover the second need or a synonym. For 93fafee7: «إصدار العنوان الوطني للمنشأة»، «إثبات العنوان الوطني للسجل التجاري»، «طباعة وثيقة العنوان الوطني».
   - Expansion is retrieval input, so the no-paraphrase rule (§2.1) doesn't apply. The compliance agent and the responder still receive the raw message.
2. **Output validator** (`_resolve_handles`, `searcher.py:866`). When `data_type == "services"` and `service_queries` is non-empty, the decision is valid with empty `selected`/`objects`. The «لم تحدّد أي كائن» retry must not fire. Mixing `service_queries` with regulation objects is a `ModelRetry`: D8 says that turn belongs to deep_search, so tell the searcher to abort.
3. **`manual_search` loses `services`** from the searcher's `data_type` Literal (`manual_search.py:1070`). Services now go through `service_queries` only, which removes the 93fafee7 loop at the source. `manual_search_core(…, "services")` stays for the eval harness.
4. **`ask_user` on services needs a prior revert.** The prompt says it, and code enforces it: `ask_user` becomes an `@agent.tool` with ctx and raises `ModelRetry` («ابحث أولًا عبر service_queries») when the data type is services and `deps.rejection_notes` is empty. Cycle 1 never asks; only a cycle after the compliance agent's revert can.
5. **Prompt section «الخدمات الحكومية»** in `SEARCHER_SYSTEM_PROMPT`. It covers when to emit `service_queries`, the D8 boundary («هل يلزمني…»، «وش الغرامة…» → abort), and what to do after a revert:
   - The revert reason names what was missing, so re-query differently.
   - If the user's need is genuinely ambiguous, `ask_user` once.
   - If it needs 3+ main services, or the law, abort.

---

## §3 — Service search (new: `agents/simple_search/compliance.py`, pure + I/O split)

- **Embed:** one call to `embed_regulation_queries_alibaba(queries)` (batch; the same 1024-d space `search_topics` uses and that `manual_search` embeds into).
- **Search:** `search_topics(p_query_embedding, p_types=['service'], p_per_type=15)` per query, run concurrently. One `record_search_fee(agent="simple_search.search_fee.service_search", count=len(queries))` (0.1 pt/search).
- **Merge:** reciprocal-rank fusion across queries (`k=60`). A service that ranks well for two queries beats one that tops a single query. That matters here: 93fafee7's two needs share «العنوان الوطني».
- **Dedup:** by `doc_id`, then by normalized `service_name_ar` (same service under two entities).
- **Cut:** keep the top 15. Fewer than 5 survive → pass what exists. Zero → the cycle counts as *insufficient* with the reason «لم تُرجِع عبارات البحث أي خدمة» (back to the searcher, D7).
- **Enrich:** one batched `services` select (`id, service_name_ar, provider_name, intro_description, service_url`) plus one `service_guides` select (`service_id in (…)`) for `has_guide`.
- **Candidate line:** `[n] {service_name_ar} — {provider_name}: {intro_description[:280]}` plus « · يتوفر لها «الدليل الشامل»» when it has one.
- **Labels are the citation numbers** (`[1]`…`[15]`), minted in code. One vocabulary, so the C-handle namespace can't collide (the latent bug in §6).

Measured: all 4,985 services are embedded and all have `intro_description`. 825 have a guide.

---

## §4 — The compliance agent (new slot `simple_search_compliance`)

- **Layer 3 Task, `_FLASH_MEDIUM`**, the same as the synthesizer it stands in for (the family's cost premise). An A/B against `_V41_MEDIUM` is listed in §9 as a follow-up, not a precondition.
- **Static system prompt** (prompt-cache friendly). Candidates, the raw question, `recent_messages` and `detail_level` go in the user message.
- **Tool `open_service(numbers: list[int])`**, called one or more times.
  - Returns each service rendered by `render_service` under its budget share (§5).
  - **Hard cap: 6 distinct services opened per run**. Beyond that the tool returns an Arabic refusal; it doesn't raise.
  - Re-opening a service returns «سبق فتحها» without spending budget.
- **Output `ComplianceOutput`:**
  - `sufficient: bool`, plus `insufficient_reason: str` when False. The reason must name what was missing. The searcher's next cycle runs on that text, so a vague reason wastes the cycle.
  - `primary: list[int]` (1–2), `supporting: list[int]` (0–4).
  - `artifact_title: str`, `artifact_md: str` (Arabic, cites `[n]` with candidate numbers), `used_refs: list[int]`.
- **Output validator:** numbers must be candidates; `primary` must be 1–2 and **opened**; `supporting` ≤ 4; every `[n]` in the body must be in `primary ∪ supporting`. Each violation gets an Arabic `ModelRetry`.
  - If 3+ main services are needed, it returns `sufficient=false` with the reason «يحتاج أكثر من خدمتين رئيسيتين». The searcher then aborts (D7 + D8).
- **Limits:** `request_limit=6, tool_calls_limit=4`, plus the shared `OUTPUT_TOKENS_CAP`. A `UsageLimitExceeded` or a crash here is an **infra failure, not a judgment**. It goes straight to the deep_search fallback (§6) and doesn't burn searcher cycles.
- **Salvager:** opts into `make_json_salvager` like the synthesizer, with a retry message listing exactly the `ComplianceOutput` fields.
- **Prompt rules (the important ones):**
  - Answer the question asked, not the services. A short question gets a short artifact (one service: its steps and documents, roughly 150–400 words).
  - Two needs get two short sections. Supporting services get 1–2 lines each («تحتاج أولًا: …»), never their full steps.
  - Walk «الدليل الشامل» when present. The image-description lines rule is the same as the L6 prompt («never كما في الصورة»).
  - Never invent a step that isn't in what was opened. Where a slice gap sits (§5), say the full illustrated guide is on its page rather than guessing.
  - Latin digits only (house policy), the official `service_url` per service, and `[n]` citations.

**Reference numbering:** after the run, code renumbers the cited candidate numbers to a contiguous `1..k` in order of first appearance. It rewrites the markers in `artifact_md` (same regex as `_CITATION_MARKER_RE`), builds `Reference`s with `build_references` semantics (`source_type=gov_service`, `domain` from `LEVEL_DOMAIN['service']`, `service_url`), and persists only the cited ones.

---

## §5 — Open budget + beginning/middle/end slicing (`agents/simple_search/unfold.py`)

Measured on 825 guides, rendered (`guide_md` + Σ image descriptions, which is what `render_service_guide` actually emits):

| p50 | p75 | p90 | p99 | max | > 12k | > 20k |
|---|---|---|---|---|---|---|
| 9.0k | 14.0k | 24.3k | 98.9k | 136k | 270 | 116 |

- **`COMPLIANCE_OPEN_BUDGET_CHARS = 72_000`** (about 26k tokens at the module's 2.75 chars/token) per compliance run, which is the cap.
- **Share per call** = `min(36_000, remaining // len(new_numbers))`. Opening 2 services gives 36k each, which keeps about 96% of guides whole. Opening 6 at once gives 12k each.
- **The card stays whole** (the structured payload is ≤ ~8k: steps, requirements, documents, link). The **guide gets the rest of the share**.
- **New pure `slice_head_middle_tail(text, budget)`:** keep **40% from the beginning, 30% centred on the middle, 30% from the end**, each snapped to a line boundary.
  - Each gap gets the line `> ⚠ [حُذف جزء من الدليل هنا لتجاوز الحد — النص الكامل في صفحة الدليل]`.
  - A slice never splits an image-description line. Holes are resolved *before* slicing, so a description is a whole line.
- **`render_service(…, guide_budget_chars: int | None = None)`:** `None` keeps today's `_clip(MAX_SERVICE_GUIDE_CHARS)` behaviour for the L6 synthesizer. A budget switches to the slicer. The `notes` gain `guide_sliced`.

---

## §6 — The deep_search fallback policy (all of simple_search)

**Today:** the only handoff is the searcher's own `aborted` (`orchestrator.py:3135` fresh, `:1650` resume). Every other way out ends the turn with no answer.

| How simple_search ends | Today | New |
|---|---|---|
| Searcher `aborted` | → deep_search | unchanged |
| Searcher `UsageLimitExceeded` (`runner.py:1216` is uncaught) | «عذراً، حدث خطأ…» | **zero answers → deep_search**; answers in hand → deliver them |
| Searcher raises (provider exhausted after fallbacks) | «عذراً، حدث خطأ…» | same as above |
| 3 cycles, no accepted answer | `_DEGRADED_AR` | **→ deep_search** |
| Every synthesizer crashed | `_DEGRADED_AR` | **→ deep_search** |
| Compliance agent crash / limit | n/a | **→ deep_search** (infra, §4) |
| Compliance `sufficient=false` | n/a | back to the searcher (D7), never a direct handoff |
| Resume re-selects only already-delivered docs | `_ALREADY_ANSWERED_AR` | unchanged; that's a true statement, not a failure |
| `ask_user` pause | pause | unchanged |
| Anything escaping `run_simple_search` / `resume_simple_search` | orchestrator catch-all «…أثناء تنفيذ المهمة» / «…أثناء استئناف البحث» | **→ deep_search** (defence in depth) |

**Mechanism:** one exit shape. `_answer_loop` returns `_empty(aborted=True, abort_reason=…, fallback_reason="<code>")`, and the **existing** orchestrator abort branches do the handoff. No new handoff code path is needed in the runner.

- `SimpleSearchRunResult.fallback_reason: str = ""` is additive to the C1 pin. Codes: `searcher_budget` · `searcher_error` · `no_confirmed_source` · `synthesizers_failed` · `compliance_error`.
- **`runner.py:1216`:** wrap `run_tracked` in `try/except UsageLimitExceeded` and a broad `Exception` (re-raise `CancelledError`). With answers in hand, `break` into `_finalise`. Otherwise return the fallback.
- **`_finalise`:** the zero-answers + `degraded_fallback` + non-skip case returns the `no_confirmed_source` fallback instead of appending `_DEGRADED_AR`. Since `_finalise` returns a result, the cleanest seam is `_answer_loop` checking `answers` before it calls `_finalise`.
- **`orchestrator.py` simple_search branch (~3028):** wrap the `run_simple_search` call. On `Exception` (not `CancelledError`), log, then call the same `_run_deep_search(major_input, …)` handoff the abort branch uses (paused → `_record_deferred`, exactly as at `:3160`).
- **`_resume_simple_search_leg` except (`orchestrator.py:1833`):** same, using the abort branch's `MajorAgentInput` built from `user_reply` (`:1656`). The pause row is still consumed by the `finally`, and that's now harmless, because the reply is answered by deep_search with the full `recent_messages`.
- **If deep_search itself fails**, the existing generic error stands (nothing left to fall back to).
- **Telemetry:** `simple_search.turn` span `outcome="fallback_deep_search"` + `fallback_reason`, and the dispatch span gets `dispatch.fallback`. Add a Logfire saved query or alert on fallback rate. A high rate means the router or the searcher is wrong, not that the fallback is working.
- **Cost:** the user pays for the simple attempt plus deep_search (5–10 pts). That's acceptable because the policy fires only on failure, and the §2/§7 fixes stop the loops that made failures slow.

---

## §7 — Resolver fixes for regs / judgments / circulars (from the 2026-09-05 plan)

1. **`manual_search` mints real handles (B1).** In the tool body, call `manual_search_core` + `decide` directly. On `resolved`, build the `ResolvedObject` (`regs → regulation_doc/regulation_id`, `judgments → judgment/case_id + case_ref`, `circulars → circular/circular_id`), call `ctx.deps.register_candidate` (guarded with `getattr`, like `embedding_fn`), and prefix the payload: `C4 — ## <title>…`.
2. **The candidate table registers each row (B2)**, so the `C…` labels in «المرشحون:» are real, selectable handles. This kills the collision with `SearcherDeps.register_candidate` (`searcher.py:170`) and the "re-call me with the full title" round trip.
3. **`missing_id` rejects non-UUIDs** for uuid-typed id fields (`models.py:171`). A title as `service_id` (93fafee7 retry) becomes a validator `ModelRetry` instead of an unfold miss and a wasted cycle.
4. The tool docstring + `SEARCHER_SYSTEM_PROMPT` («fill the ids exactly as the tool reported them») are updated to say *put the handle in `selected`*.

The singleton short-circuit in `fetch_article` (old Fix A) is **not** in this plan. It reintroduces the state the 2026-09-02 rewrite deleted and needs its own fixture diff.

---

## §8 — Runner + responder wiring (`agents/simple_search/runner.py`)

- In `_answer_loop`, after the searcher. `output.service_queries` → `run_service_search` → `run_compliance`. With `sufficient`:
  - `answers[key] = _Answer(group=_Group(key="svc:"+primary ids, level="service", objects=[primary+supporting ResolvedObjects]), output=SynthesizerOutput(synthesis_md=artifact_md, used_refs=…), references=…, force_card=True, fanout_index=dispatched)`, then `break`.
  - Insufficient: append to `deps.rejection_notes`, along with the titles it saw, so the searcher doesn't re-query the same thing, then continue the cycle.
- **`_Answer.force_card: bool = False`**. In `_finalise` step 5, `card = True` when `force_card`, whatever the verdict (D5). Applies to both responder paths: the old `ResponderOutput.verdict_for` and the unified `SimpleSearchResponse`. Title: `artifact_title` → `_object_title(primary[0])`.
- **The digest gets `card="always"`**, so the responder frames the artifact as a card (one or two lines pointing at it plus next steps) rather than restating it. That's one line each in `SIMPLE_SEARCH_RESPONDER_PROMPT` and the unified responder's simple_search mode.
- `group_documents`' fan-out cap (3) doesn't apply. The compliance answer is **one** document (one artifact) carrying up to 6 references.
- **Stages for the `llm_calls` ledger:** `simple_search.service_search` (fees only), `simple_search.compliance` (slot `simple_search_compliance`). Slot added in `agents/utils/agent_models.py` next to the other three.

---

## §9 — Router (`agents/router/router.py`)

- **Add under "When to route to simple_search":**
  - A direct question about a government service, meaning how, where, or what's needed to obtain, register, print or verify something on a government platform, **whose answer is one or two services**: «من وين أطلع العنوان الوطني لمؤسستي؟»، «كيف أطبع وثيقة إثبات العنوان الوطني؟»، «وش المستندات المطلوبة لحجز اسم تجاري؟».
- **Add under "Never simple_search":**
  - What the law requires, penalties, rights, or whether something is mandatory: «هل يلزمني نظامًا تسجيل العنوان الوطني؟»، «وش غرامة عدم التسجيل؟».
  - Broad multi-service compliance: «وش أحتاج عشان أفتح مطعم؟».
- **Reconcile `router.py:250`** («procedures under specific regulations → deep_search»). It means **legal** procedure under a نظام, not a government e-service procedure. Say so in one sentence, or Test 1 keeps sending service questions to deep_search by its own rules (it only routed 93fafee7 correctly by reading it as "one خدمة حكومية").

---

## §10 — Tests and evals

**Unit (pure, sync, so they can join the CI whitelist):**
- RRF merge + dedup (by id and by normalized title) + the 5/15 cut.
- `slice_head_middle_tail`: proportions, line snapping, never splitting an image line, gap markers, and identity under budget.
- Budget share arithmetic.
- `ComplianceOutput` validator cases.
- Citation renumbering.
- `missing_id` UUID check.
- `manual_search` handle registration (regs/judgments/circulars).
- The `force_card` veto.

**Flow (`test_runner.py` style, FunctionModel):**
- services path happy path: one artifact published, responder called, no `ask_user`.
- Insufficient → revert → `ask_user` allowed on cycle 2 and refused on cycle 1.
- 3+ primary → revert → searcher abort.
- Every §6 row → `fallback_reason` set and `aborted=True`.
- Partial answers + searcher budget → delivered, no fallback.

**Orchestrator:**
- `run_simple_search` raises → deep_search called.
- Resume leg raises → deep_search called.
- deep_search paused during a handoff → `_record_deferred`.

**Eval `agents/simple_search/eval/compliance_path_eval.py` (live DB + models):**
- 15 gold service questions (93fafee7 first, gold primary service ids), measuring: primary hit@2, supporting relevance, `ask_user` rate, cycles, wall-clock, cost from `llm_calls`, and artifact words vs question length.
- 6 regulatory / multi-service negatives that must abort.
- Prerequisite: the local `.env` Alibaba key is stale (rotated 10-24), so refresh it for the embeddings.

**Router fixtures:** add the 6 + 6 above to the existing router A/B harness (`agents_reports/simple_search_router_ab_rerun.md`).

**Replay of 93fafee7:** one turn, no clarifying question. The artifact centres on «تسجيل العنوان الوطني للأعمال», with the proof document («إدارة العنوان الوطني» / طباعة وثيقة الإثبات) as the second main or a supporting service. All `[n]` resolve, and it takes well under a minute.

---

## §11 — Order of work and deploy

Phases are ordered so each one stands alone if the next slips.

1. **Fallback + resolver fixes** (§6, §7). Small, contained, and it removes both 93fafee7 failure modes on its own.
2. **Compliance path** (§2–§5, §8): searcher fields, `compliance.py`, unfold slicer, slot, runner + responder wiring.
3. **Router** (§9) + evals + replay. Then `/ship` (backend service only: no migration, no frontend or mobile change; the card renders as kind `agent_search` «بحث»).

**Done when:**
- 93fafee7 replays cleanly.
- Eval primary hit@2 ≥ 13/15 with 0 regulatory negatives answered on the services path.
- No replayed simple_search turn ends in «عذراً، حدث خطأ» or `_DEGRADED_AR`.
- The fallback rate is visible in Logfire.

---

## §12 — Out of scope (recorded so it isn't mistaken for forgotten)

- Case B/C services (attached /compliance page, or a service cited in a prior WI) keep the existing L6 synthesizer path and its card verdict.
- deep_search's own services executor still answers without «الدليل الشامل».
- A dedicated card subtype label (e.g. «خدمات») is a one-line frontend change, done separately if wanted.
- A per-turn wall-clock budget for simple_search before falling back.
- The `fetch_article` singleton short-circuit (old Fix A).
