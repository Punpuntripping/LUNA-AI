# Wave 6D — Frontend Integration (Types, Hooks, UI Components)

> **Parent:** `wave_6_integration_overview.md`
> **Dependencies:** Wave 6B (backend APIs ready) + Wave 6C (SSE events include artifact_created)
> **Build Agent:** @nextjs-frontend
> **Quality Agents:** @validate, @security-reviewer, @integration-lead
> **MCP:** Playwright (E2E browser tests), shadcn (component install), ESLint (lint)

---

## Pre-Flight Checks

```
1. Verify Gate 6C passed (router works, artifact_created events flow)
2. Backend running: curl http://localhost:8000/health → 200
3. Frontend running: curl http://localhost:3000 → 200
4. Read existing frontend/types/index.ts → understand current type shapes
5. Read existing frontend/lib/api.ts → understand apiFetch pattern
6. Read existing frontend/hooks/use-chat.ts → understand SSE event handling
7. Read existing frontend/stores/chat-store.ts → understand state shape
8. Check shadcn components: mcp__shadcn__list_items_in_registries → check what's installed
```

---

## Stage 1: TypeScript Types + API Client (2 modified files)

### 1.1 `frontend/types/index.ts` (MODIFY)

Add to existing types file:

```typescript
// ---- Agent & Artifact Types ----

export type AgentFamily = 'deep_search' | 'simple_search' | 'end_services' | 'extraction' | 'memory';

export type ArtifactType = 'report' | 'contract' | 'memo' | 'summary' | 'memory_file' | 'legal_opinion';

export interface Artifact {
  artifact_id: string;
  user_id: string;
  conversation_id: string | null;
  case_id: string | null;
  agent_family: AgentFamily;
  artifact_type: ArtifactType;
  title: string;
  content_md: string;
  is_editable: boolean;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ArtifactListResponse {
  artifacts: Artifact[];
  total: number;
}

// ---- Preferences & Templates ----

export interface UserPreferences {
  user_id: string;
  preferences: Record<string, unknown>;
}

export interface UserTemplate {
  template_id: string;
  user_id: string;
  title: string;
  description: string;
  prompt_template: string;
  agent_family: AgentFamily;
  is_active: boolean;
  created_at: string;
}

export interface TemplateListResponse {
  templates: UserTemplate[];
  total: number;
}

// ---- SSE Event Updates ----

export interface SSEArtifactCreated {
  artifact_id: string;
  artifact_type: ArtifactType;
  title: string;
}

export interface SSEAgentSelected {
  agent_family: AgentFamily;
}

// ---- Send Message Payload Update ----

export interface SendMessagePayload {
  content: string;
  agent_family?: AgentFamily | null;
  modifiers?: string[] | null;
}
```

### 1.2 `frontend/lib/api.ts` (MODIFY)

Add new API namespaces following existing patterns (`casesApi`, `conversationsApi`):

```typescript
// ---- Artifacts API ----
export const artifactsApi = {
  listByConversation: (conversationId: string) =>
    apiFetch(`/conversations/${conversationId}/artifacts`),

  listByCase: (caseId: string) =>
    apiFetch(`/cases/${caseId}/artifacts`),

  get: (artifactId: string) =>
    apiFetch(`/artifacts/${artifactId}`),

  update: (artifactId: string, data: { title?: string; content_md?: string }) =>
    apiFetch(`/artifacts/${artifactId}`, { method: 'PATCH', body: JSON.stringify(data) }),

  delete: (artifactId: string) =>
    apiFetch(`/artifacts/${artifactId}`, { method: 'DELETE' }),
};

// ---- Preferences API ----
export const preferencesApi = {
  get: () => apiFetch('/preferences'),

  update: (preferences: Record<string, unknown>) =>
    apiFetch('/preferences', { method: 'PATCH', body: JSON.stringify({ preferences }) }),
};

// ---- Templates API ----
export const templatesApi = {
  list: () => apiFetch('/templates'),

  create: (data: { title: string; description?: string; prompt_template: string; agent_family?: string }) =>
    apiFetch('/templates', { method: 'POST', body: JSON.stringify(data) }),

  update: (templateId: string, data: Record<string, unknown>) =>
    apiFetch(`/templates/${templateId}`, { method: 'PATCH', body: JSON.stringify(data) }),

  delete: (templateId: string) =>
    apiFetch(`/templates/${templateId}`, { method: 'DELETE' }),
};
```

