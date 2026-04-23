# Deployment Patterns: Railway + Docker for Full-Stack Apps

> **Research date:** 2026-03-13
> **Scope:** Railway deployment patterns for Next.js + FastAPI + Redis + Supabase full-stack applications
> **Searches conducted:** 14 distinct web searches across deployment guides, Docker best practices, CI/CD patterns, error troubleshooting, and platform comparisons

---

## Summary Table of Resources Found

| # | Resource | Type | URL | Key Takeaway |
|---|----------|------|-----|--------------|
| 1 | Railway Official Docs — FastAPI Guide | Official Guide | https://docs.railway.com/guides/fastapi | Zero-config FastAPI deploy via Nixpacks or Dockerfile; Hypercorn/Uvicorn start commands |
| 2 | Railway Official Docs — Monorepo Deployment | Official Guide | https://docs.railway.com/guides/monorepo | Root Directory + Watch Paths + RAILWAY_DOCKERFILE_PATH for multi-service monorepos |
| 3 | Railway Official Docs — Healthchecks | Official Guide | https://docs.railway.com/guides/healthchecks | 300s default timeout; /health endpoint required; only checked at deploy start |
| 4 | Railway Official Docs — Config as Code | Official Guide | https://docs.railway.com/reference/config-as-code | railway.json/railway.toml overrides dashboard settings; absolute paths required |
| 5 | Railway Official Docs — Variables & Private Networking | Official Guide | https://docs.railway.com/variables + https://docs.railway.com/networking/private-networking | Shared variables, reference variables, service.railway.internal DNS pattern |
| 6 | Railway Blog — GitHub Actions with Railway | Official Blog | https://blog.railway.com/p/github-actions | `railway up --ci` in GitHub Actions; path-based conditional deploys for monorepos |
| 7 | Railway Blog — NX Monorepo + GitHub Actions | Official Blog | https://blog.railway.com/p/nx-railway-with-gh-actions | Per-service GitHub Actions workflows scoped to sub-directories |
| 8 | Railway Blog — Server Rendering Benchmarks | Official Blog | https://blog.railway.com/p/server-rendering-benchmarks-railway-vs-cloudflare-vs-vercel | Railway vs Cloudflare vs Vercel SSR performance comparison |
| 9 | Medium — Ultimate Guide: Next.js + FastAPI + PostgreSQL | Community Guide | https://medium.com/@zafarobad/ultimate-guide-to-deploying-next-js-d57ab72f6ba6 | Split deploy: Vercel for frontend, Railway for backend + DB |
| 10 | Medium — Mastering CORS: FastAPI + Next.js | Community Guide | https://medium.com/@vaibhavtiwari.945/mastering-cors-configuring-cross-origin-resource-sharing-in-fastapi-and-next-js-28c61272084b | Explicit origin lists (not wildcards) when using credentials |
| 11 | Replacing Nixpack with Docker on Railway | Blog Post | https://apvarun.com/blog/custom-docker-for-next-app-on-railway | Image size: 1.3GB (Nixpacks) down to 77MB (custom Docker); 5x faster publish |
| 12 | BetterStack — FastAPI Docker Best Practices | Guide | https://betterstack.com/community/guides/scaling-python/fastapi-docker-best-practices/ | Non-root user, health checks, multi-stage builds, slim base images |
| 13 | DEV Community — Next.js Docker Deployment 2025 | Guide | https://dev.to/codeparrot/nextjs-deployment-with-docker-complete-guide-for-2025-3oe8 | Standalone output + multi-stage = 90%+ image size reduction |
| 14 | Railway Docs — Fixing Common Errors | Official Docs | https://docs.railway.app/guides/fixing-common-errors | PORT binding, 0.0.0.0 host, healthcheck timeouts |
| 15 | Railway Docs — Application Failed to Respond | Official Docs | https://docs.railway.com/reference/errors/application-failed-to-respond | 502 Bad Gateway root causes and fixes |
| 16 | Railway vs Render (2026) — Northflank | Comparison | https://northflank.com/blog/railway-vs-render | Railway: flexible multi-service; Render: predictable pricing |
| 17 | Vercel vs Railway vs Render: AI Apps (2026) | Comparison | https://getathenic.com/blog/vercel-vs-railway-vs-render-ai-deployment | Platform selection matrix for different workload types |
| 18 | CodingForEntrepreneurs — FastAPI to Railway Dockerfile | Tutorial | https://www.codingforentrepreneurs.com/blog/deploy-fastapi-to-railway-with-this-dockerfile | Practical Dockerfile with Railway-specific PORT handling |
| 19 | Railway Docs — SSL Troubleshooting | Official Docs | https://docs.railway.com/networking/troubleshooting/ssl | LetsEncrypt auto-provisioning, Cloudflare Full (not Strict) mode |
| 20 | Railway Docs — Logs & Observability | Official Docs | https://docs.railway.com/observability/logs | stdout/stderr capture, 30-day metric retention, monitor alerts |

