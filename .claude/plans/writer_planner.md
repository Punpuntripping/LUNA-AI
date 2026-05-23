# Plan — Writer Planner (pre-writer distillation + plan loop)

> Adds a conversational planner phase **in front of** `agent_writer`. The planner
> examines what the user provided, dispatches a per-item analyzer + (optionally)
> a template search, presents a plan to the user, iterates on feedback, and only
> then hands a full package to the writer to draft the final document.
>
> Mirrors the shape of `agents/deep_search_v4/planner/` (decider + dynamic
> instructions + deferred tools + pause/resume). Reuses every learning from that
> pipeline.

## Goal

`agent_writer` today is a one-shot LLM call that receives a free-form
`user_request` plus router-selected `attached_items`. It works, but:

- The writer reads raw OCR'd attachments without targeted distillation of
  parties / dates / amounts / clauses, and without a notion of which
  attachment is a **template** vs **source data**.
- There is no template scaffolding from the system library.
- Research artifacts (`agent_search` results) are passed verbatim with no
  intent-focused fact-pinning — the writer has to do the relevance work itself
  inside one prompt.
- The user has no chance to shape the strategy before tokens are spent on the
  final draft.

The **writer planner** fixes all four by inserting a *director* phase: it
inspects, distills, presents a plan, accepts feedback, re-plans, and only fires
the writer when the user is satisfied. The writer then receives a rich
**WriterPackage** (plan + role-labeled analyzed items + system templates +
prior draft when revising) and focuses purely on drafting.

## Stage 1 protocol — examine before asking

A real Saudi-lawyer turn often arrives like this (real example):

> 3 attachments — 2 images (offer + commercial registry) + 1 PDF labeled
> «عقد اتعاب سنوي مسودة» — plus text:
> «سويلي بناء على العرض والطلبات والكرف الثاني منابر وبناء على نموذج العقد
> والمبلغ ٤٠٠٠٠الف، ٢٠ الف الان و٢٠الف بعد ٦ شهور، وتاريخ اليوم ١٤٤٧/١/١٨»

Everything the planner needs is already in front of it:

- **subtype**: عقد (contract) — both stated and implied by «نموذج العقد»
- **template**: the PDF labeled «مسودة» — used as scaffold
- **source data**: the two images — offer + registry → parties, scope
- **parameters**: 40K split 20+20 over 6 months, date 1447/1/18

A planner that opens this turn with «ما نوع الوثيقة؟» is broken. The decider's
first action MUST be **inspection**:

1. Parse the user message for stated subtype, parameters, references to
   specific attachments («العرض», «الكرف الثاني», «نموذج العقد»).
2. Read each attached_item's `summary` (and `title`) to infer its role
   (template vs source vs reference). Use `read_workspace_item` if the
   summary isn't decisive.
3. Identify what's missing **after** the inspection.

`ask_user` fires ONLY for genuinely missing facts. In the example above the
planner should go straight to dispatching `item_analyzer` per attachment, skip
`template_search` (user supplied their own), assemble the plan, present once,
and on «موافق» hand off to the writer. **Zero clarification questions on a
clean turn.**

This is encoded in the system prompt + reinforced by the dynamic instructions
that summarize what the decider already sees.

## Tier assignment (hard constraint)

| Agent | Model tier slot | Why |
|---|---|---|
| `writer_planner_decider` | **tier_1** | Same family as the writer — needs strong reasoning to inspect, role-label, dispatch, absorb mixed analyses, and explain itself to the user in Arabic. |
| `agent_writer` | **tier_1** | Unchanged. Final drafting. |
| `item_analyzer` | **tier_2** (deepseek-flash class) | One LLM call per workspace_item — reads full content, distills, extracts metadata. Kind-aware output discriminator. |
| `template_search` | none (no LLM) | pgvector cosine over `system_templates.summary_embedding`. |

