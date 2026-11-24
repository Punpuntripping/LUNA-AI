# «بحث موسّع» — build spec (2026-11-24)

**Design source:** `C:\Programming\LUNA_AI\.claude\plans\extended_search.md` (read it — §0, §3 D20–D33, §4.1–§4.10;
it lives in the main checkout, untracked; READ ONLY). Owner: "ok, let's start building, u can use background agents".
**Worktree:** `C:\Programming\LUNA_AI_extended_search`, branch `feat/extended-search` from origin/master `b9767f6`.
**Reference implementations (experiments, read only):** `C:\Programming\LUNA_AI\.claude\plans\attachment_segmentation\experiments\`
- `run_budget_planner.py` — the budget planner as tested 11-23 (prompt, models, validator, budget block, pricing, masking).
- `run_full_trace.py` — the whole chain on a multi-document message: production angle-map loading, marked text
  〔D1·S7 · owner · value〕, §4.9 b brief + SEARCH_INSTRUCTION, parallel searches through `run_retrieval`, merger with §4.9 c.
- `run_angle_searches.py`, `run_angle_merge.py` — single-document versions of the searches and the merger.

## Rules for every builder

1. Work ONLY inside `C:\Programming\LUNA_AI_extended_search`. Never edit `C:\Programming\LUNA_AI` (another branch, dirty tree).
2. **No git state changes:** no commit, stash, checkout, reset, rebase, branch. The owner commits.
3. **Everything off by default:** `EXTENDED_SEARCH_ENABLED=False`, `EXTENDED_SEARCH_USERS=None`. With the flag off the
   deep-search turn must be byte-for-byte today's behaviour.
4. **No DB migration, no production writes, no deploy.** Storage = `paused_runs.deferred_payload` + `messages.metadata`
   (both jsonb, no CHECK).
5. **Don't change the production planner's prompt or behaviour (D1)**, and don't remove `support` from today's deep search
   (phase S is a separate release). The extended module simply never sets `support`.
6. Prompts live in `.py` files (English instructions, Arabic output). All user-facing / error text in **Arabic**.
   **Latin digits only.** Never print secrets / the DB URL.
7. Tests: pytest, NO real LLM calls (pydantic-ai `TestModel` / `FunctionModel`, fakes like
   `agents/memory/angle_mapper/tests/fake_supabase.py`). CI has no pytest-asyncio — write async tests the way existing
   `agents/deep_search_v4/tests` do (check before writing). Add new test dirs to the "Durable test suites" whitelist in
   `.github/workflows/ci.yml`. Run only the suites you touch plus their neighbours (memory is limited — don't run
   the whole repo suite, don't run several heavy suites at once).
8. Masking (وضع السرية): everything sent to a model passes through the turn's codec exactly as the existing deep-search
   path / angle mapper do; model text is decoded before it is stored or shown.
9. Billing: every model call records its `llm_calls` row through the existing usage sink (same pattern as
   `deep_search.planner` / `deep_search.aggregator`), agent names `deep_search.extended_planner` and
   `deep_search.extended_merger`. Search stages keep their existing rows.
10. Keep the shared contract (`agents/deep_search_v4/extended/models.py`, `frontend/lib/contracts/extended-plan.ts`) stable:
    add fields if you must, never rename/remove; say so in your report.

## Defaults for the plan's open questions (owner may change later — keep them as named constants)

| Q | Default in this build |
|---|---|
| Q11 files over 100k | each search gets every attached document whole, per-doc cap 200,000 chars, total 300,000; beyond that, today's cut |
| Q12 mode table | §4.3 draft: 15 → (3, 8, 2) · 20 → (3, 8, 3) · 30 → (4, 10, 4) · 40 → (5, 10, 5) · 50 → (6, 12, 6) = (searches, per side, full) |
| Q13 cheaper option | dropped by code when it saves < 1 point (rounded estimates equal or cheaper ≥ plan) |
| Q14 planner input | D28 as tested 11-23: angle cards + documents (id, type, title) + the user's message + the conversation before it + the budget block. No summary / map client / gaps / section list / file text |
| Q15 summary | ADDED to the whole file (D17 + D29) |
| Q16 points left | `DeepSearchGate.remaining` (tightest limited window); under 10 → today's ordinary path, silently (no extended search) |
| Q18 no-budget ceiling | none (`NO_BUDGET_MAX_SEARCHES = None`) |
| D23 margins | pro 5 · pro_plus 5 · max 10; eligible plans = pro, pro_plus, max (effective plan) |
| D33 | points left > 50 → no budget (`BudgetMode(points=None, …None)`) |
| Draft estimate (§4.3, §2.8) | per search 1.95 (0.55 fixed + 1.4 whole file) + 0.285 × sub-queries (cap × sides; an uncapped side = 10 regulations / 7 judgments) + merger 2.0 when > 1 search + the planner's measured cost. Ordinary search: 1.95 + 12 × 0.285 ≈ 5.4 |

## Shared contract (already written — read it first)

- `agents/deep_search_v4/extended/models.py` — BudgetMode, PlannerView / DocView / AngleCard / SectionView, ExtendedPlan
  (LLM output), ExtendedPlanCard (card), ExtendedPlanPause (stored pause), StoredOption, ExtendedSearchReport, MergedAnswer,
  PAUSE_REASON = "extended_plan", CARD_METADATA_KEY = "extended_plan", OPTION_LABELS_AR.
- `frontend/lib/contracts/extended-plan.ts` — the card types + `parseExtendedPlan`; `messages.ts` (`metadata.extended_plan`,
  `metadata.pause_reason`, `metadata.plan_choice`, `SendMessagePayload.plan_choice`); `sse.ts`
  (`SSEAgentProgressData.searches {total, done}`).

## Work packages and interfaces (keep these signatures)

### A — planning side (`agents/deep_search_v4/extended/`: budget, eligibility, view, prompts, planner, card, brief)

```python
# agents/utils/extended_search_flags.py  (pattern: agents/utils/citation_pin_flags.py)
def extended_search_enabled(user_id: str | None) -> bool
# shared/config.py: EXTENDED_SEARCH_ENABLED: bool = False ; EXTENDED_SEARCH_USERS: Optional[str] = None
# agents/utils/agent_models.py: slot "extended_planner" = _WAFER_V41_DEFAULT (+ any slot registry / tests that list slots)