**Modify `messagesApi.send()`** to accept optional fields:
```typescript
// Current: send(conversationId, content, signal)
// Updated: send(conversationId, content, signal, options?)
send: (
  conversationId: string,
  content: string,
  signal?: AbortSignal,
  options?: { agent_family?: string; modifiers?: string[] }
) => {
  const body: Record<string, unknown> = { content };
  if (options?.agent_family) body.agent_family = options.agent_family;
  if (options?.modifiers?.length) body.modifiers = options.modifiers;

  return fetch(`${BASE_URL}/conversations/${conversationId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getAccessToken()}` },
    body: JSON.stringify(body),
    signal,
  });
},
```

---

## Stage 2: React Query Hooks (2 new files)

### 2.1 `frontend/hooks/use-artifacts.ts` (NEW)

```typescript
// Query keys
export const artifactKeys = {
  all: ['artifacts'] as const,
  byConversation: (id: string) => [...artifactKeys.all, 'conversation', id] as const,
  byCase: (id: string) => [...artifactKeys.all, 'case', id] as const,
  detail: (id: string) => [...artifactKeys.all, id] as const,
};

// Hooks
export function useConversationArtifacts(conversationId: string | undefined) { ... }
export function useCaseArtifacts(caseId: string | undefined) { ... }
export function useArtifact(artifactId: string | undefined) { ... }
export function useUpdateArtifact() { ... }  // useMutation
export function useDeleteArtifact() { ... }  // useMutation
```

### 2.2 `frontend/hooks/use-preferences.ts` (NEW)

```typescript
export const preferencesKeys = {
  all: ['preferences'] as const,
  templates: ['templates'] as const,
};

export function usePreferences() { ... }
export function useUpdatePreferences() { ... }
export function useTemplates() { ... }
export function useCreateTemplate() { ... }
export function useUpdateTemplate() { ... }
export function useDeleteTemplate() { ... }
```

---

## Stage 3: Update SSE Handler + Chat Store (2 modified files)

### 3.1 `frontend/hooks/use-chat.ts` (MODIFY)

Add handlers for new SSE events in `handleSSEEvent()`:

```typescript
case "artifact_created": {
  const payload = data as SSEArtifactCreated;
  // Invalidate artifacts query so panel refreshes
  void qc.invalidateQueries({ queryKey: artifactKeys.byConversation(conversationId) });
  // Open artifact panel and set active artifact
  useChatStore.getState().openArtifactPanel(payload.artifact_id);
  break;
}

case "agent_selected": {
  const payload = data as SSEAgentSelected;
  useChatStore.getState().setSelectedAgentFamily(payload.agent_family);
  break;
}
```

**Update `sendMessage` call** to pass `agent_family` and `modifiers` from chat store:

```typescript
const { selectedAgentFamily, modifiers } = useChatStore.getState();
const response = await messagesApi.send(
  conversationId, content, controller.signal,
  { agent_family: selectedAgentFamily ?? undefined, modifiers: modifiers.length ? modifiers : undefined }
);
```

### 3.2 `frontend/stores/chat-store.ts` (MODIFY)

Add new state fields and actions:

```typescript
// New state
selectedAgentFamily: AgentFamily | null;
modifiers: string[];
isArtifactPanelOpen: boolean;
activeArtifactId: string | null;

// New actions
setSelectedAgentFamily: (family: AgentFamily | null) => void;
setModifiers: (mods: string[]) => void;
openArtifactPanel: (artifactId: string) => void;
closeArtifactPanel: () => void;
toggleArtifactPanel: () => void;
resetAgentSelection: () => void; // Clear after send
```

---

## Stage 4: `@` Parser + Template Cards (3 new + 2 modified files)

### 4.1 `frontend/lib/commands.ts` (NEW)

Static `@` command registry and parser:

```typescript
export interface AtCommand {
  trigger: string;       // Arabic command (e.g., "بحث_معمق")
  label: string;         // Display label (e.g., "بحث معمق")
  description: string;   // Arabic description
  agent_family?: AgentFamily;
  is_modifier?: boolean; // true for @خطة, @تأمل
}

export const AT_COMMANDS: AtCommand[] = [
  { trigger: "بحث_معمق", label: "بحث معمق", description: "بحث قانوني معمق مع تقرير", agent_family: "deep_search" },
  { trigger: "بحث", label: "بحث بسيط", description: "بحث سريع في الأنظمة", agent_family: "simple_search" },
  { trigger: "عقد", label: "إنشاء عقد", description: "إنشاء مسودة عقد أو مستند", agent_family: "end_services" },
  { trigger: "استخراج", label: "استخراج معلومات", description: "استخراج بيانات من مستند", agent_family: "extraction" },
  { trigger: "ذاكرة", label: "ذاكرة القضية", description: "إدارة ذاكرة القضية", agent_family: "memory" },
  { trigger: "خطة", label: "وضع خطة", description: "التخطيط قبل التنفيذ", is_modifier: true },
  { trigger: "تأمل", label: "تأمل", description: "مراجعة وتحليل", is_modifier: true },
];

export interface ParseResult {
  content: string;              // Text after removing @ commands
  agent_family: AgentFamily | null;
  modifiers: string[];
}

export function parseAtCommands(input: string): ParseResult { ... }
export function filterCommands(query: string): AtCommand[] { ... }
```

### 4.2 `frontend/components/chat/AtCommandPalette.tsx` (NEW)

Autocomplete popup triggered when user types `@` in chat input:

- Appears below/above cursor position
- Filters commands as user types after `@`
- Arrow key navigation (up/down) + Enter to select
- Arabic labels and descriptions
- RTL-compatible positioning
- Dismisses on Escape or clicking outside

**Dependencies:** `commands.ts` for data, no new shadcn components needed (custom popup)

### 4.3 `frontend/components/chat/TemplateCards.tsx` (NEW)

Horizontal row of clickable template cards shown in chat empty state:

- Shows built-in templates + user templates (from `useTemplates()` hook)
- Each card: title + description + agent family badge
- Clicking a card populates chatbox with `prompt_template` and sets `selectedAgentFamily`
- Scrollable horizontal row with RTL support

### 4.4 `frontend/components/chat/ChatInput.tsx` (MODIFY)

Integrate @ parser and command palette:

```typescript
// On keystroke: detect @ trigger
// On send: parseAtCommands(input) → set agent_family + modifiers in chat store
// Show AtCommandPalette when @ detected
// Clear agent selection after successful send
```

### 4.5 `frontend/components/chat/ChatContainer.tsx` (MODIFY)

- Add `<TemplateCards />` row in empty state (when no messages)
- Add artifact panel toggle button in header

---

## Stage 5: Artifact Panel + Viewer (5 new + 2 modified files)

### 5.1 `frontend/components/artifacts/ArtifactPanel.tsx` (NEW)

Collapsible sidebar panel (right side in RTL → left side visually):

- Sections: Reports (تقارير), Contracts (عقود), Memos (مذكرات), Extraction (استخراج)
- Lists artifacts for current conversation using `useConversationArtifacts()`
- Click artifact → opens `ArtifactViewer`
- Collapse/expand toggle
- Empty state: "لا توجد مستندات بعد"

### 5.2 `frontend/components/artifacts/ArtifactCard.tsx` (NEW)

Preview card for artifact list:
- Title (truncated)
- Type badge (colored by artifact_type)
- Relative time (Arabic)
- Click to open in viewer

### 5.3 `frontend/components/artifacts/ArtifactViewer.tsx` (NEW)

Full markdown viewer + editor:
- Renders `content_md` as formatted markdown (RTL)
- If `is_editable`: shows edit toggle button
- Edit mode: textarea with save/cancel
- Save calls `useUpdateArtifact()` mutation
- Arabic UI throughout

### 5.4 `frontend/components/artifacts/ArtifactList.tsx` (NEW)

Reusable list component (used in ArtifactPanel and case artifacts page):
- Groups artifacts by type
- Collapsible sections
- Loading skeleton
- Empty state per section

### 5.5 `frontend/components/memory/MemoryEditor.tsx` (NEW)

Editable markdown view for `memory.md` artifact:
- Full-page editor for case memory
- Auto-save on blur or explicit save button
- Accessible from case detail page
- Arabic placeholder text

### 5.6 `frontend/components/chat/ChatLayoutClient.tsx` (MODIFY)