"tier_2" here means the **model slot**. The planner's *capability surface* —
seeing all artifact summaries in the conversation — is borrowed from the
deepsearch planner pattern and is unrelated to the model tier.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  WriterPlanner.decider (tier_1)                                         │
│                                                                         │
│  Deps (rendered into dynamic @agent.instructions):                      │
│   • intent (current user message — parsed for stated subtype/params)    │
│   • recent_messages, case_brief                                         │
│   • prior_artifacts[] — ALL convo workspace_items (title+kind+summary)  │
│   • attached_items[] — router-selected for THIS turn                    │
│   • detail_level, tone                                                  │
│                                                                         │
│  Tools:                                                                 │
│   ▸ read_workspace_item(item_id) → content_md       [sync, scoped]      │
│   ▸ analyze_item(item_id, intent, expected_role)    [tier_2 LLM]        │
│   ▸ search_templates(subtype, intent)               [pgvector]          │
│   ▸ ask_user(question)                              [DEFERRED]          │
│   ▸ present_plan_for_approval(plan_md)              [DEFERRED]          │
│                                                                         │
│  Loop: inspect → (maybe ask_user) → dispatch analyses + maybe template  │
│        search → assemble plan → present → user replies → (maybe         │
│        re-dispatch) → re-present → … → user approves → emit             │
│        WriterPackage                                                    │
│                                                                         │
│  Hard cap: 3 present_plan_for_approval cycles per turn.                 │
└─────────────────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │       (parallel tool calls per round)     │
        ▼                                           ▼
  ┌──────────────────┐                  ┌──────────────────────┐
  │ item_analyzer    │                  │ search_templates     │
  │ tier_2 deepseek  │                  │ pgvector cosine over │
  │ ONE call per     │                  │ system_templates     │
  │ workspace_item   │                  │ (NO LLM)             │
  │ KIND-AWARE       │                  │                      │
  └──────────────────┘                  └──────────────────────┘
        │                                           │
        ▼                                           ▼
   ItemAnalysis                              list[TemplateRef]
   (discriminated)                           (may be empty)
                              │
                              ▼ WriterPackage
┌─────────────────────────────────────────────────────────────────────────┐
│  agent_writer (tier_1 — existing)                                       │
│  Drafts the final Arabic document grounded in the package.              │
└─────────────────────────────────────────────────────────────────────────┘
```

## The 2 distillation agents

### `item_analyzer` (tier_2, one LLM call per item)

Lives at `agents/agent_writer/planner/distill/item_analyzer.py`. The planner
calls `analyze_item(item_id, intent, expected_role)` once per workspace_item it
needs to dig into. The agent:

1. Reads the item's full `content_md`.
2. Distills the parts relevant to the user's intent.
3. Extracts the metadata appropriate to the item's kind.
4. Returns a kind-discriminated `ItemAnalysis`.

The system prompt tells the model: «You will be given (a) the user's drafting
intent, (b) the item's kind, (c) an expected_role hint from the planner
(template/source/reference/prior_draft), and (d) the full item content.
Produce the analysis variant that matches the kind.»

Output is a **discriminated union** on `kind`:

```python
class AttachmentAnalysis(BaseModel):
    kind: Literal["attachment"] = "attachment"
    item_id: str
    role: Literal["template", "source", "reference"]   # planner-assigned hint, model may correct
    extracted_metadata: dict[str, Any]                 # parties/dates/amounts/jurisdiction/clauses/...
    drafting_distill_md: str                           # what's useful for THIS draft
    gaps_md: str                                       # what's missing or ambiguous

class ResearchAnalysis(BaseModel):
    kind: Literal["agent_search"] = "agent_search"
    item_id: str
    fact_pins: list[FactPin]                           # supporting_ref_n + claim + snippet
    unused_refs: list[int]                             # refs irrelevant to the intent

class PriorDraftAnalysis(BaseModel):
    kind: Literal["agent_writing"] = "agent_writing"
    item_id: str
    structural_outline_md: str                         # section headings of the prior draft
    revision_targets_md: str                           # what the user is asking to change

class ConvoContextAnalysis(BaseModel):
    kind: Literal["convo_context"] = "convo_context"
    item_id: str
    relevant_facts_md: str

