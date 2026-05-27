> **SUPERSEDED** by `.claude/plans/item_analyzer_v2.md` (2026-05-25).
> The 7-sub-agent dispatch table collapses to a 2-line family partition
> in the v2 runner. See v2 § 15 for the change rationale.

# Plan — Item Analyzer Request Builder (Layer 4 dispatch brain)

> Deterministic dispatcher in front of the `item_analyzer` family. Every call
> into `analyze` or `edit` goes through `build_request(call) → ResolvedRequest`
> first, which picks the sub-agent, instantiates the prompt + output schema,
> resolves group selectors into concrete `workspace_items` rows, loads N=3
> conversation history, and hands a fully-materialized request to the runner.
>
> Lives under `agents/memory/item_analyzer/request_builder.py`. **No LLM.**
> Pure rule-based dispatch — small Python registry + scope resolver + history
> fetcher.
>
> **This plan is the dispatch contract**; the sub-agents themselves are
> specified in `.claude/plans/item_analyzer.md`. The runner depends on this
> plan landing first.

## Why a dedicated builder

The `item_analyzer` family has **7 sub-agents** (4 analyze + 3 edit, one per
WI kind), each with its own system prompt, output schema, and user-message
shape. Callers (router, writer_planner, deep_search_planner) **must not pick
a sub-agent themselves** — they don't carry the right combination metadata,
and three independent callers would drift on conventions within a sprint.

The request builder is the single source of truth for:

- which sub-agent handles `(caller_id, mode, target_kind)`
- how a group selector ("all searches for this conversation") expands to
  concrete `item_id`s
- what extra context (history, caller-specific hints) gets folded into the
  rendered prompt
- which output schema each sub-agent expects

If this layer drifts, every caller drifts. Keep it tight, pure, fully tested.

## Position in the stack

Per `feedback_layer_vs_tier`, `item_analyzer` is **Layer 4 Memory** (alongside
`artifact_summarizer`, `ocr_extractor`). The request builder is the **non-LLM
entry point of Layer 4** — every request from Layer 1 Conductor (router) or
Layer 2 Major (planners) into the analyzer family hits `build_request` before
any sub-agent runs.

```
                     ┌────────────────────────────────────────────┐
  caller (any layer) │  AnalyzerCall                              │
  ───────────────────▶                                            │
                     │   • caller_id (router|writer_planner|      │
                     │     deep_search_planner)                   │
                     │   • mode (analyze|edit)                    │
                     │   • targets (Specific | Group)             │
                     │   • instruction (str — the task)           │
                     │   • tier (inherited from caller)           │
                     │   • conversation_id, user_id               │
                     │   • extras (caller-specific shaping)       │
                     └──────────────────┬─────────────────────────┘
                                        ▼
                     ┌────────────────────────────────────────────┐
                     │  build_request(call, deps) — DETERMINISTIC │
                     │                                            │
                     │  1. validate (mode/kind/target shape)      │
                     │  2. resolve targets (Specific | Group→rows)│
                     │  3. enforce same-kind invariant            │
                     │  4. pick sub-agent from dispatch table     │
                     │  5. validate caller extras                 │
                     │  6. load N=3 history                       │
                     │  7. render system + user prompt            │
                     │  8. assemble ResolvedRequest               │
                     └──────────────────┬─────────────────────────┘
                                        ▼
                     ┌────────────────────────────────────────────┐
                     │  ResolvedRequest →                         │
                     │   • sub_agent_id + factory                 │
                     │   • rendered system + user prompt          │
                     │   • output_schema                          │
                     │   • resolved_items (concrete rows)         │
                     │   • history                                │
                     │   • tier (inherited)                       │
                     └────────────────────────────────────────────┘
```

## API — input and output

### `AnalyzerCall` (the typed call surface)

