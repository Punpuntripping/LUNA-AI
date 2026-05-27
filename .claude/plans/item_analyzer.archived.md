> **SUPERSEDED** by `.claude/plans/item_analyzer_v2.md` (2026-05-25).
> Kept for historical reference only. See v2 § 15 for the change rationale.

# Plan — Item Analyzer (Layer 4 content steward family)

> Family of sub-agents under `agents/memory/item_analyzer/` that owns
> **post-release workspace_item content**. Two modes (`analyze`, `edit`),
> seven sub-agents (4 analyze + 3 edit, one per WI kind), tier inherited
> from the caller, dispatcher (see
> `.claude/plans/item_analyzer_request_builder.md`) picks the sub-agent.
>
> Replaces the original single-agent `fetch_items` design. After a WI is
> released, **only this family edits it**.

## Position and ownership

| | |
|---|---|
| Layer | **Layer 4 Memory** — alongside `artifact_summarizer`, `ocr_extractor`. Per `feedback_layer_vs_tier`: Layer = architectural position, distinct from `tier_*` (model cost class). |
| Tier | **Inherited from the caller** per call. writer_planner (Layer 2, tier_1) → tier_1 LLM. router (Layer 1, tier_2) → tier_2 LLM. One registry slot, parameterized. |
| Ownership invariant | After a `workspace_items` row's initial insert, **only `item_analyzer.edit` mutates `content_md`**. All other agents are read-only on post-release WIs. |

The ownership rule is enforced at the **service-layer + lint** level —
not via DB triggers. See "Enforcement" below.

## The two modes

| Mode | Callers | What it does | Returns | Persists? | User surface |
|---|---|---|---|---|---|
| `analyze` | router (rare), writer_planner, deep_search_planner | Reads target WI(s) + the caller's instruction; produces a focused per-kind structured output. | Per-sub-agent output schema, returned to caller. | No. | None — silent. |
| `edit` | router (primary). writer_planner / deep_search_planner not in v1. | Reads a single target WI + a precise edit instruction; produces a revised `content_md` + a short Arabic acknowledgment. Snapshots the prior version into `workspace_item_versions`. | `EditResult` (new content + `version_number` + user-facing message). | Yes (atomic). | **Yes** — Arabic «تم تعديل …» message streamed to the user via the Layer-2 SSE path injected into deps for edit mode only. |