ItemAnalysis = Annotated[
    Union[AttachmentAnalysis, ResearchAnalysis,
          PriorDraftAnalysis, ConvoContextAnalysis],
    Field(discriminator="kind"),
]
```

The planner uses `expected_role` as a hint to focus the model's attention but
the model may correct it (e.g. the planner labels an attachment as `source`
but the analyzer recognizes it's actually a template — returns
`role="template"`).

**Cache**: `WriterPlannerDeps._analysis_cache[item_id]` stores the result so
re-plan rounds don't re-pay for the same item.

### `search_templates` (no LLM)

Lives at `agents/agent_writer/planner/distill/template_search.py`.

- **Input**: `subtype` (mapped to `template_type_enum`) + `intent` string.
- **Process**: embed `intent` via Alibaba text-embedding-v4 → 1024-d query →
  cosine search over `system_templates.summary_embedding` WHERE `type =
  subtype AND deleted_at IS NULL`, LIMIT 5.
- **Output**: `list[TemplateRef(template_id, title, content, summary, cos_sim)]`.

**When the planner skips this entirely**: if any analyzed item came back with
`role="template"` (user supplied their own), the planner does NOT call
`search_templates`. The user's intent overrides the system library.

**v1 graceful no-results**: `system_templates` ships empty. Returns `[]`. The
decider's prompt covers this: «إن لم توجد قوالب، أنشئ هيكلاً مناسباً للنوع
دون الاعتماد على قالب».

### Subtype mapping (English writer subtype → Arabic enum)

```
contract       → عقد
memo           → مذكرة
legal_opinion  → رأي_قانوني
defense_brief  → مذكرة            (closest match — Saudi practice merges)
letter         → إنذار             (or none, depending on intent)
summary        → none              (no template applies)
```

## The planner — phases, tools, loop

### Phase 1 — Decider construction

`agents/agent_writer/planner/agent.py::create_writer_planner_decider()`:

- `model = get_agent_model("writer_planner_decider")` — tier_1 slot.
- `deps_type=WriterPlannerDeps`, `output_type=[PlannerDecision,
  DeferredToolRequests]` (same shape as deepsearch planner).
- `instructions=WRITER_PLANNER_SYSTEM_PROMPT` (static rules including the
  examine-before-asking protocol).
- `@agent.instructions` → `build_writer_planner_instructions(ctx.deps)`
  renders the comprehension surface (recent_messages, prior_artifacts,
  attached_items, intent) into the per-turn prompt.
- 5 tools registered: `read_workspace_item`, `analyze_item`,
  `search_templates`, `ask_user`, `present_plan_for_approval`.

### Phase 2 — The loop

The agent's run loop handles dispatch interleaving naturally:

1. Decider inspects intent + prior_artifacts (summaries) + attached_items.
2. Decider assigns expected_role to each attachment based on user wording
   («بناء على نموذج العقد» → that PDF is the template; «العرض» → that image
   is source).
3. Decider emits parallel tool calls — typically
   `analyze_item(item_id="att-A", intent="...", expected_role="template")` +
   `analyze_item(item_id="att-B", ..., expected_role="source")` +
   `analyze_item(item_id="att-C", ..., expected_role="source")` in one round.
   `search_templates` only fires if no `role="template"` came back.
4. Decider reads analyses, may dispatch more (e.g. analyze a prior_draft it
   only just realized was relevant).
5. Decider emits `present_plan_for_approval(plan_md="...")` → run pauses with
   `DeferredToolRequests`.
6. Orchestrator persists the awaiting_user row, emits an SSE token stream of
   the plan_md to chat, returns control to the user.
7. User replies. Resume: `agent.run(message_history=…,
   deferred_tool_results=DeferredToolResults({tool_call_id: user_reply}))`.
8. Decider reads reply. Either: re-dispatch + re-present, or emit final
   `PlannerDecision(approved=True, package=WriterPackage(...))`.

### Pause/resume — two deferred tools

Both raise `CallDeferred` like the deepsearch `ask_user`. The orchestrator
distinguishes them by **tool name** when persisting the pause row:

| Tool | `pause_reason` column | UI affordance |
|---|---|---|
| `ask_user` | `'clarify'` | Plain Arabic question rendered in chat |
| `present_plan_for_approval` | `'approve_plan'` | Plan_md rendered in chat; user replies in chat |

The existing `awaiting_user` row schema gains one column: `pause_reason
TEXT NOT NULL DEFAULT 'clarify'`. A single row per planner turn — overwritten
on each new pause (only the latest pending question matters for UX).

### Iteration cap — hard at 3

`WriterPlannerDeps` tracks `present_count: int`. On the 4th call to
`present_plan_for_approval` the tool implementation bypasses the deferred raise,
auto-approves with the current plan, and the decider must emit a final
`PlannerDecision` next round. Logged + telemetry-tagged so we can tune the cap
later. Each plan round = 1 tier_1 LLM call + N tier_2 analyses; capping at 3
keeps worst-case cost predictable.

## `WriterPackage` — the planner's payload to the writer

```python
class AnalyzedItem(BaseModel):
    """One workspace_item the planner inspected, with its assigned role."""
    item_id: str
    title: str
    kind: str                         # original workspace_items.kind
    role: Literal["template", "source", "reference", "prior_draft"]
    analysis: ItemAnalysis            # the discriminated union above