# budget.py
MODES: dict[int, BudgetMode]; NO_BUDGET: BudgetMode; MARGINS: dict[str, int]; MIN_POINTS = 10; NO_BUDGET_ABOVE = 50
def pick_mode(points_left: float | None, plan_id: str | None) -> BudgetMode | None
def budget_block(mode: BudgetMode) -> str                       # §4.3 text; the D33 one-liner under no budget
def estimate_points(requests, *, planner_points: float = 0.0) -> float
def ordinary_estimate_points() -> float

# eligibility.py  (sync, never raises — any failure = not eligible)
@dataclass
class Eligibility: eligible: bool; reason: str; mode: BudgetMode | None; points_left: float | None; plan_id: str | None; angle_map: dict | None
def check_eligibility(supabase, *, user_id: str, gate, attached_item_ids: Sequence[str]) -> Eligibility
#   flag → effective plan in {pro, pro_plus, max} → points_left = gate.remaining (None = unlimited → no budget) ≥ 10
#   → a FRESH stored angle map covering the turn's attachment items (store.load_fresh_angle_map_for_item /
#   load_angle_map_for_items; several maps → the one covering most of the items) with ≥ 2 angles → mode = pick_mode

# view.py  (sync; raises on a failed read)
def build_planner_view(supabase, angle_map: dict, *, user_id: str) -> PlannerView
#   docs (id D1…, title, doc_type, summary, marked_text = whole text with 〔D1·S7 · owner، قدّمه … · جوهري〕 markers),
#   angle cards with section labels (labels.section_index / angle_viewpoint / relative_label), section_refs
def render_planner_input(view: PlannerView, *, query: str, recent_messages, mode: BudgetMode) -> str

# planner.py
@dataclass
class PlannerRun: plan: ExtendedPlan; points: float; usage: Any; model: str
async def run_extended_planner(view: PlannerView, *, query: str, recent_messages, mode: BudgetMode, user_id: str) -> PlannerRun
#   V4.1 slot "extended_planner"; validator (ModelRetry): ids/refs exist, the plan AND the cheaper option inside the
#   mode's limits (skipped under no budget except ids), max_sub_queries required under a budget mode;
#   masks input / decodes output; ledger row deep_search.extended_planner; raises on failure (caller falls back)