```python
# agents/memory/item_analyzer/models.py

CallerId    = Literal["router", "writer_planner", "deep_search_planner"]
AnalyzerMode = Literal["analyze", "edit"]
TargetKind  = Literal["notes", "agent_search", "agent_writer", "attachment"]
GroupScope  = Literal["conversation", "turn", "parent_artifact"]

class SpecificTarget(BaseModel):
    """One concrete WI."""
    item_id: str

class GroupTarget(BaseModel):
    """Selector that expands into a list of WIs at resolve time."""
    kind: TargetKind
    scope: GroupScope = "conversation"
    parent_artifact_id: str | None = None   # required when scope='parent_artifact'
    turn_id: str | None = None              # required when scope='turn'

Target = SpecificTarget | GroupTarget

class AnalyzerCall(BaseModel):
    caller_id: CallerId
    mode: AnalyzerMode
    targets: list[Target]           # 1+ targets. edit rejects len>1 and GroupTarget.
    instruction: str                # the question / edit ask — most critical input
    tier: Literal["tier_1", "tier_2"]   # inherited from caller
    user_id: str
    conversation_id: str
    extras: dict[str, Any] = {}     # caller-specific shaping payload (see below)
```

### `ResolvedRequest` (what the runner consumes)

```python
class ResolvedRequest(BaseModel):
    sub_agent_id: str                            # "analyze.search", "edit.writer", …
    sub_agent_factory: Callable[..., Any]        # create_*_agent function
    rendered_system_prompt: str
    rendered_user_message: str
    output_schema: type[BaseModel]
    resolved_items: list[WorkspaceItemRow]       # RLS-scoped, created_at ASC
    history: list[ConversationMessageView]       # last N=3
    tier: Literal["tier_1", "tier_2"]
    caller_id: CallerId
    mode: AnalyzerMode
    # Telemetry:
    target_count_input: int
    target_count_resolved: int                   # may differ if resolver dropped some
    group_expanded: bool
    short_circuit: bool                          # True iff resolved_items is empty
```

When `short_circuit=True`, the runner returns an empty result without
invoking any LLM.

## The dispatch table

```python
# agents/memory/item_analyzer/dispatch.py

SUB_AGENT_REGISTRY: dict[tuple[AnalyzerMode, TargetKind], SubAgentSpec] = {
    # analyze — 4 kinds
    ("analyze", "notes"):        SubAgentSpec("analyze.notes",      create_notes_analyzer,      NotesAnalyzeOutput,      ANALYZE_NOTES_SYSTEM_AR),
    ("analyze", "agent_search"): SubAgentSpec("analyze.search",     create_search_analyzer,     SearchAnalyzeOutput,     ANALYZE_SEARCH_SYSTEM_AR),
    ("analyze", "agent_writer"): SubAgentSpec("analyze.writer",     create_writer_analyzer,     WriterAnalyzeOutput,     ANALYZE_WRITER_SYSTEM_AR),
    ("analyze", "attachment"):   SubAgentSpec("analyze.attachment", create_attachment_analyzer, AttachmentAnalyzeOutput, ANALYZE_ATTACHMENT_SYSTEM_AR),
    # edit — 3 kinds (attachments are immutable — user-uploaded files don't get rewritten)
    ("edit",    "notes"):        SubAgentSpec("edit.notes",         create_notes_editor,        NotesEditOutput,         EDIT_NOTES_SYSTEM_AR),
    ("edit",    "agent_search"): SubAgentSpec("edit.search",        create_search_editor,       SearchEditOutput,        EDIT_SEARCH_SYSTEM_AR),
    ("edit",    "agent_writer"): SubAgentSpec("edit.writer",        create_writer_editor,       WriterEditOutput,        EDIT_WRITER_SYSTEM_AR),
}

@dataclass(frozen=True)
class SubAgentSpec:
    id: str
    factory: Callable[..., Any]
    schema: type[BaseModel]
    prompt: str
```

**Lookup rule:** the builder reads `target_kind` from the resolved items
(all items must share one kind — see validation below) and looks up
`SUB_AGENT_REGISTRY[(call.mode, target_kind)]`.

### Validation table — every rejection rule

