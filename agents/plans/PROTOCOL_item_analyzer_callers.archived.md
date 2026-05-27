> **SUPERSEDED** by `.claude/plans/item_analyzer_v2.md` (2026-05-25).
> v2 replaces the per-caller `EXTRAS_TAGS` + 4 caller-family output schemas
> with a single `AnalyzerCall(query, targeted_wi)` interface and per-caller
> prompt dirs. See v2 § 15 for the change rationale.

# Protocol — How callers invoke the `item_analyzer` family

> Companion to `INITIAL_item_analyzer_family.md`. That document defines what
> the family **is**; this document defines how each upstream agent should
> **call** it. Read this if you are the author of `router`, `writer_planner`,
> `deep_search_planner`, or any future caller proposing to use the family.
>
> **Audience**: agent authors. **Status**: contract (PRs that violate this
> protocol will be reverted unless they also update this file).
>
> **Companion files**
> - `.claude/plans/item_analyzer.md` — family spec (7 sub-agents)
> - `.claude/plans/item_analyzer_request_builder.md` — dispatcher spec
> - `agents/plans/INITIAL_item_analyzer_family.md` — enriched implementation plan

---

## 0. The one-line rule

> **Callers describe a request; they do NOT pick a sub-agent.** The request
> builder is the only code that maps `(caller_id, mode, target_kind)` → one
> of the 7 sub-agents. Every caller's job is to build a well-typed
> `AnalyzerCall`, hand it to the runner, and consume the returned
> `AnalyzeResult` or `EditResult`.

If your caller code mentions `analyze.search` or `edit.writer` by name,
you've drifted from the protocol. Use `mode` + `targets` + `extras` only.

---

## 1. Universal call surface — what every caller builds

```python
from agents.memory.item_analyzer import (
    analyze, edit,                                # runners
    AnalyzerCall, SpecificTarget, GroupTarget,    # call shape
    RouterAnalyzeExtras, RouterEditExtras,        # extras subtypes
    WriterPlannerAnalyzeExtras,
    DeepSearchPlannerAnalyzeExtras,
    AnalyzerDeps, build_analyzer_deps,            # deps factory
    AnalyzeResult, EditResult,                    # return shapes
    AnalyzerCallError, ItemNotFoundError,         # exceptions
)
```

Every call is two stages:

```python
# 1. Build deps. Edit needs user_emit; analyze passes user_emit=None.
deps = build_analyzer_deps(
    supabase=ctx.deps.supabase,
    http_client=ctx.deps.http_client,
    user_id=ctx.deps.user_id,
    conversation_id=ctx.deps.conversation_id,
    tier=<caller's tier>,                  # 'tier_1' or 'tier_2'
    user_emit=<sse emitter or None>,       # NON-None ONLY in router-edit
)

# 2. Build the call. Extras is a typed subclass — NEVER a raw dict.
call = AnalyzerCall(
    caller_id=<your caller id>,
    mode=<'analyze' | 'edit'>,
    targets=[SpecificTarget(item_id=...) | GroupTarget(...)],
    instruction="<the question or the edit ask, in Arabic>",
    tier=<same tier as deps.tier>,
    user_id=ctx.deps.user_id,
    conversation_id=ctx.deps.conversation_id,
    extras=<typed extras subclass>,
    idempotency_key=<str if edit, None otherwise>,
)

# 3. Dispatch through the runner.
result = await analyze(call, deps)   # or await edit(call, deps)
```

### 1.1 Universal invariants every caller MUST satisfy

| Invariant | Why |
|---|---|
| `call.tier == deps.tier` | The runner passes `deps.tier` into `get_agent_model(slot, tier_override=...)`. Mismatched values are a programmer bug — assert in your caller. |
| `extras.caller_id == call.caller_id` and `extras.mode == call.mode` | Enforced by the call's `model_validator`. If you build a `RouterEditExtras` and set `mode='analyze'`, Pydantic raises. |
| `mode == 'edit'` → exactly one `SpecificTarget`, no `GroupTarget` | Edit mutates one WI per call. Builder rejects violations. |
| `mode == 'edit'` → resolved kind != `attachment` | Attachments are immutable. Builder rejects post-resolution. |
| `mode == 'analyze'` → all targets share one kind after resolution | Callers must batch per-kind. If you have 3 notes + 2 searches, that's two calls. |
| All Arabic-facing strings actually in Arabic | `instruction`, every `extras` string field, every emission. Project rule. |
| Never import `_KIND_DB_TO_PY`, the dispatch table, or any sub-agent factory directly | Those are private to `agents/memory/item_analyzer/`. Public API is the runners + models. |

### 1.2 Universal failure handling

| Exception | When | What you do |
|---|---|---|
| `AnalyzerCallError` | Builder rejected the shape (bad extras, bad target, missing scope param). | Programmer error. Log + re-raise. Do NOT surface to user — your caller had bad inputs. |
| `ItemNotFoundError` | Edit target unresolvable (RLS, deleted, wrong scope). | Surface a user-facing Arabic message OR fall back to a different action depending on caller. See per-caller sections. |
| `AnalyzeResult(items=[], llm_invoked=False)` | Analyze short-circuited (zero items resolved). | Treat as "nothing to do." No LLM cost was billed. Continue your flow. |
| `AnalyzeResult(fallback_used=True)` with truncated payloads | Both primary + fallback LLM failed. | Items contain truncated raw views. Use what you can; flag in telemetry. |
| `EditResult(success=False, fallback_used=True)` | Both LLMs failed in edit. **No version row, no content change.** Apology already emitted to user via `user_emit`. | Stop the flow. Do NOT retry without a fresh user turn. |
| `EditResult(success=True, no_change=True)` | Editor judged the edit unnecessary or unsafe. Polite acknowledgment already emitted. | Stop the flow. The WI is unchanged. |

