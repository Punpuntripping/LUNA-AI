# Wave 4: Messages + SSE Streaming + Documents + Memories + Mock RAG

> Detailed build plan with agent assignments for every file.
> Source of truth for @plan-reviewer alignment checks.

---

## Overview

Wave 4 is the core chat experience — everything from typing a message to seeing streaming AI tokens in the UI. It covers:

- **Messages**: Send messages, receive SSE stream, display history
- **SSE Streaming**: Backend → frontend token streaming via Server-Sent Events
- **Documents**: Upload files to cases, browse, download, delete
- **Memories**: View/add/edit/delete case memories (manual; auto-extraction is future)
- **Mock RAG Pipeline**: Fake AI that yields Arabic tokens for end-to-end testing

**Prerequisite:** Wave 3 bugs must be fixed and validated before starting Wave 4.

**Explicitly NOT in scope:**
- Real AI/LLM calls (OpenRouter, Claude, GPT)
- Embeddings, vector search, RAG retrieval
- Mistral document extraction
- Memory auto-extraction from conversations
- Any changes to existing Wave 1-3 code (unless required for integration)

**Post-sub-wave validation:** After each sub-wave (4A, 4B, 4C, 4D), run @integration-lead to verify contracts before proceeding to the next sub-wave. Full validation (@validate + @security-reviewer) runs in 4E after all builds complete.

---

## Sub-Wave 4A: Foundation (Parallel, No Dependencies)

### @shared-foundation: Supabase Storage Client

**File:** `shared/storage/client.py`

Create the storage client for Supabase Storage operations. This wraps the supabase-py storage API.

**Functions to implement:**
```python
async def upload_file(
    bucket: str,           # "documents"
    path: str,             # "cases/{case_id}/convos/{convo_id}/{uuid}_{filename}"
    file_bytes: bytes,
    content_type: str,     # "application/pdf", "image/png", etc.
) -> str:                  # Returns storage path

async def get_signed_url(
    bucket: str,
    path: str,
    expires_in: int = 3600,  # 1 hour default
) -> str:                    # Returns signed download URL

async def delete_file(
    bucket: str,
    path: str,
) -> bool:

async def delete_folder(
    bucket: str,
    folder_path: str,      # Delete all files under this prefix
) -> int:                  # Returns count of deleted files
```

**Storage path conventions:**
- Case documents: `cases/{case_id}/convos/{conversation_id}/{uuid}_{sanitized_filename}`
- General chat attachments: `general/{user_id}/convos/{conversation_id}/{uuid}_{sanitized_filename}`

**Dependencies:** `shared/config.py` (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY), `shared/db/client.py` (supabase client)

**Rules:**
- Use service-role client for storage operations (bypasses RLS on storage)
- Sanitize filenames: remove path separators, null bytes
- Generate UUID prefix for each file to prevent collisions

---

### @sse-streaming: Mock RAG Pipeline

**Files:**
- `agents/__init__.py` (empty)
- `agents/rag/__init__.py` (empty)
- `agents/rag/pipeline.py`

**Spec for `pipeline.py`:**
```python
import asyncio
from typing import AsyncGenerator

MOCK_RESPONSE = "وفقاً لنظام العمل السعودي، المادة 74، يحق للعامل الحصول على أجره كاملاً في مواعيد استحقاقه المحددة. كما تنص المادة 80 على أنه لا يجوز لصاحب العمل فسخ العقد دون مكافأة أو إشعار العامل إلا في حالات محددة."

MOCK_CITATIONS = [
    {"article_id": "mock-1", "law_name": "نظام العمل", "article_number": 74, "relevance_score": 0.92},
    {"article_id": "mock-2", "law_name": "نظام العمل", "article_number": 80, "relevance_score": 0.87},
]

async def query(
    question: str,
    context_messages: list = None,
    case_context: dict = None,
    memories: list = None,
    document_summaries: list = None,
    model: str = "mock",
    user_id: str = None,
    conversation_id: str = None,
) -> AsyncGenerator:
    """Mock RAG pipeline — yields events for SSE streaming."""
    await asyncio.sleep(0.5)  # Simulate thinking delay

    words = MOCK_RESPONSE.split(" ")
    for i, word in enumerate(words):
        token = word if i == 0 else f" {word}"
        yield {"type": "token", "text": token}
        await asyncio.sleep(0.05)  # 50ms between tokens

    yield {"type": "citations", "articles": MOCK_CITATIONS}

    yield {
        "type": "done",
        "usage": {
            "prompt_tokens": 1200,
            "completion_tokens": len(MOCK_RESPONSE),
            "cost": 0.003,
            "latency_ms": len(words) * 50,
            "finish_reason": "stop",
            "model": "mock-model"
        }
    }
```

