# Writer Planner — User Templates (قوالبي) + Resume Wiring

**Status:** BUILT 2026-06-06 (all waves) — NOT deployed; migration 064 written but NOT yet applied to prod.
**Supersedes the system_templates path in:** `.claude/plans/writer_planner.md` § Templates
**Depends on:** writing-family pause/resume fix — **DONE 2026-06-06** (see Wave 0)

> ### Build status (2026-06-06)
> - **Wave 0** resume wiring — DONE (orchestrator.py, 3 routing tests pass).
> - **Wave A** kill system_templates — DONE (migration `064`, `search_templates`/distill removed, `WriterPackage.system_templates`→`templates`, renderer `source="library"`).
> - **Wave B** قوالبي titles injected as `<my_templates>` (TPL-{n}) + `chosen_template` pick + runner fetch→TemplateRef — DONE.
> - **Wave C** in-plan disambiguation + D11 naming — DONE (prompt rules + present_plan docstring).
> - **Wave D** `agents/memory/template_ingester/` (tier_2) — DONE (9 tests pass).
> - **Wave E** offer chip + `POST /api/v1/templates/ingest` + frontend — DONE (tsc/lint clean; contract verified end-to-end).
> - Tests: writer + writer_planner + ingester = 71 passed, 1 pre-existing fail (`test_verdict_walk::test_partial_refs`, stale `agent_writer` literal — not this change).
> - **Remaining:** apply migration 064 to prod (drops EMPTY `system_templates`, 0 rows) + deploy + smoke test.

ChatGPT-style template support for the writer pipeline, scoped to the user's
personal **قوالبي** library only. The system-wide `system_templates` corpus is
deleted. The planner gains the ability to (1) draft *from* one of the user's
templates, (2) **disambiguate the choice inside the approval plan** when several
match, and (3) **offer to save** an attached document as a reusable template,
which a new ingestion agent cleans + titles.

---

## 0. Context & the resume finding (read first)

While scoping this we discovered the writer_planner's **pause→resume is not
wired**. The planner can pause (`ask_user` / `present_plan_for_approval` write a
`paused_runs` row), but on the user's reply the orchestrator hits the guard in
`agents/orchestrator.py::_resume_major_agent_inner` (~L411):

```python
if agent_family != "deep_search":
    ... "does not support resume; abandoning run_id=%s" ...
    resolve_pause(...)
    async for ev in _route(question=user_reply, ...): yield ev   # re-route COLD
    return
```

So a paused writing turn's reply is **abandoned and re-routed through the
router**, which re-plans amnesiacally. Production telemetry (convo
`391aec61-803d-467b-9628-55ab087fa99b`) confirms it: every writing turn restarts
with a fresh `router.classify` (spans 18/22/27), and on the reply «فصل اكثر» the
router reasoned it *couldn't see the paused draft* and created a **new**
document. The multi-turn plan→refine→approve loop is therefore degraded today,
and `item_analyzer` is re-paid every round.

**Decision:** this feature's "choose the template within the plan" requirement
relies on `present_plan_for_approval` actually resuming. So we fixed resume for
real (not the non-blocking-chip workaround).

**Root cause (verified via `git log -S`, 2026-06-06): writer resume was NEVER
wired — not a regression.** The `only deep_search` guard is *original* to commit
`a265f04` (2026-05-02), the commit that first built pause/resume. The
`agent_runs`→`paused_runs` migration (`36d44b8`, 2026-06-03) moved the guard
verbatim; it didn't introduce or break it. The writer runner
`handle_writer_planner_turn` was built resume-ready in the wave-9 redesign, but
the orchestrator seam was never connected. (Original hypothesis — the agent_runs
drop — was wrong.)

---

## Locked decisions (from reflection 2026-06-06)

