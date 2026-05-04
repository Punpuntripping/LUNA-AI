# Wave 8: Conversation Workspace — Two-Screen Layout with Unified Workspace Items

> **Supersedes:** `archive/task_orchestration_layer.md` (task abstraction dropped — over-engineered)
> **Dependencies:** Wave 6B (artifacts table, service, routes), Wave 7A (SSE heartbeat)
> **Date:** 2026-05-01

---

## Overview

Redesign the conversation page into a **two-pane layout**: chat on one side, workspace on the other. Each conversation owns a bundle of "workspace items" (attachments, notes, agent outputs, conversation context, references) shown as chips above the chat. Clicking a chip opens that item in the workspace pane.

**Current:** sidebar | chat (full width) | optional 400px artifact panel
**New:** sidebar | (chat | workspace) — siblings inside a resizable split, default 50/50, workspace collapsible

The existing `artifacts` table already covers ~90% of what we need. We extend it (add `kind`, file pointers, lock column), rename it to `workspace_items`, and reuse the existing service/routes/UI as the foundation.

---

## Goals

1. **One unified table** for everything attached to a conversation — no separate `notes`/`drafts`/`references` tables.
2. **Per-conversation, not per-case.** Items belong to a conversation. Attachments can *link* to case documents (no file duplication).
3. **Permission is deterministic from `kind`** — no separate permission column, no policy mistakes.
4. **Two-pane resizable layout** with a context chip bar. Workspace renders any kind via a kind-switched renderer.
5. **Agents read all visible workspace items** as part of their input context (router, deep_search, planner, writer).

---

## Workspace Item Kinds

| `kind` | Content lives in | Who edits | UX role |
|---|---|---|---|
| `attachment` | Supabase Storage (`storage_path`) OR existing `case_documents` (`document_id` FK) | nobody — viewable only | uploaded PDF/PNG, or pinned from case library |
| `note` | inline MD (`content_md`) | user only | lawyer's draft pad |
| `agent_search` | inline MD (`content_md`) | nobody — immutable output | "بحث عن:" research output |
| `agent_writing` | inline MD (`content_md`) | user + agent (turn-locked) | drafted document — both can revise |
| `convo_context` | inline MD (`content_md`) | agent only | running summary; user can hide via chip toggle |
| `references` | inline MD (`content_md`) — placeholder | TBD | placeholder; spec deferred |

`agent_search` and `agent_writing` carry a free-form `metadata.subtype` string (e.g. `report`, `contract`, `memo`, `legal_opinion`) that drives chip color/icon. Backend doesn't validate — frontend treats unknown subtypes as a default style.

---

## Schema — Migration `026_workspace_items.sql`