class WriterPackage(BaseModel):
    intent_ar: str                    # one-paragraph distilled intent
    subtype: WriterSubtype
    edit_mode: Literal["fresh", "revise", "instruct"]
    plan_md: str                      # the user-approved plan
    analyzed_items: list[AnalyzedItem]      # everything the planner inspected
    system_templates: list[TemplateRef]     # from search_templates (may be empty)
    style: WriterStyle                       # detail_level + tone

    # Convenience views — derived properties, not separate fields:
    # - templates(): user-attached templates filtered from analyzed_items + system_templates
    # - sources(): analyzed_items with role='source'
    # - prior_draft(): analyzed_items with role='prior_draft' (at most one)
    # - fact_pins(): flattened from ResearchAnalysis variants in analyzed_items
```

The writer's user message renders the package as XML blocks:

```
<plan>...</plan>
<templates>
  <template source="user" item_id="..." title="...">{full content}</template>
  <template source="system" template_id="..." type="عقد">{full content}</template>
</templates>
<sources>
  <source item_id="..." kind="attachment">
    <metadata>{parties, dates, amounts, ...}</metadata>
    <distill>{drafting_distill_md}</distill>
    <gaps>{gaps_md}</gaps>
  </source>
</sources>
<research>
  <pin ref="3" item_id="...">claim — snippet</pin>
</research>
<prior_draft>
  <outline>{structural_outline_md}</outline>
  <revisions>{revision_targets_md}</revisions>
  <body>{full content_md of the prior draft}</body>
</prior_draft>
<user_request>{intent_ar}</user_request>
<preferences detail_level="..." tone="..." />
```

## Role assignment

The planner labels each analyzed item with one of four roles:

| Role | Meaning | Writer behavior |
|---|---|---|
| `template` | Scaffolding to mimic — structure, boilerplate, clauses | Adopt structure; fill in parties/dates/amounts from sources |
| `source` | Raw facts the document is about — parties, terms, contracts being summarized | Quote / cite / fold into prose |
| `reference` | Background that may be cited but isn't the subject | Cite when relevant, otherwise ignore |
| `prior_draft` | The agent_writing being revised | Treat as the starting point; apply revision_targets |

The planner assigns roles in two passes:

1. **Pre-dispatch hint**: based on user wording and titles, the decider passes
   `expected_role` to `analyze_item`.
2. **Post-dispatch correction**: the analyzer may return a different `role` in
   its `AttachmentAnalysis` if the content clearly says otherwise. The planner
   accepts the analyzer's correction.

`AnalyzedItem.role` in the WriterPackage is the **final** role after correction.

## Edit routing

The decider decides the dispatch set itself based on the intent + edit_mode:

| Scenario | edit_mode | Typical dispatch |
|---|---|---|
| Fresh draft with user-attached template + 2 source attachments | `"fresh"` | `analyze_item × 3` (1 template + 2 sources), NO search_templates |
| Fresh draft with sources but no template | `"fresh"` | `analyze_item × N` (sources) + `search_templates(subtype, intent)` |
| Tone tweak on prior draft | `"instruct"` | `analyze_item × 1` on the prior_draft (to extract revision_targets) only |
| New attachment dropped, revise existing draft | `"revise"` | `analyze_item` on new attachment + `analyze_item` on prior_draft |

`skip_distill_for_instruct_edits` is implicit: when the decider emits no
dispatch calls and the plan_md is trivial, the loop short-circuits past
`present_plan_for_approval` and goes straight to writer. (Configurable via a
prompt rule, not a code flag.)

## Templates — graceful no-results (v1)

`system_templates` table exists per migration 046 but has zero ingested rows.
v1 ships with this state. `search_templates` returns `[]`. The decider's prompt
covers the no-template path explicitly. Ingestion is a **separate follow-up
plan**.

## File manifest

### NEW

```
agents/agent_writer/planner/
  __init__.py
  agent.py                     ← create_writer_planner_decider()
  prompts.py                   ← WRITER_PLANNER_SYSTEM_PROMPT +
                                  build_writer_planner_instructions() +
                                  examine-before-asking protocol rules
  deps.py                      ← WriterPlannerDeps + build_writer_planner_deps()
  models.py                    ← PlannerDecision, WriterPackage, AnalyzedItem,
                                  ItemAnalysis (discriminated union),
                                  AttachmentAnalysis, ResearchAnalysis,
                                  PriorDraftAnalysis, ConvoContextAnalysis,
                                  TemplateRef, FactPin, WriterStyle
  runner.py                    ← handle_writer_planner_turn(major_input, supabase) →
                                  internally awaits handle_writer_turn(package, deps)
  tools.py                     ← The 5 @agent.tool definitions (kept out of
                                  agent.py to keep the factory readable)
  distill/
    __init__.py
    item_analyzer.py           ← create_item_analyzer() + run_item_analysis()
                                  Tier_2 agent with the discriminated-output system prompt
    template_search.py         ← async def search_templates(supabase, subtype, intent)
                                  → list[TemplateRef]  — NO LLM, pgvector only
  tests/
    __init__.py
    test_decider.py            ← Pydantic AI TestModel + FunctionModel coverage
    test_item_analyzer.py      ← Per-kind variant coverage (attachment / agent_search /
                                  agent_writing / convo_context)
    test_template_search.py    ← pgvector mocked + graceful empty path
    test_runner.py             ← end-to-end with stubbed dispatch tools
    test_loop_iteration_cap.py ← exercises the 3-round cap
    test_examine_before_asking.py ← real-world examples (the contract example)
                                  must NOT call ask_user