| # | Decision |
|---|----------|
| D1 | **Delete `system_templates` entirely** — drop the table + `search_system_templates` RPC; delete the `search_templates` tool + `distill/template_search.py`. |
| D2 | **قوالبي titles = passive injected context (NOT a tool).** The user's active template titles are loaded each turn and rendered into the planner's dynamic instructions as a `<my_templates>` block — exactly like `<attached_items>`/`<prior_artifacts>` summaries. Titles only, no embedding, no semantic search, no "dig" tool call. The planner reads them and picks. |
| D3 | **Inject via the existing path.** Rename `WriterPackage.system_templates` → `templates` (`list[TemplateRef]`); make `TemplateRef.template_type` + `.score` optional. Runner fetches the chosen row's `content_md` → `TemplateRef` → `package.templates`. Executor's `<templates>` renderer gets a `source="library"` branch. |
| D4 | **Candidates = attached docs this turn only.** |
| D5 | **Disambiguation lives in the plan.** If ≥2 قوالبي templates plausibly match and the user did not name one, the planner uses `present_plan_for_approval` to list them and the user picks one (requires Wave 0). |
| D6 | **Save offer = non-blocking** SSE chip shown at end of the writing turn (after publish). No pause. |
| D7 | **Ingestion = new agentic Layer-4 Memory transformer, tier_2 (deepseek)**, living under `agents/memory/` beside `item_analyzer` (system-side content transform, no user talk). Cleans concrete names/dates/amounts into **bracketed Arabic placeholders** («[اسم المستأجر]»), fixes spelling, writes a **unique descriptive title** («نموذج عقد إيجار لعمارة سكنية»). |
| D11 | **Plan names the template.** Whenever the planner invokes `present_plan_for_approval` (for any reason) and a قوالبي template will be used, the plan_md must name it in `## المرجع` («القالب: <title>») so the user sees which template before approving. On clean no-plan turns the template is used silently. |
| D8 | **Click «نعم» → dedicated endpoint → ingester pipeline directly** (no router/planner/executor). Any failure → «فشل حفظ القالب، يمكنك حفظه يدويًا من خلال قوالبي». |
| D9 | **Scenario D (router saves a template directly) = out of scope** (unlikely per user). |
| D10 | Precedence: attached `role='template'` WI  >  chosen قوالبي template  >  no template. |

### Resolved (2026-06-06)
- **A1 — LOCKED:** 1 candidate → use it directly; **≥2 → ask in the plan**
  («أستخدم A أم B؟»). No special-casing beyond the count.
- **A2 — LOCKED (default):** the planner always *sees* the قوالبي titles in its
  `<my_templates>` context block. A simple prompt rule sets precedence: if the
  user **attached a template this turn**, use that and ignore the saved list (no
  pick, no «A أم B»). Only when no template is attached does it choose from
  `<my_templates>`. (= D10 precedence; no tool, no "dig".)
- **A3 — LOCKED:** Scenario C (draft AND save in one turn) is **dropped**. A
  "draft and save" request is handled as Scenario B: draft now, show the save
  chip, save on click. **Wave F removed.**

---

## Wave 0 — Wire writing-family pause/resume  *(DONE 2026-06-06)*

Implemented in `agents/orchestrator.py` (+139 lines). What landed:

- Guard relaxed: `if agent_family not in ("deep_search", "writing")` — writing no
  longer abandons + re-routes.
- New `writing` branch in `_resume_major_agent_inner`: rehydrates `message_history`
  (bytes) + `DeferredToolResults({tool_call_id: user_reply})` (shared with
  deep_search), rebuilds `MajorAgentInput` (attached_items + recent_messages +
  `describe_query=user_reply`), and resumes via `_run_writer(..., message_history=,
  deferred_results=)`.
- `_run_writer` + the planner runner already supported the resume kwargs; the
  call now forwards them.
- Result handling mirrors fresh dispatch: `completed` → stream `SpecialistResult`
  (sse_events + chat_summary + key_findings) then `resolve_pause`; `paused` (2nd
  present round) → re-record a NEW pause row under a **fresh `run_id`** (avoids the
  `paused_runs.run_id` PK collision the agent found), resolve the old leg, keep the
  run alive. `CancelledError` re-raised; Arabic error fallback on exception.
