# FastAPI + PostgreSQL Production Patterns & Real-World Projects

> Research compiled: 2026-03-13
> Focus: FastAPI + PostgreSQL (especially Supabase), production deployment, JWT auth, SSE streaming, Redis caching, connection pooling

---

## Summary Table of Projects & Resources

| # | Project / Resource | URL | Stack | Key Takeaway |
|---|---|---|---|---|
| 1 | **Full-Stack FastAPI Template** (Official) | [GitHub](https://github.com/fastapi/full-stack-fastapi-template) | FastAPI, React, SQLModel, PostgreSQL, Docker, Traefik | Official reference architecture; Gunicorn + Uvicorn workers; CI/CD with GitHub Actions |
| 2 | **Quivr** (LLM SaaS) | [Substack Analysis](https://euclideanai.substack.com/p/fastapi-supabase-template-for-llm) | FastAPI, Supabase, Celery, Redis, pgvector | 24k+ stars; FastAPI + Supabase + Celery architecture for AI/LLM workloads |
| 3 | **fastapi_supabase_template** (AtticusZeller) | [GitHub](https://github.com/AtticusZeller/fastapi_supabase_template) | FastAPI, Supabase Auth, PostgreSQL, File Upload | Deep Supabase integration — auth, CRUD, file upload, all-in-one |
| 4 | **fastapi_best_architecture** | [GitHub](https://github.com/fastapi-practices/fastapi_best_architecture) | FastAPI, SQLAlchemy, Celery, Pydantic, Grafana, Docker | Enterprise-grade layered architecture with monitoring |
| 5 | **FastAPI Best Practices** (zhanymkanov) | [GitHub](https://github.com/zhanymkanov/fastapi-best-practices) | FastAPI conventions | Netflix Dispatch-inspired; startup-tested conventions and project structure |
| 6 | **FastAPI-boilerplate** (benavlabs) | [GitHub](https://github.com/benavlabs/FastAPI-boilerplate) | FastAPI, Pydantic V2, SQLAlchemy 2.0, PostgreSQL, Redis | Async-first with Redis caching layer |
| 7 | **fastapi-container** (jmitchel3) | [GitHub](https://github.com/jmitchel3/fastapi-container) | FastAPI, Docker, Railway | Dockerfile boilerplate optimized for Railway deployments |
| 8 | **fastapi-sqlalchemy-asyncpg** (grillazz) | [GitHub](https://github.com/grillazz/fastapi-sqlalchemy-asyncpg) | FastAPI, SQLAlchemy, asyncpg, PostgreSQL | Reference for async DB integration with connection pooling |

---

## 1. Full-Stack FastAPI Template (Official)

**URL:** https://github.com/fastapi/full-stack-fastapi-template
**Docs:** https://fastapi.tiangolo.com/project-generation/

### What It Does
The official full-stack template maintained by the FastAPI organization. Provides a complete production-ready setup with FastAPI backend, React frontend, PostgreSQL database, Docker Compose, Traefik reverse proxy, and GitHub Actions CI/CD.

### Architecture Decisions

- **ORM:** SQLModel (Pydantic + SQLAlchemy hybrid) for type-safe DB models
- **DB Driver:** Sync SQLAlchemy by default (psycopg2); async optional
- **Auth:** JWT with secure password hashing (bcrypt), email-based recovery
- **Server:** Gunicorn managing Uvicorn workers (industry standard for production)
- **Proxy:** Traefik for automatic HTTPS (Let's Encrypt), load balancing
- **Testing:** Pytest with Docker Compose test environment
- **Migrations:** Alembic

### Key Patterns

```
app/
  api/          # Route handlers (thin — delegate to services/CRUD)
  core/         # Config, security, deps
  models/       # SQLModel classes (DB + API models unified)
  crud/         # Database operations (repository-like layer)
  tests/        # Pytest test suite
```

### Production Lessons
- Gunicorn + Uvicorn workers is the gold standard for ASGI deployment
- SQLModel unifies Pydantic validation and SQLAlchemy ORM in one class
- Connection pool exhaustion is a known issue under load (see Issue #104, #290)

---

## 2. Quivr — LLM SaaS with FastAPI + Supabase

**Analysis:** https://euclideanai.substack.com/p/fastapi-supabase-template-for-llm
**Part 2 (Celery + pgvector):** https://euclideanai.substack.com/p/fastapi-supabase-template-for-llm-941

### What It Does
Open-source "second brain" AI app (24k+ GitHub stars). Users upload documents, Quivr embeds them into vectors, and provides AI-powered Q&A over those documents. The backend is FastAPI + Supabase + Celery.

### Architecture Decisions

- **Three-tier backend:** FastAPI (API server) + Celery (background worker) + Supabase (DB + Auth + Storage)
- **Task queue:** Redis as Celery broker and result backend
- **Vector store:** Supabase pgvector for embeddings
- **File processing pipeline:** Upload to Supabase Storage -> Celery downloads & processes -> Document Loader -> Text Splitter -> Embedder -> pgvector
- **Auth:** Supabase Auth (signUp/signIn from client library)
- **ORM:** SQLAlchemy (works with both FastAPI async handlers and Celery sync workers)

### Key Patterns

```
FastAPI endpoint (upload) -> Supabase Storage bucket
  -> Celery task triggered
  -> Celery worker: download file, split text, embed chunks
  -> Insert vectors into Supabase pgvector
```

### Production Lessons
- Celery doesn't support asyncio well — use SQLAlchemy (not asyncpg) if sharing ORM between FastAPI and Celery
- Decouple latency-sensitive request handling from I/O-intensive embedding operations
- Run FastAPI, Celery workers, and Celery Beat scheduler as separate services in production
- Supabase Storage handles file uploads directly, reducing FastAPI server load

---

## 3. fastapi_supabase_template (AtticusZeller)

**URL:** https://github.com/AtticusZeller/fastapi_supabase_template

### What It Does
A deeply integrated FastAPI + Supabase template covering auth, CRUD, file upload. Inspired by the official full-stack-fastapi-postgresql template but purpose-built for Supabase.

### Architecture Decisions

- **Auth:** Supabase Auth via supabase-py client (signUp, signIn, token verification)
- **Database:** Supabase PostgreSQL accessed through supabase-py REST client (not raw SQLAlchemy)
- **File uploads:** Supabase Storage buckets via client library
- **Background tasks:** Celery + Redis for heavy operations
- **Sync client:** Uses synchronous supabase-py client in FastAPI route handlers (works because supabase-py v2 REST calls are HTTP-based and short-lived)

### Key Patterns
- Environment variables: `SUPABASE_URL` + `SUPABASE_KEY` (service role for server-side ops)
- Dependency injection for Supabase client instance
- Table queries: `.from_("table").select("*").eq("col", val).execute()`

### Production Lessons
- supabase-py v2 uses httpx internally, so even the "sync" client is efficient for typical CRUD
- For read-heavy APIs, consider caching Supabase responses in Redis
- Service role key must never be exposed to the frontend

---

## 4. fastapi_best_architecture

**URL:** https://github.com/fastapi-practices/fastapi_best_architecture

### What It Does
Enterprise-level backend architecture solution providing a comprehensive, layered structure for production FastAPI applications. Includes Celery integration, Grafana monitoring, and Docker deployment.

### Architecture Decisions

- **Layered architecture:** Router -> Service -> Repository -> Model
- **ORM:** SQLAlchemy 2.0 with async support
- **Task queue:** Celery with Redis broker
- **Monitoring:** Grafana dashboards
- **Containerization:** Full Docker Compose stack
- **Config:** Pydantic Settings for type-safe configuration

### Key Patterns

```
app/
  api/v1/        # Versioned route handlers
  services/      # Business logic layer
  repositories/  # Data access layer (repository pattern)
  models/        # SQLAlchemy models
  schemas/       # Pydantic request/response schemas
  core/          # Config, security, middleware
  tasks/         # Celery task definitions
```

### Production Lessons
- Strict layer separation enables independent testing of each layer
- Repository pattern abstracts DB access, making it easy to swap data sources
- Celery integration requires separate configuration for broker and backend
- Grafana integration provides production observability out of the box

---

## 5. FastAPI Best Practices (zhanymkanov)

**URL:** https://github.com/zhanymkanov/fastapi-best-practices

### What It Does
A curated collection of best practices and conventions tested at a real startup. Inspired by Netflix's Dispatch project structure.

### Key Recommendations

1. **Project structure:** Organize by domain, not by file type. Each module gets its own `router.py`, `schemas.py`, `models.py`, `service.py`, `dependencies.py`, `constants.py`, `exceptions.py`
2. **Dependency injection:** Use `Depends()` extensively; chain dependencies for auth, DB sessions, permissions
3. **Pydantic:** Use strict mode, custom validators; separate request and response models
4. **Background tasks:** Use `BackgroundTasks` for lightweight work; Celery for heavy/distributed tasks
5. **Error handling:** Custom exception handlers with consistent error response format
6. **CORS:** Configure explicitly; never use `allow_origins=["*"]` in production

### Project Structure Pattern (Netflix Dispatch-inspired)

```
src/
  auth/
    router.py
    schemas.py
    models.py
    service.py
    dependencies.py
  cases/
    router.py
    schemas.py
    ...
  core/
    config.py
    exceptions.py
```

---

## 6. FastAPI + Supabase JWT (ES256 / JWKS) Patterns

### The Problem
Supabase uses **ES256** (Elliptic Curve) JWTs, not the more common HS256. This requires asymmetric key verification via JWKS endpoint.

### Production Implementation Pattern

**Sources:**
- [Validating a Supabase JWT locally with Python and FastAPI](https://dev.to/zwx00/validating-a-supabase-jwt-locally-with-python-and-fastapi-59jf)
- [Integrating FastAPI with Supabase Auth](https://dev.to/j0/integrating-fastapi-with-supabase-auth-780)
- [Migrating from Static JWT Secrets to JWKS in Supabase](https://objectgraph.com/blog/migrating-supabase-jwt-jwks/)
- [Build authentication API with ES256 encryption](https://dc1888.medium.com/build-simple-authentication-api-using-fast-api-with-es256-encryption-in-10-mins-f8c0113937a)

### Key Architecture Decisions

1. **Library:** Use **PyJWT** (not python-jose) with `pyjwt[crypto]` for ES256 support
2. **JWKS fetching:** Use `PyJWKClient` to auto-fetch public keys from Supabase JWKS endpoint
3. **Caching:** JWKS endpoint is cached by Supabase Edge for 10 minutes; implement local caching too
4. **Fallback:** Implement retry logic when JWKS fetch fails
5. **kid header:** Each Supabase JWT includes a `kid` (key id) to identify which public key to use

### Code Pattern

```python
from jwt import PyJWKClient, decode as jwt_decode

JWKS_URL = "https://<project>.supabase.co/auth/v1/.well-known/jwks.json"
jwks_client = PyJWKClient(JWKS_URL, cache_keys=True)

async def verify_token(token: str):
    signing_key = jwks_client.get_signing_key_from_jwt(token)
    payload = jwt_decode(
        token,
        signing_key.key,
        algorithms=["ES256"],
        audience="authenticated",
    )
    return payload
```

### Common Pitfalls
- Using `python-jose` instead of `PyJWT` — jose has stale dependencies and less ES256 support
- Forgetting to set `audience="authenticated"` causes validation failures
- Not caching JWKS keys leads to excessive network calls on every request
- HS256 vs ES256 mismatch: older Supabase docs show HS256 with `JWT_SECRET`, but hosted Supabase uses ES256

---

## 7. SSE Streaming with sse-starlette

### The Library
**sse-starlette** — Production-ready W3C-compliant SSE for Starlette/FastAPI

**Sources:**
- [sse-starlette PyPI](https://pypi.org/project/sse-starlette/)
- [GitHub](https://github.com/sysid/sse-starlette)
- [FastAPI SSE Tutorial](https://fastapi.tiangolo.com/tutorial/server-sent-events/)
- [DeepWiki: Error Handling](https://deepwiki.com/sysid/sse-starlette/3.4-error-handling)
- [DeepWiki: Client Disconnection Detection](https://deepwiki.com/sysid/sse-starlette/3.5-client-disconnection-detection)

### Production Architecture

```python
from sse_starlette.sse import EventSourceResponse

@router.get("/stream/{conversation_id}")
async def stream_response(request: Request, conversation_id: str):
    async def event_generator():
        try:
            async for chunk in ai_service.generate(conversation_id):
                if await request.is_disconnected():
                    break
                yield {"event": "token", "data": chunk}
            yield {"event": "done", "data": "[DONE]"}
        except asyncio.CancelledError:
            # Cleanup on client disconnect
            raise

    return EventSourceResponse(event_generator())
```

### Key Production Patterns

1. **Keep-alive pings:** FastAPI sends a comment every 15 seconds by default to keep connections alive
2. **Headers:** `Cache-Control: no-cache` and `X-Accel-Buffering: no` are set automatically
3. **Nginx buffering:** Nginx buffers responses by default (~16KB), delaying SSE events. Always set `X-Accel-Buffering: no`
4. **Disconnect detection:** Three-layer approach:
   - Passive: ASGI `http.disconnect` monitoring
   - Active: `request.is_disconnected()` polling in generator
   - Exception: `asyncio.CancelledError` cleanup
5. **Graceful shutdown:** `shutdown_grace_period` must be less than Uvicorn's `--timeout-graceful-shutdown`
6. **Client reconnection:** Browser `EventSource` has built-in reconnection with exponential backoff; control via `retry` field
7. **Multi-process scaling:** Use Redis Pub/Sub as central event source when running multiple Uvicorn workers

### Common Pitfalls
- Not handling `asyncio.CancelledError` — leads to resource leaks
- Forgetting `X-Accel-Buffering: no` behind Nginx/reverse proxies
- Not checking `request.is_disconnected()` inside long generators
- Using WSGI servers instead of ASGI (Uvicorn/Daphne)

---

## 8. Redis Caching Patterns

### Sources
- [FastAPI + Redis Tutorial (Official Redis)](https://redis.io/learn/develop/python/fastapi)
- [fastapi-cache (GitHub)](https://github.com/long2ice/fastapi-cache)
- [FastAPI Caching with Middleware (Redis)](https://sayanc20002.medium.com/fastapi-caching-with-middleware-redis-097a17bcef82)
- [3x Faster Responses with async_lru and Redis](https://medium.com/@bhagyarana80/3x-faster-responses-in-fastapi-smart-caching-with-async-lru-and-redis-for-high-concurrency-apis-6b8428772f22)

### Production Caching Strategies

**Cache-Aside (Lazy Loading):**
```python
async def get_case(case_id: str):
    cached = await redis.get(f"case:{case_id}")
    if cached:
        return json.loads(cached)
    result = await db.fetch_case(case_id)
    await redis.set(f"case:{case_id}", json.dumps(result), ex=300)
    return result
```

**Write-Through:**
```python
async def update_case(case_id: str, data: dict):
    result = await db.update_case(case_id, data)
    await redis.set(f"case:{case_id}", json.dumps(result), ex=300)
    # Invalidate list caches
    await redis.delete("cases:list:*")
    return result
```

### Best Practices

| Pattern | When to Use |
|---------|-------------|
| Cache-aside | Read-heavy paths, tolerant of stale data |
| Write-through | Strong read-after-write consistency needed |
| Pattern deletion | After writes, invalidate related list caches |
| TTL strategy | Conservative TTLs (60-300s); shorter for frequently changing data |

### Key Configuration

- **Key prefixing:** Use `resource:id` or `list:name` for predictable invalidation
- **Lifespan integration:** Connect/disconnect Redis in FastAPI lifespan handler
- **Circuit breaker:** Implement fallback when Redis is unavailable (degrade gracefully to DB-only)
- **Serialization:** Use `orjson` for faster JSON serialization/deserialization
- **Connection pooling:** Use `redis.asyncio.ConnectionPool` with explicit `max_connections`

---

## 9. Connection Pooling Strategies (Supabase + FastAPI)

### Sources
- [Supabase Connection Scaling Guide for FastAPI](https://dev.to/papansarkar101/supabase-connection-scaling-the-essential-guide-for-fastapi-developers-348o)
- [Supabase Pooling and asyncpg Don't Mix](https://medium.com/@patrickduch93/supabase-pooling-and-asyncpg-dont-mix-here-s-the-real-fix-44f700b05249)
- [Handling PostgreSQL Connection Limits in FastAPI](https://medium.com/@rameshkannanyt0078/handling-postgresql-connection-limits-in-fastapi-efficiently-379ff44bdac5)
- [Supabase PgBouncer Blog](https://supabase.com/blog/supabase-pgbouncer)

### Supabase Connection Modes

| Mode | Port | Pooler | Use Case |
|------|------|--------|----------|
| **Direct** | 5432 | None | Migrations, simple apps, < 20 connections |
| **Transaction** (Supavisor) | 6543 | PgBouncer/Supavisor | Production APIs, high concurrency |
| **Session** | 5432 | Session-level | Long-lived connections (not recommended for APIs) |

### Critical Decision: Which Port?

**For supabase-py (REST client):** Use the default Supabase URL — it goes through PostgREST, not direct Postgres. Connection pooling is handled by Supabase infrastructure. No changes needed.

**For SQLAlchemy/asyncpg (direct DB):** Use **port 6543** (transaction mode) with these settings:

```python
from sqlalchemy import create_engine, NullPool

engine = create_engine(
    "postgresql://postgres:password@db.xxx.supabase.co:6543/postgres",
    poolclass=NullPool,  # Let Supavisor manage pooling
)
```

### asyncpg + PgBouncer Gotcha

asyncpg uses **prepared statements** by default. PgBouncer in transaction mode **does not support prepared statements**. This causes:

```
asyncpg.exceptions._base.InterfaceError:
  prepared statement "asyncpg_stmt_9" does not exist
```

**Fix:** Disable statement caching:

```python
engine = create_async_engine(
    DATABASE_URL,
    poolclass=NullPool,
    connect_args={
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
    }
)
```

### Connection Pool Configuration (SQLAlchemy)

| Parameter | Default | Recommendation |
|-----------|---------|----------------|
| `pool_size` | 5 | Match to expected concurrent requests |
| `max_overflow` | 10 | 2x pool_size for burst traffic |
| `pool_pre_ping` | False | **True** — detects stale connections |
| `pool_recycle` | -1 | 1800 (30 min) for long-running apps |
| `pool_timeout` | 30 | Lower to 10-15s to fail fast |

### When Using supabase-py (Luna's Pattern)

Luna uses **supabase-py sync client** in FastAPI route handlers. This is the simpler path:
- No connection pooling config needed — supabase-py uses HTTP/REST
- Each request creates an HTTP call to PostgREST
- PostgREST handles its own connection pooling to PostgreSQL
- Tradeoff: Slightly higher latency per query vs. direct SQL, but zero connection management complexity

---

## Common Patterns & Best Practices

### 1. Service Layer Architecture

The consensus across all production projects is a clear **three-layer separation**:

```
Route Handler (thin) -> Service Layer (business logic) -> Repository/CRUD (data access)
```

- **Route handlers** validate input (via Pydantic), call services, return responses
- **Service layer** contains business rules, orchestrates multiple repos, handles transactions
- **Repository/CRUD layer** encapsulates all database queries

### 2. Dependency Injection

FastAPI's `Depends()` is the backbone of clean architecture:

```python
# Chain dependencies: auth -> user -> service
async def get_current_user(token: str = Depends(oauth2_scheme)) -> User: ...
async def get_user_service(user: User = Depends(get_current_user)) -> UserService: ...

@router.get("/cases")
async def list_cases(service: CaseService = Depends(get_case_service)):
    return await service.list_all()
```

### 3. Error Handling

- Custom exception classes per domain
- Global exception handler returning consistent JSON format
- All user-facing messages in the target language (Arabic for Luna)
- Log full stack traces server-side; return safe messages to client

### 4. Configuration Management

- Pydantic `BaseSettings` for type-safe config with env var loading
- Separate `.env` files for dev/staging/prod
- Secrets via environment variables (never in code)
- Use `@lru_cache` on settings factory to avoid re-parsing

### 5. Docker Best Practices

```dockerfile
# Multi-stage build
FROM python:3.12-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY . .
CMD ["gunicorn", "app.main:app", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000"]
```

Key points:
- Use `python:3.12-slim` (not alpine — alpine causes issues with compiled packages)
- Multi-stage builds reduce image size
- `--no-cache-dir` saves space
- 4 Uvicorn workers is typical for 1-2 vCPU containers

### 6. Railway-Specific Deployment

```json
// railway.json
{
  "$schema": "https://railway.app/railway.schema.json",
  "build": { "builder": "DOCKERFILE" },
  "deploy": {
    "healthcheckPath": "/health",
    "healthcheckTimeout": 300,
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 5
  }
}
```

- Railway auto-detects Dockerfiles
- Set `PORT` env var (Railway assigns it dynamically)
- Use internal networking for Redis (`redis.railway.internal:6379`)
- Health checks prevent deploying broken builds

---

## Typical Errors & Debugging

### 1. Connection Pool Exhaustion

**Error:** `QueuePool limit of size 5 overflow 10 reached, connection timed out`

**Cause:** Too many concurrent requests, each holding a DB connection too long.

**Fix:**
- Increase `pool_size` and `max_overflow`
- Ensure sessions are closed properly (use context managers or `Depends` cleanup)
- Set `pool_pre_ping=True` to evict stale connections
- For Supabase: switch to port 6543 (transaction mode) or use supabase-py REST client

### 2. Prepared Statement Errors with PgBouncer

**Error:** `prepared statement "asyncpg_stmt_X" does not exist`

**Cause:** asyncpg creates prepared statements; PgBouncer transaction mode doesn't support them.

**Fix:** Set `statement_cache_size=0` and `prepared_statement_cache_size=0` in `connect_args`, or use `NullPool`.

### 3. "Too Many Connections" on Supabase

**Error:** `FATAL: Max client connections reached`

**Cause:** Using direct connections (port 5432) without pooling; each FastAPI worker + request opens a new connection.

**Fix:**
- Switch to transaction mode (port 6543)
- Use `NullPool` in SQLAlchemy (let Supavisor manage connections)
- For supabase-py: this shouldn't happen (REST-based), but check for leaked client instances
- Monitor via Supabase dashboard > Database > Connection Pooling

### 4. JWT Validation Failures

**Error:** `jwt.exceptions.InvalidSignatureError` or `jwt.exceptions.DecodeError`

**Common Causes:**
| Symptom | Cause | Fix |
|---------|-------|-----|
| Invalid signature | Using HS256 secret to verify ES256 token | Switch to JWKS-based verification |
| Audience mismatch | Missing `audience="authenticated"` | Add audience parameter to decode() |
| Expired token | Access token not refreshed | Implement refresh token flow on frontend |
| kid not found | JWKS cache stale | Clear cache and re-fetch from JWKS endpoint |

### 5. SSE Connection Drops

**Symptoms:** Client stops receiving events; connection silently closes.

**Common Causes & Fixes:**
| Cause | Fix |
|-------|-----|
| Nginx buffering | Add `X-Accel-Buffering: no` header |
| Proxy timeout | Set `proxy_read_timeout 86400s` in Nginx |
| No keep-alive | sse-starlette sends pings every 15s by default; ensure it's not disabled |
| Resource leak | Handle `asyncio.CancelledError`; always re-raise it |
| Railway timeout | Railway has a 5-minute idle timeout; keep-alive pings prevent this |

### 6. Async vs Sync Confusion

**Error:** `RuntimeError: cannot perform operation: another operation is in progress`

**Cause:** Using sync DB operations inside async route handlers, or sharing a sync session across async contexts.

**Fix:**
- If using sync supabase-py client: this is fine for HTTP/REST calls (they're short-lived I/O)
- If using SQLAlchemy: choose async engine + async session OR sync engine with `run_in_threadpool`
- Never mix sync and async SQLAlchemy sessions in the same handler

### 7. Redis Connection Issues

**Error:** `ConnectionError: Error connecting to redis.railway.internal:6379`

**Common Causes:**
| Cause | Fix |
|-------|-----|
| Wrong host | Use internal host for Railway-to-Railway; public host for external access |
| No password | Railway Redis requires password; check `REDIS_URL` env var |
| Connection timeout | Increase timeout; check if Redis is in same Railway project |
| Pool exhaustion | Set `max_connections` on `ConnectionPool` |

### 8. CORS Errors

**Error:** `Access to fetch blocked by CORS policy`

**Fix:**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://your-frontend.up.railway.app"],  # Never ["*"] in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### 9. Docker Build Failures on Railway

**Common Issues:**
| Issue | Fix |
|-------|-----|
| `pip install` timeout | Add `--timeout 120` to pip install |
| Missing system deps | Install `build-essential`, `libpq-dev` for psycopg2 |
| Large image size | Use multi-stage builds; use `.dockerignore` |
| Wrong Python version | Pin version in Dockerfile: `FROM python:3.12-slim` |

### 10. Supabase RLS Blocking Queries

**Error:** Empty results or `permission denied` when queries should return data.

**Cause:** Row Level Security policies don't match the JWT claims or user context.

**Fix:**
- Verify RLS policies in Supabase dashboard
- Check that `auth.uid()` matches the user making the request
- For service-role operations, use the service role key (bypasses RLS)
- Test policies with the SQL Editor using `set role authenticated; set request.jwt.claims = '...'`

---

## Sources

### Projects & Templates
- [Full Stack FastAPI Template (Official)](https://github.com/fastapi/full-stack-fastapi-template)
- [fastapi_supabase_template (AtticusZeller)](https://github.com/AtticusZeller/fastapi_supabase_template)
- [fastapi_best_architecture](https://github.com/fastapi-practices/fastapi_best_architecture)
- [FastAPI Best Practices (zhanymkanov)](https://github.com/zhanymkanov/fastapi-best-practices)
- [FastAPI-boilerplate (benavlabs)](https://github.com/benavlabs/FastAPI-boilerplate)
- [fastapi-container (jmitchel3)](https://github.com/jmitchel3/fastapi-container)
- [fastapi-sqlalchemy-asyncpg (grillazz)](https://github.com/grillazz/fastapi-sqlalchemy-asyncpg)
- [python-supabase-crud-api](https://github.com/theinfosecguy/python-supabase-crud-api)

### Tutorials & Guides
- [FastAPI + Supabase Template for LLM SaaS Part 1](https://euclideanai.substack.com/p/fastapi-supabase-template-for-llm)
- [FastAPI + Supabase Template for LLM SaaS Part 2 (Celery + pgvector)](https://euclideanai.substack.com/p/fastapi-supabase-template-for-llm-941)
- [Building a Supabase and FastAPI Project (Medium)](https://medium.com/@abhik12295/building-a-supabase-and-fastapi-project-a-modern-backend-stack-52030ca54ddf)
- [Building a CRUD API with FastAPI and Supabase](https://blog.theinfosecguy.xyz/building-a-crud-api-with-fastapi-and-supabase-a-step-by-step-guide)
- [Supabase FastAPI Integration (Hrekov)](https://hrekov.com/blog/supabase-with-fastapi)
- [Setting up FastAPI with SupabaseDB (DEV)](https://dev.to/j0/setting-up-fastapi-with-supabasedb-2jm0)
- [Integrating FastAPI with Supabase Auth (DEV)](https://dev.to/j0/integrating-fastapi-with-supabase-auth-780)

### Deployment
- [Deploy a FastAPI App (Railway Docs)](https://docs.railway.com/guides/fastapi)
- [Deploy FastAPI to Railway with Dockerfile](https://www.codingforentrepreneurs.com/blog/deploy-fastapi-to-railway-with-this-dockerfile)
- [FastAPI Deployment Guide 2026](https://www.zestminds.com/blog/fastapi-deployment-guide/)
- [FastAPI Best Practices for Production 2026](https://fastlaunchapi.dev/blog/fastapi-best-practices-production-2026)
- [FastAPI Production Deployment 2025 Guide](https://craftyourstartup.com/cys-docs/fastapi-production-deployment/)
- [Deploying Next.js, FastAPI, and PostgreSQL (Medium)](https://medium.com/@zafarobad/ultimate-guide-to-deploying-next-js-d57ab72f6ba6)

### Architecture & Patterns
- [Layered Architecture & DI in FastAPI (DEV)](https://dev.to/markoulis/layered-architecture-dependency-injection-a-recipe-for-clean-and-testable-fastapi-code-3ioo)
- [Repository Pattern in Python with FastAPI (Medium)](https://medium.com/@kmuhsinn/the-repository-pattern-in-python-write-flexible-testable-code-with-fastapi-examples-aa0105e40776)
- [Service Layer Pattern (Marc Puig)](https://mpuig.github.io/Notes/fastapi_basics/04.service_layer_pattern/)
- [FastAPI Service Layer Best Practices (Medium)](https://medium.com/@hadiyolworld007/%EF%B8%8F-fastapi-service-layer-best-practices-what-goes-where-and-why-1bfdfdd6b55e)
- [Building Production-Ready FastAPI with Service Layer (Medium)](https://medium.com/@abhinav.dobhal/building-production-ready-fastapi-applications-with-service-layer-architecture-in-2025-f3af8a6ac563)
- [FastAPI Best Practices (Auth0)](https://auth0.com/blog/fastapi-best-practices/)

### Auth & Security
- [FastAPI OAuth2 JWT Tutorial (Official)](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/)
- [Securing FastAPI with JWT (TestDriven.io)](https://testdriven.io/blog/fastapi-jwt-auth/)
- [FastAPI Security Guide: JWT, OAuth2, API Keys (Greeden)](https://blog.greeden.me/en/2025/12/30/practical-fastapi-security-guide-designing-modern-apis-protected-by-jwt-auth-oauth2-scopes-and-api-keys/)
- [ES256 Authentication with FastAPI (Medium)](https://dc1888.medium.com/build-simple-authentication-api-using-fast-api-with-es256-encryption-in-10-mins-f8c0113937a)
- [Validating Supabase JWT with Python (DEV)](https://dev.to/zwx00/validating-a-supabase-jwt-locally-with-python-and-fastapi-59jf)
- [Migrating from Static JWT to JWKS in Supabase](https://objectgraph.com/blog/migrating-supabase-jwt-jwks/)
- [Supabase JWT Docs](https://supabase.com/docs/guides/auth/jwts)

### SSE Streaming
- [sse-starlette (PyPI)](https://pypi.org/project/sse-starlette/)
- [sse-starlette (GitHub)](https://github.com/sysid/sse-starlette)
- [FastAPI SSE Tutorial (Official)](https://fastapi.tiangolo.com/tutorial/server-sent-events/)
- [sse-starlette Error Handling (DeepWiki)](https://deepwiki.com/sysid/sse-starlette/3.4-error-handling)
- [sse-starlette Disconnect Detection (DeepWiki)](https://deepwiki.com/sysid/sse-starlette/3.5-client-disconnection-detection)
- [Stream LLM Responses with FastAPI + SSE (GoPenAI)](https://blog.gopenai.com/how-to-stream-llm-responses-in-real-time-using-fastapi-and-sse-d2a5a30f2928)

### Caching & Redis
- [FastAPI + Redis Tutorial (Official Redis)](https://redis.io/learn/develop/python/fastapi)
- [fastapi-cache (GitHub)](https://github.com/long2ice/fastapi-cache)
- [FastAPI Caching with Redis Middleware (Medium)](https://sayanc20002.medium.com/fastapi-caching-with-middleware-redis-097a17bcef82)
- [3x Faster Responses with async_lru + Redis (Medium)](https://medium.com/@bhagyarana80/3x-faster-responses-in-fastapi-smart-caching-with-async-lru-and-redis-for-high-concurrency-apis-6b8428772f22)
- [Rate Limiting AI APIs with FastAPI + Redis (DasRoot)](https://dasroot.net/posts/2026/02/rate-limiting-ai-apis-async-middleware-fastapi-redis/)

### Connection Pooling & Database
- [Supabase Connection Scaling for FastAPI (DEV)](https://dev.to/papansarkar101/supabase-connection-scaling-the-essential-guide-for-fastapi-developers-348o)
- [Supabase Pooling + asyncpg Fix (Medium)](https://medium.com/@patrickduch93/supabase-pooling-and-asyncpg-dont-mix-here-s-the-real-fix-44f700b05249)
- [PostgreSQL Connection Limits in FastAPI (Medium)](https://medium.com/@rameshkannanyt0078/handling-postgresql-connection-limits-in-fastapi-efficiently-379ff44bdac5)
- [FastAPI DB Connection Pools (DevGenius)](https://blog.devgenius.io/fast-api-with-db-connection-pools-cdfd6000827)
- [SQLAlchemy Pooling for Serverless FastAPI](https://davidmuraya.com/blog/sqlalchemy-connection-pooling-for-serverless-fastapi/)
- [Async Database Connections in FastAPI](https://oneuptime.com/blog/post/2026-02-02-fastapi-async-database/view)
- [FastAPI + SQLAlchemy Async + Back Pressure](https://www.peterspython.com/en/blog/fastapi-sqlalchemy-asynchronous-io-and-back-pressure)
- [Supabase Database Connecting Docs](https://supabase.com/docs/guides/database/connecting-to-postgres)
- [Supabase Database Troubleshooting](https://supabase.com/docs/guides/database/troubleshooting)