**Rules:**
- Must be an async generator (yields dicts, not strings)
- Event types: `token`, `citations`, `done`
- Simulates realistic delays (0.5s thinking + 50ms per token)
- Accepts all parameters the real pipeline will use (for interface compatibility)

---

## Sub-Wave 4B: Backend Endpoints + Frontend State (Parallel, Depends on 4A)

### @fastapi-backend: Messages, Documents, Memories Endpoints

#### New Files to Create

**1. `backend/app/api/messages.py`** — 2 endpoints

```
GET  /api/v1/conversations/{conversation_id}/messages
  Query params: ?limit=50&before={message_id}
  Response: { messages: Message[], has_more: bool }
  Notes: Paginated, newest-first. "before" is cursor for infinite scroll.

POST /api/v1/conversations/{conversation_id}/messages
  Body (JSON): { content: str, attachments: [] }
  Body (multipart): content=str + files=File[]
  Response: SSE stream (text/event-stream)
  SSE Events:
    message_start → { user_message_id, assistant_message_id, conversation_id }
    token         → { text: str }
    citations     → { articles: [...] }
    done          → { message_id, usage: { prompt_tokens, completion_tokens } }
    error         → { message: str }
  Notes: User message saved BEFORE AI call (crash-safe).
```

**2. `backend/app/api/documents.py`** — 5 endpoints

```
GET    /api/v1/cases/{case_id}/documents
  Query params: ?page=1&limit=20
  Response: { documents: Document[], total: int }

POST   /api/v1/cases/{case_id}/documents
  Body: multipart/form-data (file + optional metadata)
  Response: { document: Document }
  Notes: Upload to Supabase Storage, create case_documents record.

GET    /api/v1/documents/{document_id}
  Response: { document: Document }

GET    /api/v1/documents/{document_id}/download
  Response: { url: str, expires_at: str }
  Notes: Returns signed URL from Supabase Storage (1hr expiry).

DELETE /api/v1/documents/{document_id}
  Response: { success: true }
  Notes: Soft delete (set deleted_at). Also delete from storage.
```

**3. `backend/app/api/memories.py`** — 4 endpoints

```
GET    /api/v1/cases/{case_id}/memories
  Query params: ?type=all&page=1&limit=50
  Response: { memories: Memory[], total: int }

POST   /api/v1/cases/{case_id}/memories
  Body: { memory_type: str, content_ar: str }
  Response: { memory: Memory }

PATCH  /api/v1/memories/{memory_id}
  Body: { content_ar?: str, memory_type?: str }
  Response: { memory: Memory }

DELETE /api/v1/memories/{memory_id}
  Response: { success: true }
  Notes: Soft delete (set deleted_at).
```

**4. `backend/app/services/message_service.py`**

Core service — orchestrates the entire message pipeline:

```python
async def list_messages(conversation_id, user_id, limit=50, before=None) -> dict:
    """Paginated message list with ownership check."""

async def send_message(conversation_id, user_id, content, files=None) -> AsyncGenerator:
    """The main pipeline:
    1. Verify conversation ownership
    2. Process file attachments (if any) → upload to storage, create case_documents
    3. Save user message to DB (BEFORE AI call)
    4. Create message_attachments links
    5. Determine mode (general vs case) and load context bundle
    6. Create assistant message placeholder
    7. Yield message_start SSE event
    8. Call agents.rag.pipeline.query() → yield token events
    9. Yield citations event
    10. Update assistant message with full content
    11. Update conversation.last_message_at and message_count
    12. Yield done event
    """
```

**5. `backend/app/services/document_service.py`**

```python
async def list_documents(case_id, user_id, page=1, limit=20) -> dict:
async def upload_document(case_id, user_id, file, conversation_id=None) -> dict:
async def get_document(document_id, user_id) -> dict:
async def get_download_url(document_id, user_id) -> dict:
async def delete_document(document_id, user_id) -> dict:
```