- Pause-WRITE side needed no change — it already stores serialized
  `message_history` + `deferred_payload.tool_call_id` identically to deep_search.

**Verification:** `agents/writer_planner/tests/test_resume_routing.py` — 3 passed
(writer-invoked-not-router; chained pause re-records with fresh id; memory still
abandons→router). `writer_planner` + deep_search `test_planner_pause` suites:
51 passed. Report: `agents_reports/resume_fix_2026-06-06.md`.

> Note: `.gitignore` ignores `tests/` and `agents_reports/`, so the new test +
> report live locally but are untracked (repo-wide convention).
>
> **Pre-existing, unrelated red** (NOT caused by this fix; flag for a separate
> cleanup): `agents/.../test_cost_ledger.py` collection error (imports removed
> symbol `tier_of_subagent`); `test_verdict_walk.py::test_partial_refs...` fails on
> a clean baseline.

---

## Wave A — Kill `system_templates`

**DB**
- New migration `0XX_drop_system_templates.sql`: `DROP` the `search_system_templates`
  function/RPC, then `DROP TABLE public.system_templates` (idempotent guards).
  Leave `template_type_enum` only if nothing else references it; otherwise drop too.

**Code removal / rename**
- `agents/writer_planner/tools.py` — remove the `search_templates` tool (#2) and its imports.
- Delete `agents/writer_planner/distill/template_search.py` (and `distill/__init__.py`
  if now empty) + `tests/test_template_search.py`.
- `agents/writer/models.py` — rename `WriterPackage.system_templates` → `templates`;
  make `TemplateRef.template_type: str | None` and `TemplateRef.score: float | None`
  optional; update `TemplateRef` docstring (now a قوالبي row, not a system row).
  Update `_from_package` (drops `templates` mention).
- `agents/writer/prompts.py` (~L413–435) — `<templates>` renderer reads
  `package.templates`; emit `source="library"`; tolerate missing `type`.
- `agents/writer_planner/prompts.py` — remove `search_templates` / "system library"
  language from the static system prompt + the tools table.
- Grep for any remaining `system_templates` references and clean.

**Exit:** project builds; `npx tsc`/pytest green for touched modules; no live reads of `system_templates`.

---

## Wave B — Inject قوالبي titles as context + pick one into the draft (Scenario A)

**No tool.** قوالبي titles ride in the planner's context, passively (D2), with a
`TPL-{n}` alias per title — mirroring the `WI-{seq}` alias discipline so the LLM
never emits a raw UUID.

**Context loader + deps**
- New loader (e.g. `backend/app/services/writer_planner_context.load_user_template_titles`
  or an agent-side helper) → active `user_templates` rows for the user, **titles +
  template_id only**, scoped by `user_id` (service-role + user_id filter, same
  discipline as `add_user_template`). Cap at ~50 most-recent titles (safety valve;
  current users have ≤handful).
- `agents/writer_planner/deps.py::WriterPlannerDeps` — add
  `user_templates: list[UserTemplateTitle]` (template_id + title) and
  `tpl_alias_map: dict[int, str]` (`{n: template_id}`), built in
  `build_writer_planner_deps` alongside `wi_alias_map`. Add a
  `resolve_tpl_alias("TPL-3") -> template_id | None` helper.
- Hydrate in `runner.py::_build_writer_planner_deps_from_input` (fresh AND resume,
  like prior_artifacts).

**Prompt render**
- `agents/writer_planner/prompts.py::build_writer_planner_instructions` — add a
  `<my_templates>` block: one `TPL-{n} | title` line per row (titles only, no body).
  Add a "# قوالبي" section to the static prompt: precedence rule (A2 — attached
  template wins) + disambiguation rule (A1/Wave C — ≥2 plausible → ask in plan).

**Decision model**
- `agents/writer_planner/models.py::PlannerDecision` — add
  `chosen_template: str | None = None` — a `TPL-{n}` alias (NOT a raw id). Include
  its presence in `tracking_output()`. (Wave D/E add `offer_save`/`offer_item_id`.)

**Runner injection**
- `agents/writer_planner/runner.py` — resolve `decision.chosen_template` →
  template_id via `deps.resolve_tpl_alias`; in `_build_package_from_decision` fetch
  that row's `content_md`+`title`, build a `TemplateRef` (`template_type=None`,
  `score=None`), pass as `templates=[ref]`. Honor A2/D10: if an attached
  `role='template'` item is present, ignore `chosen_template`.