Add ArtifactPanel as collapsible third column:
```
RTL layout: [ArtifactPanel] | [ChatContainer] | [Sidebar]
LTR layout: [Sidebar] | [ChatContainer] | [ArtifactPanel]
```

- Responsive: panel hidden on mobile, shown as overlay/drawer
- Width: ~400px collapsible
- Controlled by `isArtifactPanelOpen` in chat store

### 5.7 Update imports / wiring in existing components as needed.

---

## Stage 6: Case Artifacts Page (1 new + 1 modified)

### 6.1 `frontend/app/cases/[case_id]/artifacts/page.tsx` (NEW)

Dedicated page listing all artifacts for a case:
- Uses `useCaseArtifacts(caseId)` hook
- Groups by artifact_type
- Click to open `ArtifactViewer` in a dialog/modal
- Arabic headings and labels

### 6.2 Integrate artifacts tab into case detail view

If `app/cases/[case_id]/page.tsx` exists — add artifacts tab/section.
If it doesn't exist — create a simple case detail page with tabs (conversations, documents, memories, artifacts).

---

## Validation Gate 6D (FINAL)

This is the most comprehensive gate — all 6 acceptance tests must pass.

### Automated Checks

| # | Check | Tool | Pass Criteria |
|---|-------|------|---------------|
| 1 | TypeScript compilation | `cd frontend && npx tsc --noEmit` | Zero errors |
| 2 | ESLint | `mcp__eslint__lint-files` | Zero warnings/errors |
| 3 | Frontend build | `cd frontend && npm run build` | Build succeeds |
| 4 | Backend import | `python -c "from backend.app.main import app"` | No errors |

### @integration-lead Checks

| # | Check | Pass Criteria |
|---|-------|---------------|
| 5 | TypeScript `Artifact` ↔ Pydantic `ArtifactResponse` | All fields match |
| 6 | TypeScript `UserTemplate` ↔ Pydantic `TemplateResponse` | All fields match |
| 7 | API URLs in `api.ts` ↔ route paths in FastAPI | All match |
| 8 | SSE event types (artifact_created, agent_selected) | Frontend handles all events backend sends |
| 9 | `SendMessagePayload` ↔ `SendMessageRequest` | Fields match |

### @validate — 6 Acceptance Tests (E2E)

**Test 1: Plain text → Simple Search (auto-routed)**
```
1. Login via API
2. Create conversation
3. POST /messages with { content: "ما هي حقوق العامل؟" }
4. Parse SSE stream
5. Verify: agent_selected event → "simple_search"
6. Verify: token events received (Arabic text)
7. Verify: citations event with articles
8. Verify: done event
9. Verify: NO artifact_created event
10. Browser: navigate to chat → message visible with streaming text
```
**MCP:** `mcp__playwright__browser_navigate`, `browser_snapshot`, `browser_take_screenshot`

**Test 2: @بحث_معمق → Deep Search with artifact**
```
1. Login, create conversation
2. POST /messages with { content: "...", agent_family: "deep_search" }
3. Verify: agent_selected → "deep_search"
4. Verify: token events
5. Verify: artifact_created event with artifact_id, type="report"
6. GET /artifacts/{id} → returns artifact with content_md
7. Browser: artifact panel opens, report card visible
8. Browser: click report → ArtifactViewer shows markdown
```
**MCP:** `mcp__playwright__browser_click`, `browser_snapshot`, `browser_take_screenshot`

**Test 3: @خطة @عقد → End Services with plan modifier**
```
1. Login, create conversation
2. POST /messages with { content: "عقد إيجار تجاري", agent_family: "end_services", modifiers: ["plan"] }
3. Verify: plan tokens appear first (خطة العمل...)
4. Verify: then execution tokens
5. Verify: artifact_created event with type="contract"
6. Verify: artifact is_editable=true
```

**Test 4: Template card click → End Services**
```
1. Browser: navigate to chat (empty state)
2. Verify: template cards row visible
3. Click "عقد إيجار تجاري" template card
4. Verify: chatbox populated with template text
5. Verify: agent_family set to "end_services" in store
6. Send message
7. Verify: correct flow (same as Test 3 without plan modifier)
```
**MCP:** `mcp__playwright__browser_click`, `browser_type`, `browser_snapshot`

