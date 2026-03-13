# Wave 7B: Security & Deployment Hardening

> No new features — security headers, Docker hardening, Railway healthcheck, per-user rate limiting, error boundaries.
> Can run in parallel with Wave 7A (zero shared files).

---

## Overview

Five security and deployment gaps identified across all 5 best-practice audit reports:

1. **Security headers** missing on both backend and frontend (no CSP, HSTS, X-Frame-Options)
2. **Backend Dockerfile** runs as root + single-stage build
3. **Railway healthcheck** not wired despite `/api/v1/health` existing
4. **Rate limiting** is IP-only, no X-Forwarded-For handling behind Railway proxy
5. **No root-level error boundary** + naive query retry (`retry: 1`)

All tasks are isolated — zero dependencies between them. All can run in parallel.

---

## Sub-Wave 7B.1: Build Phase (All Parallel)

### @fastapi-backend (Tasks 1.1, 2, 3, 4)

#### Task 1.1: Backend Security Headers

**File: `backend/app/main.py` (MODIFY — after line 106, before rate limit middleware)**

Add middleware function (matches existing `request_id_middleware` pattern at line 90):

```python
@application.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response
```

**Why check X-Forwarded-Proto:** Railway terminates TLS at proxy. Backend sees `http://` but actual connection is HTTPS.

#### Task 2: Non-Root Backend Dockerfile + Multi-Stage Build

**File: `backend/Dockerfile` (MODIFY — full rewrite)**

Stage 1 (builder): `python:3.11-slim`, install deps into `/opt/venv`
Stage 2 (runtime): copy venv + app code, create non-root user

```dockerfile
FROM python:3.11-slim AS builder
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /build
COPY backend/requirements.txt .
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/opt/venv/bin:$PATH"
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY shared/ /app/shared/
COPY agents/ /app/agents/
COPY backend/ /app/backend/
RUN addgroup --system --gid 1001 appgroup && \
    adduser --system --uid 1001 --ingroup appgroup appuser
USER appuser
WORKDIR /app/backend
ENV PORT=8000
EXPOSE ${PORT}
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
```

UID 1001 matches frontend convention. `/opt/venv` added to PATH.

#### Task 3: Railway Healthcheck

**File: `railway.json` (MODIFY)**

Add to `deploy` section:
```json
"healthcheckPath": "/api/v1/health",
"healthcheckTimeout": 120
```

120s is generous for Railway cold starts. Endpoint already exists at `main.py:129-131`.

#### Task 4: Per-User Rate Limiting + X-Forwarded-For

**File: `backend/app/middleware/rate_limit.py` (MODIFY — line 68 area)**

**Change 1 — X-Forwarded-For (replace line 68):**
```python
forwarded = request.headers.get("x-forwarded-for", "")
client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
```

**Change 2 — Per-user key for authenticated routes (after IP resolution):**
```python
rate_key_id = client_ip  # default: IP-based

auth_header = request.headers.get("authorization", "")
if auth_header.startswith("Bearer ") and not request.url.path.startswith("/api/v1/auth/"):
    try:
        # Decode WITHOUT verification — just extract 'sub' for rate limiting.
        # Full verification happens in deps.get_current_user().
        token = auth_header[7:]
        payload = pyjwt.decode(token, options={"verify_signature": False})
        user_sub = payload.get("sub")
        if user_sub:
            rate_key_id = f"user:{user_sub}"
    except Exception:
        pass  # Fall back to IP-based

key = f"ratelimit:{rate_key_id}:{request.url.path}:{window}"
```

Add `import jwt as pyjwt` to file-level imports. PyJWT is already a dependency.

**Design decisions:**
- Unverified decode is safe — attacker with forged JWT only isolates into their own bucket
- Auth routes (`/api/v1/auth/*`) stay IP-based — no JWT available
- `X-Forwarded-For` trusted because Railway is the only proxy layer

---

