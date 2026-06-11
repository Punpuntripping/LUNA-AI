# Artifact Editor — surgical edits as a router tool

**Status:** BUILT 2026-06-11 (same day) — all 7 file groups implemented, 91 tests green (router + tool_repository + artifact_editor suites), migration 068 applied to prod. NOT yet deployed / live-validated (validation plan below pending).
**Build deviations:** (1) bad-alias handling in `edit_artifact` returns a plain Arabic string instead of `ModelRetry` — router-tool house rule (matches unfold/save_memo; keeps TestModel smoke green). (2) `artifact_editor` is the first `run_tracked` adopter (rest of the codebase uses `track_stage`+`record_run`); ledger row lands as agent=`artifact_editor.run`, agent_family=`editing`.
**Origin:** `agents/tool_repository/edit_supabase_md.py` (anchored-replace primitive, already written) + convo `21b0d2f4` forensic report (Turn 5: «بدل كلمة الطاعنة اذكر موكلتي عدل اللائحة وارجعها» → full 275s writer re-run for an 18-instance term swap).

## Design decisions (settled in /reflect with user, 2026-06-11)

| Decision | Choice |
|---|---|
| Architecture | Editor is a **router tool** (`edit_artifact`), NOT a dispatched family. Router calls the tool (in parallel for multiple WIs, one editor per WI, max 3), gets change summaries back as tool returns, then briefs the user itself via `ChatResponse`. Router owns the task end-to-end. |
| Agent layer | Layer 3 task agent — never talks to the user, no streaming of its own. |
| Edit semantics | **In-place** on the same `item_id`. Pre-edit content snapshotted to a new `workspace_items.prev_content_md` column (one-level undo, overwritten on each edit) — user chose column over a revisions table for simplicity. |
| Batch tool | `edit_supabase_md` extended to accept a **list of `{old_text, new_text}` pairs applied atomically in one call** (all located against one content snapshot, one guarded write). No `replace_all` primitive — the agent reasons per location (grammar agreement: الطاعنة→موكلتي changes feminine agreement around each hit) and emits the full batch. |
| Routing boundary | **Conservative**: only clearly-scoped surgical edits (substitute / delete / insert small / rephrase a named span). Structural rewrites, new sections, anything needing research → still `DispatchAgent` to `writing` with `target_wi`. |
| Model | deepseek-v4-flash, **reasoning_effort = medium** → new `Reasoning = "medium"` value in `agent_models.py`, slot `"artifact_editor"`. |
| ask_user | None in v1. Best judgment; assumptions stated in the change summary. |
| Timing | Runs inline inside the router's run → inside the SSE stream. Seconds, not background minutes. `workspace_item_updated` SSE refreshes the panel. |
| Context surface | The target artifact's full `content_md` is fetched fresh and injected into the editor's prompt deterministically (unfold philosophy — no read tools). One artifact per editor instance. |
| No-paraphrase rule | The editor receives the **raw user message** via deps (like `save_memo`), plus a router-supplied `task` arg whose prompt contract is "quote the user's words for the part that applies to this artifact" — needed only to split a multi-artifact request. |

## Why an agent and not a bare tool on the router

The router must stay cheap and must not load full artifacts to do edit work itself (its context is summaries). The editor is one flash call that gets the whole document, reasons about *every* location needing change — including grammatical ripples a find-replace misses — then emits one batch tool call. Router pays only the tool-return summary.

## Files

### 1. `agents/tool_repository/edit_supabase_md.py` — MODIFY (batch + snapshot)
- New pure function `apply_edits(content, pairs) -> (new_content, list[Match])`:
  - Locate every pair against the **original** snapshot (existing `locate` ladder per pair).
  - Reject overlapping spans (`MatchError` naming the colliding pair indices).
  - Apply sorted by `start` descending so offsets never shift.
  - **All-or-nothing**: any pair failing to locate fails the whole batch; the `ModelRetry` hint lists each failing pair index + its specific hint (not-found closest-line / non-unique count).
- Tool signature becomes `edits: list[EditPair]` (pydantic model `{old_text, new_text}`), keep `dry_run`. Single edit = list of one.
- `_write` also sets `prev_content_md = <pre-edit content>` in the same guarded UPDATE (version guard on `updated_at` unchanged).
- Keep pure engine dependency-free; extend existing unit tests (overlap, duplicate old_text across pairs, whitespace drift, Arabic text).