# card.py
def build_options(plan: ExtendedPlan, *, planner_points: float) -> list[StoredOption]   # plan, [cheaper], ordinary
def build_card(options: list[StoredOption], plan: ExtendedPlan, *, run_id: str, points_left: float | None) -> ExtendedPlanCard
def card_text_ar(card: ExtendedPlanCard) -> str   # §4.4 mock in words; ends with «أو اكتب طلبك في خانة الرسالة»

# brief.py
def build_planner_brief(request: ExtendedRequest, *, plan: ExtendedPlan, view: PlannerView) -> str   # §4.9 b exactly
```

### B — execution side (`extended/`: runner, merger, publish + minimal hooks in `deep_search_v4/orchestrator.py`)

```python
# runner.py
async def run_extended_searches(
    requests: Sequence[ExtendedRequest], *, plan: ExtendedPlan, view: PlannerView, query: str,
    deps_factory: Callable[[int], "PlannerDeps"],        # fresh PlannerDeps per search (index → deps); the wiring builds it
    emit_sse: Callable[[dict], None] | None = None,
) -> list[ExtendedSearchReport]
#   each request → PlannerDecision(mode, support=False, planner_brief=brief.build_planner_brief(...),
#   case_full_text_wis / reg_full_text_wis = every attached document) → build_retrieval_config(decision,
#   cap=request.max_sub_queries) → run_retrieval on ITS OWN deps (run_retrieval writes results onto deps);
#   every document delivered WHOLE with its DocView.marked_text + summary (caps 200k / 300k, segmented section
#   selection bypassed for these searches only); distinct query ids; all in parallel (asyncio.gather);
#   one failure never sinks the others; progress: ONE combined stream — stage = the least-advanced search's stage,
#   sources summed, data.searches = {total, done}; never let the first finished search move the bar to "writing".

# merger.py
async def merge_reports(reports: Sequence[ExtendedSearchReport], *, query: str, recent_messages,
                        plan: ExtendedPlan, view: PlannerView) -> MergedAnswer
#   port run_full_trace.py's merger + §4.9 c; shared reference list with excerpts (D19); citations validated and
#   renumbered 1..k; confidence ≤ the highest report confidence; slot "extended_merger" = the aggregator's policy;
#   ledger row deep_search.extended_merger; masked like the aggregator

# publish.py
@dataclass
class PublishedExtended: report_item_ids: list[str]; merged_item_id: str | None; sse_events: list[dict]
async def publish_extended(reports, merged: MergedAnswer, *, supabase, user_id, conversation_id, case_id,
                           message_id, query: str, plan: ExtendedPlan, detail_level) -> PublishedExtended