backend/app/services/
  writer_planner_context.py    ← load_writer_planner_context(supabase, user_id,
                                  conversation_id) → ArtifactSummaryView list

shared/db/migrations/
  047_awaiting_user_pause_reason.sql ← ALTER TABLE awaiting_user ADD COLUMN
                                        pause_reason TEXT NOT NULL DEFAULT 'clarify'
```

### MODIFIED

```
agents/agent_writer/models.py
  + WriterPackage class
  + WriterInput.from_package(package: WriterPackage, ...) helper

agents/agent_writer/prompts.py
  + build_writer_user_message_from_package(package) — XML render described above
  (existing build_writer_user_message stays for legacy callers / unit tests)

agents/agent_writer/runner.py
  + handle_writer_turn() accepts either WriterInput OR WriterPackage; when
    given a package, calls _populate_deps_from_package + the new message builder.
  No behavior change for existing callers.

agents/orchestrator.py
  ~ _run_writer() — replaced body with `return await handle_writer_planner_turn(
    major_input, supabase)`. The planner internally calls the writer.
  ~ pause-handling branch extended to also catch planner pauses from the
    writing family. _record_deferred takes pause_reason kwarg.

agents/utils/agent_models.py
  + add slots: "writer_planner_decider" (tier_1), "item_analyzer" (tier_2).
    template_search has no slot (no LLM).