### @nextjs-frontend (Tasks 1.2, 5)

#### Task 1.2: Frontend Security Headers

**File: `frontend/next.config.mjs` (MODIFY)**

Add `headers()` config alongside existing `rewrites()`:

```javascript
async headers() {
  return [
    {
      source: "/(.*)",
      headers: [
        {
          key: "Content-Security-Policy",
          value: "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' *.supabase.co data:; connect-src 'self' *.supabase.co *.railway.app; font-src 'self' fonts.gstatic.com",
        },
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "X-Frame-Options", value: "DENY" },
        { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        { key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" },
      ],
    },
  ];
},
```

**CSP rationale:**
- `style-src 'unsafe-inline'` — required for Tailwind
- `img-src data:` — for inline SVGs/icons
- `connect-src *.railway.app` — backend API calls via SSE
- `font-src fonts.gstatic.com` — IBM Plex Sans Arabic from Google Fonts

If CSP violations occur during validation, adjust `script-src` to include `'unsafe-inline'` as fallback.

#### Task 5: React Error Boundary + Smart Query Retry

**File: `frontend/app/error.tsx` (NEW)**

Root-level error boundary. Follow existing `app/chat/error.tsx` pattern:
- `"use client"` directive
- Arabic text: "حدث خطأ غير متوقع" + "نعتذر عن هذا الخطأ..."
- Two buttons: "إعادة المحاولة" (retry via `reset()`) + "العودة للرئيسية" (navigate to `/`)
- Uses existing shadcn `Button` + `AlertTriangle` icon from lucide-react
- `min-h-screen` centering layout

**File: `frontend/components/providers.tsx` (MODIFY — line 17)**

Replace `retry: 1` with smart retry:
```typescript
retry: (failureCount, error) => {
  if (error instanceof ApiClientError && [401, 403, 404].includes(error.status)) {
    return false;
  }
  return failureCount < 3;
},
```

Add import: `import { ApiClientError } from "@/lib/api";`

**Rationale:**
- 401: `api.ts` already handles refresh-and-retry, TanStack retry would cause race conditions
- 403: Permission errors are deterministic
- 404: Resource doesn't exist
- 5xx/network: Transient, benefit from retry

---

## Parallelism & Sequencing

```
┌──────────────────────────────────────────────────────────────┐
│ ALL PARALLEL (zero dependencies between tasks)               │
│                                                              │
│  @fastapi-backend:                                           │
│    Task 1.1 — Security headers in main.py                    │
│    Task 2   — Backend Dockerfile rewrite                     │
│    Task 3   — Railway healthcheck in railway.json            │
│    Task 4   — Per-user rate limiting in rate_limit.py        │
│                                                              │
│  @nextjs-frontend:                                           │
│    Task 1.2 — Security headers in next.config.mjs            │
│    Task 5   — Error boundary + smart retry                   │
│                                                              │
└──────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│ VALIDATION GATE                                              │
│  @security-reviewer + @integration-lead + @validate          │
│  + @deploy-checker                                           │
└──────────────────────────────────────────────────────────────┘
```

---

## File Manifest

### New Files: 1

| # | Path | Agent |
|---|------|-------|
| 1 | `frontend/app/error.tsx` | @nextjs-frontend |

### Modified Files: 6

| # | Path | Agent | Changes |
|---|------|-------|---------|
| 1 | `backend/app/main.py` | @fastapi-backend | +10 lines (security headers middleware) |
| 2 | `backend/Dockerfile` | @fastapi-backend | Full rewrite (two-stage + non-root) |
| 3 | `railway.json` | @fastapi-backend | +2 lines (healthcheckPath + timeout) |
| 4 | `backend/app/middleware/rate_limit.py` | @fastapi-backend | +15 lines (X-Forwarded-For + per-user key) |
| 5 | `frontend/next.config.mjs` | @nextjs-frontend | +25 lines (headers config) |
| 6 | `frontend/components/providers.tsx` | @nextjs-frontend | +6 lines (smart retry + import) |

