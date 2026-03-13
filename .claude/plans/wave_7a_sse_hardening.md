# Wave 7A: SSE Hardening (Heartbeat + Disconnect Detection + CancelledError)

> Production-hardening wave. No new features — only reliability and resilience improvements to the existing SSE streaming pipeline.
> Runs AFTER Wave 6 is complete (or can run in parallel with Wave 6D since it touches different code paths).

---

## Overview

The current SSE implementation (`send_message_stream` in `backend/app/services/message_service.py`) has three production gaps:

1. **No heartbeat**: Long-running agent pipelines leave the connection idle. Railway proxy times out idle SSE connections after 60 seconds. Streams die silently mid-response.
2. **No disconnect detection**: When the user closes their browser, the backend continues running the full agent pipeline — wasting CPU and (in future waves) LLM API costs. The `Request` object is never passed to the generator.
3. **No `asyncio.CancelledError` handling**: When ASGI cancels the generator on client disconnect, `except Exception` may catch it incorrectly. Explicit handling is best practice.

All three fixes are localized to 2 backend files and 1 frontend file. The user message is already saved BEFORE streaming starts (line 167, Absolute Rule #7), so early termination is always crash-safe.

---

## Sub-Wave 7A.1: Build Phase (Sequential then Parallel)

### Step 1: @sse-streaming (Tasks 1 + 2 + 3 — core pipeline changes)

**File: `backend/app/services/message_service.py` (MODIFY)**

Restructure `send_message_stream()` (lines 138-307):

**Task 1 — Heartbeat:**
- Add `import asyncio` (verify if already present)
- Add `from fastapi import Request` import
- After the `message_start` yield (line 209), create an `asyncio.Queue` shared between a heartbeat coroutine and the main pipeline iteration
- Launch heartbeat as `asyncio.create_task()` that puts heartbeat sentinel events every 15 seconds
- Main pipeline puts real events on the same queue
- Generator reads from queue and yields whatever arrives first
- On pipeline completion or error, cancel the heartbeat task in a `finally` block
- Heartbeat event format: `_sse_event("heartbeat", {})`

**Task 2 — Disconnect detection:**
- Add `request: Request` parameter to `send_message_stream()` signature
- In the queue consumer loop, before each yield: `if await request.is_disconnected(): break`
- Log: `logger.info("Client disconnected during streaming for conversation %s", conversation_id)`

**Task 3 — CancelledError:**
- Restructure try/except to catch `asyncio.CancelledError` BEFORE `except Exception`:
```python
try:
    # queue-based loop with disconnect checks
except asyncio.CancelledError:
    logger.info("SSE stream cancelled for conversation %s (client disconnect)", conversation_id)
    raise  # MUST re-raise per asyncio contract
except Exception as e:
    logger.exception("Error in agent pipeline: %s", e)
    yield _sse_event("error", {"detail": "حدث خطأ أثناء معالجة الرسالة"})
```

### Step 2 (parallel with Step 3): @fastapi-backend (Route wiring)

**File: `backend/app/api/messages.py` (MODIFY)**

- Add `Request` to the fastapi import (line 10)
- Add `request: Request` parameter to `send_message` handler (line 46)
- Pass `request=request` to `message_service.send_message_stream()` call (line 58)

### Step 3 (parallel with Step 2): @nextjs-frontend (Frontend heartbeat ignore)

**File: `frontend/hooks/use-chat.ts` (MODIFY)**

- In `handleSSEEvent` switch (line ~195), add:
```typescript
case "heartbeat":
  // Keep-alive ping from server — ignore silently
  break;
```

---

## Dependency Graph

```
Step 1: @sse-streaming (message_service.py — all 3 tasks) ─────┐
  │                                                              │
  ├─► Step 2: @fastapi-backend (messages.py)      [parallel] ──►│ VALIDATE
  └─► Step 3: @nextjs-frontend (use-chat.ts)      [parallel] ──►│
```

---

## File Manifest

### New Files: 0

### Modified Files: 3

| # | Path | Agent | Changes |
|---|------|-------|---------|
| 1 | `backend/app/services/message_service.py` | @sse-streaming | Heartbeat producer, queue-based event merge, `request` param, disconnect polling, CancelledError handling |
| 2 | `backend/app/api/messages.py` | @fastapi-backend | Add `Request` import + param, pass to service |
| 3 | `frontend/hooks/use-chat.ts` | @nextjs-frontend | Add `case "heartbeat": break;` in SSE event switch |

---

## Validation Gate 7A

### @integration-lead (read-only)
- Verify `send_message_stream()` signature includes `request: Request` parameter
- Verify `send_message` route handler passes `request` to service
- Verify frontend `handleSSEEvent` switch has `heartbeat` case
- Verify SSE event format: `event: heartbeat\ndata: {}\n\n` matches `_sse_event("heartbeat", {})`

### @validate (test execution)

| # | Test | MCP Tools | Pass Criteria |
|---|------|-----------|---------------|
| 1 | Heartbeat arrives during streaming | `mcp__playwright__browser_navigate`, `mcp__playwright__browser_type`, `mcp__playwright__browser_click`, `mcp__playwright__browser_network_requests` | At least one `event: heartbeat` in SSE stream |
| 2 | Backend handles client disconnect gracefully | `mcp__railway-mcp-server__get-logs` | Log line "Client disconnected during streaming" (not an exception traceback) |
| 3 | CancelledError not swallowed | Code inspection | `except asyncio.CancelledError` block has `raise` (not `pass`) |
| 4 | Frontend ignores heartbeat | `mcp__playwright__browser_navigate`, `mcp__playwright__browser_console_messages` | No "heartbeat" text in chat UI, no JS console errors |
| 5 | TypeScript compiles | Bash: `cd frontend && npx tsc --noEmit` | Zero errors |
| 6 | Existing streaming works | `mcp__playwright__browser_navigate` → send message → verify response | message_start, token, done events received normally |

### @deploy-checker (post-deploy)

| Check | MCP Tool | Pass Criteria |
|-------|----------|---------------|
| Services healthy | `mcp__railway-mcp-server__check-railway-status` | Both services up |
| No crash loops | `mcp__railway-mcp-server__get-logs` | No new crash loops after deploy |
| Deploy succeeded | `mcp__railway-mcp-server__list-deployments` | Latest deployment status = success |

---

## Success Criteria (Wave 7A)

- [ ] `send_message_stream()` yields `event: heartbeat\ndata: {}\n\n` every ~15 seconds during idle periods
- [ ] `send_message_stream()` accepts a `request: Request` parameter
- [ ] `send_message` route handler passes `request` to the service
- [ ] Generator breaks out of loop when `await request.is_disconnected()` returns `True`
- [ ] `asyncio.CancelledError` is caught, logged, and re-raised (not swallowed)
- [ ] `except asyncio.CancelledError` appears BEFORE `except Exception` in the try/except chain
- [ ] Frontend `handleSSEEvent` switch includes `case "heartbeat": break;`
- [ ] No heartbeat text appears in the chat UI
- [ ] `cd frontend && npx tsc --noEmit` passes with zero errors
- [ ] Existing SSE streaming (message_start, token, citations, done) works unchanged
- [ ] All error messages remain in Arabic
- [ ] Railway deployment healthy after push

---

## Design Rationale

**Why `asyncio.Queue` over `async for` with timeout?**
The timeout approach (`asyncio.wait_for(agen.__anext__(), timeout=15)`) resets the interval after each real event — heartbeats only fire during gaps >15s. The queue approach guarantees heartbeats every 15s regardless of pipeline activity, which is more reliable for proxy keep-alive. Either approach solves the core problem — the implementing agent may choose based on complexity.

**Why poll `is_disconnected()` instead of relying on `CancelledError`?**
ASGI server behavior around connection cancellation is not deterministic. Polling gives explicit, reliable detection. `CancelledError` is the safety net.

**Why 15 seconds?**
Railway proxy timeout is ~60s. 15s provides 4 heartbeats of margin without excessive network chatter.