backend/app/services/  (existing awaiting_user persistence)
  ~ extend the awaiting_user write to set pause_reason
  ~ SSE event for present_plan_for_approval — same shape as ask_user today,
    but event_type: "plan_presented" so the frontend renders plan_md as a
    styled markdown block (RTL, ## headings supported).
```

## Orchestrator wiring change

`agents/orchestrator.py::_run_writer` becomes a one-liner:

```python
async def _run_writer(
    input: MajorAgentInput,
    subtype: str | None,
    supabase: SupabaseClient,
) -> SpecialistResult:
    from agents.agent_writer.planner import handle_writer_planner_turn
    return await handle_writer_planner_turn(input, subtype, supabase)
```

The pause-handling branch (currently keyed on `ds_outcome.kind == "paused"` for
`deep_search`) extends to also catch planner pauses from the writing family.

## Test plan

| Test | Covers |
|---|---|
| `test_decider.py::test_decides_dispatch_for_fresh_draft` | Decider with TestModel returns expected dispatch set for a fresh contract intent + 2 attachments + 1 research item |
| `test_decider.py::test_pauses_with_present_plan_for_approval` | FunctionModel raises CallDeferred on the gate tool; runner returns DeferredToolRequests |
| `test_decider.py::test_resumes_with_approval` | message_history + DeferredToolResults("موافق") resumes and emits final PlannerDecision |
| `test_decider.py::test_resumes_with_edit_feedback` | User reply rejecting the plan triggers re-dispatch, then re-present |
| `test_decider.py::test_skips_search_templates_when_user_attaches_one` | One attachment comes back with role="template" → search_templates is NOT called |
| `test_item_analyzer.py::test_attachment_variant_extracts_metadata` | Real Arabic contract sample → AttachmentAnalysis with parties/dates/amounts + drafting_distill |
| `test_item_analyzer.py::test_research_variant_pins_relevant_refs` | agent_search item with 5 refs → ResearchAnalysis with FactPins for the 2 relevant ones |
| `test_item_analyzer.py::test_prior_draft_variant_extracts_outline` | agent_writing item → PriorDraftAnalysis with structural outline |
| `test_item_analyzer.py::test_corrects_planner_role_hint` | Planner hints role="source", model recognizes template → returns role="template" |
| `test_template_search.py::test_returns_empty_when_no_rows` | v1 graceful path |
| `test_template_search.py::test_pgvector_cosine_with_type_filter` | Mocked pgvector returns top-5 ordered |
| `test_runner.py::test_handle_writer_planner_turn_end_to_end` | Stubbed tools, real PlannerDecision → WriterPackage → handle_writer_turn called with package |
| `test_loop_iteration_cap.py::test_caps_at_3_present_cycles` | 4th call to present_plan_for_approval auto-approves |
| `test_runner.py::test_skip_distill_for_instruct_edit` | "اجعل النبرة أرسم" → empty dispatch, no present_plan call, writer fires |
| `test_examine_before_asking.py::test_contract_example_no_clarify` | The real example from the design discussion (PDF template + 2 image sources + explicit parameters) must NOT trigger ask_user — decider goes straight to dispatch + present |

Existing `agent_writer/tests/test_runner.py` continues to pass — the writer's
input contract (WriterInput) is unchanged, only a new constructor path
(WriterPackage) is added.

## Out of scope (deferred follow-ups)

1. **`system_templates` ingestion** — separate plan: curate 5-10 templates per
   `template_type_enum` value, embed via Alibaba text-embedding-v4, INSERT via
   service_role script. Until then, planner runs with empty template_search
   results.
2. **User-authored templates** — referenced in migration 046's comment as v2 /
   Rayhan.
3. **Workspace-pane plan artifact** — for now plan_md lives in chat only. If we
   want pin/recall, add a `kind='writer_plan'` workspace_items mirror later.
4. **Inline-span surgical edits** ("change only this paragraph") — out of v1.
5. **Cost tuning** — first measurement after first 20 real runs decides
   whether to keep item_analyzer at tier_2 or split it back into specialized
   agents when latency / quality warrants.

## Dependencies

- Migration 046 (`system_templates`) — already drafted; verify applied state
  with `mcp__supabase__list_migrations` before merging.
- Migration 047 (`awaiting_user_pause_reason`) — included in this plan's manifest.
- `agents/utils/agent_models.py` — `get_agent_model` slot registration extended.

## Build order

1. Migration 047 (`pause_reason` column) — unblocks orchestrator pause-row write.
2. `WriterPackage` + `AnalyzedItem` + `ItemAnalysis` union + `WriterInput.from_package`
   + new `build_writer_user_message_from_package` in `agents/agent_writer/` —
   writer accepts the new shape, behavior unchanged for existing callers. Tests
   for the new path.
3. `item_analyzer` + `template_search` — each independently testable.
4. `WriterPlannerDeps` + `build_writer_planner_instructions` + the static system
   prompt (including the examine-before-asking protocol) + the 5 tools.
5. `create_writer_planner_decider()` + `handle_writer_planner_turn()`.
6. Orchestrator wiring + pause-row extension + SSE event for plan presentation.
7. End-to-end loop tests, including the examine-before-asking regression test.