### 2. `shared/db/migrations/067_workspace_items_prev_content.sql` — NEW
```sql
ALTER TABLE public.workspace_items
    ADD COLUMN IF NOT EXISTS prev_content_md text;
COMMENT ON COLUMN public.workspace_items.prev_content_md IS
    'Pre-edit snapshot written by the artifact editor in the same UPDATE as content_md. One-level undo; overwritten on each edit.';
```
- Per `project_migration_drift`: verify live schema via Supabase MCP first, apply via `apply_migration`, idempotent. No RLS change (column on an RLS'd table).

### 3. `agents/artifact_editor/` — NEW (agent + runner)
- `__init__.py`, `agent.py`:
  - `EditorDeps`: `supabase`, `item_id`, `artifact_title`, `artifact_content_md` (fetched fresh by the runner), `user_message` (raw, verbatim), `task` (router's scoped quote). Satisfies `HasSupabase` for the edit tool.
  - Output `EditorResult`: `status: Literal["edited","no_change","failed"]`, `change_summary: str` (Arabic, 1–3 sentences: what changed, where, count), `assumptions: str | None`, `edits_applied: int`.
  - deepseek-flash structured-output-as-text trap (`project_structured_output_salvage`) → register the `TextOutput` JSON salvager from `agents/utils/structured_output.py` alongside `EditorResult`.
  - System prompt (Arabic): surgical legal-document editor; the artifact is below with its item_id; identify EVERY location the request touches **including grammatical agreement around each change**; issue ONE `edit_supabase_md` call carrying the whole batch; quotes must be verbatim and unique (add a surrounding line if not); after the tool confirms, return `EditorResult` — do not rewrite the document, do not add content beyond the request, never address the user.
  - **Deletion rules** (deletion = pair with `new_text=""`): when deleting a numbered point/clause, the SAME batch must also (a) renumber subsequent items («البند الرابع» → «البند الثالث», ordinal schemes أولاً/ثانياً likewise), (b) patch or remove cross-references anywhere in the document to the deleted/renumbered items («كما ورد في البند الرابع»), (c) fix enumeration/transition sentences whose counts changed («للأسباب الثلاثة»), and (d) include the block's surrounding blank lines/separators in `old_text` so no orphaned `---` or double blank line remains. All-or-nothing batching guarantees no half-state (clause gone, numbering stale). Sweeping deletions woven through the argument («احذف كل ما يتعلق بـ X») are a restructure → router routes to writing, not here.
  - Tools: `register_edit_supabase_md(agent)` only.
  - `UsageLimits`: `request_limit=4`, `tool_calls_limit=5`, `output_tokens_limit=8000` (batch new_text can be sizable).
  - Runner `run_artifact_editor(...) -> EditorResult`: fetch `content_md` + `title` for `item_id`; refuse kinds it must not touch (allow `agent_writing`, `note`; for `agent_search` return `failed` with reason — search reports are regenerable, edits to them are almost certainly misroutes); wrap with `run_tracked(slot="artifact_editor", agent_family="editing")` → llm_calls row lands automatically inside the turn's `collect_llm_calls` scope (`agent_family` is text — no enum change).

### 4. `agents/tool_repository/edit_artifact.py` — NEW (the router tool, save_memo pattern)
- `register_edit_artifact(agent)`; deps contract = `HasMemoContext`-style protocol needing `supabase`, `user_id`, `user_message`, `wi_alias_map`, `pending_sse_events` (RouterDeps already satisfies it).
- Tool `edit_artifact(ctx, target_wi: str, task: str) -> str`:
  1. Resolve `target_wi` via `deps.wi_alias_map` (`_resolve_wi_alias` logic — import or duplicate the small helper); `ModelRetry` on unknown alias.
  2. Run `run_artifact_editor(...)` with the raw `deps.user_message` + `task`.
  3. On `status="edited"`: push `{"type": "workspace_item_updated", "item_id": ...}` onto `deps.pending_sse_events` (drained by `run_router` → `_route`, already forwarded by message_service and handled by use-chat.ts → query invalidation).
  4. Return the Arabic `change_summary` (or no-change/failure explanation) as the tool result — this is what the router uses to brief the user.
- Parallel fan-out is free: the router model emits 2–3 `edit_artifact` calls in one response; pydantic-ai runs them concurrently; each editor owns one `item_id` so the optimistic locks never collide.

### 5. `agents/utils/agent_models.py` — MODIFY
- `Reasoning = Literal["default", "medium", "max"]`; `_reasoning_settings` gains the `"medium"` branch:
  - deepseek-on-Alibaba → `{"thinking": {"type": "enabled"}, "reasoning_effort": "medium"}`
  - OpenRouter → `{"reasoning": {"effort": "medium"}}`
  - qwen-on-Alibaba → `enable_thinking=True`, `thinking_budget=8_000`
- New slot: `"artifact_editor": ModelPolicy("tier_2", primary="deepseek", reasoning="medium")`.

### 6. `agents/router/router.py` — MODIFY
- `register_edit_artifact(router_agent)`.
- Prompt additions:
  - New section **«متى تستخدم أداة edit_artifact»**: clearly-scoped surgical edit on an existing WI (استبدال لفظ، حذف فقرة محددة، تصحيح اسم/رقم/تاريخ، إعادة صياغة جملة بعينها) → call `edit_artifact(target_wi, task)` where `task` quotes the user's words; multiple artifacts → one call per WI in the same response, max 3; after the tool(s) return, issue `ChatResponse` briefing the user on what changed (from the summaries). **Conservative rule**: structural changes (أضف قسماً، أعد هيكلة، فصّل أكثر), anything needing new legal sources, or vague enhancement requests («حسّن الصياغة» across the whole doc) → still `DispatchAgent` to `writing` with `target_wi`.
  - Amend the provenance section (`«عدّل البند»` example currently routes to writing): surgical-edit phrasings on the last tagged WI now go to `edit_artifact`; expansion/enhancement phrasings keep routing to `writing`.
- `ROUTER_LIMITS` unchanged (`request_limit=5`, `tool_calls_limit=8` accommodate edit calls + final output); `end_strategy="exhaustive"` already runs batched tool calls.

### 7. `backend/app/services/message_service.py` — MODIFY (one block)
- In the SSE drain, `workspace_item_updated` should ALSO append `item_id` to `captured_artifact_ids` (today only `workspace_item_created` does) so `messages.artifact_ids` records the edited WI and `_load_wi_provenance` tags the router's reply turn — keeping «refine the last artifact» chains working after an edit turn.

### 8. Tests
- `agents/tool_repository/tests` (or alongside existing edit tests): `apply_edits` batch semantics — overlap rejection, all-or-nothing, descending-offset application, whitespace fallback per pair, Arabic/RTL.
- `agents/artifact_editor/tests`: TestModel run — editor emits one batched tool call, returns `EditorResult`; kind-refusal path; salvager path.
- Router: registration smoke (tool present), alias-resolution failure → ModelRetry.

## Failure & edge handling
- **Concurrent UI edit**: version-guard miss → `ModelRetry` → editor re-reads? No — the runner injected content once. The retry hint tells the model the doc changed; the tool's `_fetch` happens inside the tool so the *tool* sees fresh content, but the model's quotes came from the stale injected copy. Mitigation: on a version-conflict retry, the tool returns the CURRENT content excerpt around the failed quote in the hint so the model can re-quote. (Implementation note: keep it simple — include the fresh content in the ModelRetry hint only when it differs; cap the excerpt.)
- **Nothing to edit** (request already satisfied): `status="no_change"`, router tells the user honestly.
- **Misroute to a search artifact**: runner refuses, router falls back to explaining or dispatching.
- **Huge artifact**: content injected whole; flash context is ample for legal docs (~7k words observed); no chunking in v1.

## Validation plan
1. Unit tests green.
2. Local turn against the real backend: reproduce convo `21b0d2f4` Turn 5 («بدل كلمة الطاعنة اذكر موكلتي…») on a seeded brief → expect: router calls `edit_artifact`, edit lands in seconds within the stream, panel refreshes, router briefs in the same turn — no 275s background gap, no new WI.
3. `/convo-monitor` the test conversation: verify one `artifact_editor` llm_calls row (agent_family=editing), cost ≪ writer re-run.
4. Verify `prev_content_md` holds the pre-edit text and `messages.artifact_ids` carries the edited item.

## Out of scope (deferred)
- Revision history table / restore endpoint (column is one-level only).
- ask_user pause/resume for ambiguous edits.
- Editor handling research-dependent edits (router keeps routing those to deep_search→writing).
- Frontend diff view of an edit.