**Exit:** the planner sees قوالبي titles in `<my_templates>`, picks one by
`TPL-{n}`, and the executor renders it under `<templates source="library">`.

---

## Wave C — Disambiguation inside the plan  *(needs Wave 0)*

- Prompt rule in `writer_planner/prompts.py` (reads the `<my_templates>` block):
  - If the user **named** a template explicitly → use it (`chosen_template`=`TPL-{n}`), no ask.
  - If **≥2** قوالبي titles plausibly match the subtype/intent → call
    `present_plan_for_approval` with a plan_md that lists the candidate titles and
    asks the user to choose ONE («اختر القالب: ١) عقد إيجار التميمي ٢) عقد إيجار الخير»).
  - On resume, the `<my_templates>` block is re-rendered (deps rebuilt fresh — same
    `TPL-{n}` ordering), so the planner re-reads the titles, maps the user's reply →
    the matching `TPL-{n}`, and sets `chosen_template`.
  - Single match (A1) → use directly.
  - **Always-name rule (D11):** any time `present_plan_for_approval` is invoked and
    a قوالبي template will be used, the plan_md names it under `## المرجع`
    («القالب: <title>») — even when there's only one and no disambiguation is needed.
- No new tool needed — reuses `present_plan_for_approval` (now resumable via Wave 0).

**Exit:** with 2 matching templates, the planner pauses with the choice in the
plan; the user's pick resumes the planner and pins `chosen_template_id`.

---

## Wave D — Template ingester agent (new package under memory)

`agents/memory/template_ingester/` — **Layer-4 Memory**, tier_2 (deepseek),
agentic. Co-located with `agents/memory/item_analyzer/` (same family: system-side
content transform, reads `content_md`, no user talk).

- `models.py` — `IngestInput(item_id, user_id)`, `IngestResult(template_id, title, ok, error_ar)`,
  and the LLM output schema `CleanedTemplate(title, content_md)`.
- `prompts.py` — system prompt: input is one raw legal doc's `content_md`; output a
  reusable template: replace concrete parties/dates/amounts with bracketed Arabic
  placeholders, fix spelling, produce a **specific unique title**. Arabic-first.
- `agent.py` — `get_agent_model("template_ingester")` (register the slot in
  `agents/utils/agent_models.py` as tier_2), `output_type=CleanedTemplate`.
- `deps.py` — `supabase`, `http_client`, `user_id`.
- `runner.py` — `handle_template_ingestion(item_id, deps) -> IngestResult`:
  1. fetch `workspace_items.content_md` for `item_id` (scoped to user).
  2. run the LLM → `CleanedTemplate`.
  3. INSERT `user_templates` (reuse `add_user_template`'s insert primitive;
     `created_by='agent'`). 
  4. any failure → `IngestResult(ok=False, error_ar="فشل حفظ القالب، يمكنك حفظه يدويًا من خلال قوالبي")`.
  - `track_stage("template.ingest", ...)` for telemetry/cost (self-emits to `llm_calls`).
- `agent_models.py` — add `template_ingester` slot (tier_2).
- Tests: TestModel/FunctionModel happy path + failure → Arabic error.