Edit mode is only allowed for **a single, concrete WI** of kind `notes`,
`agent_search`, or `agent_writer`. Attachments are immutable (user-uploaded
files don't get rewritten). The request builder rejects any violation
before the LLM is touched.

### The "small edit" boundary

Edit handles changes that **don't require formalizing the WI from scratch**:

- factual corrections («التاريخ ١٤٤٧/٢/٣ وليس ١/١٨»)
- tightening / dropping redundant sections
- single-clause adjustments or paragraph-level tone tweaks
- inserting a missing line or fact
- refocusing existing content around the caller's instruction

Anything that needs the document **reorganized**, **multiple sections
re-drafted**, or **new sources folded in** — routes back to `agent_writer`
via the normal writing flow. The router is the dispatcher that picks the
path; item_analyzer never escalates a request to the writer itself.

## The seven sub-agents

The request builder picks one per call based on `(mode, target_kind)`:

| `(mode, kind)` | Sub-agent id | Output schema | Specialization |
|---|---|---|---|
| `analyze, notes` | `analyze.notes` | `NotesAnalyzeOutput` | Distill notes against the caller's focus; preserve verbatim user-written facts. |
| `analyze, agent_search` | `analyze.search` | `SearchAnalyzeOutput` | Pull the parts of a research artifact that match the caller's angle; cite chunk ids. |
| `analyze, agent_writer` | `analyze.writer` | `WriterAnalyzeOutput` | Extract sections of a drafted document relevant to the caller's instruction (e.g. "find the late-payment clauses"). |
| `analyze, attachment` | `analyze.attachment` | `AttachmentAnalyzeOutput` | Distill an OCR'd attachment against the caller's focus; preserve party names, dates, amounts verbatim. |
| `edit, notes` | `edit.notes` | `NotesEditOutput` | Surgical edit to a note (tighten, fix fact, add line). |
| `edit, agent_search` | `edit.search` | `SearchEditOutput` | Tighten or refocus a search artifact in place. |
| `edit, agent_writer` | `edit.writer` | `WriterEditOutput` | Small in-place edit to a drafted document — **not** a full rewrite. |

Each sub-agent is a standalone Pydantic AI agent (own factory, system
prompt in Arabic, structured output schema). All sub-agents share the
same model fallback chain at any tier — the prompt and schema are what
differ.

### Output schema shape (sketch)

The exact field set is owned by each sub-agent; the patterns below are
representative — finalize in the prompt-engineering pass.

```python
class NotesAnalyzeOutput(BaseModel):
    findings: list[NoteFinding]              # focused excerpts keyed to instruction
    coverage: Literal["full", "partial", "none"]

class SearchAnalyzeOutput(BaseModel):
    relevant_chunks: list[ChunkRef]          # chunk_id + verbatim excerpt + reason
    angle_coverage: str                      # short Arabic narrative

class WriterAnalyzeOutput(BaseModel):
    matched_sections: list[SectionRef]       # heading + slice + relevance
    summary_ar: str

class AttachmentAnalyzeOutput(BaseModel):
    facts: list[ExtractedFact]               # entity / value / verbatim_span
    narrative: str                           # short distilled prose

class NotesEditOutput(BaseModel):
    new_content_md: str
    edit_summary_ar: str                     # «تم تشديد …» (shown to user)
    no_change: bool = False                  # editor decided edit was a no-op

class SearchEditOutput(BaseModel):
    new_content_md: str
    edit_summary_ar: str
    no_change: bool = False

class WriterEditOutput(BaseModel):
    new_content_md: str
    edit_summary_ar: str
    no_change: bool = False
```

`no_change=True` is a valid editor outcome — when the instruction can't be
honored or would not improve the WI, the editor refuses politely (the
acknowledgment becomes «لم أجد ما يستوجب التعديل …») and the runner skips
the version commit.

## Tier inheritance

There is **one** model-registry slot, `item_analyzer`, registered with both
tier_1 and tier_2 fallback chains. Sub-agent factories call
`get_agent_model("item_analyzer", tier_override=ctx.deps.tier)` so the tier
flows from the caller:

| Caller | Caller's tier slot | Item analyzer tier per call |
|---|---|---|
| `router` | tier_2 (today) | tier_2 |
| `writer_planner_decider` | tier_1 | tier_1 |
| `deep_search_planner_decider` | tier_1 | tier_1 |

This requires a small extension to `get_agent_model`:

```python
def get_agent_model(
    slot: str,
    tier_override: Literal["tier_1", "tier_2"] | None = None,
) -> FallbackModel:
    """When tier_override is provided, returns that tier's chain regardless
    of the slot's default. Existing callers unaffected."""
```

Existing callers that don't pass `tier_override` continue to receive their
slot's default tier — no behavior change anywhere else in the codebase.

## Edit mode — user surface via Layer-2 dep injection

Layer 4 Memory agents don't normally talk to the user — that's Layer 2
Major's job. **For edit mode only**, the orchestrator injects the SSE emit
path into `AnalyzerDeps` so the editor can stream a short Arabic
acknowledgment after the version commit.

```python
class AnalyzerDeps(BaseModel):
    supabase: SupabaseClient
    http_client: AsyncClient
    user_id: str
    conversation_id: str
    tier: Literal["tier_1", "tier_2"]
    # Edit-only — None in analyze mode. Layer-2 SSE dep injection.
    user_emit: Callable[[str], Awaitable[None]] | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)
```

The orchestrator's **router-edit dispatch path** constructs deps with
`user_emit=create_layer2_emitter(sse_context, message_kind="message")`.
Analyze dispatches set `user_emit=None`. The editor runner asserts
`user_emit is not None` (programmer error otherwise); the analyzer runner
asserts `user_emit is None` (defense in depth).

Emitted strings are short, present-tense Arabic acknowledgments — examples:

- «تم تشديد البند الثالث وحذف العبارات المكررة.»
- «تم تصحيح التاريخ إلى ١٤٤٧/٢/٣.»
- «تم إضافة فقرة عن الجزاء التأخيري.»

They stream as **normal SSE message events** (no special event kind) so the
existing frontend chat rendering shows them as assistant messages. The
`edit_summary_ar` field of `EditResult` carries the same text for downstream
logging / replay.

## History — N=3 last messages

Each sub-agent call receives the **last 3 conversation messages** in the
rendered user message, loaded by the request builder
(`load_history(..., limit=3)`). This is sufficient context for deictic
instructions ("the date we just discussed," "the third clause") without
bloating tokens. Window size is `ITEM_ANALYZER_HISTORY_N` (default 3,
override per-deploy).

## Versioning — `workspace_item_versions`

Every edit snapshots the **before-image** of `content_md` into a new table:

```sql
-- shared/db/migrations/049_workspace_item_versions.sql

CREATE TABLE workspace_item_versions (
    version_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id            UUID NOT NULL REFERENCES workspace_items(item_id) ON DELETE CASCADE,
    version_number     INT  NOT NULL,                  -- monotonic per item, starts at 1
    content_md         TEXT NOT NULL,                  -- snapshot taken BEFORE the edit
    word_count_before  INT  NOT NULL,
    edited_by_agent    TEXT NOT NULL,                  -- "edit.notes" | "edit.search" | "edit.writer"
    edit_caller_id     TEXT NOT NULL,                  -- "router" | "writer_planner" | "deep_search_planner"
    edit_instruction   TEXT NOT NULL,                  -- the caller's instruction string
    edit_summary_ar    TEXT NOT NULL,                  -- the user-visible Arabic summary
    edit_kind          TEXT,                            -- "factual"|"tighten"|"insert"|"reframe"|NULL
    fallback_used      BOOLEAN NOT NULL DEFAULT FALSE,
    user_id            UUID NOT NULL,                  -- denormalized for RLS
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (item_id, version_number)
);

CREATE INDEX ix_workspace_item_versions_item
  ON workspace_item_versions(item_id, version_number DESC);

ALTER TABLE workspace_item_versions ENABLE ROW LEVEL SECURITY;

CREATE POLICY workspace_item_versions_owner_select ON workspace_item_versions
  FOR SELECT USING (user_id = auth.uid());
-- No INSERT/UPDATE/DELETE policy — service role only.

ALTER TABLE workspace_items
  ADD COLUMN current_version_number INT NOT NULL DEFAULT 1;
```

**Semantics**: a row in `workspace_item_versions` is the **before-image**
of one edit. The current live content lives in `workspace_items.content_md`.

- Initial insert (any producing agent) → `workspace_items.content_md` set,
  `current_version_number = 1`. No `workspace_item_versions` row.
- First edit → snapshot the current (v1) content into a
  `workspace_item_versions` row with `version_number=1`, update
  `workspace_items.content_md`, bump `current_version_number = 2`.
- Nth edit → snapshot the (N-1)th live content into
  `workspace_item_versions` with `version_number = N - 1`, update,
  bump to `N`.

Reconstructing the full edit history of an item: read all
`workspace_item_versions` rows for that `item_id` in `version_number` ASC,
then append the live `workspace_items.content_md` as the final
(`current_version_number`) entry.

## Enforcement — service-layer helper + lint

One backend service function is the **only** path that updates a WI's
`content_md` post-insert:

```python
# backend/app/services/workspace_items.py

async def commit_item_revision(
    supabase: SupabaseClient,
    *,
    item_id: str,
    new_content_md: str,
    edited_by_agent: str,           # must start with "edit." — defense in depth
    edit_caller_id: str,
    edit_instruction: str,
    edit_summary_ar: str,
    edit_kind: str | None,
    fallback_used: bool,
    user_id: str,
) -> tuple[int, str]:
    """Atomic: snapshot prior content into workspace_item_versions, update
    workspace_items.content_md, bump current_version_number. word_count
    is recomputed by the existing migration-048 trigger.

    Returns (new_current_version_number, new_content_md). Raises
    ItemNotFoundError or RLSViolationError on scope failures. Raises
    OwnershipViolation if edited_by_agent does not start with 'edit.'."""
```

Implementation uses a single Supabase RPC (or two-statement transaction)
to keep the snapshot + update + counter bump atomic. The
`(item_id, version_number)` unique constraint catches concurrent edits;
the runner retries once on conflict.

### The three enforcement layers

1. **`commit_item_revision` is the only writer of `workspace_items.content_md`
   post-insert.** All initial INSERTs by producing agents (`agent_search`,
   `agent_writer`, `ocr_extractor`, …) flow through their existing insert
   paths and form the "version 1" baseline.
2. **`commit_item_revision` is only imported from
   `agents/memory/item_analyzer/`.** A grep-based CI step
   (`scripts/lint/forbid_direct_content_md_updates.py`) flags any other
   importer.
3. **No direct `UPDATE workspace_items SET content_md` SQL or
   `.update({"content_md": ...})` exists outside `commit_item_revision`'s
   implementation.** Same CI grep covers this.

This is intentionally **not** a DB trigger. Triggers can't cleanly tell
"edit by item_analyzer" apart from "edit by a future migration or a
one-off repair script" without session GUC dances; service-layer + lint
is sufficient because writes already flow through the backend service
role.

## Cost tracking

Each sub-agent invocation records an `agent_runs` row when an LLM was
actually invoked:

- `agent_family='memory'`
- `subtype` ∈ {`analyze.notes`, `analyze.search`, `analyze.writer`,
  `analyze.attachment`, `edit.notes`, `edit.search`, `edit.writer`}
- `output_item_id` = `item_id` for edit mode (the WI that was edited);
  `NULL` for analyze (no new WI is produced)
- `input_item_ids` = list of resolved target item_ids (for both modes)
- `tokens_in / tokens_out / tokens_reasoning` from usage
- `per_phase_stats={"caller_id": "...", "tier": "...",
   "group_expanded": bool, "target_count_resolved": int,
   "version_number": int|None, "fallback_used": bool,
   "no_change": bool|None}`

A `short_circuit=True` `ResolvedRequest` (no resolved items) writes no
`agent_runs` row — the request builder records a logfire span, the runner
returns an empty result.

## Runner — `analyze()` and `edit()`

```python
# agents/memory/item_analyzer/runner.py

async def analyze(call: AnalyzerCall, deps: AnalyzerDeps) -> AnalyzeResult:
    """Layer 4 analyze path. Builds the request, dispatches to the matched
    sub-agent, returns the structured output. Silent (no user emission)."""
    assert deps.user_emit is None, "analyze must not be given a user emitter"
    req = await build_request(call, deps)
    if req.short_circuit:
        return AnalyzeResult(items=[], llm_invoked=False, sub_agent_id=None)

    agent = req.sub_agent_factory(
        model=get_agent_model("item_analyzer", tier_override=req.tier),
        output_type=req.output_schema,
    )
    try:
        result = await agent.run(req.rendered_user_message,
                                  message_history=_history_to_pai(req.history))
        await _record_run(deps, req, result, fallback_used=False, no_change=None,
                          version_number=None, output_item_id=None)
        return AnalyzeResult(items=result.output, llm_invoked=True,
                              sub_agent_id=req.sub_agent_id, fallback_used=False)
    except ModelHTTPError:
        # FallbackModel chain has already retried; this is a hard fail.
        truncated = _truncated_raw_fallback(req.resolved_items)
        return AnalyzeResult(items=truncated, llm_invoked=True,
                              sub_agent_id=req.sub_agent_id, fallback_used=True)


async def edit(call: AnalyzerCall, deps: AnalyzerDeps) -> EditResult:
    """Layer 4 edit path. Builds the request, dispatches to the matched
    sub-agent, commits the version, emits Arabic summary to the user."""
    assert deps.user_emit is not None, "edit requires a user emitter (Layer-2 dep)"
    req = await build_request(call, deps)
    if req.short_circuit:
        raise ItemNotFoundError("لم أتمكن من العثور على العنصر المطلوب تعديله")

    agent = req.sub_agent_factory(
        model=get_agent_model("item_analyzer", tier_override=req.tier),
        output_type=req.output_schema,
    )
    try:
        result = await agent.run(req.rendered_user_message,
                                  message_history=_history_to_pai(req.history))
        out = result.output
    except ModelHTTPError:
        # Primary + fallback both failed — no version write, no content change.
        await deps.user_emit("تعذر إجراء التعديل حالياً، حاول مرة أخرى.")
        return EditResult(success=False, fallback_used=True, no_change=False,
                           version_number=None, edit_summary_ar=None)

    if out.no_change:
        await deps.user_emit(out.edit_summary_ar)
        await _record_run(deps, req, result, fallback_used=False, no_change=True,
                          version_number=None, output_item_id=req.resolved_items[0].item_id)
        return EditResult(success=True, fallback_used=False, no_change=True,
                           version_number=req.resolved_items[0].current_version_number,
                           edit_summary_ar=out.edit_summary_ar)

    # Commit the revision; retry once on (item_id, version_number) conflict.
    version_number, _new_md = await _commit_with_retry(
        supabase=deps.supabase,
        item_id=req.resolved_items[0].item_id,
        new_content_md=out.new_content_md,
        edited_by_agent=req.sub_agent_id,
        edit_caller_id=call.caller_id,
        edit_instruction=call.instruction,
        edit_summary_ar=out.edit_summary_ar,
        edit_kind=call.extras.get("edit_kind"),
        fallback_used=False,
        user_id=deps.user_id,
    )

    # User-facing emission AFTER the DB commit succeeds (crash-safe ordering).
    try:
        await deps.user_emit(out.edit_summary_ar)
    except Exception as exc:
        log.warning("edit committed but user_emit failed",
                     exc_info=exc,
                     extra={"item_id": req.resolved_items[0].item_id})

    await _record_run(deps, req, result, fallback_used=False, no_change=False,
                      version_number=version_number,
                      output_item_id=req.resolved_items[0].item_id)
    return EditResult(success=True, fallback_used=False, no_change=False,
                       version_number=version_number,
                       edit_summary_ar=out.edit_summary_ar)
```

The **emission-after-commit** ordering is the same crash-safety rule the
codebase already enforces for user-visible writes ("user message saved
BEFORE AI call"): for edits we flip it to "DB committed BEFORE user
emission" so a mid-flight failure can never show the user "I edited X"
while the DB still holds the old content.

## File manifest

### NEW

```
agents/memory/item_analyzer/
  __init__.py                   ← re-exports: analyze, edit, AnalyzerCall,
                                   ResolvedRequest, AnalyzerDeps,
                                   build_analyzer_deps, AnalyzerCallError
  runner.py                     ← analyze(), edit(), _commit_with_retry,
                                   _truncated_raw_fallback, _record_run
  deps.py                       ← AnalyzerDeps + build_analyzer_deps(
                                   supabase, http_client, user_id,
                                   conversation_id, tier, user_emit=None)
  models.py                     ← AnalyzeResult, EditResult,
                                   per-sub-agent output schemas (NotesAnalyzeOutput,
                                   SearchAnalyzeOutput, WriterAnalyzeOutput,
                                   AttachmentAnalyzeOutput, NotesEditOutput,
                                   SearchEditOutput, WriterEditOutput),
                                   shared atomic types (ChunkRef, ExtractedFact,
                                   SectionRef, NoteFinding)
  prompts/
    __init__.py
    analyze_notes.py            ← ANALYZE_NOTES_SYSTEM_AR
    analyze_search.py
    analyze_writer.py
    analyze_attachment.py
    edit_notes.py               ← EDIT_NOTES_SYSTEM_AR
    edit_search.py
    edit_writer.py
  analyzers/
    __init__.py
    notes.py                    ← create_notes_analyzer(model, output_type)
    search.py
    writer.py
    attachment.py
  editors/
    __init__.py
    notes.py                    ← create_notes_editor(model, output_type)
    search.py
    writer.py
  tests/
    __init__.py
    test_runner_analyze.py      ← analyze() per sub-agent with TestModel
    test_runner_edit.py         ← edit() per sub-agent including version row write
                                   and user_emit invocation
    test_tier_inheritance.py    ← tier_override flows from call into get_agent_model
    test_edit_emits_to_user.py  ← user_emit called exactly once with the Arabic
                                   summary, AFTER the DB commit succeeds
    test_analyze_silent.py      ← user_emit=Mock → never called in analyze mode
    test_attachment_no_edit.py  ← edit(attachment) rejected by builder
    test_edit_revision_chain.py ← 3 sequential edits → 3 workspace_item_versions
                                   rows (1, 2, 3); current_version_number=4
    test_no_change_path.py      ← editor returns no_change=True → no version commit
                                   but user_emit still fires the polite acknowledgment
    test_version_conflict_retry.py ← simulate (item_id, version_number) unique
                                      violation on first attempt → second succeeds
    test_both_models_fail_edit.py ← primary + fallback both fail in edit → no
                                     version row, EditResult.success=False,
                                     apology Arabic message emitted
    test_fallback_truncation_analyze.py ← analyze double-fail → truncated raw fallback
    test_ownership_guard.py     ← commit_item_revision rejects edited_by_agent
                                   not starting with "edit."

backend/app/services/
  workspace_items.py            ← commit_item_revision() implementation
                                   (extend existing module if it already exists)
  tests/
    test_commit_item_revision.py ← atomic snapshot+update+counter bump,
                                    conflict path, RLS path, ownership guard

shared/db/migrations/
  049_workspace_item_versions.sql   ← table + index + RLS policy +
                                       workspace_items.current_version_number

scripts/lint/
  forbid_direct_content_md_updates.py ← grep CI step.
                                          Flags non-allowlisted writes:
                                            - `.update({"content_md"` outside the allowlist
                                            - raw SQL `UPDATE workspace_items SET content_md`
                                          Allowlist:
                                            - backend/app/services/workspace_items.py
                                            - shared/db/migrations/**/*.sql
                                          Hooked into CI via .github/workflows/ci.yml.
```

### MODIFIED

```
agents/utils/agent_models.py
  + slot: "item_analyzer" with tier_1 and tier_2 fallback chains
    (qwen3.6-plus / deepseek-v4-pro for tier_1;
     qwen3.5-flash / deepseek-v4-flash for tier_2 — same families
     as other Layer-4 agents)
  + signature: get_agent_model(slot, tier_override: Literal["tier_1","tier_2"]|None = None)
    Behavior unchanged when tier_override is None.

agents/orchestrator.py
  + router-edit dispatch path wires user_emit into AnalyzerDeps before
    calling item_analyzer.edit. All other dispatch paths into the
    item_analyzer family pass user_emit=None.
  + helper: create_layer2_emitter(sse_context, message_kind="message")
    (reuse the Layer-2 SSE writer used by writer_planner / deep_search planner;
     do not duplicate.)

agents/agent_writer/planner/tools.py   (cross-plan follow-up — see writer_planner.md)
  ~ fetch_items tool body — REPLACE with item_analyzer.analyze.
    See "Migration from fetch_items" below. Owned by the writer_planner
    plan's build order, not this one; this plan flags the dependency.

shared/observability.py
  + spans: "item_analyzer.analyze" and "item_analyzer.edit" with attributes
    caller_id, mode, sub_agent_id, tier, target_count_resolved,
    group_expanded, version_number (edit only), fallback_used,
    no_change (edit only).
```

## Migration from the old `fetch_items` API

The original plan defined `fetch_items(targets)` with per-target queries
and a raw-passthrough flag. The new design folds that into `analyze` and
removes the raw-passthrough path entirely — callers do raw reads themselves
from `workspace_items.content_md` (the `word_count` column on the row makes
this trivial to gate on).

| Old `fetch_items` call | New equivalent |
|---|---|
| `fetch_items([{item_id, query=None}])` (raw passthrough) | **No call** — caller reads `content_md` straight from the WI row. |
| `fetch_items([{item_id, query="..."}])` (single distilled) | `await analyze(AnalyzerCall(caller_id='writer_planner', mode='analyze', targets=[SpecificTarget(item_id)], instruction=query, tier='tier_1', extras={"role_hint": …, "query": query}, user_id, conversation_id))` |
| Mixed raw + queried in one call | Caller splits: raw items read directly from DB; queried items go in one `analyze` call. |

Updating `agents/agent_writer/planner/tools.py::fetch_items` to call
`analyze` is captured in the writer_planner plan's follow-up list (this
plan flags it as a downstream dependency but doesn't own the edit).

## Failure modes

| Failure | Behavior |
|---|---|
| Request builder rejects the call | Caller receives `AnalyzerCallError` with Arabic message. No LLM call, no version row. |
| Group resolution returns zero items | `analyze` returns `AnalyzeResult(items=[], llm_invoked=False)`. `edit` cannot reach this path (edit forbids group selectors and requires a specific item). |
| Specific target not found / out of scope | `analyze`: silently dropped (logged). `edit`: raises `ItemNotFoundError` («لم أتمكن من العثور على العنصر …»). |
| Primary LLM fails | FallbackModel chain retries once. `fallback_used=True` when the secondary succeeded. |
| Both models fail in `analyze` | `AnalyzeResult` with `fallback_used=True` and per-item truncated-raw views. |
| Both models fail in `edit` | `EditResult(success=False, fallback_used=True)`. **No version row written, no `content_md` update.** Apology emitted via `user_emit`. |
| Editor returns `no_change=True` | No version row written. `user_emit` still fires the polite acknowledgment. `agent_runs` row recorded. |
| `(item_id, version_number)` unique violation (concurrent edit) | Runner retries once with the next `current_version_number`. Second failure returns `EditResult(success=False)` with «تعذر التعديل بسبب تعارض متزامن» emission. |
| `user_emit` raises mid-edit | The DB commit had already succeeded. Logged + telemetry-flagged; user just doesn't see the chat acknowledgment. The version row exists and `EditResult.success=True`. |
| Editor returns `edited_by_agent` not starting with `"edit."` (programmer bug) | `commit_item_revision` raises `OwnershipViolation`. Covered by `test_ownership_guard.py`. |

## Test plan

| Test | Covers |
|---|---|
| `test_runner_analyze.py::test_analyze_search_specific` | router calls analyze on a specific agent_search WI → returns `SearchAnalyzeOutput` populated. |
| `test_runner_analyze.py::test_analyze_writer_group_in_conversation` | writer_planner calls analyze with `Group(kind='agent_writer', scope='conversation')` → all writer WIs in conv, single LLM call, output keyed per item. |
| `test_runner_analyze.py::test_analyze_attachment_preserves_facts_verbatim` | FunctionModel-asserted: party names + amounts unchanged. |
| `test_runner_edit.py::test_edit_writer_commits_version_and_updates_content` | router edit.writer → `workspace_item_versions` row at `version_number=1` (snapshot of original), `workspace_items.content_md` updated, `current_version_number=2`. |
| `test_runner_edit.py::test_edit_emits_arabic_summary_after_commit` | DB row visible before `user_emit` is called (crash-safe ordering). |
| `test_runner_edit.py::test_edit_attachment_rejected_at_builder` | edit + attachment kind → `AnalyzerCallError` before any LLM call. |
| `test_runner_edit.py::test_edit_group_target_rejected` | edit + GroupTarget → `AnalyzerCallError`. |
| `test_tier_inheritance.py::test_writer_planner_call_uses_tier_1_chain` | `AnalyzerCall(tier='tier_1')` → `get_agent_model` returns tier_1 chain. |
| `test_tier_inheritance.py::test_router_call_uses_tier_2_chain` | `AnalyzerCall(tier='tier_2')` → tier_2 chain. |
| `test_edit_revision_chain.py::test_three_edits_produce_three_versions` | After 3 sequential edits, `workspace_item_versions` has `version_number=1,2,3` (before-images), `workspace_items.current_version_number=4`. |
| `test_no_change_path.py::test_no_change_skips_version_write_but_emits` | editor output `no_change=True` → no version row, `user_emit` called with the polite acknowledgment, `EditResult(success=True, no_change=True)`. |
| `test_analyze_silent.py::test_analyze_never_calls_user_emit` | analyze with `user_emit=Mock` → Mock never invoked. |
| `test_ownership_guard.py::test_commit_rejects_non_edit_agent_id` | `commit_item_revision(edited_by_agent="rogue")` → `OwnershipViolation`. |
| `test_ownership_guard.py::test_lint_flags_direct_content_md_update` | The CI grep script flags a synthetic file containing `.update({"content_md": …})` outside the allowlist. |
| `test_ownership_guard.py::test_lint_allowlists_service_module` | The same write inside `backend/app/services/workspace_items.py` is NOT flagged. |
| `test_version_conflict_retry.py::test_retry_succeeds` | First commit hits unique constraint; second succeeds at next `version_number`. |
| `test_both_models_fail_edit.py::test_no_version_written_on_double_fail` | Primary + fallback both raise → `workspace_item_versions` row count unchanged; `workspace_items.content_md` unchanged; apology emitted. |
| `test_fallback_truncation_analyze.py::test_double_fail_returns_truncated_views` | analyze with both models failing → `AnalyzeResult.fallback_used=True` with per-item truncated raw content. |
| `test_commit_item_revision.py::test_atomic_snapshot_update_counter` | Snapshot + content update + counter bump all visible together; partial-failure scenarios verified. |

## Out of scope (deferred)

1. **Cross-conversation edits.** Today `(user_id, conversation_id)` scopes every call. Cross-conversation memory edits are a separate plan.
2. **Structural edits** — changing `kind`, `title`, `parent_item_id`, `summary`, or embeddings. v1 is `content_md`-only. Title rename is the most likely v2 addition.
3. **Restore-to-version UX** — the data structure supports it (`workspace_item_versions` carries the before-images), but no API or frontend yet. Add when product asks.
4. **Edit diffs in SSE** — current contract emits a plain Arabic summary, not a structured diff. Frontend diff rendering is a v2 of `EditResult`.
5. **Bulk edits** — one call, one item. Bulk operations (e.g. apply the same fix to N items) belong in a higher-layer wrapper, not in item_analyzer.
6. **Auto-summarization on edit.** Per discussion, edits do NOT re-trigger `artifact_summarizer`; the WI's persisted `summary` and embeddings go stale. We'll add a stale-summary flag column later if telemetry shows this matters.
7. **writer_planner / deep_search_planner as edit callers.** v1 wires only the router for edit. If a planner later wants to edit a prior_draft inline, add the `(caller_id, "edit")` schema to the request builder's `CALLER_EXTRAS_SCHEMA`.
8. **Distinguishing fallback-attempt count.** `fallback_used` is boolean today; if granular cost analysis later needs "which provider answered," persist provider name into `per_phase_stats`.

## Dependencies

- `.claude/plans/item_analyzer_request_builder.md` — **hard dependency**.
  Builder must land first (with `NotImplementedError` sub-agent factory
  stubs) before this plan replaces them with real factories.
- Migration 048 (`workspace_items.word_count`) — already drafted.
- Migration 049 (`workspace_item_versions` + `current_version_number`) —
  in this plan's manifest.
- Migration 050 (`turn_id` + `parent_item_id`) — owned by the request
  builder plan's manifest. Required for group-resolver scopes.
- `agents/utils/agent_models.py` — `item_analyzer` slot + `tier_override`
  parameter on `get_agent_model`.
- `shared/observability.py::get_logfire`.
- Layer-2 SSE emitter helper (`create_layer2_emitter`) — already exists
  in the orchestrator per Wave 7 work; reused as-is.

## Build order

1. **Land the request builder plan first** (separate file). Until that
   ships with at least dispatch + group resolver + history loader, the
   runner has nothing to dispatch through. The builder's
   `NotImplementedError` sub-agent factory stubs are this plan's seam.
2. Migration 049 (`workspace_item_versions` + `current_version_number`).
3. `backend/app/services/workspace_items.py::commit_item_revision` + its
   tests + the CI grep lint script.
4. `agents/utils/agent_models.py` — `item_analyzer` slot + `tier_override`.
   Unblocks every sub-agent factory.
5. `agents/memory/item_analyzer/models.py` (per-sub-agent output schemas,
   `AnalyzeResult`, `EditResult`, shared atomic types).
6. `agents/memory/item_analyzer/deps.py` (`AnalyzerDeps` + builder).
7. `prompts/` (7 Arabic system prompts, one per sub-agent).
8. `analyzers/` (4 factories) + `editors/` (3 factories), each replacing
   the matching stub in the request builder's registry.
9. `runner.py::analyze` (covers happy path + truncated-raw fallback).
10. `runner.py::edit` (happy path + no-change + version-conflict retry +
    double-fail apology path + user_emit invocation order).
11. Orchestrator wiring — router-edit dispatch builds deps with
    `user_emit=create_layer2_emitter(...)`. All other dispatches into the
    family pass `user_emit=None`.
12. End-to-end tests including the version chain, lint guard, and the
    ownership_guard regression.
13. **Follow-up (cross-plan)**: edit `agents/agent_writer/planner/tools.py`
    `fetch_items` to call `item_analyzer.analyze` per the migration table
    above. Owned by the writer_planner plan.