| Condition | Result |
|---|---|
| `mode='edit'` and any target is `GroupTarget` | Reject — edit accepts only `SpecificTarget`. |
| `mode='edit'` and `len(targets) > 1` | Reject — edit operates on exactly one WI. |
| `mode='edit'` and resolved kind is `attachment` | Reject — attachments are immutable. |
| `mode='analyze'` and resolved items mix `kind`s | Reject — one kind per call (callers batch per kind). |
| `(mode, kind)` not in `SUB_AGENT_REGISTRY` | Reject — missing sub-agent. |
| `GroupTarget(scope='turn')` without `turn_id` | Reject — `turn_id` required. |
| `GroupTarget(scope='parent_artifact')` without `parent_artifact_id` | Reject. |
| `extras` keys don't match the caller's schema | Reject. |

All rejections raise `AnalyzerCallError` carrying an Arabic message
(project rule: all error messages in Arabic).

## Group selector resolution

Only relevant for `mode='analyze'` + `GroupTarget`. Lives in
`group_resolver.py`:

| Scope | Resolves to |
|---|---|
| `"conversation"` | All non-deleted `workspace_items` in `conversation_id` with the requested `kind`. |
| `"turn"` | All non-deleted `workspace_items` stamped with `turn_id` and the requested `kind`. The orchestrator stamps `turn_id` on every WI it produces (column added by migration 050). |
| `"parent_artifact"` | All non-deleted `workspace_items` whose `parent_item_id = parent_artifact_id` with the requested `kind`. Used when an `agent_writer` artifact ran research first — its child `agent_search` WIs link back via `parent_item_id`. |

RLS scopes the query to `user_id`. Items the user can't read are silently
dropped (logged WARNING with `item_id`). If resolution returns zero rows,
the builder returns a `ResolvedRequest` with `resolved_items=[]` and
`short_circuit=True`.

**Sort order**: `created_at ASC` (chronological) so the rendered prompt
presents items in conversation order.

## History loader (N=3)

`load_history(supabase, conversation_id, limit=3)` reads from the
`messages` table:

```sql
SELECT role, content, created_at
FROM messages
WHERE conversation_id = $1
  AND user_id = $2
ORDER BY created_at DESC
LIMIT $3
```

Result is reversed in Python so the renderer sees chronological order
(oldest → newest of the last 3). Mix of `user` and `assistant` messages
— whichever 3 are most recent.

Window size is configurable via `ITEM_ANALYZER_HISTORY_N` env var (default
3). When N=0 the loader returns `[]` and renderers skip the history block.

## Caller-specific shaping (`extras`)

Each caller passes a typed `extras` payload the builder folds into the
rendered prompt or output-schema selection. The **dispatcher knows the
shape per caller** — callers don't add ad-hoc fields:

| Caller | Mode | `extras` schema | How the builder uses it |
|---|---|---|---|
| `router` | `analyze` (rare) | `{focus: str}` | Appended to the user message as an «التركيز:» line. |
| `router` | `edit` | `{edit_kind: Literal["factual","tighten","insert","reframe"]}` | Selects a one-line modifier in the system prompt to bias the editor. Persisted to `workspace_item_versions.edit_kind`. |
| `writer_planner` | `analyze` | `{role_hint: Literal["template","source","reference","prior_draft"], query: str}` | The role hint tunes the prompt (e.g. for `role='source'` the prompt says "extract facts verbatim, preserve party names + dates + amounts"). `query` is the focus passed straight into the user message. |
| `deep_search_planner` | `analyze` | `{angle: str, carry_evidence: bool}` | `angle` = the research angle being investigated; `carry_evidence=true` tightens the schema toward verbatim quoting. |
| `deep_search_planner` | `edit` | _(not yet — deep_search doesn't edit today)_ | n/a |
| `writer_planner` | `edit` | _(not yet — writer_planner doesn't edit today)_ | n/a |

Per-caller Pydantic schemas live in
`dispatch.py::CALLER_EXTRAS_SCHEMA: dict[tuple[CallerId, AnalyzerMode], type[BaseModel]]`.
The builder validates `extras` against the matching schema before composing
the prompt; unknown keys or missing required keys raise `AnalyzerCallError`.

Adding a new (caller, mode) is a single-file edit to `dispatch.py` plus a
new schema row — explicitly NOT a runtime extension surface.

## The `build_request` flow

```python
# agents/memory/item_analyzer/request_builder.py

async def build_request(
    call: AnalyzerCall,
    deps: AnalyzerDeps,
) -> ResolvedRequest:
    """Deterministic dispatch — picks sub-agent, resolves targets, composes prompt.

    No LLM call here. Pure rules + DB reads (workspace_items, messages).
    """
    # 1. shape validation (mode + targets + scope params, before any DB read)
    _validate_call_shape(call)

    # 2. validate extras against the (caller_id, mode) schema
    extras_schema = CALLER_EXTRAS_SCHEMA.get((call.caller_id, call.mode))
    if extras_schema is None:
        raise AnalyzerCallError("هذا المستدعي لا يدعم هذا الوضع")
    validated_extras = extras_schema.model_validate(call.extras)

    # 3. resolve targets → list[WorkspaceItemRow], RLS-scoped, created_at ASC
    resolved_items = await resolve_targets(call.targets, call.user_id,
                                           call.conversation_id, deps.supabase)

    # 4. enforce same-kind invariant
    kinds = {item.kind for item in resolved_items}
    if len(kinds) > 1:
        raise AnalyzerCallError("جميع العناصر في الاستدعاء يجب أن تشترك في النوع")

    if not resolved_items:
        return _short_circuit_request(call)

    target_kind = next(iter(kinds))

    # 5. post-resolution edit guards (kind-dependent rejections)
    if call.mode == "edit" and target_kind == "attachment":
        raise AnalyzerCallError("لا يمكن تعديل مرفقات المستخدم")

    # 6. pick sub-agent
    spec = SUB_AGENT_REGISTRY[(call.mode, target_kind)]

    # 7. load history (skipped if N_HISTORY=0)
    history = await load_history(deps.supabase, call.conversation_id,
                                  call.user_id, limit=N_HISTORY)

    # 8. render prompts (per-sub-agent user-message renderer)
    system_prompt = spec.prompt
    user_message = USER_MESSAGE_RENDERERS[spec.id](
        items=resolved_items,
        instruction=call.instruction,
        extras=validated_extras,
        history=history,
    )

    return ResolvedRequest(
        sub_agent_id=spec.id,
        sub_agent_factory=spec.factory,
        rendered_system_prompt=system_prompt,
        rendered_user_message=user_message,
        output_schema=spec.schema,
        resolved_items=resolved_items,
        history=history,
        tier=call.tier,
        caller_id=call.caller_id,
        mode=call.mode,
        target_count_input=len(call.targets),
        target_count_resolved=len(resolved_items),
        group_expanded=any(isinstance(t, GroupTarget) for t in call.targets),
        short_circuit=False,
    )
```

`USER_MESSAGE_RENDERERS` is a `dict[str, Callable]` keyed by `sub_agent_id`.
Each renderer is a small pure function living in `user_message_renderers/`.

## File manifest

### NEW

```
agents/memory/item_analyzer/
  request_builder.py              ← build_request(call, deps)
  dispatch.py                     ← SUB_AGENT_REGISTRY, CALLER_EXTRAS_SCHEMA,
                                    _validate_call_shape, SubAgentSpec
  group_resolver.py               ← resolve_targets(targets, user_id, conversation_id, supabase)
                                    Handles conversation / turn / parent_artifact scopes.
  history.py                      ← load_history(supabase, conversation_id, user_id, limit)
  user_message_renderers/
    __init__.py                   ← USER_MESSAGE_RENDERERS dict
    _common.py                    ← shared helpers (render_history_block,
                                    render_item_block, render_extras_block)
    analyze_notes.py
    analyze_search.py
    analyze_writer.py
    analyze_attachment.py
    edit_notes.py
    edit_search.py
    edit_writer.py
  tests/
    test_dispatch_table.py        ← every (mode, kind) routes to expected spec
    test_validation.py            ← every rejection rule from the validation table
    test_group_resolver.py        ← conversation / turn / parent_artifact + RLS
    test_history_loader.py        ← N=3 ordering, empty conv, RLS scope, N=0 path
    test_caller_extras.py         ← each (CallerId, mode) extras schema validated
    test_user_message_renderers.py← per-sub-agent rendering snapshots
    test_request_builder_e2e.py   ← end-to-end build_request matrix
```

### MODIFIED

```
agents/memory/item_analyzer/models.py
  + AnalyzerCall, ResolvedRequest, SpecificTarget, GroupTarget, Target,
    CallerId, AnalyzerMode, TargetKind, GroupScope,
    AnalyzerCallError, ConversationMessageView, WorkspaceItemRow
  (per-sub-agent output schemas — NotesAnalyzeOutput, … — defined alongside
   their sub-agent modules in the item_analyzer plan)

shared/db/migrations/
  050_workspace_items_turn_parent.sql   ← if not already present:
                                          ALTER TABLE workspace_items
                                          ADD COLUMN turn_id UUID NULL,
                                          ADD COLUMN parent_item_id UUID NULL
                                          REFERENCES workspace_items(item_id);
                                          + ix_workspace_items_turn
                                          + ix_workspace_items_parent
                                          Required by group resolver's
                                          scope='turn' and 'parent_artifact'.
                                          Verify with mcp__supabase__list_migrations
                                          before drafting — may already exist.

agents/orchestrator.py
  + on every workspace_items INSERT in a turn, stamp turn_id (current turn UUID)
    and parent_item_id (when the producing agent declares a parent — e.g.
    agent_writer with research children). Existing INSERTs adjusted minimally.

shared/observability.py
  + span "item_analyzer.build_request" with attributes:
      caller_id, mode, target_count_input, target_count_resolved,
      sub_agent_id (or "none" on short-circuit), group_expanded, short_circuit.
```

## Test plan

| Test | Covers |
|---|---|
| `test_dispatch_table.py::test_every_pair_routes_to_spec` | Every `(mode, kind)` in the registry returns a `SubAgentSpec`. No missing entries. |
| `test_dispatch_table.py::test_no_attachment_editor_registered` | `("edit","attachment")` is NOT in the registry. |
| `test_validation.py::test_edit_rejects_group_target` | `mode='edit'` + `GroupTarget` → `AnalyzerCallError`. |
| `test_validation.py::test_edit_rejects_multiple_specific_targets` | `mode='edit'` + 2 specific targets → error. |
| `test_validation.py::test_analyze_rejects_mixed_kinds` | Specific targets `kind='notes'` + `kind='agent_search'` → error post-resolution. |
| `test_validation.py::test_edit_attachment_rejected_post_resolution` | `mode='edit'` + specific target whose row is `kind='attachment'` → error. |
| `test_validation.py::test_group_turn_requires_turn_id` | `GroupTarget(scope='turn', turn_id=None)` → error. |
| `test_validation.py::test_group_parent_requires_parent_id` | `GroupTarget(scope='parent_artifact', parent_artifact_id=None)` → error. |
| `test_group_resolver.py::test_conversation_scope_returns_all_kind_in_convo` | `Group(kind='agent_search', scope='conversation')` → exactly the search rows in this conv. |
| `test_group_resolver.py::test_turn_scope_filters_by_turn_id` | Three searches: two with `turn_id=T1`, one with `turn_id=T2`. Group(turn_id=T1) → exactly the two. |
| `test_group_resolver.py::test_parent_artifact_scope_filters_by_parent_id` | Searches with various `parent_item_id` → returns only matching. |
| `test_group_resolver.py::test_rls_drops_other_user_items` | User A calls with B's item_ids → dropped silently, logger warned. |
| `test_group_resolver.py::test_chronological_sort` | Items returned `created_at ASC`. |
| `test_history_loader.py::test_returns_last_n_messages_chronological` | 5 messages exist → returns last 3 in order (oldest first within window). |
| `test_history_loader.py::test_empty_conversation_returns_empty` | New conversation → `[]`. |
| `test_history_loader.py::test_n_zero_returns_empty` | `ITEM_ANALYZER_HISTORY_N=0` → `[]` regardless of message count. |
| `test_caller_extras.py::test_router_edit_kind_validated` | `edit_kind='factual'` accepted; `edit_kind='banana'` rejected. |
| `test_caller_extras.py::test_writer_planner_analyze_requires_role_hint` | Missing `role_hint` rejected. |
| `test_caller_extras.py::test_writer_planner_edit_mode_rejected` | `caller_id='writer_planner', mode='edit'` → rejected (no schema registered). |
| `test_caller_extras.py::test_unknown_extras_key_rejected` | Any caller passes an unknown key → error. |
| `test_user_message_renderers.py::test_each_renderer_snapshot` | Each of the 7 renderers produces the expected XML/markdown structure for a canonical input. |
| `test_user_message_renderers.py::test_history_block_rendering` | History list of 3 messages → rendered as `<history>` block in correct order. |
| `test_request_builder_e2e.py::test_analyze_search_group_end_to_end` | writer_planner calls analyze with `Group(kind='agent_search', scope='conversation')` → `ResolvedRequest(sub_agent_id='analyze.search', resolved_items=[…3 rows…], group_expanded=True, short_circuit=False)`. |
| `test_request_builder_e2e.py::test_edit_writer_specific_end_to_end` | router calls edit on a specific writer WI → `sub_agent_id='edit.writer'`, single resolved item, `edit_kind` extras present in rendered prompt. |
| `test_request_builder_e2e.py::test_empty_resolution_short_circuits` | All target item_ids missing/out-of-scope → `short_circuit=True`, `resolved_items=[]`. |

## Out of scope (deferred)

1. **LLM-based dispatch.** If real-world callers want fuzzy "let the dispatcher figure out what I meant," we add a small classifier upstream. v1 is strict typed dispatch.
2. **Multi-kind analyze in one call.** v1 forces one kind per call. If telemetry shows many callers chain N analyze calls with different kinds, we add a fan-out wrapper later (still using `build_request` internally per kind).
3. **History beyond messages.** N=3 messages only. If a sub-agent needs recent WIs or recent searches, we add a separate `recent_artifacts` loader as a new field on `ResolvedRequest`.
4. **Per-caller threshold gating** (the old `fetch_items` 1000-word policy). Removed in the new design — callers decide "analyze vs read raw" themselves by reading `word_count` on the WI row. The builder doesn't gate.
5. **Cross-conversation group selectors.** Today `scope` is bounded to one conversation. If the user wants "all my searches across all conversations about topic X," that's a separate plan.
6. **Streaming sub-agent output.** The runner is request/response; the builder shape doesn't change when streaming is added in v2.
7. **Hot-reload of the registry.** The registry is a compile-time Python dict. Adding a sub-agent or a caller is a code change + redeploy.

## Dependencies

- `.claude/plans/item_analyzer.md` — defines the sub-agent factories
  (`create_notes_analyzer`, `create_writer_editor`, …) and the per-sub-agent
  output schemas (`NotesAnalyzeOutput`, `WriterEditOutput`, …) that the
  registry references. This builder plan ships **first** with stub factories
  that raise `NotImplementedError`; sub-agent bodies land in the item_analyzer
  plan.
- Migration 050 (`workspace_items.turn_id` + `parent_item_id`) — required by
  the group resolver. Verify state with `mcp__supabase__list_migrations`.
- `messages` table already exists per Wave 4.
- `shared/observability.py::get_logfire` for span wrapping.

## Build order

1. `models.py` types: `AnalyzerCall`, `ResolvedRequest`, `SpecificTarget`,
   `GroupTarget`, `Target`, `CallerId`, `AnalyzerMode`, `TargetKind`,
   `GroupScope`, `AnalyzerCallError`, `ConversationMessageView`,
   `WorkspaceItemRow`.
2. `dispatch.py` skeleton: `SUB_AGENT_REGISTRY` with `NotImplementedError`
   factories, `CALLER_EXTRAS_SCHEMA`, `_validate_call_shape`.
3. Migration 050 if `turn_id` / `parent_item_id` columns are missing.
4. `group_resolver.py` + `history.py` — pure Supabase reads, fully
   unit-testable in isolation. Tests first.
5. `user_message_renderers/_common.py` + the 7 per-sub-agent renderers
   (with snapshot tests on canonical inputs).
6. `request_builder.py::build_request` — wires the pieces together.
7. End-to-end `test_request_builder_e2e.py` matrix.
8. The item_analyzer plan replaces the `NotImplementedError` stubs with
   real sub-agent factories + output schemas in its own build order.