**Exit:** `handle_template_ingestion` turns a raw attached doc into a cleaned,
placeholder'd, well-titled `user_templates` row, or returns the Arabic failure.

---

## Wave E — Proactive offer + ingest endpoint + frontend chip

**Planner side (offer decision)**
- `PlannerDecision` — add `offer_save: bool = False` + `offer_item_id: str | None`
  (the attached WI to offer). Prompt: set when an attached doc looks template-worthy
  AND the user didn't already ask to save. Non-blocking — no pause.

**Runner emits the offer (after publish)**
- `agents/writer_planner/runner.py` step 8 — after `publish_writer_result` succeeds,
  if `decision.offer_save`, append an SSE event to `SpecialistResult.sse_events`:
  `{"type": "template_save_offer", "item_id": <offer_item_id>, "title_hint": <attached title>}`.
  Only on successful publish.

**Backend endpoint**
- `backend/app/api/templates.py` — `POST /api/templates/ingest` `{item_id}` → auth →
  `handle_template_ingestion(item_id, deps)`; return `{ok, template_id, title}` or
  `{ok:false, error}` (Arabic). Thin wrapper in `templates_service` if needed.
- `backend/app/errors.py` — reuse `TEMPLATE_FAILED` for the failure code.

**Frontend**
- Render `template_save_offer` as an inline chip in the assistant chat message:
  «💾 احفظ المرفق كقالب؟ [نعم]». Disable after one click (no double-insert).
- Click → `POST /api/templates/ingest {item_id}` → chip states in place:
  «جاري الحفظ…» → «✓ تم حفظ القالب «…»» (refresh قوالبي list) or
  «فشل حفظ القالب، يمكنك حفظه يدويًا من خلال قوالبي».
- Wire into the SSE event handler + the templates query (`use-templates` hook / store).

**Exit:** after a writing turn with a template-worthy attachment, the chip appears;
clicking it ingests via the dedicated endpoint (no router/planner), with in-place
success/failure states.

---

## Wave F — ~~Scenario C~~ DROPPED (A3)

A "draft and save" request is handled as Scenario B (Wave E): the planner drafts,
then surfaces the save-offer chip; the user saves on click. No same-turn
dual-dispatch, no `ingest_template` field on `PlannerDecision`.

---

## File manifest (net)

**New:** `agents/memory/template_ingester/` (models, prompts, agent, deps, runner, tests),
migration `0XX_drop_system_templates.sql`, backend route `POST /api/templates/ingest`,
قوالبي-titles context loader, frontend chip + handler,
`agents_reports/resume_fix_2026-06-06.md` (Wave 0).

**Modified:** `agents/orchestrator.py` (Wave 0),
`agents/writer_planner/{tools,models,runner,prompts,deps}.py`
(tools.py = *remove* `search_templates` only; deps.py = `user_templates` +
`tpl_alias_map`), `agents/writer/{models,prompts}.py`, `agents/utils/agent_models.py`,
`backend/app/api/templates.py` (+ maybe `templates_service.py`, `errors.py`),
frontend SSE handler + templates hook/store.

**Deleted:** `agents/writer_planner/distill/template_search.py` (+ test),
`list_user_templates` tool (never built — replaced by passive context), `system_templates` table + RPC.

---

## Success criteria

1. `system_templates` is gone (DB + code); writing pipeline unaffected.
2. Planner lists قوالبي titles, picks one, executor drafts from it (`source="library"`).
3. With ≥2 matches, the planner asks **inside the plan** and the user's pick
   resumes the planner correctly (no cold re-route; Wave 0 verified via telemetry).
4. After a writing turn with a template-worthy attachment, the «احفظ كقالب» chip
   appears; clicking ingests via the dedicated endpoint into a cleaned, placeholder'd,
   uniquely-titled `user_templates` row; failures show the Arabic fallback message.
5. Ingestion cost lands in `llm_calls` under `template.ingest` (tier_2).