---

## 1. Deployment Architecture Patterns

### 1.1 Monorepo with Separate Railway Services (Recommended for Luna)

This is the pattern most relevant to the Luna project, which has `frontend/`, `backend/`, `shared/`, and `agents/` directories in a single repository.

**Architecture:**
```
GitHub Repository (monorepo)
  |
  +-- frontend/          --> Railway Service: "luna-frontend"
  |     Dockerfile           Root Directory: / (build context = repo root, Dockerfile in frontend/)
  |
  +-- backend/           --> Railway Service: "luna-backend"
  |     Dockerfile           Root Directory: / (build context = repo root, Dockerfile in backend/)
  |
  +-- shared/            --> Copied into backend Docker image
  +-- agents/            --> Copied into backend Docker image
  |
  +-- Redis              --> Railway Plugin (one-click provision)
```

**Key configuration per service:**
- Each Railway service points to the **same GitHub repository**
- **Root Directory** is set per service (but for Luna, both Dockerfiles use repo root as build context since backend needs `shared/` and `agents/`)
- **RAILWAY_DOCKERFILE_PATH** variable specifies which Dockerfile to use: `backend/Dockerfile` or `frontend/Dockerfile`
- **Watch Paths** prevent unnecessary rebuilds: `backend/**` for backend service, `frontend/**` for frontend service

**Reference:** https://docs.railway.com/guides/monorepo

### 1.2 Split Platform Deployment

A popular alternative pattern deploys the frontend on Vercel and the backend on Railway:

```
Vercel (edge CDN, automatic preview deploys)
  +-- Next.js frontend

Railway (container hosting, persistent services)
  +-- FastAPI backend
  +-- Redis (plugin)
  +-- PostgreSQL (or external Supabase)
```

**When to use this pattern:**
- When you want Vercel's edge CDN and automatic preview deploys for the frontend
- When the backend needs persistent connections (Redis, WebSocket, SSE)
- When you want to minimize Railway costs (backend-only)

**Trade-off:** More complex CORS configuration, two deployment platforms to manage, environment variable duplication.

**Reference:** https://medium.com/@zafarobad/ultimate-guide-to-deploying-next-js-d57ab72f6ba6

### 1.3 Railway Private Networking

Services within the same Railway project communicate over encrypted Wireguard tunnels using internal DNS:

```
Frontend  ---(public internet)---> https://luna-frontend-xxx.up.railway.app
Frontend  ---(API calls)---------> https://luna-backend-xxx.up.railway.app (public)

Backend   ---(private network)---> redis.railway.internal:6379
Backend   ---(public internet)---> https://dwgghvxogtwyaxmbgjod.supabase.co (external Supabase)
```

**Internal DNS pattern:** `<service-name>.railway.internal`

**Important:** Use `RAILWAY_PRIVATE_DOMAIN` for inter-service communication. This avoids egress charges and reduces latency.

**Reference:** https://docs.railway.com/networking/private-networking

---

## 2. Docker Best Practices

### 2.1 Next.js Production Dockerfile (Multi-Stage)

The Luna project already follows best practices. Key principles:

**Stage 1 — Dependencies:**
- Use `node:20-alpine` as base (lightweight)
- Copy only `package.json` and `package-lock.json` first (layer caching)
- Use `npm ci` (not `npm install`) for reproducible builds