**6. `backend/app/services/memory_service.py`**

```python
async def list_memories(case_id, user_id, memory_type=None, page=1, limit=50) -> dict:
async def create_memory(case_id, user_id, memory_type, content_ar) -> dict:
async def update_memory(memory_id, user_id, content_ar=None, memory_type=None) -> dict:
async def delete_memory(memory_id, user_id) -> dict:
```

**7. `backend/app/services/context_service.py`**

```python
async def build_context(conversation_id, user_id) -> dict:
    """Build context bundle for AI pipeline.
    General mode: { mode: "general", messages: [...], user_locale: "ar" }
    Case mode: adds case metadata, memories (top 15), document summaries
    Uses Redis cache for hot conversations (30min TTL).
    """
```

**8. Update `backend/app/models/requests.py`** — Add:
```python
class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10_000)

class CreateMemoryRequest(BaseModel):
    memory_type: str  # fact, document_reference, strategy, deadline, party_info
    content_ar: str = Field(..., min_length=1, max_length=5_000)

class UpdateMemoryRequest(BaseModel):
    content_ar: str | None = None
    memory_type: str | None = None
```

**9. Update `backend/app/models/responses.py`** — Add:
```python
class MessageResponse(BaseModel):
    message_id: str
    conversation_id: str
    role: str  # user, assistant, system
    content: str
    model: str | None
    attachments: list[AttachmentResponse]
    created_at: str

class AttachmentResponse(BaseModel):
    id: str
    document_id: str
    attachment_type: str  # pdf, image, file
    filename: str
    file_size: int | None

class MessageListResponse(BaseModel):
    messages: list[MessageResponse]
    has_more: bool

class DocumentResponse(BaseModel):
    document_id: str
    case_id: str
    document_name: str
    mime_type: str
    file_size_bytes: int
    extraction_status: str
    created_at: str

class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]
    total: int

class DownloadResponse(BaseModel):
    url: str
    expires_at: str

class MemoryResponse(BaseModel):
    memory_id: str
    case_id: str
    memory_type: str
    content_ar: str
    confidence_score: float | None
    created_at: str
    updated_at: str

class MemoryListResponse(BaseModel):
    memories: list[MemoryResponse]
    total: int
```

**10. Update `backend/app/main.py`** — Register 3 new routers:
```python
from app.api import messages, documents, memories
app.include_router(messages.router, prefix="/api/v1", tags=["messages"])
app.include_router(documents.router, prefix="/api/v1", tags=["documents"])
app.include_router(memories.router, prefix="/api/v1", tags=["memories"])
```