**Test 5: @ذاكرة → Memory Agent**
```
1. Login, create case + conversation linked to case
2. POST /messages with { content: "أضف أن المدعي هو شركة الراجحي", agent_family: "memory" }
3. Verify: artifact_created event with type="memory_file"
4. GET /artifacts/{id} → memory.md artifact exists
5. Verify: case_memories table has mock entry (via mcp__supabase__execute_sql)
6. Browser: artifact panel shows memory.md
```
**MCP:** `mcp__supabase__execute_sql`, `mcp__playwright__*`

**Test 6: Artifact editing**
```
1. Login, open conversation with an editable artifact
2. Browser: navigate to chat → open artifact panel
3. Click report artifact → ArtifactViewer opens
4. Verify: edit button visible (for is_editable=true artifacts)
5. Browser: click edit → textarea appears
6. Browser: type new content → click save
7. Verify: PATCH /artifacts/{id} called
8. Verify: viewer refreshes with new content
9. Verify: non-editable artifacts don't show edit button
```
**MCP:** `mcp__playwright__browser_click`, `browser_type`, `browser_snapshot`

### @security-reviewer Checks

| # | Check | Pass Criteria |
|---|-------|---------------|
| 10 | Artifact API auth | All endpoints require valid JWT |
| 11 | Artifact ownership | User cannot access other user's artifacts |
| 12 | Preferences isolation | User cannot read other user's preferences |
| 13 | Agent input validation | agent_family validated against enum (not arbitrary string) |
| 14 | Modifiers validation | Only "plan" and "reflect" accepted |
| 15 | XSS in artifact content | content_md rendered safely (no raw HTML injection) |
| 16 | @ command injection | Parsing doesn't allow arbitrary command execution |

---

## MCP Usage During 6D

| MCP Server | Specific Tools | Purpose |
|------------|---------------|---------|
| **shadcn** | `search_items_in_registries` | Check if we need Popover for AtCommandPalette |
| **shadcn** | `get_add_command_for_items` | Install components (Popover, Sheet for artifact panel) |
| **ESLint** | `lint-files` | Lint all new .tsx/.ts files before commit |
| **Playwright** | `browser_navigate` | Navigate to chat page |
| **Playwright** | `browser_type` | Type messages with @ commands |
| **Playwright** | `browser_click` | Click template cards, artifact items |
| **Playwright** | `browser_snapshot` | Verify DOM state |
| **Playwright** | `browser_take_screenshot` | Visual verification of RTL/Arabic |
| **Playwright** | `browser_wait_for` | Wait for SSE streaming completion |
| **Supabase** | `execute_sql` | Verify artifacts in DB after tests |
| **Supabase** | `generate_typescript_types` | Optional: generate fresh types for cross-check |

---

## File Manifest

| File | Action | Stage |
|------|--------|-------|
| `frontend/types/index.ts` | MODIFY | 1 |
| `frontend/lib/api.ts` | MODIFY | 1 |
| `frontend/hooks/use-artifacts.ts` | NEW | 2 |
| `frontend/hooks/use-preferences.ts` | NEW | 2 |
| `frontend/hooks/use-chat.ts` | MODIFY | 3 |
| `frontend/stores/chat-store.ts` | MODIFY | 3 |
| `frontend/lib/commands.ts` | NEW | 4 |
| `frontend/components/chat/AtCommandPalette.tsx` | NEW | 4 |
| `frontend/components/chat/TemplateCards.tsx` | NEW | 4 |
| `frontend/components/chat/ChatInput.tsx` | MODIFY | 4 |
| `frontend/components/chat/ChatContainer.tsx` | MODIFY | 4 |
| `frontend/components/artifacts/ArtifactPanel.tsx` | NEW | 5 |
| `frontend/components/artifacts/ArtifactCard.tsx` | NEW | 5 |
| `frontend/components/artifacts/ArtifactViewer.tsx` | NEW | 5 |
| `frontend/components/artifacts/ArtifactList.tsx` | NEW | 5 |
| `frontend/components/memory/MemoryEditor.tsx` | NEW | 5 |
| `frontend/components/chat/ChatLayoutClient.tsx` | MODIFY | 5 |
| `frontend/app/cases/[case_id]/artifacts/page.tsx` | NEW | 6 |

**Total: 11 new + 7 modified = 18 files**