**Stage 2 — Build:**
- Pass NEXT_PUBLIC_* variables as build args (they're baked into the client bundle at build time)
- Enable `output: "standalone"` in `next.config.js` (reduces image from ~2GB to <200MB)
- Disable telemetry with `NEXT_TELEMETRY_DISABLED=1`

**Stage 3 — Runner:**
- Copy only `.next/standalone`, `.next/static`, and `public/` (minimal footprint)
- Create non-root user (`nextjs:nodejs`) for security
- Set `HOSTNAME=0.0.0.0` to bind to all interfaces
- Final image size target: 70-200MB

**Image size impact:**
| Approach | Image Size |
|----------|-----------|
| Nixpacks (default) | ~1.3 GB |
| Multi-stage without standalone | ~500 MB |
| Multi-stage with standalone | ~70-200 MB |

**Reference:** https://apvarun.com/blog/custom-docker-for-next-app-on-railway, https://dev.to/codeparrot/nextjs-deployment-with-docker-complete-guide-for-2025-3oe8

### 2.2 FastAPI Production Dockerfile

The Luna backend uses a single-stage `python:3.11-slim` build. Key principles:

**Current approach (Luna):**
```dockerfile
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt
COPY shared/ /app/shared/
COPY agents/ /app/agents/
COPY backend/ /app/backend/
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
```

**Enhancement opportunities:**
1. **Multi-stage build** — Use a build stage to install dependencies, then copy only the virtualenv to a slim runtime stage
2. **Non-root user** — Add `useradd` and run as unprivileged user
3. **Health check** — Add `HEALTHCHECK` instruction in Dockerfile
4. **Gunicorn wrapper** — Use `gunicorn -k uvicorn.workers.UvicornWorker` for multi-process handling in production
5. **Worker count** — Formula: `(2 * CPU_cores) + 1`; for Railway's typical 1-2 vCPU, use 2-4 workers

**Example enhanced Dockerfile pattern:**
```dockerfile
# Build stage
FROM python:3.11-slim AS builder
WORKDIR /app
COPY backend/requirements.txt .
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# Runtime stage
FROM python:3.11-slim AS runtime
RUN groupadd -r appuser && useradd -r -g appuser appuser
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY shared/ /app/shared/
COPY agents/ /app/agents/
COPY backend/ /app/backend/
WORKDIR /app/backend
USER appuser
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "${PORT}"]
```

**Reference:** https://betterstack.com/community/guides/scaling-python/fastapi-docker-best-practices/

### 2.3 .dockerignore Best Practices

Both frontend and backend should have `.dockerignore` files to speed up builds:

**Frontend .dockerignore:**
```
node_modules
.next
.git
*.md
tests/
coverage/
.env.local
```

**Backend .dockerignore:**
```
__pycache__
.pytest_cache
.git
*.md
tests/
.env
venv/
```

---

## 3. Railway Configuration

### 3.1 Config as Code (railway.json / railway.toml)

Railway reads configuration from `railway.json` or `railway.toml` at the project root. **Config in code always overrides dashboard settings.**

**Luna's current `railway.json`:**
```json
{
  "$schema": "https://railway.app/railway.schema.json",
  "build": {
    "builder": "DOCKERFILE"
  },
  "deploy": {
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 10
  }
}
```

**Enhanced configuration with healthcheck:**
```json
{
  "$schema": "https://railway.app/railway.schema.json",
  "build": {
    "builder": "DOCKERFILE",
    "dockerfilePath": "backend/Dockerfile"
  },
  "deploy": {
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 10,
    "healthcheckPath": "/health",
    "healthcheckTimeout": 120
  }
}
```

**Important notes:**
- The config file does **not** follow the Root Directory path — specify absolute paths (e.g., `/backend/Dockerfile`)
- Each service should have its own config, or use per-service dashboard settings
- For monorepos, you can place separate `railway.json` files in each service directory

**Reference:** https://docs.railway.com/reference/config-as-code

### 3.2 Environment Variables

**Variable types on Railway:**

| Type | Scope | Use Case |
|------|-------|----------|
| Service Variables | Single service | API keys, DB URLs specific to one service |
| Shared Variables | Project-wide | SUPABASE_URL, JWT secrets shared across services |
| Reference Variables | Cross-service | `${{backend.DATABASE_URL}}` — reference another service's var |
| Railway-Provided | Automatic | `PORT`, `RAILWAY_ENVIRONMENT`, `RAILWAY_PUBLIC_DOMAIN` |

**Best practices:**
- Use **Shared Variables** for values needed by both frontend and backend (e.g., `SUPABASE_URL`, `SUPABASE_ANON_KEY`)
- Use **Reference Variables** for inter-service URLs (e.g., frontend referencing backend's domain)
- Use **Private Networking** variables for service-to-service communication (e.g., Redis URL)
- Never hardcode secrets — use Railway's variable management
- Build-time variables for Next.js (`NEXT_PUBLIC_*`) must be set as service variables, not shared variables

**Reference:** https://docs.railway.com/variables

### 3.3 Health Checks

**Configuration:**
- Set a healthcheck path (e.g., `/health`) in service settings or `railway.json`
- Default timeout: 300 seconds (5 minutes)
- Override with `RAILWAY_HEALTHCHECK_TIMEOUT_SEC` service variable
- Health check is only called **at deployment start** (not continuous monitoring)
- Railway marks deployment as "Active" only after healthcheck succeeds

**FastAPI health endpoint example:**
```python
@app.get("/health")
async def health_check():
    return {"status": "ok"}
```

**Reference:** https://docs.railway.com/guides/healthchecks

---

## 4. CI/CD Pipeline Patterns

### 4.1 Railway Auto-Deploy (Default)

Railway's simplest CI/CD: push to GitHub, Railway auto-deploys.

```
GitHub push --> Railway detects change --> Build --> Deploy
```

**Configuration:**
- Connect GitHub repo in Railway dashboard
- Set branch trigger (e.g., `main`)
- Optionally set Watch Paths to deploy only when relevant files change

**Limitations:**
- No pre-deploy testing
- No approval gates
- No multi-environment promotion

### 4.2 GitHub Actions + Railway CLI (Recommended)

For more control, disable auto-deploy and use GitHub Actions:

```yaml
name: Deploy Backend
on:
  push:
    branches: [main]
    paths:
      - 'backend/**'
      - 'shared/**'
      - 'agents/**'

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run tests
        run: |
          pip install -r backend/requirements.txt
          pytest backend/tests/

  deploy:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install Railway CLI
        run: npm i -g @railway/cli
      - name: Deploy
        env:
          RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}
        run: railway up --service backend --environment production
```

**Key points:**
- Use `railway up --ci` for non-interactive deployment
- Set `RAILWAY_TOKEN` as a GitHub secret
- Use `paths:` filter to deploy only affected services
- Run tests before deploy (the `needs: test` dependency)

**Reference:** https://blog.railway.com/p/github-actions

### 4.3 Monorepo CI/CD with Path Filtering

For monorepos, create separate workflows per service:

```
.github/workflows/
  deploy-frontend.yml    # triggers on frontend/** changes
  deploy-backend.yml     # triggers on backend/**, shared/**, agents/** changes
```

Each workflow uses path-based triggers and deploys only the affected service using `RAILWAY_SERVICE_ID` to target the correct Railway service.

**Reference:** https://blog.railway.com/p/nx-railway-with-gh-actions

---

## 5. Monitoring, Logging, and Observability

### 5.1 Railway Built-in Monitoring

**Metrics (available in dashboard):**
- CPU usage
- Memory usage
- Disk usage
- Network traffic (ingress/egress)
- Up to 30 days of historical data

**Logging:**
- All stdout/stderr is automatically captured
- Searchable in the Railway dashboard
- No additional logging library required (but structured JSON logging is recommended)

**Alerting:**
- **Monitors:** Email alerts when CPU/memory/disk exceed thresholds
- **Webhooks:** Deployment status notifications (success/failure)

### 5.2 Recommended Logging Setup

**FastAPI (Python):**
```python
import logging
import json

logging.basicConfig(
    format='{"time":"%(asctime)s","level":"%(levelname)s","message":"%(message)s"}',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
```

**Next.js:**
Use `console.log` / `console.error` — Railway captures both.

### 5.3 External Monitoring Integration

For production-grade monitoring beyond Railway's built-in tools:
- **Uptime monitoring:** UptimeRobot, Better Uptime, or Checkly (hit `/health` endpoint)
- **Error tracking:** Sentry (both Python and Next.js SDKs available)
- **APM:** Datadog or New Relic for detailed performance traces

**Reference:** https://docs.railway.com/observability/logs, https://blog.railway.com/p/using-logs-metrics-traces-and-alerts-to-understand-system-failures

---

## 6. SSL / Custom Domain Configuration

### 6.1 Railway Domain Setup

**Railway-provided domains:**
- Format: `<service-name>-<hash>.up.railway.app`
- Automatic HTTPS with Railway's wildcard certificate
- No configuration needed

**Custom domains:**
1. Add domain in Railway service settings
2. Railway provides DNS records to configure:
   - CNAME record for the domain
   - `_acme-challenge` CNAME for SSL certificate issuance
3. Railway auto-provisions LetsEncrypt SSL certificate (RSA 2048-bit)
4. Certificates auto-renew at 30 days before expiry

### 6.2 Cloudflare Integration

If using Cloudflare as DNS/CDN in front of Railway:
- Set SSL/TLS mode to **Full** (NOT Full Strict)
- Full Strict will fail because Railway uses LetsEncrypt, not a Cloudflare Origin Certificate
- If Cloudflare proxy is enabled (orange cloud), ensure timeout settings accommodate your app's response time

### 6.3 SSL Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Browser shows `*.up.railway.app` cert | Custom domain cert not issued yet | Wait up to 1 hour; verify DNS records |
| Certificate not provisioning | Missing `_acme-challenge` CNAME | Add the ACME challenge DNS record |
| Mixed content warnings | Frontend making HTTP requests to backend | Ensure all API URLs use `https://` |

**Reference:** https://docs.railway.com/networking/troubleshooting/ssl

---

## 7. CORS Configuration for Separate Services

### 7.1 FastAPI CORS Middleware

When frontend and backend are on different Railway domains, CORS must be explicitly configured:

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://luna-frontend-production-1124.up.railway.app",
        "http://localhost:3000",  # local dev
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Critical rules:**
- **Never use `allow_origins=["*"]` with `allow_credentials=True`** — browsers reject this combination
- List explicit origins when using cookies or Authorization headers
- Include both production Railway URL and localhost for development
- Use environment variables for origin URLs to avoid hardcoding

### 7.2 SSE-Specific CORS Considerations

For Server-Sent Events (SSE) connections:
- SSE uses regular HTTP GET requests, so standard CORS applies
- Ensure `text/event-stream` content type is not blocked by any middleware
- Railway's proxy does not buffer SSE responses by default (good for streaming)
- Set appropriate timeout values — Railway's default proxy timeout may terminate long-lived SSE connections

### 7.3 Next.js Proxy Alternative

Instead of configuring CORS, you can proxy backend requests through Next.js:

```javascript
// next.config.js
module.exports = {
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: 'https://backend-service.railway.internal:8000/:path*',
      },
    ];
  },
};
```

**Trade-off:** Simplifies CORS but adds latency (extra hop through Next.js server).

**Reference:** https://medium.com/@vaibhavtiwari.945/mastering-cors-configuring-cross-origin-resource-sharing-in-fastapi-and-next-js-28c61272084b

---

## 8. Common Deployment Errors and Fixes

### 8.1 Build Failures

| Error | Cause | Fix |
|-------|-------|-----|
| `npm ci` exit code 1 | Missing or mismatched `package-lock.json` | Run `npm install` locally and commit the updated lockfile |
| `apt-get` timeout during Nixpacks build | Network timeout fetching system packages | Retry the build; switch to custom Dockerfile if persistent |
| Nixpacks fails but Dockerfile works | Nixpacks auto-detection picks wrong config | Set `builder: DOCKERFILE` in railway.json and provide a Dockerfile |
| `ModuleNotFoundError` in Python | Missing dependency or wrong PYTHONPATH | Verify requirements.txt is complete; set `ENV PYTHONPATH=/app` in Dockerfile |
| Next.js build fails with missing env vars | `NEXT_PUBLIC_*` vars not available at build time | Pass them as `ARG` in Dockerfile and set as build-time variables in Railway |
| Docker build context too large (slow upload) | Missing `.dockerignore` | Add `.dockerignore` excluding `node_modules`, `.git`, `__pycache__` |

### 8.2 Runtime Errors (502 Bad Gateway)

This is the **most common** Railway deployment error. Root causes:

| Cause | Fix |
|-------|-----|
| App binds to `localhost` or `127.0.0.1` | Bind to `0.0.0.0` — Railway can't reach localhost inside the container |
| App doesn't read `PORT` env var | Use `process.env.PORT` (Node) or `os.environ["PORT"]` (Python) |
| PORT variable set but service not redeployed | Trigger a new deployment after changing env vars |
| Health check path returns non-200 | Ensure `/health` returns 200 status code |
| App takes too long to start | Increase healthcheck timeout via `RAILWAY_HEALTHCHECK_TIMEOUT_SEC` |
| Target port mismatch | Verify the public domain's target port matches the app's listening port |

### 8.3 Environment Variable Issues

| Error | Cause | Fix |
|-------|-------|-----|
| `NEXT_PUBLIC_*` vars undefined in browser | Set at runtime, not build time | Must be set as build args in Dockerfile and as Railway build variables |
| Redis connection refused | Using public URL from within Railway | Switch to `redis.railway.internal:6379` (private networking) |
| Supabase connection timeout | Wrong region or network config | Verify Supabase region; use connection pooler for high-concurrency |
| JWT verification fails | Wrong secret or algorithm | Ensure `SUPABASE_JWT_SECRET` matches; handle ES256 vs HS256 |

### 8.4 SSE / Streaming Errors

| Error | Cause | Fix |
|-------|-------|-----|
| SSE connection drops after 30-60s | Proxy timeout | Send periodic heartbeat events (every 15-30s) |
| SSE events arrive all at once | Response buffering | Ensure no middleware is buffering; `X-Accel-Buffering: no` header |
| Client reconnects in infinite loop | Server returns error on SSE endpoint | Return proper HTTP error codes; implement backoff in client |

### 8.5 Performance Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| Slow response times | Services in different regions | Deploy all services in the same Railway region |
| High egress costs | Services communicating over public URLs | Use private networking (`*.railway.internal`) |
| Cold start latency | Service scaled to zero | Railway Hobby tier doesn't scale to zero; Pro tier can configure min instances |
| Memory spikes during build | Large dependency tree | Use multi-stage builds; install only production dependencies |

### 8.6 Docker-Specific Issues

| Error | Cause | Fix |
|-------|-------|-----|
| `COPY failed: file not found` | Build context doesn't include the file | Verify the Docker build context (usually repo root for monorepos) |
| Image too large (>1GB) | No multi-stage build or standalone mode | Enable `output: "standalone"` in Next.js; use multi-stage for Python |
| Permission denied at runtime | Files owned by root, running as non-root | Add `chown` before switching to non-root user |
| `exec format error` | Image built for wrong architecture | Ensure Railway build matches target arch (amd64) |

---

## 9. Platform Comparison: Railway vs Vercel vs Render

### 9.1 Feature Comparison

| Feature | Railway | Vercel | Render |
|---------|---------|--------|--------|
| **Best for** | Full-stack, multi-service apps | Next.js/frontend, serverless | Simple backend deployments |
| **Docker support** | Full Dockerfile support | No (serverless only) | Full Dockerfile support |
| **Databases** | One-click Postgres, Redis, MySQL, MongoDB | None (use external) | Managed Postgres, Redis |
| **Private networking** | Yes (Wireguard tunnels) | No | Yes (private services) |
| **SSE/WebSocket** | Full support (persistent connections) | Limited (serverless timeout) | Full support |
| **Multi-service per project** | Yes (core feature) | No (one service per project) | Yes (blueprints) |
| **Preview environments** | Manual setup | Automatic per PR | Manual setup |
| **Edge CDN** | No built-in CDN | Global edge network | Cloudflare CDN |
| **Config as code** | railway.json/railway.toml | vercel.json | render.yaml |
| **Monorepo support** | Root Directory + Watch Paths | Built-in monorepo support | Root Directory |

### 9.2 Pricing Comparison (2026)

| Plan | Railway | Vercel | Render |
|------|---------|--------|--------|
| Free/Hobby | $5/month credit | Free (limited) | Free (limited, sleeps after 15min) |
| Pro | $20/month + usage | $20/user/month + usage | $7/service/month (predictable) |
| Pricing model | Usage-based (CPU/memory/egress) | Per-seat + usage-based | Per-instance (fixed) |

### 9.3 Recommendation for Luna-type Projects

**Railway is the best fit when:**
- You need FastAPI + Next.js + Redis as separate services in one project
- You use SSE (server-sent events) for streaming (Vercel's serverless has timeout limits)
- You want private networking between services
- You prefer Docker-based deployments with full control

**Consider Vercel for frontend when:**
- You want automatic preview deploys per PR
- You want global edge CDN for static assets
- You're willing to manage CORS between Vercel frontend and Railway backend

**Reference:** https://getathenic.com/blog/vercel-vs-railway-vs-render-ai-deployment, https://northflank.com/blog/railway-vs-render

---

## 10. Recommendations for Luna Legal AI

Based on this research, here are specific recommendations for the Luna project:

### 10.1 Current Setup Assessment

The Luna project's current Dockerfiles are well-structured:
- **Frontend Dockerfile:** Already uses multi-stage build with standalone output, non-root user, Alpine base -- follows best practices
- **Backend Dockerfile:** Single-stage slim build -- functional but could be enhanced
- **railway.json:** Minimal -- could benefit from healthcheck configuration

### 10.2 Recommended Improvements

1. **Add healthcheck to railway.json** per service, pointing to `/health` for backend and `/` for frontend
2. **Add non-root user to backend Dockerfile** for security parity with frontend
3. **Add `.dockerignore` files** to both frontend and backend directories to speed up builds
4. **Use shared variables** in Railway for `SUPABASE_URL` and `SUPABASE_ANON_KEY` (shared between services)
5. **Use private networking** for Redis: `redis://default:password@redis.railway.internal:6379` instead of public proxy
6. **Configure Watch Paths** per service: `frontend/**` for frontend, `backend/**,shared/**,agents/**` for backend
7. **Set up GitHub Actions CI/CD** with test-then-deploy pipeline and path-based triggers
8. **Add SSE heartbeat** (every 15-30 seconds) to prevent Railway proxy timeout on streaming connections
9. **Configure monitors** in Railway for CPU and memory threshold alerts

### 10.3 Environment Variable Strategy

```
Shared Variables (project-wide):
  SUPABASE_URL=https://dwgghvxogtwyaxmbgjod.supabase.co
  SUPABASE_ANON_KEY=<key>

Backend Service Variables:
  SUPABASE_SERVICE_KEY=<key>
  SUPABASE_JWT_SECRET=<secret>
  REDIS_URL=redis://default:<password>@redis.railway.internal:6379
  CORS_ORIGINS=https://luna-frontend-production-1124.up.railway.app

Frontend Service Variables (build-time):
  NEXT_PUBLIC_API_URL=https://luna-backend-production-35ba.up.railway.app
  NEXT_PUBLIC_SUPABASE_URL=${{shared.SUPABASE_URL}}
  NEXT_PUBLIC_SUPABASE_ANON_KEY=${{shared.SUPABASE_ANON_KEY}}
```

---

## Sources

- [Railway Docs — Deploy FastAPI](https://docs.railway.com/guides/fastapi)
- [Railway Docs — Deploying a Monorepo](https://docs.railway.com/guides/monorepo)
- [Railway Docs — Healthchecks](https://docs.railway.com/guides/healthchecks)
- [Railway Docs — Config as Code](https://docs.railway.com/reference/config-as-code)
- [Railway Docs — Using Variables](https://docs.railway.com/variables)
- [Railway Docs — Private Networking](https://docs.railway.com/networking/private-networking)
- [Railway Docs — Domains](https://docs.railway.com/networking/domains)
- [Railway Docs — SSL Troubleshooting](https://docs.railway.com/networking/troubleshooting/ssl)
- [Railway Docs — Logs & Observability](https://docs.railway.com/observability/logs)
- [Railway Docs — Errors Reference](https://docs.railway.com/reference/errors)
- [Railway Docs — Application Failed to Respond](https://docs.railway.com/reference/errors/application-failed-to-respond)
- [Railway Docs — Fixing Common Errors](https://docs.railway.app/guides/fixing-common-errors)
- [Railway Docs — Slow Deployments Troubleshooting](https://docs.railway.com/deployments/troubleshooting/slow-deployments)
- [Railway Docs — Redis Guide](https://docs.railway.com/guides/redis)
- [Railway Blog — GitHub Actions with Railway](https://blog.railway.com/p/github-actions)
- [Railway Blog — NX Monorepo + GitHub Actions](https://blog.railway.com/p/nx-railway-with-gh-actions)
- [Railway Blog — Deploying Monorepos](https://blog.railway.com/p/deploying-monorepos)
- [Railway Blog — Comparing Deployment Methods](https://blog.railway.com/p/comparing-deployment-methods-in-railway)
- [Railway Blog — Server Rendering Benchmarks](https://blog.railway.com/p/server-rendering-benchmarks-railway-vs-cloudflare-vs-vercel)
- [Railway Blog — Monitoring & Observability](https://blog.railway.com/p/using-logs-metrics-traces-and-alerts-to-understand-system-failures)
- [Medium — Ultimate Guide: Next.js + FastAPI + PostgreSQL Deployment](https://medium.com/@zafarobad/ultimate-guide-to-deploying-next-js-d57ab72f6ba6)
- [Medium — Mastering CORS: FastAPI + Next.js](https://medium.com/@vaibhavtiwari.945/mastering-cors-configuring-cross-origin-resource-sharing-in-fastapi-and-next-js-28c61272084b)
- [Medium — Mastering Gunicorn + Uvicorn for FastAPI](https://medium.com/@iklobato/mastering-gunicorn-and-uvicorn-the-right-way-to-deploy-fastapi-applications-aaa06849841e)
- [BetterStack — FastAPI Docker Best Practices](https://betterstack.com/community/guides/scaling-python/fastapi-docker-best-practices/)
- [DEV Community — Next.js Docker Deployment 2025](https://dev.to/codeparrot/nextjs-deployment-with-docker-complete-guide-for-2025-3oe8)
- [Replacing Nixpack with Docker on Railway](https://apvarun.com/blog/custom-docker-for-next-app-on-railway)
- [CodingForEntrepreneurs — FastAPI to Railway Dockerfile](https://www.codingforentrepreneurs.com/blog/deploy-fastapi-to-railway-with-this-dockerfile)
- [Railway vs Render (2026) — Northflank](https://northflank.com/blog/railway-vs-render)
- [Vercel vs Railway vs Render: AI Apps (2026)](https://getathenic.com/blog/vercel-vs-railway-vs-render-ai-deployment)
- [Deploying Full Stack Apps in 2026 — NuCamp](https://www.nucamp.co/blog/deploying-full-stack-apps-in-2026-vercel-netlify-railway-and-cloud-options)
- [Redis on Railway — Redis Docs](https://redis.io/docs/latest/integrate/railway-redis/)
- [FastAPI SSE — Official Docs](https://fastapi.tiangolo.com/tutorial/server-sent-events/)
- [sse-starlette — PyPI](https://pypi.org/project/sse-starlette/)
- [Next.js Deployment — Official Docs](https://nextjs.org/docs/app/getting-started/deploying)
- [Docker Multi-Stage Builds for Python — Collabnix](https://collabnix.com/docker-multi-stage-builds-for-python-developers-a-complete-guide/)
- [FastAPI Production Deployment Guide — CYS Docs](https://craftyourstartup.com/cys-docs/fastapi-production-deployment/)