#   each ok report → agent_search WI (publish_search_result, title = request title); merged → agent_search WI with
#   metadata.children = report ids, metadata.extended_search = {run_id?, titles, resolved}; the merged text lists
#   its reports at the end (code-written «التقارير التي بُني عليها هذا الجواب: …», D14)
async def write_chat_summary(merged: MergedAnswer, *, query: str, deps) -> Any   # the existing responder (unified or
#   legacy, as today's path picks) fed with the merged answer → the PlannerResponse-like object the orchestrator uses
```

### C — wiring (`agents/orchestrator.py`, `backend/app/models/requests.py`, `backend/app/services/message_service.py`,
`backend/app/api/messages.py`)

- `SendMessageRequest.plan_choice: Optional[PlanChoice{run_id: str, option: Literal["plan","cheaper","ordinary"]}]`;
  passed through `message_service` → `handle_message(plan_choice=…)`; the user row gets
  `metadata = {"kind": "agent_answer", "run_id": …, "plan_choice": option}` when it answers a card.
- Fresh deep-search turn (`_run_deep_search`, decision None, no pinned plan): `check_eligibility` → if eligible:
  `build_planner_view` → `run_extended_planner` (any exception → log + today's path) →
  - 1 request → the plan is dropped (log `deep_search.extended.single_search_fallback`) and today's planner runs from
    scratch with `decision=None` — phase 1 with all its checks (D6 "runs as today"; lead 11-24, review #2);
  - ≥ 2 requests → no room in the workspace for N reports + the merged answer (`count + N + 1 > 15`) → today's path, no
    card (lead 11-24, review #8); else `build_options` / `build_card` → pause: `paused_runs` row (pause_reason "extended_plan",
    `deferred_payload["extended_plan"] = ExtendedPlanPause`, normal 24 h expiry) + assistant row (content =
    `card_text_ar`, metadata `{kind: "agent_question", run_id, agent_family: "deep_search", pause_reason: "extended_plan",
    extended_plan: card}`) + the usual `agent_question` / `done` SSE (relay `pause_reason` too).
- Next message while an `extended_plan` pause is pending (in the pause check BEFORE routing, `orchestrator.py` ~2884):
  - `plan_choice` with matching run_id, OR typed text equal (after trim / tashkeel / alef normalisation) to an option's
    label → that option: re-read the gate; `mode_now = pick_mode(points_left, plan_id)` — plan / cheaper fit only when
    `mode_now` exists and is no budget or holds the option's requests (count ≤ max_searches, `full` ≤ max_full, every cap
    ≤ max_per_side; an uncapped request never fits a budget mode); «بحث عادي» fits while points_left ≥ 0 or unlimited.
    No fit → outcome "insufficient", reply in Arabic, nothing runs (the estimate is display-only — D25; lead 11-24,
    review #1/#3). Else outcome = option («بحث عادي»: recorded only once `_dispatch` starts the search; its refusals →
    "insufficient" (plan gate) / "expired" (workspace cap) — review #4); "plan" / "cheaper" → `run_extended_searches` → `merge_reports`
    (skip the merger when only one report succeeded — publish that one) → `publish_extended` → `write_chat_summary` →
    the normal delivery events (+ next-step chips like today's resume); "ordinary" → today's deep search on the stored
    `describe_query` with the extended planner disabled for this run.
  - anything else → resolve the pause, outcome "typed", then **continue to normal routing (the router)** with this
    message (D32). An expired pause → outcome "expired" (best effort), normal routing.
  - The outcome is merged into the card row's `metadata.extended_plan.outcome / outcome_at` (read-merge, never replace).
- Deep-search gate, workspace cap, run_id binding, ledger scope: as today's dispatch / resume do.
- Log every outcome (Logfire span attrs + `logger.info`) — the data D30 says to revisit with.
- `agent_progress` relay in `message_service.py` must pass `data.searches` through.

### D — web (`frontend/`)

- `PlanCard` rendered by `MessageBubble` for `metadata.kind === "agent_question" && pause_reason === "extended_plan"` with a
  valid `parseExtendedPlan(metadata.extended_plan)` (else the row's text as today). Layout = plan §4.4 mock: title
  «بحث موسّع — الخطة المقترحة», numbered searches «title — sides», «ثم أجمع النتائج في جواب واحد», «التكلفة التقريبية: نحو
  X نقطة · المتبقي لك: Y نقطة», the cheaper line (`cheaper_note` + «— وأوفّر نحو N نقاط») when present, the buttons in
  `options` order «label · points», hint «أو اكتب طلبك في خانة الرسالة». Frame like the agent-question box.
- Buttons active only on the latest card with no `outcome`, while nothing is streaming; a click sends a normal message
  (`content` = label, `plan_choice` = {run_id, option}) through the existing send path (extend `messagesApi.send`,
  `runSend`, `useSendMessage`); then the buttons lock and show the choice (optimistically, then from `outcome`).
  Outcome shown: chosen option highlighted; typed/expired → «انتهت صلاحية الخطة»; insufficient → «لم يعد رصيدك يكفي».
- `DeepSearchProgress`: when `data.searches` is present show «N بحوث متوازية · اكتمل M» (Latin digits) in the header line;
  store it in the progress state.
- `npm ci` in `frontend/` of the worktree if `node_modules` is missing (NEVER junction/symlink node_modules); `npx tsc
  --noEmit` + `npm run lint` must pass.

### E — mobile (`mobile/`) — after D

Mirror D in the Expo app (mobile/CLAUDE.md rule 10: the app mirrors the web): plan card in `AgentQuestion` /
`AssistantBody`, `plan_choice` through `openMessageStream` / `run-send.ts`, the progress line. Contracts only via `@contracts`.