```sql
-- 1. New enum
CREATE TYPE workspace_item_kind AS ENUM (
  'attachment', 'note', 'agent_search', 'agent_writing', 'convo_context', 'references'
);
CREATE TYPE workspace_creator AS ENUM ('user', 'agent');

-- 2. Rename existing table & PK column
ALTER TABLE artifacts RENAME TO workspace_items;
ALTER TABLE workspace_items RENAME COLUMN artifact_id TO item_id;

-- 3. New columns
ALTER TABLE workspace_items
  ADD COLUMN kind          workspace_item_kind  NOT NULL DEFAULT 'agent_writing',
  ADD COLUMN created_by    workspace_creator    NOT NULL DEFAULT 'agent',
  ADD COLUMN storage_path  TEXT,
  ADD COLUMN document_id   UUID REFERENCES case_documents(document_id),
  ADD COLUMN is_visible    BOOLEAN              NOT NULL DEFAULT true,
  ADD COLUMN locked_by_agent_until TIMESTAMPTZ;

-- 4. Backfill: migrate existing artifact_type → kind + metadata.subtype
UPDATE workspace_items
SET kind = CASE WHEN is_editable THEN 'agent_writing' ELSE 'agent_search' END,
    created_by = 'agent',
    metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('subtype', artifact_type::text);

-- 5. Drop old columns
ALTER TABLE workspace_items
  DROP COLUMN is_editable,
  DROP COLUMN artifact_type;

DROP TYPE IF EXISTS artifact_type_enum;

-- 6. Content shape constraint
ALTER TABLE workspace_items
  ALTER COLUMN content_md DROP NOT NULL,
  ADD CONSTRAINT workspace_content_shape CHECK (
    (kind = 'attachment' AND (storage_path IS NOT NULL OR document_id IS NOT NULL))
    OR (kind <> 'attachment' AND content_md IS NOT NULL)
  );

-- 7. Indexes
CREATE INDEX idx_workspace_items_kind ON workspace_items (conversation_id, kind) WHERE deleted_at IS NULL;
CREATE INDEX idx_workspace_items_visible ON workspace_items (conversation_id) WHERE deleted_at IS NULL AND is_visible = true;

-- 8. Fix dependent FK in retrieval_artifacts (migration 023): artifact_id used to
-- reference artifacts(artifact_id); column was renamed to workspace_items(item_id).
-- ALTER...RENAME on the parent table cascades the FK target automatically, but
-- the column rename does not -- the FK now points at workspace_items(item_id).
-- No SQL change needed here; verified by re-running 023's CHECK on a clean DB.
-- TODO: smoke-test FK after migration applies; if Postgres did NOT auto-update,
-- add: ALTER TABLE retrieval_artifacts DROP CONSTRAINT retrieval_artifacts_artifact_id_fkey,
--      ADD CONSTRAINT retrieval_artifacts_item_id_fkey FOREIGN KEY (artifact_id) REFERENCES workspace_items(item_id);
```

RLS policies inherited from `artifacts` (user_id-based) — already correct, just renamed.

**Backing tables left in place (NOT renamed):**
- `retrieval_artifacts` (migration 023) — URA per deep_search turn
- `reranker_runs` (migration 024) — pre-merge reranker output per sub-query
- These are forensic/backing data, not user-facing. They keep their names; their `artifact_id` FK now points at `workspace_items(item_id)` automatically via Postgres rename cascade.

### deep_search_v4 → workspace_items mapping

The deep_search_v4 aggregator (`agents/deep_search_v4/aggregator/`) is the producer. Per turn:

| Producer output | Persistence target | Notes |
|---|---|---|
| `agg_output.artifact.content` (full synthesis_md + reference block + disclaimer) | `workspace_items.content_md` with `kind='agent_search'` | This is the **search.md** the user sees in the workspace pane |
| `agg_output.artifact.title` | `workspace_items.title` | First 80 chars of original query |
| `agg_output.artifact.references_json`, `confidence`, `detail_level`, `subtype='legal_synthesis'` | `workspace_items.metadata` | drives chip color/icon + citation popovers |
| `deps._ura` (UnifiedRetrievalArtifact) | `retrieval_artifacts.ura_json` | full merged retrieval object, schema_version='2.0' (or '3.0' if v4 evolves shape) |
| `deps._reg_rqrs` + `_comp_rqrs` + `_case_rqrs` | `reranker_runs` (one row per sub-query per executor) | pre-merge forensic layer |
| `deps._per_executor_stats` | `retrieval_artifacts.produced_by` + `reranker_runs.tokens_*`/`duration_ms` | timing/cost |

