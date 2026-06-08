# Router v1 — `save_memo` tool + workflow guidance

Two additions to the router, scoped to `agents/router/` + `agents/tool_repository/`
plus one orchestrator wiring change. **No DB migration** (reuse `kind='note'`),
**no frontend changes**.

## Motivation

- **Workflow guidance** — today the router classifies each turn into one
  `ChatResponse | DispatchAgent` and routes by keyword/topic. It has no notion
  of a *multi-step workflow*. When a user pastes a complete legal draft with no
  instruction (see `agents_reports/agentic_monitor/convo_72055084-.../llm_calls/00_router_*.md`),
  the router falls back to a generic "what do you want? (1/2/3/4)" menu. We want
  it to recognise the intent and **suggest the canonical search→write path**.
- **`save_memo`** — the router's context is filtered + truncated: it only loads
  messages *after* `conversations.compacted_through_message_id`
  (`agents/router/context.py:_load_filtered_messages`) and the memory pre-hook
  compacts every turn (`orchestrator.py` step 1). A huge pivotal first message
  (full request + template) can **fall out of the live window**. Workspace items,
  by contrast, are always re-injected as summaries and unfoldable on demand. So
  promoting the core message to a WI makes it **permanent + always-visible** to
  every future agent — the anchor of the conversation.

## Plumbing facts that drive the design

1. `wi_alias_map` is built **once at run start** (`router.run_router`). A memo
   created mid-run has a fresh `wi_seq` NOT in that map, so if the LLM echoes its
   `WI-N` in `attached_wis`, the output validator raises `ModelRetry`. → the tool
   must inject the new alias into `ctx.deps.wi_alias_map`.
2. `run_router` returns only `result.output`; `_route` never sees what tools did.
   → to emit the `workspace_item_created` SSE and to force-attach the memo, the
   tool stashes state on `deps`, and `run_router` returns it to `_route`.
3. Auto-attach can't rely on the LLM remembering. → belt-and-suspenders: prompt
   tells the LLM to attach **and** the orchestrator force-appends the memo id.

## Decisions

| Decision | Choice |
|---|---|
| Tool shape | new `agents/tool_repository/save_memo.py`, `register_save_memo(agent)` — mirrors `add_user_template.py` |
| Content fidelity | tool copies the **raw user message verbatim** from a new `RouterDeps.user_message`; LLM supplies only `title`. No paraphrase. |
| Marker | prepend `> 📌 رسالة أساسية من المستخدم — تتضمن تفاصيل جوهرية للطلب` + `\n\n` + verbatim block |
| Row | `kind='note'`, `created_by='agent'`, `metadata={"subtype":"memo"}`, scoped `user_id`+`conversation_id`; `wi_seq` auto by trigger (052) |
| Dedup | **append-only / multiple allowed**; only guard = skip if a `subtype='memo'` note with identical normalized content already exists |
| Attach | auto-attached via alias-map injection + orchestrator force-attach safety net |
| Announce | **allowed** (not required). The `workspace_item_created` SSE shows the chip regardless; the LLM may also acknowledge it in chat. |
| Cap | **exempt** `subtype='memo'` notes from the 15-item cap (`_count_artifact_kinds`) — core context, not a generated artifact |

## File changes

### NEW `agents/tool_repository/save_memo.py`
`register_save_memo(agent)`. Structural deps `HasMemoContext`: `.supabase`,
`.user_id`, `.conversation_id`, `.user_message: str`, `.wi_alias_map: dict[int,str]`,
`.workspace_item_summaries: list[dict]`, `.pending_sse_events: list[dict]`,
`.force_attach_item_ids: list[str]`.

Tool `save_memo(ctx, title) -> str`:
- read verbatim `ctx.deps.user_message`; empty → `ModelRetry`.
- `content_md = _MARKER + "\n\n" + raw`.
- same-content guard via `_identical_memo_exists(...)` → return early if dupe.
- `create_workspace_item(..., kind="note", created_by="agent",
  metadata={"subtype":"memo"}, content_md=...)`.
- inject `wi_alias_map[wi_seq]=item_id`; append a summary dict to
  `workspace_item_summaries`; append `workspace_item_created` to
  `pending_sse_events`; append `item_id` to `force_attach_item_ids`.
- return Arabic confirmation incl. `WI-{seq}`.

Pure helpers (unit-testable): `_MARKER`, `build_memo_content(raw)`,
`_normalize(s)`, `_identical_memo_exists(supabase, conversation_id, content_md)`.

### `agents/router/router.py`
- `RouterDeps`: add `user_message: str = ""`,
  `pending_sse_events: list[dict] = field(default_factory=list)`,
  `force_attach_item_ids: list[str] = field(default_factory=list)`.
- `register_save_memo(router_agent)` beside `register_unfold_workspace_item`.
- new `@dataclass RouterRunResult { output, sse_events, force_attach_item_ids }`.
- `run_router`: set `deps.user_message = question`; return `RouterRunResult`.
- `SYSTEM_PROMPT`: add sections A (workflow guidance) + B (save_memo usage).

### `agents/orchestrator.py` — `_route`
- consume `RouterRunResult`; `for ev in memo_sse: yield ev` right after the call
  (both ChatResponse + DispatchAgent paths).
- on `DispatchAgent`: merge `force_attach` into `attached_item_ids` (dedup, memo
  always included) before `_dispatch`.
- verify `run_router` has no other callers (grep) — expected only `_route`.

### `agents/orchestrator.py` — `_count_artifact_kinds`
Exclude `metadata->>'subtype' = 'memo'` notes from the cap count.

### NEW `agents/tool_repository/tests/test_save_memo.py`
Reuse `conftest.FakeSupabase`. Cases: marker+verbatim content; title used /
fallback; same-content guard skips 2nd identical but allows distinct; alias_map
gets new seq; sse + force_attach sinks populated; empty user_message → ModelRetry.

## Prompt sections

**A — workflow guidance:** when the user asks to draft a legal document needing
precise legal grounding (مذكرة دعوى/لائحة/عقد بمواد محددة): if NO relevant
`agent_search` WI exists → emit `ChatResponse` suggesting search-then-write
(«أقترح أن أبحث أولاً ... ثم أصيغ المستند — هل أبدأ بالبحث؟»). If one exists (or
the user began by searching) → don't re-suggest; dispatch writing directly and
attach the search WI.

**B — save_memo:** when the user explicitly shares a substantial core
request/template whose details must not be lost → call `save_memo(title=...)`
before routing. It stores the message verbatim and attaches it automatically.
You MAY briefly acknowledge it. Don't call it for ordinary short messages.

## Out of scope (v1)
Auto-chaining search→write in one turn; intent patterns beyond the legal-doc
case; a dedicated `memo` enum kind / distinct chip style; frontend work.

## Sequencing
1. `save_memo.py` + tests (pure surface first).
2. `RouterDeps` fields + `register_save_memo` + `RouterRunResult` + `run_router`.
3. `_route` consumption (SSE drain + force-attach) + grep-verify callers.
4. Prompt sections A & B.
5. cap-exclusion one-liner.
6. run `agents/tool_repository/tests/` + router smoke check.
