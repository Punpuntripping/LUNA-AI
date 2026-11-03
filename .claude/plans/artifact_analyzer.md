# Plan — Artifact Analyzer: answer follow-ups from prior reports before searching

> **Status:** design agreed with the owner 2026-11-02 (`/reflect`). Nothing built.
> **Code base:** `origin/master` @ `68ba3d7`. Every `file:line` below is master at that commit.
> **Do not build on `feat/mobile-phase4b-iap`.** It is 34 commits behind master and holds uncommitted
> changes in files this plan edits (`agents/orchestrator.py`, `agents/deep_search_v4/orchestrator.py`,
> `agents/deep_search_v4/planner/*`, `agents/deep_search_v4/aggregator/{prompts,runner}.py`,
> `shared/config.py`). Work in a fresh worktree off `origin/master`, and diff those uncommitted changes
> before starting in case any belong in this work.
> **No migration.** `workspace_items.metadata`, `messages.metadata` and `paused_runs.deferred_payload`
> are all existing jsonb.

---

## 1. Why

Convo `9c647ef6` (user `0d0a97d4`, 2026-11-02): four full deep searches in 30 minutes on one salary
garnishment matter, ~$0.07 and ~4 minutes each.

| Turn | User said | Needed a search? |
|---|---|---|
| 3 | Answered the clarifying question | Yes, first search |
| 4 | The order is for a debt | **No.** WI-2 already held the non-نفقة rule, and the planner's own thinking said "No new articles needed" |
| 5 | Date 2026/1/27 | Yes. It changed the governing law and the remedy route |
| 6 | Salary arrives monthly | **No.** WI-4 held the branch. The re-search also lost the date and argued from the wrong law, and turn 6's search never retrieved the 24-hour circular that WI-2 and WI-4 both cited |

Root causes:
- `router.py:420` orders a dispatch for any answer to a follow-up question.
- `target_wi` means nothing to deep search; only `writer_planner` reads it.
- `PlannerDecision.mode` (`planner/models.py:60`) has no no-search outcome.
- The only "already covered" check is the responder's publish gate, after the money is spent.

Scale: 29 of 142 deep-search dispatches in the last 14 days carried a `target_wi`. That is about $2, so
the real cost is the waits, the user's points, and inconsistent legal answers.

The old `item_analyzer` is not reusable: it judged relevance for the writer and its prompt orders
paraphrase (`writer/prompts/refs_kinds.py:130`). The shape we want is the never-built `analyze.search`
from `agents/plans/INITIAL_item_analyzer_family.archived.md` (lines 324-371, 967-990): `coverage`
full/partial/none plus `verbatim` chunks.

---

## 2. Decisions (locked by the owner)

| # | Decision |
|---|---|
| D1 | New `artifact_analyzer` with three verdicts: **sufficient**, **partial**, **insufficient** |
| D2 | Linked to the **router**: the router decides when it runs |
| D3 | Model: **DeepSeek V4 Flash**, not V4.1. Silent; it never talks to the user |
| D4 | Conversation: the current message plus the **last 6** messages |
| D5 | Reports: **3 or fewer → all**. **More than 3 → the ones the router picks** |
| D6 | Sees each report's **full text and its gaps** |
| D7 | Sources: **kept by the reranker, cited or not, each with its summary** in reranker format. Reranker-dropped sources are excluded |
| D8 | Tool opens **any list of whole chunks, no cap** |
| D9 | **Verbatim quotes only**, from a report or an opened chunk. Never rephrased |
| D10 | **Never assumes** a new search would change anything about a gap |
| D11 | A gap the last search already reported is **at most partial** |
| D12 | **Insufficient: the analyzer starts deep search directly**, passing the missing parts, its restatement and the covered quotes to the planner |
| D13 | **The responder writes** sufficient and partial replies (owner confirmed the recommendation) |
| D14 | Planner must search if the analyzer said insufficient or the user insists. It may revert **only when the analyzer did not check**. A revert goes through the analyzer |
| D15 | Replies name the source. **No reply anywhere exposes internal processes**: «وجدنا مصدرًا متعلقًا بسؤالك», never «الجامع لم يستخدم هذا المصدر» |
| D16 | The task input is **built by code** from the raw message and conversation. The router writes no description of the question (the 2026-05-29 rule). The analyzer writes its own restatement |
| D17 | An explicit request to search («ابحث», «ابحث مرة ثانية») skips the analyzer |
| D18 | Medium-confidence reports can be sufficient; low never can from report text alone |
| D19 | Out of scope: the analyzer publishing its own card, reading attachments or drafts, `simple_search` and writing flows |