`workspace_items.kind = 'agent_search'` because deep_search output is **immutable** — neither user nor agent can edit it once produced. (If we later want a mode where the user can refine/extend the search output into a draft, we'd produce a separate `kind='agent_writing'` row from it, leaving the original untouched.)

### v4 dispatch switch (one-line change)

`agents/orchestrator.py:378` currently imports v3:
```python
from agents.deep_search_v3.orchestrator import FullLoopDeps, run_full_loop
```
After v4 reaches deps-surface parity (must populate `_ura`, `_reg_rqrs`, `_comp_rqrs`, `_case_rqrs`, `_per_executor_stats` and return an `agg_output` with `.artifact`, `.synthesis_md`, `.confidence`, `.log_id`), flip to:
```python
from agents.deep_search_v4.orchestrator import FullLoopDeps, run_full_loop
```
No other changes required — persistence to `retrieval_artifacts` + `reranker_runs` works automatically.

**Pre-flight gate:** `agents/deep_search_v4/tests/test_v4_persistence_parity.py` — six pytest assertions that pin the persistence contract. Must all pass before flipping the import. Verified 2026-05-01: 6/6 PASS.

---

## Backend Changes

### Renames (no logic change)
- `backend/app/services/artifact_service.py` → `workspace_service.py`
- `backend/app/api/artifacts.py` → `workspace.py`
- `ArtifactResponse` → `WorkspaceItemResponse` (in `responses.py`)
- `UpdateArtifactRequest` → `UpdateWorkspaceItemRequest`
- Route prefix `/artifacts` → `/workspace`; `/conversations/{id}/artifacts` → `/conversations/{id}/workspace`

### Updated response shape
```python
class WorkspaceItemResponse(BaseModel):
    item_id: str
    user_id: str
    conversation_id: str
    case_id: Optional[str]
    message_id: Optional[str]
    agent_family: Optional[str]
    kind: str                       # NEW
    created_by: str                 # NEW: user | agent
    title: str
    content_md: Optional[str]       # null for attachments
    storage_path: Optional[str]     # NEW: signed URL fetched separately
    document_id: Optional[str]      # NEW
    is_visible: bool                # NEW
    metadata: dict
    created_at: str
    updated_at: str
```

### New endpoints (in `workspace.py`)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/conversations/{cid}/workspace/notes` | Create `kind=note`. Body: `{ title, content_md }` |
| `POST` | `/conversations/{cid}/workspace/attachments/upload` | Create `kind=attachment` from multipart upload. Stores in Supabase Storage. |
| `POST` | `/conversations/{cid}/workspace/attachments/from-document` | Create `kind=attachment` linking `document_id` from case library |
| `POST` | `/conversations/{cid}/workspace/references` | Create `kind=references` (placeholder) |
| `PATCH` | `/workspace/{id}/visibility` | Toggle `is_visible` (used by convo_context chip) |
| `GET` | `/workspace/{id}/file` | Returns `{ url, expires_at }` — signed Storage URL for attachments |

### Existing endpoints (kept, renamed paths)
- `GET /conversations/{cid}/workspace` — list
- `GET /cases/{cid}/workspace` — list (for case-level surfaces, unchanged behavior)
- `GET /workspace/{id}` — fetch one
- `PATCH /workspace/{id}` — update title/content_md (enforces kind permission, see below)
- `DELETE /workspace/{id}` — soft delete

### Permission enforcement (`workspace_service.update_workspace_item`)

```python
EDITABLE_BY_USER = {"note", "agent_writing"}

if item["kind"] not in EDITABLE_BY_USER:
    raise HTTPException(403, "هذا العنصر غير قابل للتعديل")

if item["kind"] == "agent_writing" and item.get("locked_by_agent_until"):
    if datetime.now(UTC) < parse(item["locked_by_agent_until"]):
        raise HTTPException(409, "Luna يحرر هذا الملف الآن، انتظر لحظة")
```

### Agent context helper (`backend/app/services/workspace_context.py` NEW)

```python
async def load_workspace_context(supabase, conversation_id: str) -> dict:
    """Load all visible workspace items for a conversation, formatted for agent prompt injection."""
    items = supabase.table("workspace_items").select("*").eq(
        "conversation_id", conversation_id
    ).eq("is_visible", True).is_("deleted_at", "null").execute().data
    return {
        "attachments": [...],   # title + signed URL or extracted text
        "notes": [...],         # title + content_md
        "agent_outputs": [...], # title + content_md (search + writing)
        "convo_context": ...,   # single most recent
        "references": [...],
    }
```

Wired into router, deep_search, and writer agents (`agents/deep_search_v3/orchestrator.py`, `agents/orchestrator.py`) — each call site loads workspace context before LLM call.

### Agent lock during streaming (`backend/app/services/message_service.py`)

When an agent begins writing to an `agent_writing` item:
```python
supabase.table("workspace_items").update({
    "locked_by_agent_until": (datetime.now(UTC) + timedelta(seconds=30)).isoformat()
}).eq("item_id", item_id).execute()
```
Refresh every 10s while streaming. Clear on completion or error. Heartbeat-style — survives orphaned streams.

---

## Frontend Changes

### Layout split

`frontend/components/chat/ChatLayoutClient.tsx` — replace fixed `<ArtifactPanel>` with shadcn `<ResizablePanelGroup>`:

```
<Sidebar />
<ResizablePanelGroup direction="horizontal">
  <ResizablePanel defaultSize={isWorkspaceOpen ? 50 : 100}>
    {children}  {/* ChatContainer */}
  </ResizablePanel>
  {isWorkspaceOpen && (
    <>
      <ResizableHandle />
      <ResizablePanel defaultSize={50} minSize={25}>
        <WorkspacePane />
      </ResizablePanel>
    </>
  )}
</ResizablePanelGroup>
```

Need to add via shadcn CLI: `npx shadcn@latest add resizable`.

### New components

| File | Purpose |
|---|---|
| `frontend/components/chat/ConversationContextBar.tsx` | Chip row above message list. One chip per workspace item. `+` dropdown opens creation menu (note / upload file / link from case docs / references placeholder). Click chip → `openWorkspaceItem(id)`. `convo_context` chip has eye icon for hide/show. |
| `frontend/components/workspace/WorkspacePane.tsx` | Container. Header: title + close button. Body: switch on `item.kind` → renderer. |
| `frontend/components/workspace/AttachmentRenderer.tsx` | If `metadata.mime_type` starts with `image/` → `<img src={signedUrl}>`. If `application/pdf` → embedded viewer (`<iframe>` of signed URL). Loading state while signed URL fetches. |
| `frontend/components/workspace/NoteEditor.tsx` | MD editor (textarea + preview tabs, or single textarea). Autosave debounced 800ms. Used for `note` (always editable) and `agent_writing` (editable when not locked, shows "Luna يحرر…" when locked). |
| `frontend/components/workspace/AgentSearchViewer.tsx` | Read-only MD render. Used for `agent_search`. |
| `frontend/components/workspace/ConvoContextViewer.tsx` | Read-only MD render of conversation summary. |
| `frontend/components/workspace/ReferencesRenderer.tsx` | Stub: shows "قيد التطوير" placeholder. |
| `frontend/components/workspace/ChipBar.tsx` | (lives inside ConversationContextBar) — individual chip with kind icon, title, color from `subtype` for agent outputs. |

### Renames

| From | To |
|---|---|
| `frontend/components/artifacts/ArtifactPanel.tsx` | `frontend/components/workspace/WorkspacePane.tsx` (rewritten — split into chip-driven design) |
| `frontend/components/artifacts/ArtifactList.tsx` | DELETE — chips replace the list |
| `frontend/components/artifacts/ArtifactViewer.tsx` | Split into `AgentSearchViewer.tsx` + `NoteEditor.tsx` (NoteEditor used for both `note` and `agent_writing`) |
| `frontend/components/artifacts/ArtifactCard.tsx` | DELETE — chips replace cards |
| `frontend/hooks/use-artifacts.ts` | `frontend/hooks/use-workspace.ts` — rename queries/mutations, add `useCreateNote`, `useCreateAttachment`, `useToggleVisibility`, `useWorkspaceItemSignedUrl` |
| `frontend/lib/api.ts` `artifactsApi` | `workspaceApi` |

### State (`frontend/stores/chat-store.ts`)

```ts
// Remove
isArtifactPanelOpen: boolean
activeArtifactId: string | null
openArtifactPanel(id)
closeArtifactPanel()
toggleArtifactPanel()

// Add
workspace: {
  isOpen: boolean,
  openItemId: string | null,
  splitRatio: number,         // 0..100, persisted to localStorage
}
openWorkspaceItem(itemId)     // sets openItemId + isOpen=true
closeWorkspace()              // isOpen=false, openItemId=null
setSplitRatio(ratio)
```

### Types (`frontend/types/workspace.ts` NEW)

```ts
export type WorkspaceItemKind =
  | "attachment" | "note" | "agent_search"
  | "agent_writing" | "convo_context" | "references";

export interface WorkspaceItem {
  item_id: string;
  conversation_id: string;
  kind: WorkspaceItemKind;
  created_by: "user" | "agent";
  title: string;
  content_md: string | null;
  storage_path: string | null;
  document_id: string | null;
  is_visible: boolean;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}
```

---

## Permission semantics — single source of truth

```ts
const USER_EDITABLE: WorkspaceItemKind[] = ["note", "agent_writing"];
const AGENT_LOCK_APPLIES: WorkspaceItemKind[] = ["agent_writing"];
```

Both backend (`workspace_service`) and frontend (`NoteEditor` shows/hides edit affordance) consult the same kind→capability table. Defined twice (Python + TS) — accept the duplication.

---

## SSE event vocabulary

Three new events plus back-compat aliases during transition (Cut-1 → 8A).

| Event | Payload | When emitted | Replaces |
|---|---|---|---|
| `workspace_item_created` | `{ item_id, kind, title, subtype?, created_by }` | After workspace_items insert (any kind) | `artifact_created` |
| `workspace_item_updated` | `{ item_id }` | After update (e.g. agent_writing edit) | `artifact_updated` |
| `workspace_item_locked` | `{ item_id, locked_until }` | When agent acquires lock on agent_writing | NEW |
| `workspace_item_unlocked` | `{ item_id }` | When agent releases lock | NEW |

During Cut-1 the publisher emits **both** `workspace_item_created` and the legacy `artifact_created` so the existing frontend keeps working. Wave 8B drops the legacy alias once the frontend rename lands.

---

## Cut-1 — Ship value on the existing schema (no migration)

**Goal:** real writer + research experience this week, without waiting for migration 026 or any frontend rename. Everything Cut-1 produces is forward-compatible with Wave 8A.

### Cut-1 includes

1. **v4 cutover (one-line).** Flip `agents/orchestrator.py:378` import from v3 → v4. Pre-flight gate `agents/deep_search_v4/tests/test_v4_persistence_parity.py` already passes (6/6).

2. **`agents/agent_search/` package — publishing adapter, NOT an LLM.**
   ```
   agent_search/
     __init__.py
     models.py         # SearchPublishInput / SearchPublishOutput
     publisher.py      # takes AggregatorOutput → workspace_items row
     deps.py           # supabase + emit_sse handle
     tests/test_publisher.py
   ```
   - Consumes the existing `AggregatorOutput` from `run_full_loop` (no extra LLM call).
   - Writes one row to `artifacts` table with `artifact_type='legal_synthesis'`, `is_editable=False` (these map to `kind='agent_search'` after migration 026 backfill).
   - Emits `workspace_item_created` + legacy `artifact_created`.
   - Replaces the inline persistence block at `agents/orchestrator.py:436-510`.

3. **`agents/agent_writer/` package — real Pydantic AI agent.**
   ```
   agent_writer/
     __init__.py
     models.py         # WriterInput / WriterLLMOutput / WriterOutput
     prompts.py        # WRITER_PROMPTS dict, one per subtype
     agent.py          # Pydantic AI agent: structured-output sections
     publisher.py      # WriterLLMOutput → workspace_items row
     deps.py
     runner.py         # handle_writer_turn(input, deps)
     tests/test_lock.py
     tests/test_publisher.py
   ```
   - **Inputs:** `user_request`, `subtype` (`contract|memo|legal_opinion|defense_brief|letter|summary`), `research_items` (optional list of agent_search content_md), `workspace_context` (notes + attachments + convo_context), `revising_item_id`, `detail_level`, `tone`.
   - **LLM output:** `WriterLLMOutput { title_ar, sections[{heading_ar, body_md}], citations_used, confidence, notes_ar }`.
   - **Persistence:** writes to `artifacts` with `artifact_type='memo'` (or matching subtype), `is_editable=True` (maps to `kind='agent_writing'` after backfill). Sets `metadata.subtype`, `metadata.research_item_ids`, `metadata.tone`.
   - **Lock:** acquires `locked_by_agent_until = now() + 30s` on the row before streaming, releases on completion. (Column doesn't exist yet — Cut-1 stores it in `metadata.locked_until` as a stopgap; 8A migration moves it to a real column.)
   - **Models:** primary `qwen3.6-plus`, fallback `gemini-3-flash`, temperature 0.4.
   - **Versioning:** on revision (`revising_item_id` provided), soft-deletes the old row and inserts a new one.

4. **Citation pipeline fix** — addresses `v4_app_integration` GAP-1, GAP-2, GAP-4.
   - **Backend:** add `_reference_to_citation(ref)` in `backend/app/services/message_service.py` that maps v4 `Reference` → frontend `Citation` shape (`article_id` ← `ref_id`, `law_name` ← `regulation_title`, `article_number` ← `int(article_num)`, `relevance_score` from tier).
   - **Frontend:** add `streamingCitations` to `chat-store.ts`. Wire `case "citations"` in `use-chat.ts` to `setCitations`. Pass to `MessageBubble` from `ChatContainer`. Persist into message metadata at `done` so they survive reload.

5. **Router upgrade** — add a new `task_type="writing"` and a `list_workspace_items(conversation_id)` tool so the router can see existing chips and reference them when opening a writing task.

6. **No schema changes, no renames, no UI overhaul.** All Cut-1 work writes to current `artifacts` table and consumes current frontend.

### Cut-1 file inventory

**New (~16):** 7 files in `agent_search/`, 9 files in `agent_writer/` (incl. tests).

**Modified (~6):** `agents/orchestrator.py` (import flip + delegate persistence to publishers), `agents/router/router.py` (add writing task_type + workspace tool), `agents/models.py` (extend `OpenTask.task_type`), `backend/app/services/message_service.py` (Reference→Citation mapper, emit new SSE events), `frontend/hooks/use-chat.ts` (wire citations), `frontend/stores/chat-store.ts` (streamingCitations), `frontend/components/chat/ChatContainer.tsx` (pass citations to MessageBubble).

**Deleted (~0):** none yet.

### Cut-1 success criteria

- User asks for a contract draft → agent_writer produces editable `memo`-type artifact, ArtifactViewer (existing) renders it, `is_editable=True` lets the user edit.
- User asks "بحث عن…" → deep_search_v4 produces synthesis → agent_search publisher persists → ArtifactViewer renders, citations appear in CitationPills under MessageBubble.
- Citations pill click navigates to source (existing ArtifactViewer behavior).
- All existing Wave 7 tests still pass.

**Agents:** `@fastapi-backend` (router + message_service + Reference mapper), new agent definitions for `@agent-search-builder` + `@agent-writer-builder` if needed (or generic Python work via `@fastapi-backend`), `@nextjs-frontend` (citation wiring), `@validate`.

---

## Wave breakdown

### 8A — Schema + backend (server-only, behind-the-scenes)
- Migration 026 (rename artifacts → workspace_items, add columns, backfill `legal_synthesis`→`agent_search`, `memo`/`contract`/etc.→`agent_writing`)
- Service rename + new endpoints (notes, attachments upload/from-document, references stub, visibility toggle, signed URL)
- Promote `metadata.locked_until` (Cut-1 stopgap) into the real `locked_by_agent_until` column
- Permission/lock enforcement on update keyed on `kind` (drop `is_editable` reads)
- `workspace_context` helper + wire into agent_writer + router prompt context
- `agent_search.publisher` and `agent_writer.publisher` switch from `artifact_type` writes to direct `kind`/`created_by` writes
- Drop legacy SSE aliases (`artifact_created`/`artifact_updated`) — emit only `workspace_item_*`
- `@validate` regression: full citation flow + writer round-trip + ArtifactViewer (still pre-rename) still rendering

**Agents:** `@sql-migration`, `@fastapi-backend`, `@validate`

### 8B — Frontend rename pass (no UX change)
- Rename store fields, hook, api client, types — keep current single-pane UI working
- Verify chat page still functions, agent outputs still render
- Generate TS types from backend

**Agents:** `@nextjs-frontend`, `@integration-lead`

### 8C — Two-pane layout + chip bar
- Add shadcn resizable
- Build `ConversationContextBar` with `+` dropdown
- Build `WorkspacePane` with kind-switched renderers
- `AttachmentRenderer` (PDF + image)
- `NoteEditor` (MD editor with autosave) — used for `note` only at this stage
- `AgentSearchViewer`, `ConvoContextViewer`, `ReferencesRenderer` (stub)
- Persist `splitRatio` to localStorage

**Agents:** `@nextjs-frontend`, `@frontend-planner`

### 8D — Collaborative writing + lock
- Wire `NoteEditor` to handle `agent_writing` items
- Lock indicator UI ("Luna يحرر…")
- Backend: agents that produce writing artifacts switch from `kind=agent_search` to `kind=agent_writing` and acquire locks during streaming
- Conflict toast when user tries to edit during lock

**Agents:** `@nextjs-frontend`, `@fastapi-backend`, `@sse-streaming`, `@validate`

---

## Out of scope (deferred)

- **Real-time collab CRDT** — turn-based lock is sufficient
- **`references` content spec** — kind reserved, renderer is placeholder
- **Mobile layout** — focus on desktop split; mobile collapses workspace by default
- **Workspace items at case level** — items remain conversation-scoped; case-level documents/memories unchanged
- **Versioning / history** — soft delete only, no per-edit version trail
- **Multi-item open simultaneously** — one item open at a time; chips switch focus

---

## Open items (decide before 8A starts)

1. **Convo_context generation cadence:** when does the agent (re)generate it? After every N messages? On-demand? Out of scope for 8A — defer to 8D, ship initially as user-only manual.
2. **Attachment storage bucket:** new bucket `workspace-attachments` or reuse `case-documents`? Recommend new bucket scoped to user_id with conversation prefix.
3. **`task_type` field on `SendMessageRequest` and SSE event names `task_started`/`task_ended`:** unrelated to deleted task plan, leave alone.

---

## Files inventory (estimate, all cuts combined)

**New (~30):**
- Cut-1: 7 in `agents/agent_search/`, 9 in `agents/agent_writer/`
- 8A: 1 SQL migration, 1 backend service helper (`workspace_context.py`)
- 8C: 7 new frontend components, 1 frontend types file (`workspace.ts`), 1 hooks file (renamed)

**Renamed (~8):** `artifact_service` → `workspace_service`, `artifacts` route → `workspace`, response/request models, artifact frontend components/hooks/api

**Modified (~12):** Cut-1: orchestrator.py (import flip + delegate), router.py (writing task), models.py (OpenTask), message_service.py (Reference→Citation + new SSE), use-chat.ts, chat-store.ts, ChatContainer.tsx. 8A: workspace_service writes kind directly. 8C: ChatLayoutClient (split), globals.css (resizable handle).

**Deleted (~2):** ArtifactList, ArtifactCard (chips replace them in 8C)