---

## 2. Per-caller protocol

### 2.1 `router` (Layer 1 Conductor, runs on `tier_2` today)

**When the router calls item_analyzer**

| Trigger | Mode | Frequency |
|---|---|---|
| User says «عدّل …», «صحح …», «اضف …», «احذف من …» referencing a specific WI in the workspace | `edit` | **Primary** path. |
| User asks a quick "what does the third clause say?" against a specific WI without invoking the full writer/search loop | `analyze` | **Rare**. Most analyze calls come from planners, not the router. |
| User explicitly references multiple WIs to edit in one turn | (n/a) | The router must turn this into **N sequential edits**, one per WI. Never a batch — edit is single-target. |

**Which sub-agents the router touches (via the dispatcher)**

| `(mode, kind)` | Sub-agent dispatched | Used by router? |
|---|---|---|
| `(edit, notes)` | `edit.notes` | Yes |
| `(edit, agent_search)` | `edit.search` | Yes |
| `(edit, agent_writer)` | `edit.writer` | Yes |
| `(edit, attachment)` | rejected | n/a — attachments immutable |
| `(analyze, notes)` | `analyze.notes` | Rare |
| `(analyze, agent_search)` | `analyze.search` | Rare |
| `(analyze, agent_writer)` | `analyze.writer` | Rare |
| `(analyze, attachment)` | `analyze.attachment` | Rare |

**Extras shape**

```python
# Edit (primary path)
extras = RouterEditExtras(
    edit_kind=<'factual' | 'tighten' | 'insert' | 'reframe'>,
    # caller_id and mode are literal defaults; do not set them
)

# Analyze (rare)
extras = RouterAnalyzeExtras(focus="<short Arabic focus phrase>")
```

The `edit_kind` is the router's classification of what kind of edit the user
asked for. Use a deterministic mapper (LLM-classified is fine in v2, but v1
keeps it rule-based on verbs):

| User says | `edit_kind` |
|---|---|
| «صحح», «التاريخ خطأ», «الاسم الصحيح» | `factual` |
| «اختصر», «احذف التكرار», «شدد» | `tighten` |
| «اضف فقرة عن …», «ضع بند …» | `insert` |
| «ركز على …», «اعد صياغته من زاوية …» | `reframe` |

`edit_kind` is persisted to `workspace_item_versions.edit_kind` and shapes a
one-line modifier in the editor's system prompt. Pick something — the
schema rejects unknown values.

**Tier**

