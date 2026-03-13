# Wave 7C: Operational Maturity (Error Codes + Audit + CI + Reconnect + Optimistic Updates)

> Operational hardening wave. Improves error handling, observability, CI, and UX resilience.
> **Prerequisite:** Wave 7A must be complete (heartbeat needed for reconnect logic).
> Wave 7B recommended but not strictly required.

---

## Overview

Five cross-cutting improvements:

1. **SSE auto-reconnect** — resilient streaming that retries on transient failures
2. **Structured error codes** — machine-readable error classification across all ~111 HTTPException calls
3. **Audit logging** — compliance-grade tracking of user actions (table exists, services don't write to it)
4. **CI pipeline** — automated type checking, linting on every push/PR
5. **Optimistic updates for conversations** — instant sidebar feedback for create/rename

No database schema changes needed. The `audit_logs` table and `audit_action_enum` already exist from migration 012.

---

## Task 1: SSE Auto-Reconnect with Exponential Backoff

### Agent: @sse-streaming (backend context) + @nextjs-frontend (frontend implementation)

**Depends on Wave 7A** — heartbeat mechanism lets client detect "stream stalled" vs hard TCP failure.

**File: `frontend/stores/chat-store.ts` (MODIFY)**

Add to `ChatState` interface:
- `reconnectAttempts: number` (default 0)
- `maxReconnectAttempts: number` (default 5)
- `isReconnecting: boolean` (default false)

Add actions:
- `startReconnect()` — sets `isReconnecting = true`, increments `reconnectAttempts`
- `resetReconnect()` — resets both to 0/false
- `finishStreaming()` — also calls `resetReconnect()` internally

**File: `frontend/hooks/use-chat.ts` (MODIFY)**

Replace the outer catch block (line 185-193). Currently:
```typescript
catch (err) {
  if (err instanceof DOMException && err.name === "AbortError") { ... }
  markOptimisticFailed(qc, conversationId, optimisticId);
  setError("حدث خطأ غير متوقع...");
}
```

New logic:
1. If `AbortError` → do NOT retry (user intentionally stopped)
2. If `reconnectAttempts < maxReconnectAttempts`:
   - Compute delay: `Math.min(1000 * 2 ** reconnectAttempts, 30000)` (1s → 2s → 4s → 8s → 16s → cap 30s)
   - Call `startReconnect()` on store
   - `await new Promise(r => setTimeout(r, delay))`
   - Do NOT re-send user message (already saved in DB per Absolute Rule #7)
   - Re-call `messagesApi.send()` with same params to get a new SSE stream
   - Do NOT create new optimistic message — original is already in cache
3. If `reconnectAttempts >= maxReconnectAttempts`:
   - `markOptimisticFailed()`
   - `setError("فشل الاتصال بعد عدة محاولات. يرجى المحاولة مرة أخرى.")`

**Retry scope:**
- Network errors (TypeError from fetch) → retry
- HTTP 5xx → retry
- HTTP 4xx → do NOT retry (client error)

---

## Task 2: Structured Error Codes

### Agent: @fastapi-backend

**File: `backend/app/errors.py` (NEW)**

```python
from enum import Enum
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi import HTTPException

class ErrorCode(str, Enum):
    # Auth
    AUTH_INVALID = "AUTH_INVALID"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    # Cases
    CASE_NOT_FOUND = "CASE_NOT_FOUND"
    CASE_INVALID_TYPE = "CASE_INVALID_TYPE"
    CASE_INVALID_STATUS = "CASE_INVALID_STATUS"
    CASE_INVALID_PRIORITY = "CASE_INVALID_PRIORITY"
    # Conversations
    CONV_NOT_FOUND = "CONV_NOT_FOUND"
    CONV_ACCESS_DENIED = "CONV_ACCESS_DENIED"
    # Documents
    DOC_NOT_FOUND = "DOC_NOT_FOUND"
    DOC_TOO_LARGE = "DOC_TOO_LARGE"
    DOC_INVALID_TYPE = "DOC_INVALID_TYPE"
    DOC_EMPTY = "DOC_EMPTY"
    DOC_MAGIC_MISMATCH = "DOC_MAGIC_MISMATCH"
    DOC_UPLOAD_FAILED = "DOC_UPLOAD_FAILED"
    # Memories
    MEMORY_NOT_FOUND = "MEMORY_NOT_FOUND"
    MEMORY_INVALID_TYPE = "MEMORY_INVALID_TYPE"
    # Messages
    MSG_SEND_FAILED = "MSG_SEND_FAILED"
    MSG_LIST_FAILED = "MSG_LIST_FAILED"
    # Artifacts
    ARTIFACT_NOT_FOUND = "ARTIFACT_NOT_FOUND"
    ARTIFACT_NOT_EDITABLE = "ARTIFACT_NOT_EDITABLE"
    # Templates
    TEMPLATE_NOT_FOUND = "TEMPLATE_NOT_FOUND"
    TEMPLATE_INVALID_AGENT = "TEMPLATE_INVALID_AGENT"
    # Preferences
    PREFERENCES_FAILED = "PREFERENCES_FAILED"
    # User
    USER_NOT_FOUND = "USER_NOT_FOUND"
    # Validation
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NO_UPDATE_DATA = "NO_UPDATE_DATA"
    INVALID_UUID = "INVALID_UUID"
    # Rate limiting
    RATE_LIMITED = "RATE_LIMITED"
    # Generic
    INTERNAL_ERROR = "INTERNAL_ERROR"

class LunaHTTPException(HTTPException):
    """HTTPException subclass carrying an ErrorCode."""
    def __init__(self, status_code: int, code: ErrorCode, detail: str):
        super().__init__(status_code=status_code, detail=detail)
        self.code = code

async def luna_exception_handler(request: Request, exc: LunaHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {"code": exc.code.value, "message": exc.detail, "status": exc.status_code},
            "detail": exc.detail,  # backward compatibility
        },
    )
```

**File: `backend/app/main.py` (MODIFY)**

Register handler after existing exception handlers:
```python
from backend.app.errors import LunaHTTPException, luna_exception_handler
application.add_exception_handler(LunaHTTPException, luna_exception_handler)
```

Update generic `Exception` handler to return structured format too.

**Files: All 8 backend service files (MODIFY)**

Replace every `raise HTTPException(...)` with `raise LunaHTTPException(...)` + appropriate `ErrorCode`.

| File | HTTPException count |
|------|-------------------|
| `case_service.py` | ~22 |
| `conversation_service.py` | ~15 |
| `message_service.py` | ~4 |
| `document_service.py` | ~20 |
| `memory_service.py` | ~17 |
| `artifact_service.py` | ~13 |
| `preferences_service.py` | ~16 |
| `memory_md_service.py` | ~4 |
| **Total** | **~111** |

**File: `backend/app/deps.py` (MODIFY)**

Update 5 HTTPException calls: `AUTH_INVALID`, `AUTH_EXPIRED`, `INVALID_UUID`.

**File: `frontend/lib/api.ts` (MODIFY — line 140-155)**

Update error parsing to handle nested `error.code`:
```typescript
throw new ApiClientError(
  res.status,
  errorBody.error?.code || errorBody.code || "unknown",
  errorBody.error?.message || errorBody.detail || "Request failed"
);
```

This is backward compatible (checks nested first, falls back to flat).

---

## Task 3: Wire Audit Logging

### Agent: @fastapi-backend

**File: `backend/app/services/audit_service.py` (NEW)**

Fire-and-forget audit writer:
```python
def write_audit_log(supabase, *, user_id, action, resource_type, resource_id=None, metadata=None):
    try:
        payload = {"user_id": user_id, "action": action, "resource_type": resource_type}
        if resource_id: payload["resource_id"] = resource_id
        if metadata: payload["metadata"] = metadata
        supabase.table("audit_logs").insert(payload).execute()
    except Exception as e:
        logger.warning("Audit log write failed (non-blocking): %s", e)
```

**Key design:** Audit failures NEVER block user operations. All exceptions caught and logged.

**Service files to add audit calls (MODIFY):**

| Service | Function | Action | resource_type |
|---------|----------|--------|---------------|
| `case_service.py` | `create_case()` | `create` | `case` |
| `case_service.py` | `delete_case()` | `delete` | `case` |
| `conversation_service.py` | `create_conversation()` | `create` | `conversation` |
| `document_service.py` | `upload_document()` | `upload` | `document` |
| `document_service.py` | `delete_document()` | `delete` | `document` |
| `memory_service.py` | `create_memory()` | `create` | `memory` |
| `memory_service.py` | `delete_memory()` | `delete` | `memory` |
| `message_service.py` | `send_message_stream()` | `create` | `message` |

Audit calls placed AFTER successful mutation (only log successful operations).

---

## Task 4: GitHub Actions CI Pipeline

### Agent: @fastapi-backend

**File: `.github/workflows/ci.yml` (NEW)**

```yaml
name: CI
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  backend:
    name: Backend
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'
      - run: pip install -r backend/requirements.txt
      - run: python -c "from backend.app.main import app; print('Backend imports OK')"
        env:
          SUPABASE_URL: "https://example.supabase.co"
          SUPABASE_SERVICE_ROLE_KEY: "test-key"
          SUPABASE_ANON_KEY: "test-key"
          SUPABASE_JWT_SECRET: "test-secret"
          REDIS_URL: "redis://localhost:6379"
          ENVIRONMENT: "test"

  frontend:
    name: Frontend
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'
          cache-dependency-path: frontend/package-lock.json
      - run: cd frontend && npm ci
      - run: cd frontend && npx tsc --noEmit
      - run: cd frontend && npm run lint
```

**Design:** Path filtering via `if` conditions can be added later. For now, both jobs run on every push/PR — simpler and catches cross-layer breakage.

---

## Task 5: Optimistic Updates for Conversations

### Agent: @nextjs-frontend

**File: `frontend/hooks/use-conversations.ts` (MODIFY — lines 27-66)**

**`useCreateConversation()` — add `onMutate`, `onError`, `onSettled`:**

```typescript
onMutate: async (data) => {
  await qc.cancelQueries({ queryKey: conversationKeys.lists() });
  const previousLists = qc.getQueriesData({ queryKey: conversationKeys.lists() });

  const optimistic = {
    conversation_id: `optimistic-${Date.now()}`,
    case_id: data.case_id ?? null,
    title_ar: "محادثة جديدة",
    message_count: 0,
    is_active: true,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };

  qc.setQueriesData({ queryKey: conversationKeys.lists() }, (old) => {
    if (!old) return old;
    return { ...old, conversations: [optimistic, ...old.conversations], total: old.total + 1 };
  });

  return { previousLists };
},
onError: (_err, _data, context) => {
  if (context?.previousLists) {
    for (const [key, data] of context.previousLists) qc.setQueryData(key, data);
  }
},
onSettled: () => void qc.invalidateQueries({ queryKey: conversationKeys.lists() }),
```

**`useRenameConversation()` — same pattern:**
- `onMutate`: snapshot + optimistically update title in cached list
- `onError`: rollback from snapshot
- `onSettled`: invalidate to sync with server

---

## Parallel vs Sequential Dependencies

```
PARALLEL GROUP (no interdependencies):
  Task 2: Structured Error Codes     (@fastapi-backend)  ←─┐
  Task 3: Wire Audit Logging         (@fastapi-backend)  ←─┤ Same service files
  Task 4: CI Pipeline                (@fastapi-backend)     │ Do Task 2 first, then 3
  Task 5: Optimistic Updates         (@nextjs-frontend)     │
                                                            │
SEQUENTIAL (depends on Wave 7A):                            │
  Task 1: SSE Auto-Reconnect         (@nextjs-frontend)    │

Recommended order:
  Phase 1: Tasks 4 + 5 (independent, no shared files)
  Phase 2: Task 2 then Task 3 (shared service files — Task 2 creates error module first)
  Phase 3: Task 1 (after Wave 7A heartbeat is in place)
```

---

## File Manifest

### New Files: 3

| # | Path | Agent | Task |
|---|------|-------|------|
| 1 | `backend/app/errors.py` | @fastapi-backend | Task 2 |
| 2 | `backend/app/services/audit_service.py` | @fastapi-backend | Task 3 |
| 3 | `.github/workflows/ci.yml` | @fastapi-backend | Task 4 |

### Modified Files: 13

| # | Path | Agent | Tasks | Changes |
|---|------|-------|-------|---------|
| 1 | `frontend/stores/chat-store.ts` | @nextjs-frontend | 1 | Add reconnect state + actions |
| 2 | `frontend/hooks/use-chat.ts` | @nextjs-frontend | 1 | Exponential backoff retry in catch block |
| 3 | `backend/app/main.py` | @fastapi-backend | 2 | Register LunaHTTPException handler |
| 4 | `backend/app/deps.py` | @fastapi-backend | 2 | Switch 5 HTTPExceptions |
| 5 | `backend/app/services/case_service.py` | @fastapi-backend | 2+3 | ~22 error codes + 2 audit calls |
| 6 | `backend/app/services/conversation_service.py` | @fastapi-backend | 2+3 | ~15 error codes + 1 audit call |
| 7 | `backend/app/services/message_service.py` | @fastapi-backend | 2+3 | ~4 error codes + 1 audit call |
| 8 | `backend/app/services/document_service.py` | @fastapi-backend | 2+3 | ~20 error codes + 2 audit calls |
| 9 | `backend/app/services/memory_service.py` | @fastapi-backend | 2+3 | ~17 error codes + 2 audit calls |
| 10 | `backend/app/services/artifact_service.py` | @fastapi-backend | 2 | ~13 error codes |
| 11 | `backend/app/services/preferences_service.py` | @fastapi-backend | 2 | ~16 error codes |
| 12 | `frontend/lib/api.ts` | @nextjs-frontend | 2 | Update error parsing for nested `error.code` |
| 13 | `frontend/hooks/use-conversations.ts` | @nextjs-frontend | 5 | Add onMutate/onError/onSettled to 2 mutations |

---

## Validation Gate 7C

### @integration-lead

1. **Error format contract:** Trigger errors from frontend → verify `ApiClientError` has correct `code` (not "unknown")
2. **SSE error events:** Verify SSE `error` events still include `detail` field (backward compat)
3. **Conversation types:** Verify `ConversationSummary` type matches optimistic object shape
4. **Reconnect state:** Verify new `chat-store.ts` fields are properly typed

### @validate

**Supabase MCP:**

| Query | MCP Tool | Purpose |
|-------|----------|---------|
| `SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT 10` | `mcp__supabase__execute_sql` | Verify audit records exist after creating case, uploading doc |
| `SELECT DISTINCT action, resource_type FROM audit_logs` | `mcp__supabase__execute_sql` | Verify all expected action/resource_type combos present |

**Playwright MCP:**

| Test | MCP Tools | Pass Criteria |
|------|-----------|---------------|
| Create conversation → sidebar updates instantly | `mcp__playwright__browser_navigate`, `mcp__playwright__browser_click`, `mcp__playwright__browser_snapshot` | New conversation appears before server response |
| Rename conversation → title changes instantly | `mcp__playwright__browser_click`, `mcp__playwright__browser_type` | Title updates without spinner |
| Trigger 404 → structured error | `mcp__playwright__browser_navigate` | Error toast shows Arabic message, console shows error code |
| SSE reconnect on failure | `mcp__playwright__browser_network_requests` | Multiple reconnect attempts visible in network tab |

**CI verification:**

| Test | Method |
|------|--------|
| Push ci.yml to main | Git push |
| Both jobs appear | Check GitHub Actions tab |
| Frontend job passes | `npm ci && tsc --noEmit && lint` |
| Backend job passes | `pip install && import check` |

**Bash checks:**
```
cd frontend && npx tsc --noEmit   # Zero errors
```

---

## Success Criteria (Wave 7C)

- [ ] SSE failures trigger exponential backoff (1s, 2s, 4s, ..., max 30s)
- [ ] After max retries, Arabic error: "فشل الاتصال بعد عدة محاولات"
- [ ] `reconnectAttempts` resets on successful stream completion
- [ ] All ~111 HTTPExceptions replaced with `LunaHTTPException` + `ErrorCode`
- [ ] Error responses: `{"error": {"code": "...", "message": "...", "status": N}, "detail": "..."}`
- [ ] Frontend `ApiClientError.code` populated with real codes (not "unknown")
- [ ] `audit_logs` receives entries for: case_create, case_delete, doc_upload, doc_delete, memory_create, memory_delete, message_send, conv_create
- [ ] Audit failures never block user operations
- [ ] `.github/workflows/ci.yml` exists and runs on push + PRs
- [ ] Frontend CI: `tsc --noEmit` + `lint` passes
- [ ] Backend CI: dependency install + import check passes
- [ ] `useCreateConversation` uses optimistic update (instant sidebar)
- [ ] `useRenameConversation` uses optimistic update (instant title change)
- [ ] Optimistic updates roll back on server error
- [ ] All error messages remain in Arabic (Absolute Rule #5)
- [ ] TypeScript compiles with zero errors