**Correction carried into this plan:** during design I said turn 4's «WI-2» was a leak. It was not.
`WI-N` is a visible label on the card button (`frontend/components/chat/MessageBubble.tsx:817`, "the
alias the reply body may have named"). The reply rule in §4.10 does **not** block `WI-N`.

---

## 3. Flow

```
user message
  └─ router
       ├─ ChatResponse ─────────────► reply (unchanged)
       │
       ├─ DispatchAgent(deep_search) ► _dispatch ► gate ► planner ─┬─ search
       │    explicit search / no prior report /                    │
       │    different matter / accepted a search offer             └─ already_covered and
       │                                                              analyzer did not check
       │                                                                   │ (D14)
       └─ CheckPriorReports ──────────► analyzer ◄─────────────────────────┘
                                          │
                  ┌───────────────────────┼─────────────────────────┐
              sufficient               partial              insufficient or failed
                  │                       │                         │
          responder(prior_report)  responder(prior_report)   _dispatch(deep_search,
                  │                 + search offer + chips     handoff, analyzer_checked)
                reply               handoff saved on message        │
                                          │                  gate ► planner MUST search
                                  user says yes                     │
                                          │                     report built on
                                  router ► _dispatch(handoff)   the covered quotes
```

The analyzer runs in `_route`, **before** `_dispatch`, so it is not behind the deep-search gate
(`orchestrator.py:3105`). A free user at their limit still gets answers from their own report, and sees
the upgrade message only when a real search is needed. A run with no retrieval is not counted as a
search (migration 197).

---

## 4. What gets built

### 4.1 Package `agents/memory/artifact_analyzer/`

Layer 4 Memory, tier_2. House layout: `__init__.py`, `models.py`, `deps.py`, `context.py`, `tools.py`,
`prompts.py`, `agent.py`, `runner.py`, `verify.py`, `tests/`.

Public entry: `analyze(call: AnalyzerCall, deps: AnalyzerDeps) -> AnalyzerResult`. It **never raises**.
Any failure returns `verdict="insufficient", degraded=True`, which falls back to searching, today's
behaviour.

Lift from `agents/memory/item_analyzer/`: the alias idea, the user-scoped loader, `_record_run` and
`_safe_usage`. Do **not** lift `META_KINDS`: it says `notes` while the DB kind is `note`
(`runner.py:65`), which makes `analyze()` raise on any note.

Model slot in `agents/utils/agent_models.py`: `"artifact_analyzer": _FLASH_MEDIUM`. Use the shared
`OUTPUT_TOKENS_CAP`, never a per-agent cap. Add `request_limit=6`: this bounds tool **rounds**, not
chunks, so D8 still holds.

⚠ The responder uses the same V4 + reasoning setup and failed on turn 6 with `Model token limit
(provider default) exceeded before any response was generated`. Baseten also hard-cuts reasoning at
~4096 tokens unless `reasoning.max_tokens` is set. Phase 2 must confirm what `_FLASH_MEDIUM` sends and
set the budget deliberately.

### 4.2 Context, built by code (D4–D7, D16)

```xml
<current_message>raw user message</current_message>
<conversation>last 6 rows</conversation>
<report label="R1" title="…" confidence="high" asked="describe_query">
  <content_md>full body</content_md>
  <gaps><gap>…</gap></gaps>
</report>
<sources>
### [S1] chunk title  (R1[34], R2[12])
**النظام**: …
**حالة النظام**: ملغي — لم يعد سارياً        (only when repealed)
**نطاق النظام**: …
**ملخص المقطع**: …
</sources>
```

- **Conversation:** `_load_recent_messages(..., n=7)` (`orchestrator.py:~425`). It counts non-empty rows,
  and the user message is saved before the AI call, so 7 = current + 6. It is already encoded and tagged.
- **Report selection (D5):** live `agent_search` items of the conversation. 3 or fewer: all. More: those
  in `attached_item_ids`; if the router picked none, the newest 3. Newest first; on conflict the newest
  wins (prompt rule).
- **Gaps:** `metadata.gaps` (§4.9). Reports published before that have none, so fall back to their
  `summary`, which carries gap analysis.
- **Sources:** `retrieval_artifacts.ura_json` `high_results` + `medium_results`, joined on `artifact_id`,
  filtered by `user_id` and `deleted_at`. Never read `dropped` (D7). If a report has no artifact row
  (the write is best-effort), use its `workspace_item_references` rows instead.
- **Why not `workspace_item_references`:** `_compute_final_references` strips uncited sources before
  publish (29 of 4,860 rows in October were unused). In that table `wi_id` is the report and `item_id`
  is the source.
- **One deduplicated list:** dedupe by `ref_id` across reports and label S1, S2… because `[n]` restarts
  per report (WI-2[34] and WI-4[12] are the same circular). Code holds the label map; the model never
  sees a UUID.
- **Summary per domain**, same labels and order as the reranker
  (`reg_compliance_search/prompts.py:~355`, `unfold_reranker.py`):

| Domain | Fields |
|---|---|
| regulations | `reg_title`, `reg_status`, `reg_scope` from the URA row + `chunks_v2.summary` (batched; the URA row has no summary). All 55 here have one |
| cases | title, court, date + `short_summary` |
| circulars | title, entity, first ~200 chars. There is no summary column |
| services | name, provider, ≤600 chars of `service_context` |

- **Masking:** reports are stored with real identifiers (`publisher.py` runs `decode_for_persist`). Encode
  them with `encode_active` before the LLM, then call `persist_new_mappings` before the run, the same
  order as `orchestrator.py:2763` and `:3874`. Tokens stay encoded and `message_service` decodes them
  (`_bubble_tokens` docstring), so no decode is needed in the agent.

### 4.3 Tool `open_sources(labels: list[str])` (D8)

Returns the whole text of every label in one call, no cap. It reads the source tables, not `ura_json`,
which may hold clipped text. Whole-text fields must be confirmed per domain in Phase 2:

| Domain | Key | Whole text |
|---|---|---|
| regulations | `reg:<chunks_v2.id>` | `chunks_v2.content` (check against `chunk_agent_content`) |
| cases | `case:<case_ref>` | `cases.content` |
| circulars | `circular:<id>` | `circulars.content` |
| services | service ref | `services.original_markdown` |

Reuse the ref parsing in `agents/simple_search/searcher.py::_identity_from_ref_row`. Opened text is kept
on deps so quotes can be verified against it. Sizes: regulation chunks here average 2,588 chars (max
6,362); judgments average 8,196, p95 22,455, max 113,695.

### 4.4 Output and code-enforced rules

```python
Verdict = Literal["sufficient", "partial", "insufficient"]

class Quote(BaseModel):
    source: str   # "R1" or "S3" — a report or an OPENED source
    text: str     # verbatim

class MissingItem(BaseModel):
    text: str               # what the question needs and we do not have
    reported_gap: bool      # true if this is a gap a prior report already stated

class AnalyzerOutputLLM(BaseModel):
    restatement: str
    verdict: Verdict
    quotes: list[Quote]
    missing: list[MissingItem]
    rationale: str          # logged, never shown
```

Prompt rules (English, like the router and responder prompts):
- Sufficient: the quotes as they stand answer what was asked, given the facts in the conversation.
- Partial: they answer part of it, or the question lands on a gap a prior report already stated.
- Insufficient: nothing we hold answers it, or it is a new issue.
- Summaries are for choosing what to open. Never quote a summary.
- Open any chunk that might answer the user. Do not guess from a summary when the text is one call away.
- Quote the smallest passage that answers. If a fact selects a branch, quote that branch.
- Never predict or imply what a new search would find or change (D10).
- Missing items state only what we do not have. Copy a reported gap word for word.

`verify.py` runs after the LLM. Code decides, not the model:

| Rule | Behaviour |
|---|---|
| Every quote must exist | Exact match after `normalize` (collapse whitespace, drop `[n]` markers), against a report or an opened chunk. No fuzzy match. Failures go back once via `ModelRetry` naming them; survivors are kept, the rest dropped |
| Displayed text | Taken from the source slice, never from the model's string |
| Sufficient or partial with no verified quote | Becomes insufficient, unless all `missing` are verified reported gaps |
| Sufficient resting only on a low-confidence report (D18) | Becomes partial. A quote from an opened chunk is not affected |
| Insufficient where all `missing` are verified reported gaps (D11) | Becomes partial |
| Any quote from a repealed law | Repeal status stays attached (§4.6) |

`AnalyzerResult`: `verdict`, `restatement`, `quotes` (text, source name, repeal status, item id, ref id),
`missing`, `degraded`, usage.

### 4.5 Router

**New output in `agents/models.py`:**

```python
class CheckPriorReports(BaseModel):
    type: Literal["check_reports"] = "check_reports"
    task_label: str          # same rules as DispatchAgent; becomes the card title if it searches
    target_wi: str | None = None
    attached_wis: list[str] = []   # only needed above 3 reports
    target_item_id: str | None = None      # resolver-filled
    attached_item_ids: list[str] = []      # resolver-filled
```

Why an output and not a mid-run tool: with the responder writing the reply (D13), the router never needs
a second turn. An output ends the run, so insufficient reaches deep search with no extra router call
(D12), and each output is already an output tool under Pydantic AI's list syntax.

- `router_agent` `output_type`: add it before `TextOutput(_text_as_chat)` (`router.py:486`).
- Extend the output validator's alias resolution, `RouterRunResult.output`, and the `run_router` logging.
- `agents/router/router.py:420`: for a reply tagged `agent_family=deep_search`, an answer to the
  follow-up question becomes `CheckPriorReports` with that WI as `target_wi`. **Other families keep
  today's rule.**
- "Output rule" (line ~216) and "Decisions before every reply": name the third output.
- New section, replacing the deep_search refine paths:

```
## Before repeating a search — CheckPriorReports
When the workspace holds a prior search report (kind=agent_search) and the user's
message continues that matter — it answers a question we asked, adds a fact, or asks
about the same issue — emit CheckPriorReports, not DispatchAgent(deep_search).
Emit DispatchAgent(deep_search) instead when:
- the user explicitly asks to search or repeat a search («ابحث», «دوّر», «ابحث مرة ثانية», «أعد البحث»);
- it is a different matter;
- the last reply offered to search and the user accepted.
With more than 3 prior reports, attach the ones where the answer is likely to be.
With 3 or fewer, leave attached_wis empty.
```

- `agents/router/tests/test_follow_up_answer.py` pins the old rule; update it.

### 4.6 Orchestrator and responder

**`_route` (`orchestrator.py:2725`).** After `_record_follow_up_status`, add a branch:

```python
if isinstance(result, CheckPriorReports):
    if not analyzer_enabled(user_id) or not ctx_has_prior_reports(ctx):
        result = result.to_dispatch()      # identical to today's dispatch
    else:
        async for ev in _run_prior_check(...):
            yield ev
        return
```

Flag off is a coercion in code, so the router prompt stays the same for everyone and no per-user text
enters the cached prompt.

**`_run_prior_check(...)`** (new, also used by the planner revert):
1. Run `analyze()`.
2. Insufficient or degraded: yield `agent_selected`, then `_dispatch(deep_search, ...,
   analyzer_handoff=handoff or None, analyzer_checked=True)`.
3. Sufficient or partial: build the responder input, compose the bubble, stream it through the existing
   delivery. Extract lines 3576-3608 of `_dispatch` into a helper rather than duplicating them.

**Responder mode `prior_report`.** The fourth mode; `simple_search` shows it works with no aggregator.
Touches `ResponderMode`, `RESPONDER_MODES`, `OUTPUT_TYPES`, `ResponderOutput`, `_ARTIFACT_FOR_MODE`
(`responder/models.py:32,239,351`), plus `SYSTEM_PROMPTS`, `render_artifact_package`,
`render_mode_framing`, `_FIELDS`/`_retry_msg`, `create_responder`.

```python
@dataclass
class PriorReportArtifact:
    restatement: str
    verdict: Literal["sufficient", "partial"]
    quotes: list[Any]
    missing: list[str]
    confidence: str
    version_rule: str = ""

class PriorReportResponse(ResponderBase):
    mode: Literal["prior_report"] = "prior_report"
    search_offer: str | None = None   # required on partial
```

The responder writes one plain line and, on partial, what is missing plus the offer. **Code composes the
bubble**, so quotes cannot be retyped:

```
<one plain line>

> «verbatim quote»
— <source name>

[partial] <missing + offer>
```

- **Source name:** a chunk gets its regulation and article, circular title, or judgment label. A report
  quote gets the sources behind its `[n]` markers. An uncited chunk reads «وجدنا مصدرًا متعلقًا بسؤالك»
  (D15). Confirm the chat renderer handles `>` blockquotes on web and mobile.
- **`search_offer` is a separate field from `follow_up_question`,** which means a fact from the user's
  file and is dropped by the no-nag guard. On partial, code adds two `answer` chips, «نعم، ابحث» and
  «لا، يكفي», after `salvage_next_steps`, which would otherwise drop them.
- **Law-change notice:** the sentence comes from `responder_line`
  (`aggregator/runner.py:646`, `planner/runner.py:179`). It must be computed for quoted regulations here
  too; in this conversation it is legally material.
- **Prior card button:** emit `referenced_existing_item` for the report the reply leans on, on every
  reply. `message_service` already handles it (`:1127`). The web renders it as the standard green
  «افتح التحليل القانوني» button (see §11).
- **Responder failure:** `run_responder` returns `None`. Send the verified quotes with their source names
  and a fixed Arabic line. Never a clipped text, the turn 6 bug (`_response_from_artifact`,
  `planner/runner.py:162`).

**Provenance.** A `prior_report` reply has no `artifact_ids`, so today it reads as the router's own
answer and `find_pending_follow_up` gets no `item_ids`:
- Add a marker via `build_provenance_tag`, with both the router rule **and** `responder/context.py::
  HISTORY_LEGEND` (every history marker needs both).
- `find_pending_follow_up` (`router/context.py:94`): include `referenced_item_ids` in `item_ids`.
- `_classify_follow_up`: accept `CheckPriorReports` alongside `DispatchAgent`.

### 4.7 Passing what's missing to the planner (D12)

```python
class AnalyzerHandoff(BaseModel):
    restatement: str
    covered_quotes: list[...]   # text + source name
    missing: list[str]
```

Path: `_dispatch(..., analyzer_handoff, analyzer_checked)` → `MajorAgentInput` → `_run_deep_search`
(`:3715`) → `PlannerDeps`.

- **Planner:** new `<prior_coverage>` block in `build_decider_instructions` (`planner/prompts.py:597`).
- **Downstream:** a new `ContextBlock` label `prior_coverage`, persistence `"turn"`, in the candidate
  builder (`deep_search_v4/orchestrator.py:~1030`), force-forwarded by `_select_forwarded_blocks` when
  present. **Do not use `planner_brief`**: its contract is "supplements, never steers".
- **Expander prompts:** derive queries from what is missing, not from what is covered.
- **Aggregator prompt:** treat the covered quotes as established and carry the user's facts. This is the
  fix for turn 6, where the date was lost. Keep the v1 prompt; add a rule for this block only.

**"Yes" on partial.** Save the handoff on the assistant message: a server-only event captured by
`message_service` (same pattern as `captured_follow_up`, `:1024`) and merged via
`_merged_message_metadata` (`:605`) at `done`, decoded, never forwarded to the client. On the next turn,
`load_router_context` exposes it from the **last** assistant row only, re-encoded (mirror
`find_pending_follow_up`). `_route` passes it to `_dispatch` when the router dispatches deep_search.

**Pause survival.** This is the bug class that got the old analyzer removed on 2026-06-08: state on deps
died across a pause. Store `analyzer_handoff` and `analyzer_checked` in
`paused_runs.deferred_payload` (`agents/paused_runs.py:44`) and restore them on the resume leg. Test it
explicitly.

### 4.8 Planner revert (D14)

- `PlannerDecision.already_covered: bool = False`, plus a decider rule: set it only when a prior report
  already answers, and never when the user insists on searching.
- `_run_planner_turn`: after phase 1, if `already_covered` and not `deps.analyzer_checked` and
  `deps.prior_searches` and not pinned, bill the decider and return `PlannerTurnResult(kind="reverted")`.
  If `analyzer_checked` is set, ignore the flag and search. **That one guard prevents a loop.**
- `_run_deep_search`: return `_DeepSearchOutcome(kind="reverted")`.
- `_dispatch`: skip delivery, let `agent_run_finished` go out at line 3691, **then** call
  `_run_prior_check(after_revert=True)`. The client must see the run closed before the reply.
- Resume leg (`:1517`): same handling next to the `aborted` branch, including
  `_resolve_pause_loud` and `_release_agent_answer_tag`. This covers replies to a planner question, which
  never pass through the router.
- If the analyzer then says insufficient, `_dispatch(analyzer_checked=True)` searches. One revert per
  turn follows automatically.

`aborted` is not reused: it is resume-only, re-routes through the router, and means something else.

### 4.9 Save gaps at publish (D6)

`agents/agent_search/publisher.py::_build_metadata` (`:59`): add `gaps` from `input.agg_output.gaps`,
each passed through `decode_for_persist`, excluding the sentinels `no_references_after_reranker` and
`_MODEL_FAILURE_GAP_AR`. Do it here and not in `build_artifact`, which runs before `_enrich_gaps`.

### 4.10 No internals in any reply (D15)

- **Prompt rule:** a shared constant in the router, responder `CORE_PROMPT`, planner `ask_user`
  guidance and `writer_planner` user-facing text: speak about what we found, never how we worked; never
  name an agent or say what was used, cited, dropped or ranked.
- **Code check:** `agents/utils/reply_guard.py`, one chokepoint wrapping the generator in
  `handle_message` (`:2385`), so every `token` and `agent_question` event passes through it, including
  the seven `token` sites and any added later. Persisted text equals streamed text.

| Class | Examples | Action |
|---|---|---|
| Identifiers | `deep_search`, `aggregator`, `reranker`, `planner`, `workspace_item`, `ura_id`, UUIDs, `S3`-style labels | Removed and logged as `reply_guard.hit` |
| Arabic process words | «الجامع», «المجمّع», «معيد الترتيب», «الموجّه», «لم يستخدم هذا المصدر» | **Logged only.** These are ordinary words too, so rewriting legal prose by regex is riskier than the leak |
| `WI-N` | | **Allowed.** It is a visible card label |

### 4.11 Flag

`shared/config.py`: `ARTIFACT_ANALYZER_ENABLED: bool = False` and `ARTIFACT_ANALYZER_USERS:
Optional[str] = None` (comma-separated user ids; empty means everyone once enabled). The env var name is
the alias name.

### 4.12 Telemetry and cost

`track_stage("artifact_analyzer.run", agent_family="memory")`, ledger label
`memory.artifact_analyzer`, cost counted to the user as usual. Span attributes: `verdict`,
`verdict_before_rules`, `reports_n`, `sources_n`, `opened_n`, `quotes_n`, `quotes_dropped`,
`reports_quoted_n`, `after_revert`, `degraded`.

`reports_quoted_n` is what will decide the own-card question later (only 31 of 500 conversations in 60
days have 3+ reports).

Estimates to confirm in Phase 2: analyzer ~$0.004, up to ~$0.007 when it opens sources, ~20s. Full
sufficient turn ~45–50s and ~$0.01, against ~4 min and ~$0.07 today.

---

## 5. Phases

| Phase | Work | Exit check | Suggested agent |
|---|---|---|---|
| 0 | Worktree off `origin/master`. Diff the uncommitted planner/aggregator work and keep or drop it | Clean baseline, tests green | main |
| 1 | §4.9 gaps. **Ships alone, first,** so reports start carrying gaps | New report has `metadata.gaps`, decoded, no sentinels | @fastapi-backend |
| 2 | §4.1–4.4 package, tool, verify, slot, eval harness | Eval passes (§7), reasoning budget confirmed | @pydantic-ai-prompt-engineer, @pydantic-ai-tool-integrator, @pydantic-ai-validator |
| 3 | §4.10 reply rule. **Ships alone** | Hits logged, normal replies unchanged | @fastapi-backend |
| 4 | §4.6 responder `prior_report` mode | Mode tests, composed bubble, failure path | @pydantic-ai-prompt-engineer |
| 5 | §4.5 router + §4.6 `_route` + §4.11 flag | Flag off behaves exactly as today | @luna-wiring |
| 6 | §4.7 handoff, block, prompts, "yes" path, pause survival | Pause-resume test passes | @luna-wiring |
| 7 | §4.8 planner revert, fresh and resume | No-loop tests pass | @luna-wiring |
| 8 | Rollout (§8) | Metrics hold for 7 days | @smoke-tester, @logfire-monitor-agent |
| 9 | Delete `agents/memory/item_analyzer*` and its slot | Grep clean, tests green | main |

@integration-lead checks SSE and contract compatibility for web and mobile after phase 6. No new
client-facing event is planned.

---

## 6. File manifest

**New:** `agents/memory/artifact_analyzer/*`, `agents/utils/reply_guard.py`,
`scripts/eval_artifact_analyzer.py`.

**Modified:**
- `agents/models.py`
- `agents/router/{router,context}.py`, `agents/router/tests/test_follow_up_answer.py`
- `agents/orchestrator.py`
- `agents/paused_runs.py`
- `agents/responder/{models,prompts,agent,context}.py`
- `agents/utils/{agent_models,history}.py`
- `agents/deep_search_v4/orchestrator.py`
- `agents/deep_search_v4/planner/{models,deps,prompts,runner}.py`
- `agents/deep_search_v4/reg_compliance_search/prompts.py`, case search expander prompt
- `agents/deep_search_v4/aggregator/prompts.py`
- `agents/agent_search/publisher.py`
- `backend/app/services/message_service.py`
- `shared/config.py`
- `.github/workflows/ci.yml`

**Deleted (phase 9):** `agents/memory/item_analyzer/`, `agents/memory/item_analyzer_editor/`.

---

## 7. Tests and eval

**Unit and integration:** every row of §4.4; report selection at 0, 1, 3, 4 reports and with no router
pick; dedupe across reports; repealed flag; masking round-trip; `open_sources` per domain; flag-off
coercion; planner guard; "yes" after partial; pause then resume keeps the handoff; revert then
insufficient searches once; responder failure path; reply guard per class, and `WI-N` left untouched.

- CI runs a **whitelist** of suites with no `pytest-asyncio`: add the new suites to `ci.yml` and write
  them sync-compatible. A local pass is not proof.
- New files under `agents/tests/` need `git add -f`.

**Eval** `scripts/eval_artifact_analyzer.py`, run on the real model against production data read-only,
with reports limited to those existing at that turn. Store conversation ids and message positions only;
no user content in the repo.

| Case | Expected |
|---|---|
| `9c647ef6` turn 4 | Sufficient, quoting the non-نفقة rule in WI-2 |
| `9c647ef6` turn 5 | Partial or insufficient, never sufficient |
| `9c647ef6` turn 6 | Sufficient, quoting WI-4; keeps old-law context |
| Follow-up on a new legal issue | Insufficient |
| Question on a reported gap | Partial, and no claim that a search would solve it |
| Answer sits only in an uncited source | Sufficient via `open_sources` |
| Repealed-law quote | Status attached |
| Low-confidence report only | Not sufficient |
| Sample of the 29 `target_wi` dispatches | Labelled by the owner |

**Pass:** no false sufficient on the gold set. A false sufficient gives a user a wrong legal answer with
no search behind it; a false insufficient only costs what we pay today. No unverified quote reaches a
reply, and no predictive wording about gaps.

---

## 8. Rollout

1. Deploy phases 1 and 3 on their own.
2. Deploy the rest with the flag off. Confirm behaviour is unchanged.
3. Enable for the owner and `enterprise@rayhanai.com`. Replay the convo `9c647ef6` messages. Check Logfire,
   the ledger and the rendered reply in a browser.
4. Owner decides on enabling for everyone.
5. Watch for 7 days: verdict mix, `degraded` rate, `quotes_dropped`, `opened_n`, turn time, cost per
   turn, `reply_guard.hit`, and «ابحث» sent right after a sufficient reply, the best live signal of a
   false sufficient.

Roll back by unsetting the flag.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| **False sufficient.** A quote can exist and still not answer | Eval pass bar, source shown in the reply, the «ابحث» signal, and an explicit search always available |
| Reasoning failure on Baseten V4 | Set the budget in Phase 2; failure falls back to search or to verified quotes |
| Router skips the analyzer | Planner revert (§4.8) is the safety net |
| Handoff lost across a pause | `deferred_payload` plus an explicit test (§4.7) |
| Facts lost on search, as in turn 6 | Handoff carries restatement and quotes; aggregator rule |
| Cost creep from uncapped opening | Log `opened_n` and tokens. Worst case is about the aggregator's input (~229k chars), still under a search |
| **Wait with no progress.** ~45–50s on the typing indicator only. Web `status` is a no-op outside a deep_search run (`use-chat.ts:692`) | Accepted for v1; see §10 |
| Reports published before Phase 1 have no gaps | Fall back to `summary` |
| Prompt edits invalidate the prompt cache once | Expected at deploy |

---

## 10. Later, not now

- Analyzer publishing its own card: decide from `reports_quoted_n`. The router's `writing` `summary`
  subtype can already consolidate reports on request.
- A visible progress line for the analyzer, which needs a client change on web and mobile.
- Attachments and drafts as analyzer input.
- Same check for `simple_search` and writing.

---

## 11. Build status (2026-11-02)

**Branch:** `feat/artifact-analyzer` in worktree `C:\Programming\LUNA_AI_artifact_analyzer`, off master `f170c9b`.
**Uncommitted, not deployed, flag off.** New test files are gitignored: `git add -f agents/tests/test_{search_publish_gaps,artifact_analyzer,reply_guard,prior_report_reply,prior_check_flow}.py`.

| Phase | State |
|---|---|
| 0 Worktree, uncommitted work reviewed | Done. The planner and resume hunks on the old branch were already on master; the aggregator v2 work is unrelated |
| 1 Save gaps | Built, tested |
| 2 Analyzer, tool, verify, eval | Built, eval passed |
| 3 Reply rule | Built, tested |
| 4 Responder `prior_report` | Built, tested with stubs. Its real output is not yet evaluated |
| 5 Router output, `_route`, flag | Built, real router 27/27 |
| 6 Handoff, "yes", pause survival | Built, tested |
| 7 Planner revert | Built, tested with stubs |
| 8 Rollout | Not started. Needs the owner |
| 9 Delete `item_analyzer` | Not started |

**Where the build changed this plan:**
- **Model slot is `_FLASH`, not `_FLASH_MEDIUM`.** Same 17 cases: medium took 70s against 38s, cost more, and produced the false sufficient. The §4.1 reasoning warning is obsolete: Baseten already gets `reasoning.max_tokens`.
- **New output `ask_user`.** Facts only the user holds were being listed as missing, which would offer a search that can never help. Anything left in `missing` now rules out sufficient.
- **New output `changes_governing_law`, enforced in code.** Without it turn 5 was a false sufficient in 1 of 3 runs, carrying the new law's 10-day deadline onto an old-law matter. With it, 8 of 8 correct, and the model named the old-law remedy route as missing.
- **`CheckPriorReports` is a router output, confirmed.** `DispatchAgent` also gained `accepts_search_offer`, so a pending handoff is used only when the user actually accepted.
- **No expander or aggregator prompt edits.** The `prior_coverage` block carries its own instruction, so the v1 aggregator prompt is untouched.
- **Law-change data.** During a live transition `reg_status` is empty and `reg_transitions` is not stored, so `law_change_line` computes the notice live from `load_supersessions`.
- **Whole text comes from the source tables.** URA text is clipped (cases near 6k chars). Services key on `service_ref`, because their `ref_id` is a hash.
- **`WI-N` is not blocked.** It is a visible card label.
- **Pause questions are scrubbed at save too**, since they are stored before their event reaches the guard.
- **Replies show at most 4 quotes.**

**Measured, replacing §4.12's estimates:**
- Analyzer: 47s typical, 76s p90, $0.006 average, $0.013 max.
- Gold: 26 of 26 with no false sufficient, no degraded run, no predictive wording.
- Full CI list: 2,242 pass.

**Open:**
- The owner labels the 10 real follow-ups. Sample 2 is the one to judge: asked for a ruling, the report said none settles it, and the verdict was sufficient where partial may be fairer.
- Verdicts vary between runs, so the pass bar is a rate on repeated runs, not one pass.
- A reported-gap question came back insufficient in 2 of 3 runs. It should move to partial once reports carry saved gaps. Re-measure after Phase 1 has been live.
- Not yet tested end to end: the responder's real text, a real partial followed by "yes", a real revert, and the rendered reply on web and mobile.
- `agents/router/tests/test_follow_up_answer.py` is gitignored and local. Its `test_answer_rule_is_in_the_static_system_prompt` pins the old rule and needs updating. Three failures in `test_agent.py` also fail on clean master.

### End-to-end, local (2026-11-03)

Test account `enterprise@rayhanai.com`, conversation `5e8e0464`. The opening search ran on production, because on this machine the Alibaba keys are invalid (401) and OpenRouter returns "User not found", so local searches return nothing. Every follow-up ran on the local backend with the flag on for that account only.

| Turn | Result |
|---|---|
| «الأمر صادر لدين عادي مو نفقة» | Sufficient, no search. Article 26 quoted verbatim, law-change notice, card chip |
| «الحجز صار بتاريخ 2026/1/27» | Partial, no search. Moved to the old law and quoted its by-law; yes/no offer |
| «نعم، ابحث» | Search ran. Logfire shows `<prior_coverage>` in the planner and expander prompts; empty only because of the local keys |
| «الراتب ينزل كل شهر…» | Partial, no search. Correct while the only report analyses the new law |
| «وش أول خطوة أسويها مع البنك…» | Sufficient, 43s. Quoted the 24-hour circular verbatim, the source turn 6 lost |

Verified in the database: analyzer turns bill only router, analyzer and responder; partial replies save `analyzer_handoff`; `reply_metadata` never reaches the client; no errors in the backend log.

**Fixed from this run:**
- **Responder speed:** in `prior_report` the responder now runs on the analyzer's slot. On medium it spent 7k–10k reasoning tokens and 40–60s on one or two sentences; turns fell from about 95s to 43–47s.
- **Empty missing list:** a partial made by the governing-law or low-confidence rule had nothing missing, so the search after "yes" had no target. Those rules now add one.
- **Attribution:** quotes taken from a report's own sentences were attributed to the law behind them, which reads as statute text. They now read «من بحثنا السابق «…»، استنادًا إلى: …». Only text opened from a source carries its name alone.
- **Card button on every reply (owner, 2026-11-03).** Only replies that quoted a report's own words got a chip; a reply quoting only a law got none. `_report_chip` now always points at a report: the one whose words were quoted, else the one whose search found the quoted source, else the newest read.
- **One green button (owner, 2026-11-03): «keep it as افتح التحليل القانوني with the greenish bar, so we have standardized frontend».** `MessageBubble` no longer has the «راجع: …» chip. Referenced items join the turn's own cards in the one `ArtifactChip`, which opens the card. This also changes the planner's older `build_artifact=False` path, which emits the same event. `hasArtifacts` stays own-cards-only, because citation links resolve against it. Mobile renders no button for referenced items at all (`AssistantBody` reads `artifact_ids` only), so these replies show none there.