**Rules for @fastapi-backend:**
- All error messages in Arabic
- Ownership checks on every endpoint (user can only access their own data)
- Soft deletes: set `deleted_at`, never hard delete
- User message saved BEFORE AI call (crash-safe, Absolute Rule #7)
- SSE via sse-starlette (Absolute Rule #8)
- Use `shared/storage/client.py` for file operations (created in Wave 4A)
- Import mock pipeline from `agents.rag.pipeline`
- File validation: max 50MB per file, max 5 files, PDF/PNG/JPG only

---

### @nextjs-frontend: State Layer + API Client + Types

#### Update `frontend/types/index.ts` — Add:
```typescript
// Messages
interface Message {
  message_id: string;
  conversation_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  model?: string;
  attachments: Attachment[];
  created_at: string;
  isOptimistic?: boolean;  // client-side flag for optimistic updates
  isFailed?: boolean;      // client-side flag for failed sends
  isStreaming?: boolean;    // client-side flag for currently streaming
}

interface Attachment {
  id: string;
  document_id: string;
  attachment_type: 'pdf' | 'image' | 'file';
  filename: string;
  file_size?: number;
}

interface MessageListResponse {
  messages: Message[];
  has_more: boolean;
}

// Documents
interface Document {
  document_id: string;
  case_id: string;
  document_name: string;
  mime_type: string;
  file_size_bytes: number;
  extraction_status: 'pending' | 'processing' | 'completed' | 'failed';
  created_at: string;
}

interface DocumentListResponse {
  documents: Document[];
  total: number;
}

interface DownloadResponse {
  url: string;
  expires_at: string;
}

// Memories
interface Memory {
  memory_id: string;
  case_id: string;
  memory_type: 'fact' | 'document_reference' | 'strategy' | 'deadline' | 'party_info';
  content_ar: string;
  confidence_score?: number;
  created_at: string;
  updated_at: string;
}

interface MemoryListResponse {
  memories: Memory[];
  total: number;
}

// Pending file (for upload preview)
interface PendingFile {
  id: string;
  file: File;
  previewUrl: string;
  name: string;
  size: number;
  mimeType: string;
}

// SSE events
interface SSEMessageStart {
  user_message_id: string;
  assistant_message_id: string;
  conversation_id: string;
}

interface SSEToken {
  text: string;
}

interface SSECitations {
  articles: Citation[];
}

interface Citation {
  article_id: string;
  law_name: string;
  article_number: number;
  relevance_score?: number;
}

interface SSEDone {
  message_id: string;
  usage: {
    prompt_tokens: number;
    completion_tokens: number;
  };
}
```

#### Create `frontend/stores/chat-store.ts`
```typescript
// Zustand store for chat state
interface ChatState {
  // Streaming state
  isStreaming: boolean;
  streamingMessageId: string | null;
  streamingContent: string;
  abortController: AbortController | null;

  // Pending files (before send)
  pendingFiles: PendingFile[];

  // Error state
  error: string | null;

  // Actions
  startStreaming: () => void;
  appendToken: (text: string) => void;
  stopStreaming: () => void;
  setError: (error: string | null) => void;
  addPendingFile: (file: PendingFile) => void;
  removePendingFile: (id: string) => void;
  clearPendingFiles: () => void;
  setAbortController: (controller: AbortController | null) => void;
}
```

#### Create `frontend/hooks/use-messages.ts`
```typescript
// TanStack Query hook for message history
// useMessages(conversationId) → paginated message list with infinite scroll
// Cursor-based: uses "before" param for loading older messages
// Newest messages first, reversed for display
```

#### Create `frontend/hooks/use-chat.ts`
```typescript
// Core SSE streaming hook
// useSendMessage() → mutation that:
//   1. Adds optimistic user message
//   2. POSTs to /conversations/{id}/messages
//   3. Parses SSE stream (fetch + ReadableStream, NOT EventSource)
//   4. Dispatches events to chat store (message_start, token, citations, done, error)
//   5. Handles: abort/stop, network errors, 401 retry, optimistic reconciliation
```

#### Create `frontend/hooks/use-documents.ts`
```typescript
// TanStack Query hooks for documents
// useDocuments(caseId) → paginated document list
// useUploadDocument() → mutation (multipart upload)
// useDeleteDocument() → mutation (soft delete)
// useDownloadUrl(documentId) → query (signed URL)
```

#### Create `frontend/hooks/use-memories.ts`
```typescript
// TanStack Query hooks for memories
// useMemories(caseId, type?) → paginated memory list
// useCreateMemory() → mutation
// useUpdateMemory() → mutation
// useDeleteMemory() → mutation
```

#### Update `frontend/lib/api.ts` — Add:
```typescript
// messagesApi
messagesApi: {
  list: (conversationId, params?) => apiFetch(`/conversations/${id}/messages`, params),
  send: (conversationId, content, files?) => /* returns Response for SSE reading */,
}

// documentsApi
documentsApi: {
  list: (caseId, params?) => apiFetch(`/cases/${caseId}/documents`, params),
  upload: (caseId, file, conversationId?) => /* multipart upload */,
  get: (documentId) => apiFetch(`/documents/${documentId}`),
  download: (documentId) => apiFetch(`/documents/${documentId}/download`),
  delete: (documentId) => apiFetch(`/documents/${documentId}`, { method: 'DELETE' }),
}

// memoriesApi
memoriesApi: {
  list: (caseId, params?) => apiFetch(`/cases/${caseId}/memories`, params),
  create: (caseId, body) => apiFetch(`/cases/${caseId}/memories`, { method: 'POST', body }),
  update: (memoryId, body) => apiFetch(`/memories/${memoryId}`, { method: 'PATCH', body }),
  delete: (memoryId) => apiFetch(`/memories/${memoryId}`, { method: 'DELETE' }),
}
```

**Important:** The `messagesApi.send()` must NOT use the normal `apiFetch` wrapper (which expects JSON). It should use raw `fetch` with `Authorization` header, returning the raw `Response` object so the SSE parser in `use-chat.ts` can read the stream.

**Rules for @nextjs-frontend (state layer):**
- All UI text in Arabic
- Access token in memory (Absolute Rule #4)
- Types must match Pydantic models exactly
- PendingFile uses `URL.createObjectURL()` for previews, revokes on remove
- SSE parsing via `fetch` + `ReadableStream` (NOT `EventSource` — needs POST + custom headers)
- Handle `TextDecoder` with `{ stream: true }` for Arabic UTF-8

---

## Sub-Wave 4C: Chat UI + Document/Memory Components (Depends on 4B)

### @nextjs-frontend: Chat UI Components

**1. `components/chat/ChatContainer.tsx`**
- Main chat area — renders MessageList + ChatInput
- Receives `conversationId` prop
- Loads message history via `useMessages(conversationId)`
- Manages scroll position (auto-scroll on new messages, preserve position on history load)

**2. `components/chat/ChatInput.tsx`**
- Auto-resizing `<textarea>` with `dir="rtl"` `lang="ar"`
- Placeholder: "اكتب رسالتك هنا..." (Type your message here...)
- Enter sends, Shift+Enter adds newline
- File attachment: button + drag-drop + paste support
- File preview strip below textarea (thumbnails for images, icons for PDFs)
- Send button → Stop button swap during streaming
- Disabled during streaming (`isStreaming` from chat store)
- Client-side validation: max 10K chars, max 5 files, max 50MB per file, PDF/PNG/JPG only
- Validation toasts in Arabic

**3. `components/chat/MessageList.tsx`**
- Scrollable container (shadcn ScrollArea)
- Maps messages → MessageBubble components
- Auto-scrolls to bottom on new messages
- Infinite scroll up for history (`useMessages` with cursor)
- Empty state: no messages yet
- Loading skeleton for initial load

**4. `components/chat/MessageBubble.tsx`**
- User messages: right-aligned, colored background
- Assistant messages: left-aligned, white background
- Shows content, timestamp, model badge (for assistant)
- Renders attachments (thumbnails/icons)
- Failed message: red border + retry button + Arabic error
- Streaming message: shows StreamingText component

**5. `components/chat/StreamingText.tsx`**
- Renders content that grows token by token
- Smooth text appearance (CSS transition or requestAnimationFrame)
- Blinking cursor at end while streaming
- Arabic RTL rendering

**6. `components/chat/CitationPills.tsx`**
- Horizontal row of citation badges after assistant message
- Each pill: law name + article number
- Clickable (future: opens article detail)
- Arabic text

**7. `components/chat/TypingIndicator.tsx`**
- Three bouncing dots animation
- Arabic label: "يفكر..." (Thinking...)
- Shows between message_start and first token

**8. `components/chat/FilePreview.tsx`**
- Preview strip for pending files before sending
- Image files: thumbnail
- PDF files: PDF icon + filename
- Remove button (X) on each file
- File size display

### @nextjs-frontend: Document Components

**9. `components/documents/DocumentBrowser.tsx`**
- Grid layout of DocumentCards
- Empty state: "لا توجد مستندات" (No documents)
- Upload button triggers UploadDropzone

**10. `components/documents/DocumentCard.tsx`**
- Card with: file icon, name, size, upload date, extraction status badge
- Status badges: pending (yellow), processing (blue), completed (green), failed (red)
- Actions: download, delete (with confirmation dialog)

**11. `components/documents/UploadDropzone.tsx`**
- Drag-and-drop area with dashed border
- Arabic label: "اسحب الملفات هنا أو انقر للتحميل"
- File type filter: PDF, PNG, JPG
- Upload progress indicator
- Calls `documentsApi.upload()`

### @nextjs-frontend: Memory Components

**12. `components/memories/MemoryList.tsx`**
- List of MemoryCards grouped by type
- Filter tabs: الكل (all), حقائق (facts), أطراف (parties), مواعيد (deadlines), استراتيجية (strategy)
- "إضافة ذاكرة" (Add memory) button → dialog form
- Empty state per type

**13. `components/memories/MemoryCard.tsx`**
- Card with: type badge, content, confidence score, date
- Actions: edit (inline or dialog), delete (with confirmation)
- Type badges with Arabic labels and colors:
  - fact → blue "حقيقة"
  - document_reference → gray "مرجع"
  - strategy → purple "استراتيجية"
  - deadline → red "موعد"
  - party_info → green "طرف"

**Rules for @nextjs-frontend (components):**
- All text in Arabic
- All layouts RTL
- Use shadcn/ui primitives (Button, ScrollArea, Dialog, Tabs, etc.)
- IBM Plex Sans Arabic font
- Client components ("use client") where hooks/interactivity needed
- Responsive: mobile-first with Tailwind breakpoints
- Skeleton loaders for loading states
- Error boundaries with Arabic messages

---

## Sub-Wave 4D: Page Wiring (Depends on 4C)

### @nextjs-frontend: Replace Chat Placeholder + Add Pages

**1. Update `app/chat/[id]/page.tsx`**
- Replace "coming soon" placeholder with real ChatContainer
- Load conversation by ID
- Pass conversationId to ChatContainer
- Handle: conversation not found (404), not authorized (redirect)
- Sync with sidebar store (selectedConversationId)

**2. Update `app/chat/page.tsx`**
- On first message send from empty state:
  - Create conversation via API
  - Redirect to `/chat/{new_id}` via `router.replace()`
  - The ChatContainer handles the rest

**Rules:**
- URL updates from `/chat` to `/chat/{id}` use `router.replace()` (no history entry)
- Auto-generated title from first 30 chars of first message
- Sidebar updates to reflect new conversation

---

## Sub-Wave 4E: Verification (Parallel, After 4D)

### @integration-lead
Verify cross-layer contracts after Wave 4 build:
- TypeScript `Message` type matches Pydantic `MessageResponse` fields
- TypeScript `Document` type matches Pydantic `DocumentResponse` fields
- TypeScript `Memory` type matches Pydantic `MemoryResponse` fields
- Frontend API client URLs match backend route decorators
- SSE event names match between backend stream and frontend parser
- Error response format consistent across all new endpoints

### @security-reviewer
Audit new endpoints:
- Messages: ownership check (user can only access their conversation's messages)
- Documents: ownership check (user can only access their case's documents)
- Memories: ownership check (user can only access their case's memories)
- File upload: MIME type validation, file size limits, no path traversal
- SSE: no information leakage in error events
- Rate limiting applies to message send endpoint

### @validate
Full test suite including:
- API tests for all 11 new endpoints
- SSE streaming test (send message, verify token events arrive)
- File upload test (multipart with test PDF)
- Message ordering test (verify newest-first, cursor pagination)
- Ownership isolation test (user A can't access user B's messages/docs/memories)
- Soft delete test (verify deleted_at set, data still in DB)

---

## File Manifest

### New Files (25 files)

| # | Path | Agent | Sub-Wave |
|---|------|-------|----------|
| 1 | `shared/storage/client.py` | @shared-foundation | 4A |
| 2 | `agents/__init__.py` | @sse-streaming | 4A |
| 3 | `agents/rag/__init__.py` | @sse-streaming | 4A |
| 4 | `agents/rag/pipeline.py` | @sse-streaming | 4A |
| 5 | `backend/app/api/messages.py` | @fastapi-backend | 4B |
| 6 | `backend/app/api/documents.py` | @fastapi-backend | 4B |
| 7 | `backend/app/api/memories.py` | @fastapi-backend | 4B |
| 8 | `backend/app/services/message_service.py` | @fastapi-backend | 4B |
| 9 | `backend/app/services/document_service.py` | @fastapi-backend | 4B |
| 10 | `backend/app/services/memory_service.py` | @fastapi-backend | 4B |
| 11 | `backend/app/services/context_service.py` | @fastapi-backend | 4B |
| 12 | `frontend/stores/chat-store.ts` | @nextjs-frontend | 4B |
| 13 | `frontend/hooks/use-chat.ts` | @nextjs-frontend | 4B |
| 14 | `frontend/hooks/use-messages.ts` | @nextjs-frontend | 4B |
| 15 | `frontend/hooks/use-documents.ts` | @nextjs-frontend | 4B |
| 16 | `frontend/hooks/use-memories.ts` | @nextjs-frontend | 4B |
| 17 | `frontend/components/chat/ChatContainer.tsx` | @nextjs-frontend | 4C |
| 18 | `frontend/components/chat/ChatInput.tsx` | @nextjs-frontend | 4C |
| 19 | `frontend/components/chat/MessageList.tsx` | @nextjs-frontend | 4C |
| 20 | `frontend/components/chat/MessageBubble.tsx` | @nextjs-frontend | 4C |
| 21 | `frontend/components/chat/StreamingText.tsx` | @nextjs-frontend | 4C |
| 22 | `frontend/components/chat/CitationPills.tsx` | @nextjs-frontend | 4C |
| 23 | `frontend/components/chat/TypingIndicator.tsx` | @nextjs-frontend | 4C |
| 24 | `frontend/components/chat/FilePreview.tsx` | @nextjs-frontend | 4C |
| 25 | `frontend/components/documents/DocumentBrowser.tsx` | @nextjs-frontend | 4C |
| 26 | `frontend/components/documents/DocumentCard.tsx` | @nextjs-frontend | 4C |
| 27 | `frontend/components/documents/UploadDropzone.tsx` | @nextjs-frontend | 4C |
| 28 | `frontend/components/memories/MemoryList.tsx` | @nextjs-frontend | 4C |
| 29 | `frontend/components/memories/MemoryCard.tsx` | @nextjs-frontend | 4C |

### Modified Files (5 files)

| # | Path | Agent | Sub-Wave | Changes |
|---|------|-------|----------|---------|
| 1 | `backend/app/main.py` | @fastapi-backend | 4B | Add 3 new routers |
| 2 | `backend/app/models/requests.py` | @fastapi-backend | 4B | Add message/memory request models |
| 3 | `backend/app/models/responses.py` | @fastapi-backend | 4B | Add message/document/memory response models |
| 4 | `frontend/types/index.ts` | @nextjs-frontend | 4B | Add Message/Document/Memory/SSE types |
| 5 | `frontend/lib/api.ts` | @nextjs-frontend | 4B | Add messagesApi/documentsApi/memoriesApi |
| 6 | `frontend/app/chat/[id]/page.tsx` | @nextjs-frontend | 4D | Replace placeholder with ChatContainer |
| 7 | `frontend/app/chat/page.tsx` | @nextjs-frontend | 4D | Add first-message conversation creation |

---

## SSE Protocol Reference

```
Client → POST /api/v1/conversations/{id}/messages
  Body: { content: "ما هي حقوق العامل؟" }

Server → SSE stream:

event: message_start
data: {"user_message_id":"uuid-1","assistant_message_id":"uuid-2","conversation_id":"uuid-3"}

event: token
data: {"text":"وفقاً"}

event: token
data: {"text":" لنظام"}

event: token
data: {"text":" العمل"}

... (many token events)

event: citations
data: {"articles":[{"article_id":"mock-1","law_name":"نظام العمل","article_number":74}]}

event: done
data: {"message_id":"uuid-2","usage":{"prompt_tokens":1200,"completion_tokens":450}}
```

**Frontend SSE parsing:** Use `fetch()` + `ReadableStream` + `TextDecoder` (NOT `EventSource`, which only supports GET).

---

## Success Criteria (Wave 4)

- [ ] User types Arabic text in auto-resizing textarea
- [ ] Enter sends, Shift+Enter adds newline
- [ ] User message appears immediately (optimistic update)
- [ ] Files can be attached via drag-drop, paste, or picker (PDF/PNG/JPG, max 50MB, max 5)
- [ ] File previews show before sending
- [ ] POST /messages returns SSE stream
- [ ] Mock tokens stream in real-time to chat UI
- [ ] Citations render as clickable pills after stream completes
- [ ] "Thinking..." indicator shows while waiting for first token
- [ ] Stream error shows Arabic error message
- [ ] User message saved to DB BEFORE AI call
- [ ] Assistant message saved to DB AFTER stream completes
- [ ] New conversation: URL updates from /chat to /chat/{id}
- [ ] Sidebar updates to reflect new conversation
- [ ] Documents can be uploaded, listed, downloaded, deleted
- [ ] Memories can be listed, added, edited, deleted
- [ ] All error messages in Arabic
- [ ] RTL layout throughout
- [ ] RLS prevents cross-user data access
- [ ] TypeScript types match Pydantic models
