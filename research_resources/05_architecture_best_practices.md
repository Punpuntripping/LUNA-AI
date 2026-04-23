# Architecture Best Practices for Full-Stack SaaS (Next.js + FastAPI + Supabase + Redis)

> Research compiled: 2026-03-13
> Stack: Next.js 14 App Router | FastAPI | Supabase PostgreSQL + pgvector | Redis | SSE

---

## Summary Table of Resources

| # | Resource | Topic | Type | URL |
|---|----------|-------|------|-----|
| 1 | Federated State Done Right: Zustand, TanStack Query | State Management | Blog (DEV) | [Link](https://dev.to/martinrojas/federated-state-done-right-zustand-tanstack-query-and-the-patterns-that-actually-work-27c0) |
| 2 | Supabase RLS Best Practices: Production Patterns | Database / RLS | Tutorial (MakerKit) | [Link](https://makerkit.dev/blog/tutorials/supabase-rls-best-practices) |
| 3 | What is Semantic Caching? Guide to Faster LLM Apps | Caching / AI | Official Blog (Redis) | [Link](https://redis.io/blog/what-is-semantic-caching/) |
| 4 | SSE vs WebSockets vs Long Polling: What's Best in 2025? | Real-time | Blog (DEV) | [Link](https://dev.to/haraf/server-sent-events-sse-vs-websockets-vs-long-polling-whats-best-in-2025-5ep8) |
| 5 | RTL Arabic Website Design Best Practices | RTL / Arabic UI | Guide (AivenSoft) | [Link](https://www.aivensoft.com/en/blog/rtl-arabic-website-design-guide) |
| 6 | Integrating FastAPI with Supabase Auth | Auth | Tutorial (DEV) | [Link](https://dev.to/j0/integrating-fastapi-with-supabase-auth-780) |
| 7 | Supabase + FastAPI Discussion: Proper Usage | Auth / RLS | GitHub Discussion | [Link](https://github.com/orgs/supabase/discussions/33811) |
| 8 | FastAPI Cache Invalidation Implementation | Caching | Tutorial (OneUptime) | [Link](https://oneuptime.com/blog/post/2026-02-02-fastapi-cache-invalidation/view) |
| 9 | Caching Strategies for LLM Responses | Caching / AI | Blog (DasRoot) | [Link](https://dasroot.net/posts/2026/02/caching-strategies-for-llm-responses/) |
| 10 | Ultimate Guide to AI Agent Architectures in 2025 | AI / Agents | Blog (DEV) | [Link](https://dev.to/sohail-akbar/the-ultimate-guide-to-ai-agent-architectures-in-2025-2j1c) |
| 11 | Supabase Auth Architecture | Auth | Official Docs | [Link](https://supabase.com/docs/guides/auth/architecture) |
| 12 | Setting up Server-Side Auth for Next.js | Auth / SSR | Official Docs | [Link](https://supabase.com/docs/guides/auth/server-side/nextjs) |
| 13 | OWASP Top 10:2025 | Security | Official Standard | [Link](https://owasp.org/Top10/2025/en/) |
| 14 | Playwright Best Practices | Testing | Official Docs | [Link](https://playwright.dev/docs/best-practices) |
| 15 | Supabase Connection Scaling for FastAPI | Scalability | Blog (DEV) | [Link](https://dev.to/papansarkar101/supabase-connection-scaling-the-essential-guide-for-fastapi-developers-348o) |
| 16 | Secure Markdown Rendering in React | Security / XSS | Blog (HackerOne) | [Link](https://www.hackerone.com/blog/secure-markdown-rendering-react-balancing-flexibility-and-safety) |
| 17 | FastAPI i18n: Step-by-Step Guide | Error Handling / i18n | Tutorial (Lokalise) | [Link](https://lokalise.com/blog/fastapi-internationalization/) |
| 18 | Supavisor: Scaling Postgres to 1M Connections | Scalability | Official Blog (Supabase) | [Link](https://supabase.com/blog/supavisor-1-million) |
| 19 | Redis Caching Strategies: Next.js Production Guide | Caching | Guide (DigitalApplied) | [Link](https://www.digitalapplied.com/blog/redis-caching-strategies-nextjs-production) |
| 20 | Mastering Scalable State Management in Next.js | State Management | Blog (Medium) | [Link](https://medium.com/@fadli99xyz/mastering-scalable-state-management-in-next-js-with-tanstack-query-zustand-and-typescript-ecc0205db12e) |
| 21 | Next.js + FastAPI + Supabase: A Powerful Trio | Architecture | Guide | [Link](https://ftp.sleeklens.com/master-series/next-js-fastapi-supabase-a-powerful-trio-1764800684) |
| 22 | PostgreSQL Soft Deletes Implementation | Database | Tutorial (OneUptime) | [Link](https://oneuptime.com/blog/post/2026-01-21-postgresql-soft-deletes/view) |
| 23 | FastAPI Error Handling Patterns | Error Handling | Guide (BetterStack) | [Link](https://betterstack.com/community/guides/scaling-python/error-handling-fastapi/) |
| 24 | Building a Context-Enabled Semantic Cache with Redis | Caching / AI | Official Blog (Redis) | [Link](https://redis.io/blog/building-a-context-enabled-semantic-cache-with-redis/) |
| 25 | The Ultimate RAG Blueprint 2025/2026 | AI / RAG | Blog (LangWatch) | [Link](https://langwatch.ai/blog/the-ultimate-rag-blueprint-everything-you-need-to-know-about-rag-in-2025-2026) |

---

## 1. State Management: Zustand + TanStack Query

### The Core Rule

**Never store server data in Zustand.** TanStack Query handles caching, refetching, and invalidation for server state. Duplicating it in Zustand creates synchronization bugs.

### When to Use Which

| Concern | Use Zustand | Use TanStack Query |
|---------|-------------|-------------------|
| UI toggles, theme, sidebar state | Yes | No |
| Form draft data | Yes | No |
| Auth tokens (in-memory) | Yes | No |
| Navigation/routing state | Yes | No |
| API responses (cases, messages) | No | Yes |
| Cached server data | No | Yes |
| Paginated/infinite lists | No | Yes |
| Real-time subscribed data | No | Yes |

### Cache Invalidation Strategies

**Approach 1: Automatic invalidation on mutation**
```typescript
// When a message is sent, invalidate the messages list
const sendMessage = useMutation({
  mutationFn: api.sendMessage,
  onSuccess: () => {
    queryClient.invalidateQueries({ queryKey: ['messages', conversationId] });
  }
});
```

**Approach 2: Optimistic updates**
```typescript
const sendMessage = useMutation({
  mutationFn: api.sendMessage,
  onMutate: async (newMessage) => {
    await queryClient.cancelQueries({ queryKey: ['messages', conversationId] });
    const previous = queryClient.getQueryData(['messages', conversationId]);
    queryClient.setQueryData(['messages', conversationId], (old) => [...old, newMessage]);
    return { previous };
  },
  onError: (err, newMessage, context) => {
    queryClient.setQueryData(['messages', conversationId], context.previous); // rollback
  },
  onSettled: () => {
    queryClient.invalidateQueries({ queryKey: ['messages', conversationId] });
  }
});
```

**Approach 3: Cross-component invalidation via BroadcastChannel**
Use `BroadcastChannel` to synchronize cache invalidation across tabs or micro-frontends.

### Key Decisions for Luna

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Zustand store structure | One store per domain (auth, sidebar, UI) | Keeps stores small and focused |
| TanStack Query keys | Hierarchical: `['messages', conversationId]` | Enables targeted invalidation |
| Stale time | 30s for messages, 5min for cases | Messages change more frequently |
| Optimistic updates | Yes for chat messages | Chat must feel instant |
| Refetch on focus | Yes for lists, No for active chat | Prevent jarring UX during typing |

### Reference Implementations

- [Zustand + TanStack Query Federated Patterns](https://dev.to/martinrojas/federated-state-done-right-zustand-tanstack-query-and-the-patterns-that-actually-work-27c0)
- [Next.js Project Structure with Zustand + React Query](https://medium.com/@zerebkov.artjom/how-to-structure-next-js-project-with-zustand-and-react-query-c4949544b0fe)
- [React State Management in 2025](https://www.developerway.com/posts/react-state-management-2025)

---

## 2. Auth Architecture: Supabase Auth + FastAPI

### Architecture Overview

```
Browser (Next.js)
    |
    | 1. Sign in via Supabase Auth SDK
    v
Supabase Auth Service
    |
    | 2. Returns JWT (ES256) + Refresh Token
    v
Browser stores tokens in memory (Zustand)
    |
    | 3. Sends JWT in Authorization header
    v
FastAPI Backend
    |
    | 4. Validates JWT via JWKS (ES256/HS256)
    | 5. Extracts user claims
    | 6. Passes to service layer
    v
Supabase PostgreSQL (RLS enforced)
```

### JWT Validation in FastAPI

**Key pattern**: Extend `HTTPBearer` to create a reusable dependency:

```python
from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt

class JWTBearer(HTTPBearer):
    async def __call__(self, request):
        credentials = await super().__call__(request)
        if not credentials or credentials.scheme != "Bearer":
            raise HTTPException(status_code=403, detail="Invalid scheme")
        payload = verify_jwt(credentials.credentials)  # JWKS validation
        return payload
```

**Critical**: Use PyJWT (not python-jose). Supabase uses ES256 JWTs. Validate via JWKS endpoint for key rotation safety.

### Token Refresh Pattern with @supabase/ssr

The middleware pattern for Next.js App Router:

1. **Middleware** calls `supabase.auth.getUser()` to refresh tokens
2. **Sets cookies** on both request (for Server Components) and response (for browser)
3. **Server Components** read the refreshed token without re-refreshing
4. **Matcher** excludes static assets from middleware execution

**Security notes**:
- Never trust `supabase.auth.getSession()` server-side; use `getUser()` which validates the JWT signature
- Set `Cache-Control: private, no-store` on auth-related responses

### RLS Integration from FastAPI

**The core tension**: Single admin client for performance vs. per-user clients for RLS.

**Recommended pattern**:
1. Keep a global admin Supabase client (initialized once)
2. For RLS-enforced queries, set per-request headers with the user's JWT
3. Or use `set_config('request.jwt.claims', ...)` via RPC for RLS functions

**Alternative (Luna's current approach)**: Validate JWT in FastAPI, use service-role key for DB queries, implement authorization in the service layer. Simpler but moves security checks from DB to application.

### Key Decisions for Luna

| Decision | Choice | Rationale |
|----------|--------|-----------|
| JWT algorithm | ES256 (Supabase default) | Industry standard, JWKS rotation support |
| Token storage | Memory (Zustand) | Prevents XSS token theft |
| Refresh mechanism | @supabase/ssr middleware | Server-side refresh, cookie-based |
| RLS enforcement | Application layer + DB RLS | Defense in depth |
| Session validation | `getUser()` not `getSession()` | Validates JWT signature every time |

### References

- [Supabase Auth Architecture](https://supabase.com/docs/guides/auth/architecture)
- [Server-Side Auth for Next.js](https://supabase.com/docs/guides/auth/server-side/nextjs)
- [Integrating FastAPI with Supabase Auth](https://dev.to/j0/integrating-fastapi-with-supabase-auth-780)
- [Proper Supabase + FastAPI Discussion](https://github.com/orgs/supabase/discussions/33811)

---

## 3. Database Design: Multi-Tenant SaaS with Supabase

### RLS Patterns

**Pattern 1: Direct ownership**
```sql
CREATE POLICY "users_own_data" ON public.lawyer_cases
  FOR ALL USING ((SELECT auth.uid()) = lawyer_user_id);
```

**Pattern 2: Team/organization-based access**
```sql
CREATE POLICY "team_access" ON public.cases
  FOR SELECT USING (
    EXISTS (
      SELECT 1 FROM public.team_members
      WHERE team_members.team_id = cases.team_id
      AND team_members.user_id = (SELECT auth.uid())
    )
  );
```

**Pattern 3: Role-based with permission checks**
```sql
CREATE POLICY "role_based_access" ON public.documents
  FOR ALL USING (
    public.has_permission((SELECT auth.uid()), case_id, 'documents.read')
  );
```

### Critical Performance Optimization

**Always wrap `auth.uid()` in a `SELECT`**:
```sql
-- BAD: executes auth.uid() for every row
USING (auth.uid() = user_id)

-- GOOD: caches the result, evaluates once
USING ((SELECT auth.uid()) = user_id)
```

This single change can turn 3-minute queries into 2ms responses.

**Add partial indexes for soft deletes**:
```sql
CREATE INDEX idx_cases_active ON lawyer_cases(case_id)
  WHERE deleted_at IS NULL;

CREATE INDEX idx_cases_user ON lawyer_cases(lawyer_user_id)
  WHERE deleted_at IS NULL;
```

### Soft Delete Pattern

```sql
-- Column definition
deleted_at TIMESTAMPTZ DEFAULT NULL

-- Soft delete function
CREATE OR REPLACE FUNCTION soft_delete()
RETURNS TRIGGER AS $$
BEGIN
  NEW.deleted_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- All queries must filter
SELECT * FROM cases WHERE deleted_at IS NULL AND ...;

-- RLS policies must include soft delete check
CREATE POLICY "active_cases_only" ON public.lawyer_cases
  FOR SELECT USING (
    deleted_at IS NULL
    AND (SELECT auth.uid()) = lawyer_user_id
  );
```

**Purge strategy**: Schedule periodic cleanup of records older than retention period (e.g., 90 days).

### Audit Trail Approaches

| Approach | Complexity | Use Case |
|----------|-----------|----------|
| `created_at` / `updated_at` timestamps | Low | Basic change tracking |
| `deleted_at` soft deletes | Low | Recoverable deletes |
| Separate audit log table | Medium | Full history with actor/action |
| Trigger-based versioning (SCD Type 2) | High | Complete row history |
| `pg_audit` extension | Medium | Compliance-grade logging |

### pgvector for AI Applications

```sql
-- Enable extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Embeddings table
CREATE TABLE document_embeddings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id UUID REFERENCES documents(document_id),
  content TEXT NOT NULL,
  embedding VECTOR(1536),  -- OpenAI ada-002 dimensions
  metadata JSONB DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- HNSW index for fast similarity search
CREATE INDEX ON document_embeddings
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- Similarity search query
SELECT content, 1 - (embedding <=> $1::vector) AS similarity
FROM document_embeddings
WHERE document_id IN (SELECT document_id FROM case_documents WHERE case_id = $2)
ORDER BY embedding <=> $1::vector
LIMIT 5;
```

**RLS on embeddings**: Apply the same policies as the parent document table to ensure tenant isolation.

### Key Decisions for Luna

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Multi-tenancy model | Shared tables + RLS | Simpler, cost-effective for single-product SaaS |
| Soft deletes | `deleted_at` timestamp | Recoverable, audit trail, RLS-compatible |
| Vector dimensions | 1536 (OpenAI) | Matches embedding model output |
| Vector index | HNSW | Best recall/speed tradeoff for production |
| Audit level | Timestamps + soft deletes | Sufficient for legal app compliance |

### References

- [Supabase RLS Best Practices: Production Patterns](https://makerkit.dev/blog/tutorials/supabase-rls-best-practices)
- [Multi-Tenant Applications with RLS on Supabase](https://www.antstack.com/blog/multi-tenant-applications-with-rls-on-supabase-postgress/)
- [PostgreSQL Soft Deletes Implementation](https://oneuptime.com/blog/post/2026-01-21-postgresql-soft-deletes/view)
- [Postgres RLS Implementation Guide](https://www.permit.io/blog/postgres-rls-implementation-guide)

---

## 4. Caching Strategy: Redis for AI/Chat Apps

### What to Cache

| Data | Cache? | TTL | Strategy |
|------|--------|-----|----------|
| User sessions/tokens | Yes | 1h | Write-through |
| Case metadata | Yes | 5min | Cache-aside |
| Conversation list | Yes | 30s | Cache-aside + invalidation |
| Chat messages (recent) | Yes | 1min | Write-through |
| LLM responses (exact match) | Yes | 24h | Request-response cache |
| LLM responses (semantic) | Yes | 24h | Semantic cache |
| Document embeddings | No | - | Stored in pgvector |
| User preferences | Yes | 1h | Write-through |
| Rate limit counters | Yes | 1min | Sliding window |

### Cache Invalidation Patterns for FastAPI

**Pattern 1: TTL-based (simplest)**
```python
# Set with expiration
redis.setex(f"case:{case_id}", 300, json.dumps(case_data))  # 5 min TTL
```

**Pattern 2: Event-driven invalidation**
```python
# After mutation, invalidate related keys
async def update_case(case_id: str, data: dict):
    result = await db.update(case_id, data)
    await redis.delete(f"case:{case_id}")
    await redis.delete(f"cases:user:{user_id}")  # invalidate list too
    return result
```

**Pattern 3: Tag-based invalidation**
```python
# Store key membership in sets
await redis.sadd(f"tag:case:{case_id}", f"messages:{case_id}:page:1")
await redis.sadd(f"tag:case:{case_id}", f"documents:{case_id}")

# Invalidate all keys for a case
async def invalidate_case_cache(case_id: str):
    keys = await redis.smembers(f"tag:case:{case_id}")
    if keys:
        await redis.delete(*keys)
    await redis.delete(f"tag:case:{case_id}")
```

**Pattern 4: Write-through (for critical data)**
```python
async def send_message(message: dict):
    result = await db.insert(message)
    # Update cache immediately after write
    await redis.lpush(f"messages:{conversation_id}:recent", json.dumps(result))
    await redis.ltrim(f"messages:{conversation_id}:recent", 0, 49)  # keep last 50
    await redis.expire(f"messages:{conversation_id}:recent", 60)
    return result
```

### Semantic Caching for LLM Responses

```
User Query: "What are the penalties for fraud in Saudi law?"
    |
    v
Embedding Model -> Vector (1536 dims)
    |
    v
Redis Vector Search (cosine similarity > 0.92)
    |
    +---> Cache HIT  -> Return cached response (<200ms)
    |
    +---> Cache MISS -> Call LLM -> Store embedding + response -> Return (~2-5s)
```

**TTL strategies for LLM cache**:

| Content Type | TTL | Rationale |
|-------------|-----|-----------|
| Legal FAQs / stable knowledge | 24h | Rarely changes |
| Case-specific analysis | 1h | Context-dependent |
| Document summaries | 4h | Tied to document version |
| Rapidly changing legal updates | 15min | Must stay current |

**Similarity threshold**: Start at 0.92, monitor false positives (<3-5% target), adjust down to 0.90 if hit rate is too low.

### Hybrid Approach: Always Set TTL as Safety Net

Even with perfect invalidation logic, set TTLs as a fallback. A 24h TTL ensures cache entries never persist indefinitely if invalidation fails due to bugs or edge cases.

### Key Decisions for Luna

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Primary cache pattern | Cache-aside + event-driven invalidation | Balance of simplicity and freshness |
| LLM caching | Semantic cache via Redis + pgvector | Handles query reformulations |
| Default TTL | 5min for lists, 1h for entities, 24h for LLM | Aligned with data volatility |
| Cache warming | Pre-populate on login (user data, recent cases) | Better first-load experience |
| Graceful degradation | App works without Redis (slower) | Reliability over speed |

### References

- [What is Semantic Caching? (Redis)](https://redis.io/blog/what-is-semantic-caching/)
- [Caching Strategies for LLM Responses](https://dasroot.net/posts/2026/02/caching-strategies-for-llm-responses/)
- [FastAPI Cache Invalidation](https://oneuptime.com/blog/post/2026-02-02-fastapi-cache-invalidation/view)
- [Redis Caching Strategies: Next.js Production Guide](https://www.digitalapplied.com/blog/redis-caching-strategies-nextjs-production)
- [Building a Context-Enabled Semantic Cache with Redis](https://redis.io/blog/building-a-context-enabled-semantic-cache-with-redis/)

---

## 5. Error Handling: Full-Stack Patterns

### Backend Error Architecture (FastAPI)

**Structured error response format**:
```python
{
  "error": {
    "code": "CASE_NOT_FOUND",
    "message": "لم يتم العثور على القضية",  # Arabic user message
    "details": {
      "case_id": "abc-123"
    },
    "status": 404
  }
}
```

**Error code categories**:

| Range | Category | Example |
|-------|----------|---------|
| `AUTH_*` | Authentication | `AUTH_TOKEN_EXPIRED`, `AUTH_INVALID_CREDENTIALS` |
| `CASE_*` | Cases | `CASE_NOT_FOUND`, `CASE_ACCESS_DENIED` |
| `DOC_*` | Documents | `DOC_UPLOAD_FAILED`, `DOC_TOO_LARGE` |
| `MSG_*` | Messages | `MSG_SEND_FAILED`, `MSG_CONVERSATION_CLOSED` |
| `AI_*` | AI/RAG | `AI_MODEL_UNAVAILABLE`, `AI_CONTEXT_TOO_LONG` |
| `SYS_*` | System | `SYS_INTERNAL_ERROR`, `SYS_RATE_LIMITED` |

**i18n middleware pattern**:
```python
# Detect locale from Accept-Language header
class LocaleMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        locale = request.headers.get("Accept-Language", "ar").split(",")[0]
        request.state.locale = locale
        response = await call_next(request)
        return response

# Translation lookup
ERROR_MESSAGES = {
    "ar": {
        "CASE_NOT_FOUND": "لم يتم العثور على القضية",
        "AUTH_TOKEN_EXPIRED": "انتهت صلاحية الجلسة. يرجى تسجيل الدخول مرة أخرى",
        "SYS_RATE_LIMITED": "تم تجاوز الحد المسموح. يرجى المحاولة لاحقاً",
    },
    "en": {
        "CASE_NOT_FOUND": "Case not found",
        "AUTH_TOKEN_EXPIRED": "Session expired. Please log in again",
        "SYS_RATE_LIMITED": "Rate limit exceeded. Please try again later",
    }
}
```

### Frontend Error Boundaries

**React Error Boundary hierarchy**:
```
App Error Boundary (catches crashes, shows Arabic fallback UI)
  |
  +-- Layout Error Boundary (sidebar, header)
  |
  +-- Page Error Boundary (per-route)
       |
       +-- Component Error Boundary (chat panel, document viewer)
```

**TanStack Query error handling**:
```typescript
// Global error handler
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) => {
        if (error.status === 401) return false; // Don't retry auth errors
        if (error.status === 404) return false; // Don't retry not found
        return failureCount < 3;
      },
    },
    mutations: {
      onError: (error) => {
        // Show Arabic toast notification
        toast.error(error.message || 'حدث خطأ غير متوقع');
      }
    }
  }
});
```

### Key Decisions for Luna

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Error message language | Arabic by default, code for machines | User base is Saudi lawyers |
| Error code format | `DOMAIN_SPECIFIC_ERROR` | Machine-parseable, self-documenting |
| Frontend display | Toast for recoverable, boundary for fatal | Non-disruptive UX |
| Retry strategy | 3x with backoff, skip 401/404 | Handles transient failures |
| Logging | Structured JSON, error codes indexed | Searchable in production |

### References

- [FastAPI Error Handling Patterns (BetterStack)](https://betterstack.com/community/guides/scaling-python/error-handling-fastapi/)
- [FastAPI i18n: Step-by-Step Guide (Lokalise)](https://lokalise.com/blog/fastapi-internationalization/)
- [Localize FastAPI Validation Messages](https://dev.to/whchi/localize-your-fastapi-validation-message-38h4)
- [Error Handling in Full-Stack Applications (KodNest)](https://www.kodnest.com/blog/error-handling-in-full-stack-applications)

---

## 6. Real-Time Patterns: SSE vs WebSocket

### Decision Matrix

| Criterion | SSE | WebSocket | Winner for Luna |
|-----------|-----|-----------|-----------------|
| Direction | Server -> Client only | Bidirectional | SSE (AI streaming is one-way) |
| Protocol | HTTP/1.1 | WS (separate) | SSE (simpler infrastructure) |
| Auto-reconnect | Built-in | Manual implementation | SSE |
| Overhead per message | ~5 bytes | ~2 bytes | WebSocket (negligible difference) |
| HTTP/2 multiplexing | Yes | No | SSE |
| Load balancer support | Standard HTTP | Requires upgrade support | SSE |
| Browser support | All modern | All modern | Tie |
| Max connections (HTTP/1) | 6 per domain | Unlimited | WebSocket |
| Proxy/firewall friendly | Yes (standard HTTP) | Sometimes blocked | SSE |
| **AI chat streaming** | **Perfect fit** | **Overkill** | **SSE** |

### SSE Reconnection Strategy

```
Initial Connection
    |
    v
Connected (receiving events)
    |
    v
Connection Lost
    |
    v
Auto-Reconnect (built-in EventSource behavior)
    |
    +---> Retry with Last-Event-ID header
    |     (server resumes from last sent event)
    |
    +---> If server is down: exponential backoff
          1s -> 2s -> 4s -> 8s -> 16s (max 30s)
```

**Client-side implementation**:
```typescript
const eventSource = new EventSource(`/api/stream/${conversationId}`);

eventSource.onmessage = (event) => {
  const data = JSON.parse(event.data);
  // Append to message display
};

eventSource.onerror = (event) => {
  if (eventSource.readyState === EventSource.CLOSED) {
    // Connection was closed by server - no auto-reconnect
    // Manually reconnect with backoff
  }
  // CONNECTING state = auto-reconnect in progress (browser handles this)
};
```

### Optimistic UI for Chat Messages

```
1. User sends message
    |
    v
2. Immediately add to UI with "sending" status (optimistic)
    |
    v
3. POST /api/messages (save user message)
    |
    +---> Success: Update status to "sent"
    |     Open SSE stream for AI response
    |
    +---> Failure: Show error, offer retry
          Roll back optimistic message
```

### SSE Event Protocol for AI Chat

```
event: message_start
data: {"message_id": "abc-123", "role": "assistant"}

event: token
data: {"content": "بناءً"}

event: token
data: {"content": " على"}

event: token
data: {"content": " المادة"}

event: message_end
data: {"message_id": "abc-123", "token_count": 150}

event: artifact
data: {"type": "legal_citation", "content": "..."}

event: done
data: {}
```

### Key Decisions for Luna

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Protocol | SSE | AI streaming is server-to-client; simpler infrastructure |
| Reconnection | EventSource auto-reconnect + manual fallback | Handles both transient and permanent failures |
| Event format | Typed events (token, artifact, done) | Client can handle each event type differently |
| Optimistic messages | Yes, with rollback | Chat must feel instant |
| Heartbeat | Every 30s | Keeps connection alive through proxies |

### References

- [SSE vs WebSockets vs Long Polling: What's Best in 2025?](https://dev.to/haraf/server-sent-events-sse-vs-websockets-vs-long-polling-whats-best-in-2025-5ep8)
- [SSE's Glorious Comeback: Why 2025 is the Year of Server-Sent Events](https://portalzine.de/sses-glorious-comeback-why-2025-is-the-year-of-server-sent-events/)
- [WebSockets vs SSE (Ably)](https://ably.com/blog/websockets-vs-sse)
- [Server-Sent Events Beat WebSockets for 95% of Real-Time Apps](https://dev.to/polliog/server-sent-events-beat-websockets-for-95-of-real-time-apps-heres-why-a4l)

---

## 7. RTL/Arabic UI Best Practices

### Foundation

Set direction at the HTML level:
```html
<html lang="ar" dir="rtl">
```

### CSS Logical Properties (Tailwind CSS)

**Replace physical properties with logical ones**:

| Physical (avoid) | Logical (use) | Tailwind Class |
|-------------------|---------------|----------------|
| `margin-left` | `margin-inline-start` | `ms-4` |
| `margin-right` | `margin-inline-end` | `me-4` |
| `padding-left` | `padding-inline-start` | `ps-4` |
| `padding-right` | `padding-inline-end` | `pe-4` |
| `text-align: left` | `text-align: start` | `text-start` |
| `border-left` | `border-inline-start` | `border-s` |
| `rounded-left` | `rounded-start` | `rounded-s` |
| `left: 0` | `inset-inline-start: 0` | `start-0` |
| `right: 0` | `inset-inline-end: 0` | `end-0` |

**Flexbox and Grid auto-reverse**: When `dir="rtl"`, flex rows and grid columns automatically flow right-to-left. No additional CSS needed.

### Typography Rules for Arabic

| Property | Arabic Value | English Value | Notes |
|----------|-------------|---------------|-------|
| Font size | 18px | 16px | Arabic needs 10-15% larger |
| Line height | 1.8 | 1.5 | Diacritical marks need space |
| Min font weight | 400 | 300 | Arabic illegible below 400 |
| Letter spacing | 0 (never add) | Normal | Breaks cursive connections |
| Font family | IBM Plex Sans Arabic | System | Luna already uses this |

### Bidirectional Content Handling

**Wrap embedded English text**:
```html
<p>تم رفع القضية رقم <bdi>CASE-2024-001</bdi> بنجاح</p>
```

**Auto-detect input direction**:
```html
<input dir="auto" placeholder="اكتب هنا..." />
```

**Never mirror**:
- Phone numbers and timestamps
- Mathematical expressions
- Media player controls (play/pause)
- Brand logos
- Universal symbols (checkmarks, etc.)

### Testing Checklist

- [ ] Layout mirrors correctly (sidebar on right)
- [ ] Text flows right-to-left
- [ ] Numbers display correctly in context
- [ ] Mixed Arabic/English renders properly
- [ ] Icons that should mirror do (arrows, breadcrumbs)
- [ ] Icons that should NOT mirror don't (play, checkmarks)
- [ ] Form inputs accept Arabic text
- [ ] Error messages display in Arabic
- [ ] Scrollbar position is correct
- [ ] Modal/dropdown positioning is correct

### References

- [RTL Arabic Website Design Best Practices (AivenSoft)](https://www.aivensoft.com/en/blog/rtl-arabic-website-design-guide)
- [Tailwind CSS RTL Support (Flowbite)](https://flowbite.com/docs/customize/rtl/)
- [Implementing RTL Support in Tailwind CSS React](https://madrus4u.vercel.app/blog/rtl-implementation-guide)
- [Multilingual Bidirectional Websites with Tailwind](https://medium.com/@20lives/multilingual-bidirectional-rtl-websites-with-tailwind-and-nuxt-bca6ccd2494d)

---

## 8. Testing Strategies

### Testing Pyramid for Luna

```
         /  E2E Tests (Playwright)  \        <- 10-15 tests
        /   Integration Tests        \       <- 30-50 tests
       /    API Contract Tests         \     <- 50+ tests
      /     Unit Tests (pytest + vitest) \   <- 200+ tests
```

### Playwright E2E Best Practices

**Use locators, not selectors**:
```typescript
// Good: resilient to DOM changes
await page.getByRole('button', { name: 'إرسال' }).click();
await page.getByPlaceholder('اكتب رسالتك...').fill('مرحبا');

// Bad: fragile
await page.click('.send-btn');
```

**Auto-waiting**: Playwright auto-waits for elements to be actionable. Avoid explicit `waitForTimeout()`.

**Network interception for API tests**:
```typescript
await page.route('**/api/messages', (route) => {
  route.fulfill({
    status: 200,
    body: JSON.stringify({ messages: mockMessages }),
  });
});
```

**Test isolation**: Each test gets a fresh browser context. Use `beforeEach` for login state.

### pytest API Testing

**Contract testing with schema validation**:
```python
def test_send_message_contract():
    response = client.post("/api/messages", json={"content": "test"})
    assert response.status_code == 200
    data = response.json()
    # Validate response matches expected schema
    assert "message_id" in data
    assert "content" in data
    assert "created_at" in data
    assert "role" in data
```

**RLS testing with pgTAP**:
```sql
BEGIN;
-- Create test user
SELECT tests.create_supabase_user('test-user');
SELECT tests.authenticate_as('test-user');

-- Verify user can see own cases
SELECT results_eq(
  $$SELECT count(*) FROM lawyer_cases WHERE lawyer_user_id = tests.get_supabase_uid('test-user')$$,
  $$VALUES (1::bigint)$$,
  'User can see their own cases'
);

-- Verify user cannot see other users' cases
SELECT is_empty(
  $$SELECT * FROM lawyer_cases WHERE lawyer_user_id != tests.get_supabase_uid('test-user')$$,
  'User cannot see other users cases'
);
ROLLBACK;
```

### Testing Strategy per Layer

| Layer | Tool | Focus |
|-------|------|-------|
| Frontend components | Vitest + Testing Library | Render, interaction, Arabic text |
| API routes | pytest + httpx | Contracts, error codes, auth |
| Database / RLS | pgTAP | Policy enforcement, tenant isolation |
| E2E flows | Playwright | Login, chat, document upload |
| SSE streaming | pytest + httpx SSE client | Event format, reconnection |
| Performance | k6 or Locust | Load testing, connection pooling |

### References

- [Playwright Best Practices](https://playwright.dev/docs/best-practices)
- [Playwright E2E Testing Guide (BrowserStack)](https://www.browserstack.com/guide/playwright-best-practices)
- [End-to-end Testing with Python and Playwright](https://sixfeetup.com/blog/end-to-end-testing-python-playwright)
- [API Testing with Playwright Python](https://playwright.dev/python/docs/api-testing)

---

## 9. Security: OWASP and SaaS-Specific Patterns

### OWASP Top 10:2025 Relevant to Luna

| # | Risk | Luna Relevance | Mitigation |
|---|------|----------------|------------|
| 1 | Broken Access Control | High (multi-tenant data) | RLS policies, service-layer auth checks |
| 2 | Security Misconfiguration | High (Supabase + Railway) | Env vars, no exposed keys, CORS locked |
| 3 | Vulnerable Components | Medium (npm/pip deps) | Dependabot, regular audits |
| 4 | Identification/Auth Failures | High (JWT, sessions) | ES256 JWT, JWKS rotation, token in memory |
| 5 | Injection | Medium (user input in chat) | Parameterized queries, input validation |
| 6 | Software Supply Chain | Medium | Lock files, verified packages |
| 7 | Cryptographic Failures | Medium (JWT, TLS) | ES256, HTTPS only, no secrets in code |

### XSS Prevention in Chat Applications

**The primary risk**: User messages and AI responses may contain markdown with embedded HTML.

**Defense layers**:

1. **react-markdown** (primary): Converts markdown to React components WITHOUT `dangerouslySetInnerHTML`. Safe by default.

2. **Element whitelisting**: Restrict allowed elements:
   ```typescript
   <ReactMarkdown
     allowedElements={['p', 'strong', 'em', 'ul', 'ol', 'li', 'code', 'pre', 'a', 'h1', 'h2', 'h3']}
     urlTransform={(url) => {
       if (url.startsWith('javascript:')) return '';
       return url;
     }}
   />
   ```

3. **DOMPurify** (if using raw HTML): Sanitize before rendering:
   ```typescript
   import DOMPurify from 'dompurify';
   const clean = DOMPurify.sanitize(dirtyHtml, {
     ALLOWED_TAGS: ['p', 'strong', 'em', 'ul', 'ol', 'li', 'code', 'pre'],
     ALLOWED_ATTR: ['class']
   });
   ```

4. **CSP headers**: Last line of defense:
   ```
   Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'
   ```

### Rate Limiting Patterns

**Sliding window with Redis**:
```python
async def rate_limit(user_id: str, limit: int = 60, window: int = 60):
    key = f"rate:{user_id}:{int(time.time()) // window}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, window)
    if count > limit:
        raise HTTPException(429, detail="تم تجاوز الحد المسموح")
```

**Tiered rate limits**:

| Endpoint | Limit | Window | Rationale |
|----------|-------|--------|-----------|
| POST /messages (AI chat) | 20 | 1 min | Most expensive (LLM calls) |
| POST /documents/upload | 10 | 1 min | Storage + processing cost |
| GET /cases | 120 | 1 min | Read-heavy, cheaper |
| POST /auth/login | 5 | 5 min | Brute force protection |

### References

- [OWASP Top 10:2025](https://owasp.org/Top10/2025/en/)
- [Secure Markdown Rendering in React (HackerOne)](https://www.hackerone.com/blog/secure-markdown-rendering-react-balancing-flexibility-and-safety)
- [React Security Best Practices 2025](https://hub.corgea.com/articles/react-security-best-practices)
- [React Markdown Security Guide (Strapi)](https://strapi.io/blog/react-markdown-complete-guide-security-styling)

---

## 10. Scalability: Connection Pooling and Beyond

### Supabase Connection Management

**Supavisor modes**:

| Mode | Behavior | Use Case | Luna Use |
|------|----------|----------|----------|
| Transaction | Connection returned after each transaction | Web requests, serverless | Primary |
| Session | Connection held for entire session | LISTEN/NOTIFY, prepared statements | Not needed |

**Connection limits by plan**:

| Plan | Direct Connections | Pooler Connections |
|------|-------------------|-------------------|
| Free | 60 | 200 |
| Pro | 100 | 400 |
| Team | 150 | 600 |

### Connection Pooling for FastAPI

**Critical insight**: The default supabase-py client creates a new `httpx` client per instance. For FastAPI, share a singleton:

```python
# Shared httpx client (initialized once)
import httpx
from supabase import create_client

_http_client = httpx.Client()

def get_supabase():
    return create_client(
        SUPABASE_URL,
        SUPABASE_KEY,
        options=ClientOptions(
            postgrest_client_timeout=10,
            storage_client_timeout=20,
        )
    )

# Initialize once at startup
supabase = get_supabase()
```

**Use transaction mode** for all web traffic:
```
postgresql://postgres.[project-ref]:[password]@aws-0-[region].pooler.supabase.com:6543/postgres
```

### Horizontal Scaling Patterns

```
Load Balancer (Railway)
    |
    +---> FastAPI Instance 1 ---> Supavisor (pooler) ---> PostgreSQL
    +---> FastAPI Instance 2 ---> Supavisor (pooler) ---> PostgreSQL
    +---> FastAPI Instance 3 ---> Supavisor (pooler) ---> PostgreSQL
    |
    +---> Redis (shared cache + rate limiting)
```

**Stateless requirements for horizontal scaling**:
- No in-memory session state (use Redis)
- No local file storage (use Supabase Storage)
- SSE connections must be tied to the instance (sticky sessions or Redis pub/sub for fan-out)

### Background Job Patterns

| Pattern | Tool | Use Case |
|---------|------|----------|
| FastAPI BackgroundTasks | Built-in | Quick fire-and-forget (cache invalidation, logging) |
| Celery + Redis | Celery | Long-running (document processing, embedding generation) |
| Supabase Edge Functions | Deno | Webhooks, scheduled tasks |
| pg_cron | PostgreSQL | Database maintenance, cleanup jobs |

**For Luna's AI pipeline**:
```python
from fastapi import BackgroundTasks

@router.post("/messages")
async def send_message(msg: MessageCreate, background_tasks: BackgroundTasks):
    # Save message synchronously (crash-safe)
    saved = await message_service.save(msg)

    # Start AI processing in background
    background_tasks.add_task(
        process_ai_response,
        conversation_id=msg.conversation_id,
        message_id=saved.message_id
    )

    return saved
```

### Key Decisions for Luna

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Connection mode | Transaction pooling via Supavisor | Stateless web requests, max throughput |
| Client initialization | Singleton at startup | Avoid per-request HTTP client overhead |
| Background jobs | FastAPI BackgroundTasks (now), Celery (later) | Simple first, scale when needed |
| SSE scaling | Sticky sessions (Railway) | SSE connections must stay on same instance |
| Cache layer | Redis (Railway plugin) | Shared across instances |

### References

- [Supabase Connection Scaling for FastAPI](https://dev.to/papansarkar101/supabase-connection-scaling-the-essential-guide-for-fastapi-developers-348o)
- [Supavisor: Scaling Postgres to 1M Connections](https://supabase.com/blog/supavisor-1-million)
- [Connection Management (Supabase Docs)](https://supabase.com/docs/guides/database/connection-management)
- [Performance Tuning (Supabase Docs)](https://supabase.com/docs/guides/platform/performance)

---

## 11. AI Agent Architecture Patterns

### Agent Architecture Types (Relevance to Luna Wave 6)

| Pattern | Complexity | Luna Fit |
|---------|-----------|----------|
| Single Agent + Tools | Low | Good for general Q&A mode |
| Single Agent + Router | Medium | Best for Luna's dual-mode (general vs case-specific) |
| Sequential Agents | Medium-High | Good for RAG pipeline (retrieve -> analyze -> generate) |
| Hierarchical + Parallel | High | Overkill for current scope |

### Recommended: Router + Specialized Agents

```
User Message
    |
    v
Router Agent (classifies intent)
    |
    +---> General Legal Q&A Agent
    |         |
    |         +---> Saudi Law Knowledge Base
    |         +---> Citation Tool
    |
    +---> Case-Specific Agent
    |         |
    |         +---> Document RAG (pgvector search)
    |         +---> Case Memory Retrieval
    |         +---> Legal Analysis Tool
    |
    +---> Document Analysis Agent
              |
              +---> Document Parser
              +---> Summary Generator
              +---> Key Terms Extractor
```

### Memory Management for Chat Agents

| Memory Type | Storage | TTL | Purpose |
|-------------|---------|-----|---------|
| Conversation history | PostgreSQL | Permanent | Full chat record |
| Working memory | Redis | Session | Current context window |
| Case memories | PostgreSQL + pgvector | Permanent | User-defined key facts |
| Agent scratchpad | In-memory | Request | Intermediate reasoning |

### References

- [Ultimate Guide to AI Agent Architectures 2025](https://dev.to/sohail-akbar/the-ultimate-guide-to-ai-agent-architectures-in-2025-2j1c)
- [The Ultimate RAG Blueprint 2025/2026](https://langwatch.ai/blog/the-ultimate-rag-blueprint-everything-you-need-to-know-about-rag-in-2025-2026)
- [8 RAG Architecture Diagrams (SDH Global)](https://sdh.global/blog/development/8-rag-architecture-diagrams-you-need-to-master-in-2025/)

---

## Master Decision Matrix

This matrix captures the key architectural decisions for the Luna Legal AI project with alternatives considered.

| Domain | Decision | Chosen | Alternative | Why Chosen |
|--------|----------|--------|-------------|------------|
| **State** | Client state library | Zustand | Redux Toolkit, Jotai | 1KB bundle, simple API, no boilerplate |
| **State** | Server state library | TanStack Query | SWR, RTK Query | Best cache invalidation, optimistic updates |
| **Auth** | JWT algorithm | ES256 (Supabase default) | HS256 | Industry standard, key rotation via JWKS |
| **Auth** | Token storage | In-memory (Zustand) | localStorage, cookies | Prevents XSS token theft |
| **Auth** | SSR auth | @supabase/ssr middleware | Custom cookie handling | Official library, handles edge cases |
| **DB** | Multi-tenancy | Shared tables + RLS | Schema-per-tenant | Simpler, lower cost, Supabase-native |
| **DB** | Deletion strategy | Soft deletes (deleted_at) | Hard deletes | Audit trail, recovery, legal compliance |
| **DB** | Vector search | pgvector (PostgreSQL) | Pinecone, Weaviate | No extra service, RLS applies, simpler |
| **DB** | Vector index | HNSW | IVFFlat | Better recall at similar speed |
| **Cache** | Cache store | Redis (Railway) | In-memory, Memcached | Persistence, pub/sub, data structures |
| **Cache** | Primary pattern | Cache-aside + event invalidation | Write-through, read-through | Balance of simplicity and freshness |
| **Cache** | LLM caching | Semantic cache (Redis + pgvector) | Exact match only | Handles query reformulations |
| **Real-time** | Protocol | SSE | WebSocket, Long Polling | One-way streaming, auto-reconnect, HTTP |
| **Real-time** | Event format | Typed events (token, artifact, done) | Raw text stream | Client can handle each type differently |
| **UI** | RTL approach | CSS Logical Properties | rtl/ltr class toggles | Auto-adapts, cleaner markup |
| **UI** | Arabic font | IBM Plex Sans Arabic | Noto Sans Arabic | Better readability, good weight range |
| **Error** | Error format | Structured JSON with codes | Plain text messages | Machine-parseable, i18n-ready |
| **Error** | Default language | Arabic | English | User base is Saudi lawyers |
| **Security** | Markdown rendering | react-markdown (no dangerouslySetInnerHTML) | DOMPurify + raw HTML | XSS-safe by design |
| **Security** | Rate limiting | Sliding window (Redis) | Fixed window, token bucket | Smooth distribution, no burst edge cases |
| **Testing** | E2E framework | Playwright | Cypress | Multi-browser, Python bindings, faster |
| **Testing** | API testing | pytest + httpx | Postman, Insomnia | Automated, CI-friendly, code-based |
| **Testing** | RLS testing | pgTAP | Manual SQL queries | Repeatable, CI-integrated |
| **Scale** | Connection pooling | Supavisor (transaction mode) | PgBouncer, application-side | Supabase-native, cloud-optimized |
| **Scale** | Background jobs | FastAPI BackgroundTasks | Celery, Dramatiq | Simple, no extra infrastructure |
| **Scale** | Horizontal scaling | Stateless instances + shared Redis | Single instance | Ready for traffic growth |
| **AI** | Agent pattern | Router + specialized agents | Single monolithic agent | Clean separation, extensible |
| **AI** | Memory | PostgreSQL (permanent) + Redis (working) | All in Redis | Durability for legal data |

---

## Appendix: Quick Reference Links

### Official Documentation
- [Supabase Auth Architecture](https://supabase.com/docs/guides/auth/architecture)
- [Supabase RLS Guide](https://supabase.com/docs/guides/auth/row-level-security)
- [Supabase Connection Management](https://supabase.com/docs/guides/database/connection-management)
- [FastAPI Error Handling](https://fastapi.tiangolo.com/tutorial/handling-errors/)
- [Playwright Best Practices](https://playwright.dev/docs/best-practices)
- [OWASP Top 10:2025](https://owasp.org/Top10/2025/en/)

### Reference Implementations
- [Next.js + FastAPI Template (Vinta Software)](https://github.com/vintasoftware/nextjs-fastapi-template)
- [Full-Stack FastAPI Template (Official)](https://github.com/fastapi/full-stack-fastapi-template)
- [Free Next.js Supabase SaaS Starter (MakerKit)](https://makerkit.dev/blog/changelog/free-nextjs-saas-boilerplate)
- [GPTCache: Semantic Cache for LLMs](https://github.com/zilliztech/GPTCache)
- [Supavisor: Cloud-Native Connection Pooler](https://github.com/supabase/supavisor)

### Community Discussions
- [Supabase + FastAPI Proper Usage](https://github.com/orgs/supabase/discussions/33811)
- [Next.js + FastAPI + Supabase Project Structure (Cursor Forum)](https://forum.cursor.com/t/best-practices-for-structuring-a-next-js-fastapi-supabase-project/49706)