---

## Validation Gate 7B

### @security-reviewer (read-only audit)

| Check | What to Verify |
|-------|----------------|
| HSTS conditional | Only sent on HTTPS/proxied requests (check `x-forwarded-proto` logic) |
| CSP no unsafe-eval | `script-src` does NOT include `'unsafe-eval'` |
| JWT in rate limiter | Uses `verify_signature: False` — no secret leakage |
| Docker non-root | `USER appuser` directive present, no write access to `/opt/venv` |
| X-Forwarded-For | Parsing takes first value only (not last — prevents spoofing) |

### @integration-lead

- No API contract changes (security headers don't change response bodies)
- Rate limit headers (`X-RateLimit-Remaining`, `X-RateLimit-Reset`) still exposed
- Error boundary doesn't affect normal rendering

### @validate (test execution)

| # | Test | MCP Tools | Pass Criteria |
|---|------|-----------|---------------|
| 1 | Backend security headers | Bash: `curl -I /api/v1/health` | X-Content-Type-Options, X-Frame-Options, Referrer-Policy present |
| 2 | HSTS on production | Bash: `curl -I https://luna-backend-production-...` | Strict-Transport-Security present |
| 3 | Frontend security headers | Bash: `curl -I https://luna-frontend-production-...` | CSP + all headers present |
| 4 | CSP not blocking app | `mcp__playwright__browser_navigate` + `mcp__playwright__browser_console_messages` | Zero CSP violation errors |
| 5 | Docker non-root | Bash: `docker run --rm luna-backend-test whoami` | Output: `appuser` |
| 6 | Railway healthcheck | `mcp__railway-mcp-server__list-deployments` | Latest deploy shows healthy |
| 7 | Rate limit per-user | Send authenticated requests, inspect Redis | Key contains `user:{sub}` |
| 8 | Rate limit IP for auth | Send to `/api/v1/auth/login`, inspect Redis | Key contains IP |
| 9 | Root error boundary | Inspect `frontend/app/error.tsx` | Has `"use client"`, Arabic text, retry + home |
| 10 | Smart retry | Inspect `providers.tsx` | Retry function skips 401/403/404 |
| 11 | TypeScript | Bash: `cd frontend && npx tsc --noEmit` | Zero errors |
| 12 | No regressions | @validate full suite | All previously-passing tests still pass |

### @deploy-checker

| Check | MCP Tool |
|-------|----------|
| Backend healthy | `mcp__railway-mcp-server__check-railway-status` |
| Healthcheck probe in logs | `mcp__railway-mcp-server__get-logs` |
| No startup permission errors | `mcp__railway-mcp-server__get-logs` |
| Deploy succeeded | `mcp__railway-mcp-server__list-deployments` |

---

## Success Criteria (Wave 7B)

- [ ] All backend responses include X-Content-Type-Options, X-Frame-Options, Referrer-Policy
- [ ] HSTS sent on HTTPS/proxied requests only
- [ ] Frontend responses include CSP + security headers
- [ ] CSP does not break app (no console violations)
- [ ] Backend Dockerfile is multi-stage (builder + runtime)
- [ ] Backend container runs as `appuser` (UID 1001), not root
- [ ] `railway.json` has `healthcheckPath: "/api/v1/health"`
- [ ] Railway deployment passes healthcheck
- [ ] Rate limiter uses X-Forwarded-For for real client IP
- [ ] Authenticated requests use per-user rate limit key (`user:{sub}`)
- [ ] Unauthenticated auth routes use IP-based rate limit key
- [ ] Root-level error boundary exists with Arabic text + retry + home buttons
- [ ] TanStack Query retry skips 401/403/404, retries up to 3 for transient errors
- [ ] TypeScript compiles with zero errors
- [ ] All existing tests pass (no regressions)