`tier='tier_2'` (the router's own tier). This flows through `deps.tier` into
`get_agent_model("item_analyzer", tier_override='tier_2')`. **Do not promote
to `tier_1` from the router** — if a turn warrants `tier_1` reasoning, that
turn shouldn't have been routed to the router in the first place; it should
have gone to writer_planner.

**user_emit (CRITICAL — router-only)**

The router is the **only** caller that injects a non-None `user_emit`. It
must wire the same Layer-2 SSE writer used by writer_planner / deep_search
planner. Helper:

```python
emit = create_layer2_emitter(sse_context, message_kind="message")
deps = build_analyzer_deps(..., tier='tier_2', user_emit=emit)
result = await edit(call, deps)
```

The runner asserts `user_emit is not None` for edit and `user_emit is None`
for analyze. Mismatched values are AssertionErrors (programmer bug, caught
in CI by the runner tests).

**Idempotency**

Edit calls from the router **MUST** carry an `idempotency_key`. The orchestrator
generates one key per user turn (typically `f"{turn_id}:edit:{target_item_id}"`
or the equivalent — see §11.4 of the INITIAL.md). This makes the router's edit
retry-safe across transport hiccups: if the same turn re-runs (manual retry,
crash recovery), `commit_item_revision` rejects the duplicate at the
`UNIQUE(item_id, idempotency_key)` constraint and the runner returns the
prior `EditResult` instead of writing a second version row.

**Full router edit example**

```python
async def router_edit_dispatch(ctx: RouterCtx, target_item_id: str,
                                instruction_ar: str, edit_kind: str) -> None:
    emit = create_layer2_emitter(ctx.sse, message_kind="message")
    deps = build_analyzer_deps(
        supabase=ctx.deps.supabase,
        http_client=ctx.deps.http_client,
        user_id=ctx.deps.user_id,
        conversation_id=ctx.deps.conversation_id,
        tier="tier_2",
        user_emit=emit,
    )
    call = AnalyzerCall(
        caller_id="router",
        mode="edit",
        targets=[SpecificTarget(item_id=target_item_id)],
        instruction=instruction_ar,
        tier="tier_2",
        user_id=ctx.deps.user_id,
        conversation_id=ctx.deps.conversation_id,
        extras=RouterEditExtras(edit_kind=edit_kind),
        idempotency_key=f"{ctx.turn_id}:edit:{target_item_id}",
    )
    try:
        result = await edit(call, deps)
    except ItemNotFoundError:
        await emit("لم أتمكن من العثور على العنصر المطلوب تعديله.")
        return
    # Nothing else to do. The Arabic acknowledgment was streamed by the runner
    # AFTER the DB commit. Failure paths already emitted apology text.
    if not result.success:
        log.warning("router edit failed", extra={"item_id": target_item_id,
                                                 "fallback_used": result.fallback_used})
```

**What the router does NOT do**

- Does NOT call `analyze` to "check what an item says before editing." The
  editor sub-agent reads the full WI content itself. Pre-analyzing wastes
  a tier_2 LLM call.
- Does NOT batch edits across multiple WIs in one `edit()` call (single-target rule).
- Does NOT re-stream the `EditResult.edit_summary_ar` text — the runner already
  emitted it via `user_emit` after the commit. Re-emitting double-displays.
- Does NOT call `commit_item_revision` directly. Ever. (CI lint catches this.)

---

### 2.2 `writer_planner_decider` (Layer 2 Major, runs on `tier_1`)

> **Writer planner does NOT edit. Ever.** Edit is router-exclusive by design
> (see §2.4 below). The writer planner is a strategy / context-assembly agent;
> it reads and distills, it does not mutate. Any user ask that implies
> mutating an existing draft routes through the **router edit path** (for
> surgical changes) or a **new writer turn** (for re-drafts that need fresh
> strategy). The writer planner never sits in the edit loop.

**When the writer planner calls item_analyzer**

| Trigger | Mode |
|---|---|
| The planner has triaged attached items by `summary` + `word_count` and decided one or more items need **focused distillation** against the writing goal | `analyze` |
| The planner needs to inspect a slim/missing-summary item raw before it can role-assign | (no call needed — caller reads `content_md` directly; see §5 below) |
| The user wants a prior draft modified | **Not the planner's path.** Surgical change → router edit. Re-draft → new writer turn (planner runs, but on a fresh package, not as an editor). |

**Which sub-agents the writer planner touches**

| `(mode, kind)` | Sub-agent dispatched | Used by writer_planner? |
|---|---|---|
| `(analyze, notes)` | `analyze.notes` | Yes |
| `(analyze, agent_search)` | `analyze.search` | Yes |
| `(analyze, agent_writer)` | `analyze.writer` | Yes — for `role_hint='prior_draft'` |
| `(analyze, attachment)` | `analyze.attachment` | Yes |
| Any `edit` | rejected by dispatcher | **Never** — no `(writer_planner, edit)` entry in `EXTRAS_TAGS`; the call cannot even be constructed |

**Extras shape**

```python
extras = WriterPlannerAnalyzeExtras(
    role_hint=<'template' | 'source' | 'reference' | 'prior_draft'>,
    query="<the angle, in Arabic — e.g. 'استخرج بنود التأخير والجزاء'>",
)
```

`role_hint` tunes the prompt. Examples:

| role_hint | What it tells the sub-agent |
|---|---|
| `template` | "This WI is the scaffold. Extract its structure (headings, clause skeleton, fill-in slots). Don't summarize content — describe shape." |
| `source` | "This WI is factual input. Extract entities (parties, dates, amounts) **verbatim** under the lens of the query." |
| `reference` | "This WI is background. Pull the parts that bear on the query; summarize the rest tersely." |
| `prior_draft` | "This WI is a prior version of what we're writing now. Extract sections that need to be carried forward, flagged for change, or discarded." |

**Tier**

`tier='tier_1'` (the planner's own tier — it runs on `tier_1`). Inherited via
`deps.tier` into `get_agent_model("item_analyzer", tier_override='tier_1')`.

**user_emit**

`None`. Analyze is silent. The planner displays its own plan-summary to the user;
analyze outputs feed the planner's reasoning, not the user.

**Idempotency**

Not required for analyze (no DB mutation). Pass `idempotency_key=None`.

**Batching rule — per-kind, one call each**

Source plans batch per kind. If the planner has 3 attached searches + 2
attached notes that survived triage, that's **two** `analyze()` calls — one
with all the searches in `targets`, one with all the notes. Don't loop one
item at a time (wastes per-call overhead) and don't mix kinds (builder rejects).

**Mixed batch — analyze vs raw passthrough**

The old `fetch_items` API supported `query=None` raw passthrough. The new
design removes that path from item_analyzer entirely:

| Item satisfies | Planner action |
|---|---|
| `word_count <= ITEM_RAW_THRESHOLD` (default 1000) AND planner just wants to see it | **Read `content_md` from the WI row directly.** No item_analyzer call. The planner already loaded `WorkspaceItemRow` in its dynamic instructions; the field is right there. |
| `word_count > threshold` OR planner needs focused extraction | `analyze()` with `query="..."` |

The threshold is the planner's own decision — the builder does NOT gate. See
§5 of this document.

**Full writer-planner analyze example**

```python
async def planner_distill_sources(ctx: WriterPlannerCtx,
                                   source_items: list[WorkspaceItemRow],
                                   writing_goal_ar: str) -> SearchAnalyzeOutput | None:
    """All source-role items (kind='agent_search') analyzed in one call."""
    if not source_items:
        return None
    deps = build_analyzer_deps(
        supabase=ctx.deps.supabase,
        http_client=ctx.deps.http_client,
        user_id=ctx.deps.user_id,
        conversation_id=ctx.deps.conversation_id,
        tier="tier_1",
        user_emit=None,   # analyze is silent
    )
    call = AnalyzerCall(
        caller_id="writer_planner",
        mode="analyze",
        targets=[SpecificTarget(item_id=it.item_id) for it in source_items],
        instruction=f"استخرج الحقائق ذات الصلة بـ: {writing_goal_ar}",
        tier="tier_1",
        user_id=ctx.deps.user_id,
        conversation_id=ctx.deps.conversation_id,
        extras=WriterPlannerAnalyzeExtras(
            role_hint="source",
            query=writing_goal_ar,
        ),
    )
    result = await analyze(call, deps)
    if not result.llm_invoked:
        return None
    return result.items   # SearchAnalyzeOutput
```

**Migration shim — `fetch_items` → `analyze`**

The legacy `agents/agent_writer/planner/tools.py::fetch_items` is the only
non-test caller still using the old API. Per the INITIAL.md migration plan,
replace it with a thin shim that:

1. Splits incoming targets by `query is None` vs `query is not None`.
2. For `query is None`: read `content_md` directly from the WI rows (raw passthrough).
3. For `query is not None`: group by `kind`, call `analyze()` once per kind, merge results back into the legacy return shape.

This migration is owned by the writer_planner sprint, not the item_analyzer
sprint, but the shim is the **only** correct path forward. New writer-planner
code MUST call `analyze()` directly, not `fetch_items`.

**What the writer planner does NOT do**

- Does NOT call `edit` — the `(writer_planner, edit)` pair is **not registered** in `EXTRAS_TAGS` and never will be. Edit is router-exclusive. If a user wants a draft mutated, the router handles it; if they want a different draft, the writer planner runs again on a fresh package.
- Does NOT call `analyze` to inspect every attached item indiscriminately. Triage by `summary` + `word_count` FIRST; only call when distillation is actually needed.
- Does NOT pass `query` strings in English. The downstream Arabic prompts assume Arabic-language input.
- Does NOT call multiple times with one item per call. Batch per kind.

---

### 2.3 `deep_search_planner_decider` (Layer 2 Major, runs on `tier_1`)

> **Deep-search planner does NOT edit. Ever.** Same architectural rule as
> writer planner: search is generative (each turn produces a new
> `agent_search` artifact), never destructive. New evidence → new artifact.
> If a user wants a prior search artifact tightened or refocused, that's a
> router edit (`edit.search`), not a deep-search-planner call.

**When the deep search planner calls item_analyzer**

| Trigger | Mode |
|---|---|
| The planner has a corpus of prior research artifacts (kind=`agent_search`) and needs the slices relevant to the current research **angle** | `analyze` |
| The planner has a user-attached PDF/image (kind=`attachment`) that needs OCR + distillation against the current angle | `analyze` with `kind=attachment` (the underlying OCR happens pre-router; analyzer reads the OCR'd `content_md`) |
| The user wants a prior search artifact mutated | **Not the planner's path.** Surgical change → router edit (`edit.search`). New angle → new deep-search turn → new artifact. |

**Which sub-agents the deep search planner touches**

| `(mode, kind)` | Sub-agent dispatched | Used by deep_search_planner? |
|---|---|---|
| `(analyze, notes)` | `analyze.notes` | Yes — when the user dropped a note relevant to the angle |
| `(analyze, agent_search)` | `analyze.search` | Yes — primary path |
| `(analyze, agent_writer)` | `analyze.writer` | Rare — when a prior draft hints at sub-questions to research |
| `(analyze, attachment)` | `analyze.attachment` | Yes |
| Any `edit` | rejected by dispatcher | **Never** — no `(deep_search_planner, edit)` entry in `EXTRAS_TAGS`; the call cannot even be constructed |

**Extras shape**

```python
extras = DeepSearchPlannerAnalyzeExtras(
    angle="<the research angle being investigated, in Arabic>",
    carry_evidence=<True if downstream needs verbatim quotes; False for narrative-only>,
)
```

`carry_evidence=True` tightens the schema — sub-agents are biased toward
filling `relevant_chunks[].verbatim` / `facts[].verbatim_span` precisely
rather than paraphrasing. Use it when the planner intends to feed quotes
into the aggregator or writer downstream.

**Tier**

`tier='tier_1'`. Same chain as writer_planner.

**user_emit**

`None`. The deep-search planner streams its own progress messages; analyze
results feed planning, not the user surface.

**Idempotency**

Not required for analyze. Pass `idempotency_key=None`.

**Group selectors — the deep-search use case**

Deep-search benefits most from `GroupTarget`. Common patterns:

```python
# All searches in this conversation, all in one call
GroupTarget(kind="agent_search", scope="conversation")

# Just the searches produced in the current turn
GroupTarget(kind="agent_search", scope="turn", turn_id=ctx.turn_id)

# All searches that were children of a specific writer artifact
GroupTarget(kind="agent_search", scope="parent_artifact",
            parent_artifact_id=parent_writer_item_id)
```

The dispatcher resolves the group to concrete rows (RLS-scoped) and feeds
them to `analyze.search` in one LLM call. Output is keyed per-item.

**Full deep-search-planner analyze example**

```python
async def dsp_analyze_prior_research(ctx: DSPCtx, angle_ar: str,
                                       carry_evidence: bool) -> SearchAnalyzeOutput | None:
    deps = build_analyzer_deps(
        supabase=ctx.deps.supabase,
        http_client=ctx.deps.http_client,
        user_id=ctx.deps.user_id,
        conversation_id=ctx.deps.conversation_id,
        tier="tier_1",
        user_emit=None,
    )
    call = AnalyzerCall(
        caller_id="deep_search_planner",
        mode="analyze",
        targets=[GroupTarget(kind="agent_search", scope="conversation")],
        instruction=f"استخرج المقاطع المتعلقة بـ: {angle_ar}",
        tier="tier_1",
        user_id=ctx.deps.user_id,
        conversation_id=ctx.deps.conversation_id,
        extras=DeepSearchPlannerAnalyzeExtras(
            angle=angle_ar,
            carry_evidence=carry_evidence,
        ),
    )
    result = await analyze(call, deps)
    if not result.llm_invoked:
        return None   # no prior searches in this conversation
    return result.items
```

**What the deep search planner does NOT do**

- Does NOT call `edit` — the `(deep_search_planner, edit)` pair is **not registered** in `EXTRAS_TAGS` and never will be. Deep search is purely additive (new artifacts per turn). Mutation of a prior search artifact, when needed, is a router edit (`edit.search`).
- Does NOT mix kinds in one call. If you need both prior searches AND a user-attached PDF analyzed, that's two calls (or rely on the planner's own scratchpad).
- Does NOT set `carry_evidence=True` unless the next pipeline stage actually consumes verbatim spans — tighter constraints raise validation-retry risk.

---

### 2.4 Why edit is router-exclusive (architectural rationale)

Edit access is restricted to the router **as an invariant of the design**,
not as a v1 limitation that loosens later. Three reasons:

1. **Edits are user-driven, not strategy-driven.** The router is the only
   agent whose entire job is "interpret what the user just said about a
   specific WI." Planners reason about goals, not about mutating identified
   artifacts. Letting a planner edit blurs the line between "thinking about
   the work" and "changing the work" — which is exactly the audit boundary
   `workspace_item_versions` exists to protect.
2. **Single-target + single-emitter contract.** Edit is single-target,
   atomic, and requires a `user_emit` injection so the Arabic acknowledgment
   streams in the right SSE channel. Multi-step planners don't have a clean
   point to inject one emitter per edit without unraveling their tool-call
   loop. The router does — it's a leaf dispatcher.
3. **Idempotency keys live at the turn boundary.** Edit requires a turn-scoped
   `idempotency_key` for retry safety (§11.4 of INITIAL.md). The router owns
   the turn; planners run inside a turn and don't generate stable keys.

If a planner ever needs to "change" an artifact, the correct paths are:

| Planner intent | Correct path |
|---|---|
| Tighten / fix / refocus an existing artifact | Hand back to the router with a clear user-facing summary; the router decides whether to dispatch an edit. Planners do not auto-edit. |
| Produce a new version with different strategy | The planner finishes its current run with a `WriterPackage` / search plan; the producing agent (`agent_writer` / `agent_search`) creates a **new** WI. The old WI stays untouched; the user keeps the history. |
| Carry forward content from a prior draft | `analyze` the prior draft (`role_hint='prior_draft'` for the writer planner), then synthesize fresh content. Never mutate the prior draft in place. |

This rule is enforced structurally — the `EXTRAS_TAGS` dict has no
`(writer_planner, edit)` or `(deep_search_planner, edit)` entry, so the
discriminated-union `AnalyzerExtras` literally cannot represent such a call.
A planner attempting to build one gets a Pydantic validation error before
the runner is reached.

---

### 2.5 References — eager (used) + tool-fetched (unused)

The item_analyzer family handles `workspace_item_references` rows on **two
tracks**:

- **Used refs (`used=true`)** are auto-loaded by the request builder for
  every resolved WI whose `kind == 'agent_search'`, rendered into a
  `<references>` block in the user message. The sub-agent sees them up
  front.
- **Unused refs (`used=false`)** are NOT pre-rendered. Every sub-agent
  registers a Pydantic AI tool `get_unused_references(wi_id)` and calls
  it on demand — typically when the analyzer is asked to widen coverage
  or the editor wants to **promote** a previously-unused ref to cited.

See INITIAL.md §7A for the full spec including the tool surface,
reconciliation transaction, and validation rules.

What this means for each caller:

| Caller | Effect |
|---|---|
| `router` (edit on `agent_search`) | The editor sees used refs eagerly, calls the tool when considering promotion, returns `refs_used_after` (final cited set, may include newly-promoted refs) + `refs_dropped`. Runner verifies every cited `n` exists in the table and reconciles the `used` column atomically with the version commit. `EditResult` doesn't surface the delta; read it from `per_phase_stats.{refs_used_after, refs_dropped, refs_promoted}` in the `agent_runs` row. |
| `writer_planner` (analyze on `agent_search` source items) | `SearchAnalyzeOutput.cited_references` carries the **used** refs the analyzer judged relevant to your `query`. `unused_but_relevant` carries `n`'s the analyzer **fetched via tool** and judged worth promoting — surface these in WriterPackage so the writer turn can decide whether to cite them. `broken_refs` lists `n`'s whose source row was unresolvable. |
| `deep_search_planner` (analyze on `agent_search` group) | Same `SearchAnalyzeOutput` shape. With `carry_evidence=True`, `cited_references[].verbatim_excerpt` is reliable for downstream quoting. The unused-ref tool is available but rarely useful for a research-angle inspection (the planner is asking "what does this artifact contain about X," not "what could it contain"). |
| Any caller, kind ∈ {notes, attachment} | No used refs, no rendered block. The tool is registered but returns `[]` cheaply. Output schemas unchanged. |
| Any caller, kind = `agent_writer` | No refs in v1 (writer borrows `[n]` from a parent agent_search and doesn't persist its own ref rows — verified in Supabase). v2 follow-up will let the tool resolve via `parent_item_id` → parent agent_search refs. |

The headline new capability: **editors can promote unused refs**. An
editor that calls `get_unused_references`, picks an unused ref that
strengthens the document, and cites it in `new_content_md` will see that
ref's `used` column flip `false → true` as part of the atomic commit.
The constraint that survives: every cited `n` must already exist as a
row for this `wi_id` — the runner SELECTs to enforce this, so editors
cannot fabricate new evidence, only redistribute what the original
search produced.

Caller responsibilities:

- **Do NOT pre-load refs and pass them in.** The builder owns the eager
  preload; the model owns the on-demand fetch via tool. Passing refs
  through `extras` is rejected (no such field).
- **Do NOT try to write/update `workspace_item_references` from a caller.**
  Only `commit_item_revision` (called by `runner.edit`) may update the
  `used` column. Inserts remain owned by `agent_search.publisher`.
- **Trust `broken_refs`.** If the analyzer flags a ref as broken, the
  source row is genuinely gone (regulation re-chunked, case deleted).
  Don't quote from it; don't try to re-render its snippet.
- **For editors**: three new apology messages map to three validation
  failures — «تعذر التعديل بسبب تعارض في المراجع» (cited-set ≠
  `refs_used_after`), «تعذر التعديل: مرجع غير معروف» (cited an `n` not
  in the table), «تعذر التعديل: محاولة حذف مرجع غير مستشهد» (tried to
  drop something that was already unused). All three are model-quality
  signals; surface to telemetry for prompt tuning.
- **For planners**: when `unused_but_relevant` is non-empty in an
  `analyze.search` result, the analyzer did the work of fetching unused
  refs and judging them. Don't discard that signal — surface those `n`'s
  to the user's plan presentation so the writer can choose whether to
  promote them.

---

## 3. Per-sub-agent reverse index — who calls what

For each of the 7 sub-agents, who can dispatch to it and what they pass.

### 3.1 `analyze.notes`

| Field | Value |
|---|---|
| Sub-agent id | `analyze.notes` |
| Output schema | `NotesAnalyzeOutput` (`findings: list[NoteFinding]`, `coverage`) |
| Callers | `router` (rare), `writer_planner`, `deep_search_planner` |
| Router extras | `RouterAnalyzeExtras(focus=...)` |
| Writer-planner extras | `WriterPlannerAnalyzeExtras(role_hint=..., query=...)` — typically `role_hint='reference'` or `'source'` |
| Deep-search-planner extras | `DeepSearchPlannerAnalyzeExtras(angle=..., carry_evidence=...)` |
| Typical instruction | «استخرج النقاط المرتبطة بالتاريخ ١٤٤٧/١/١٨», «لخّص ما قاله الموكل عن المبلغ» |
| What callers do with the output | Feed `findings` into planner reasoning, plan presentation, or aggregator input |

### 3.2 `analyze.search`

| Field | Value |
|---|---|
| Sub-agent id | `analyze.search` |
| Output schema | `SearchAnalyzeOutput` (`relevant_chunks: list[ChunkRef]`, `angle_coverage`) |
| Callers | `router` (rare), `writer_planner`, `deep_search_planner` (primary) |
| Typical use | Pull the slices of a research artifact that match the caller's angle, with chunk ids for traceability |
| `carry_evidence=True` impact | `relevant_chunks[].verbatim` is required to be exact; sub-agent is biased to quote, not paraphrase |
| Downstream consumers | `agent_writer` (via writer_planner's WriterPackage), deep_search aggregator |

### 3.3 `analyze.writer`

| Field | Value |
|---|---|
| Sub-agent id | `analyze.writer` |
| Output schema | `WriterAnalyzeOutput` (`matched_sections: list[SectionRef]`, `summary_ar`) |
| Callers | `router` (rare), `writer_planner` (for `role_hint='prior_draft'`), `deep_search_planner` (rare) |
| Typical instruction | «استخرج البنود المتعلقة بالتأخير», «اقتبس الفقرات التي تذكر الجزاء التأخيري» |
| What callers do with the output | Map `matched_sections.heading + slice_md` into the next writer turn or planner reasoning |

### 3.4 `analyze.attachment`

| Field | Value |
|---|---|
| Sub-agent id | `analyze.attachment` |
| Output schema | `AttachmentAnalyzeOutput` (`facts: list[ExtractedFact]`, `narrative`) |
| Callers | `router` (rare), `writer_planner`, `deep_search_planner` |
| Pre-requisite | The attachment's `content_md` MUST be populated by `ocr_extractor` first. The analyzer reads the OCR'd Markdown, not the binary file. |
| Verbatim guarantee | Each `ExtractedFact.verbatim_span` MUST appear in the source `content_md` — enforced by a `FunctionModel` test on every CI run |
| Typical use | Extract parties, dates, amounts from offers, contracts, registries with exact-match guarantees |

### 3.5 `edit.notes`

| Field | Value |
|---|---|
| Sub-agent id | `edit.notes` |
| Output schema | `NotesEditOutput` extends `EditOutputBase` (`new_content_md`, `edit_summary_ar`, `no_change`) |
| Callers | **`router` — exclusive.** Planners cannot dispatch edits (see §2.4). |
| Typical use | «أضف ملاحظة عن موعد التسليم», «صحّح اسم المدعى عليه», «اختصر هذه الملاحظة» |
| Post-call effect | Snapshot to `workspace_item_versions`, update `workspace_items.content_md`, bump `current_version_number`, emit Arabic acknowledgment, write `agent_runs` row |

### 3.6 `edit.search`

| Field | Value |
|---|---|
| Sub-agent id | `edit.search` |
| Output schema | `SearchEditOutput` extends `EditOutputBase` |
| Callers | **`router` — exclusive.** Deep-search planner produces new artifacts; it never edits an existing one. |
| Typical use | «اختصر هذا البحث», «احذف الإشارات للأنظمة الملغاة», «أعد تركيزه على المادة الخامسة» |
| Caveat | If the requested change requires re-doing the search itself (new sources, new chunks), the router should route to a new `agent_search` turn instead. Edit is for in-place refocus only. |

### 3.7 `edit.writer`

| Field | Value |
|---|---|
| Sub-agent id | `edit.writer` |
| Output schema | `WriterEditOutput` extends `EditOutputBase` |
| Callers | **`router` — exclusive.** Writer planner produces new drafts; it never edits an existing one. |
| Typical use | «اجعل البند الثالث أكثر صرامة», «أضف فقرة عن الجزاء التأخيري», «صحّح التاريخ» |
| Caveat | If the user is asking for a wholesale re-draft, the router should route to a fresh writer turn (with a `role_hint='prior_draft'` analyze handoff). Edit is for surgical changes only. |
| Stale embeddings | After edit, the WI's `summary` + embeddings go stale until a future re-summarization. We accept this; a stale flag may be added later if telemetry shows it matters. |

---

## 4. Orchestrator wiring — what the orchestrator must guarantee

The orchestrator is the layer that constructs `AnalyzerDeps` and dispatches
into `analyze` / `edit`. It is responsible for the cross-cutting wiring that
individual callers can't enforce:

| Concern | Orchestrator responsibility |
|---|---|
| `user_emit` injection | Build `create_layer2_emitter(sse_context, message_kind="message")` and pass it ONLY for the router-edit path. All other paths pass `None`. |
| `idempotency_key` generation | One key per user turn × target item (e.g. `f"{turn_id}:edit:{target_item_id}"`). Hand the key to the router; the router puts it on the call. |
| `tier` assignment | Match the caller's own model tier — `router` → `tier_2`, planners → `tier_1`. Asserted: `call.tier == deps.tier`. |
| `turn_id` and `parent_item_id` stamping | On every WI INSERT during a turn, stamp `turn_id`; for child WIs (e.g. an `agent_search` produced under an `agent_writer` parent), stamp `parent_item_id`. Required for group resolver. |
| Cross-conversation guardrail | The deps always carry `user_id` + `conversation_id`. The orchestrator MUST NOT pass a different conversation's id, even if a planner thinks it has cross-context evidence. v1 is conversation-scoped. |
| Logfire context | Open a parent span `orchestrator.dispatch_<caller>` that wraps the entire `analyze`/`edit` call so the family's spans nest under it for trace correlation. |

The orchestrator is the **only** module allowed to import
`create_layer2_emitter`. If a caller wants to emit to the user, it asks the
orchestrator for an emitter; it does not construct one itself.

---

## 5. Threshold gating — caller-side, not builder-side

The OLD `fetch_items` API had a 1000-word threshold inside the builder. The
NEW design removes that gate from the builder entirely. Each caller decides:

```python
ITEM_RAW_THRESHOLD = int(os.getenv("ITEM_RAW_THRESHOLD", "1000"))

def caller_decision(item: WorkspaceItemRow, focus_ar: str) -> Literal["raw", "analyze", "skip"]:
    if item.summary and not _summary_implies_relevance(item.summary, focus_ar):
        return "skip"             # triage drops it — never enters batch
    if (item.word_count or 0) <= ITEM_RAW_THRESHOLD:
        return "raw"              # caller reads content_md directly
    return "analyze"              # caller adds to next analyze() batch
```

Why caller-side: only the caller knows its **focus**. A 1500-word note may be
short enough to read raw if the planner is broad-scope, but warrant analysis
if the planner is angle-scoped. The builder doesn't have the angle; the
caller does.

The builder will still happily analyze a 50-word note if you tell it to. It
just won't *force* you.

---

## 6. Anti-patterns — what no caller may do

| Anti-pattern | Why it's wrong | Correct alternative |
|---|---|---|
| `extras = {"focus": "..."}` (raw dict) | Bypasses the discriminated union; loses static typing; risks tag mismatch at validation. | `extras = RouterAnalyzeExtras(focus="...")` |
| Calling `analyze` then `edit` to "verify before mutating" | The editor reads the WI itself. Pre-analyzing doubles cost. | Call `edit` directly. |
| Importing `SUB_AGENT_REGISTRY` or any sub-agent factory | Private API. | Call `analyze` / `edit` only. |
| Batching mixed kinds in one `analyze()` call | Builder rejects post-resolution. | Two separate calls. |
| Calling `commit_item_revision` directly from a caller | CI grep lint catches it. | Use `edit()`; the runner calls the commit. |
| Emitting `EditResult.edit_summary_ar` after `edit()` returns | The runner already emitted it via `user_emit`. Double-display. | Trust the runner. |
| Setting `idempotency_key=None` for an edit call from the router | Loses retry safety. | Always generate a turn-scoped key. |
| Setting `tier='tier_1'` from the router | Tier mismatch with `deps.tier`. Router runs on tier_2 — keep cost honest. | Use `tier='tier_2'`. |
| Passing instructions in English | Downstream Arabic prompts produce mixed-language output that fails Arabic-rendering tests. | All `instruction`, `query`, `angle`, `focus` strings are Arabic. |
| Surfacing `AnalyzerCallError` to the user | It's a programmer error, not a user-facing condition. | Log and re-raise; if user-facing context is needed, emit a separate Arabic apology. |

---

## 7. Per-caller observability contract

Each caller MUST open one Logfire span around its dispatch so the family's
spans nest underneath it. Span names and attributes:

| Caller | Wrap span name | Attributes |
|---|---|---|
| `router` (edit) | `router.edit_dispatch` | `target_item_id`, `edit_kind`, `idempotency_key`, `turn_id` |
| `router` (analyze) | `router.analyze_dispatch` | `target_item_id`, `focus_len` |
| `writer_planner` | `writer_planner.analyze_dispatch` | `role_hint`, `target_count`, `kind`, `query_len` |
| `deep_search_planner` | `deep_search_planner.analyze_dispatch` | `angle_len`, `carry_evidence`, `target_count`, `group_scope` (when group) |

The family's own spans (`item_analyzer.build_request`, `item_analyzer.analyze`,
`item_analyzer.edit`, `commit_item_revision`) attach the universal attributes
listed in §12.2 of the INITIAL.md. Caller-side spans add **caller-meaningful**
attributes the family can't know.

Correlation: every span tree threads `conversation_id`, `user_id`, and
`turn_id` so the dashboard can roll up "all item_analyzer work in turn T"
across callers.

---

## 8. Adding a new caller

If a future agent (say a hypothetical `compliance_reviewer`) needs to analyze
or edit WIs:

1. **Add a `CallerId` literal**: extend `CallerId = Literal[..., "compliance_reviewer"]` in `models.py`.
2. **Add the extras subclass(es)**: one per `(caller_id, mode)` you support. Mirror existing examples — caller_id + mode as fixed literals, plus the caller-specific shaping fields.
3. **Add the union members and tag entries**: extend `AnalyzerExtras` union and the `EXTRAS_TAGS` dict.
4. **Add tests**: extends `test_caller_extras.py` and `test_request_builder_e2e.py` matrix with the new caller's (mode, kind) cells.
5. **Update this protocol**: add a §2.x subsection describing the new caller's calling conventions.
6. **For edit callers**: confirm the orchestrator wires `user_emit` for that caller's edit path. Edit without `user_emit` is an AssertionError.

There is intentionally **no runtime extension surface** — adding a caller is
a code change + redeploy. This keeps the dispatch table tight and the
contract auditable.

---

## 9. Quick-reference cheat sheet

```
                ┌─────────────────────────────────────────────────────────┐
                │                  item_analyzer family                   │
                │                                                         │
                │   analyze(call, deps)   →   AnalyzeResult                │
                │   edit(call, deps)      →   EditResult                   │
                └─────────────────────────────────────────────────────────┘
                          ▲                ▲                ▲
                          │                │                │
       ┌──────────────────┘                │                └──────────────────┐
       │                                   │                                   │
┌─────────────┐                ┌────────────────────┐               ┌────────────────────┐
│   router    │                │  writer_planner    │               │ deep_search_planner│
│  (tier_2)   │                │     (tier_1)       │               │      (tier_1)      │
│             │                │                    │               │                    │
│ edit: ★     │                │ analyze: ★         │               │ analyze: ★         │
│ analyze: ◇  │                │ edit:    ✗  never  │               │ edit:    ✗  never  │
│             │                │                    │               │                    │
│ extras:     │                │ extras:            │               │ extras:            │
│  RouterEdit │                │  WriterPlannerAnz  │               │  DSPAnalyze        │
│  RouterAnz  │                │   {role_hint,      │               │   {angle,          │
│   {focus}   │                │    query}          │               │    carry_evidence} │
│  Edit       │                │                    │               │                    │
│   {edit_    │                │ group selectors:   │               │ group selectors:   │
│    kind}    │                │  rare              │               │  primary path      │
│             │                │                    │               │                    │
│ user_emit:  │                │ user_emit: None    │               │ user_emit: None    │
│  edit ONLY  │                │                    │               │                    │
│             │                │ idempotency_key:   │               │ idempotency_key:   │
│ idempotency │                │  None              │               │  None              │
│  _key: req  │                │                    │               │                    │
│  for edit   │                │                    │               │                    │
└─────────────┘                └────────────────────┘               └────────────────────┘

Legend:  ★ primary    ◇ rare    ✗ never (structurally impossible — no EXTRAS_TAGS entry)
```

---

## 10. References

- `agents/plans/INITIAL_item_analyzer_family.md` — enriched implementation plan (data model, dispatcher, runner, versioning, observability)
- `.claude/plans/item_analyzer.md` — original family spec
- `.claude/plans/item_analyzer_request_builder.md` — original dispatcher spec
- `.claude/plans/writer_planner.md` — writer planner caller spec
- `.claude/plans/wave_9_agent_runs.md` § "Agent Hierarchy" — Layer 1–4 definitions
- `agents/utils/agent_models.py:32-45` — `Tier` definition + `get_agent_model`
- `CLAUDE.md` § "Vocabulary — 'Layer' vs 'Tier'" — the distinction these
  callers must keep straight
