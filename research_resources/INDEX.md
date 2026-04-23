# Luna Legal AI — Research Resources

Curated reference library of 40+ real-world projects, patterns, and best practices
for the Luna stack: **Next.js + FastAPI + Supabase + Redis + Railway**.

Generated: 2026-03-13

---

## Reports

| # | Report | Focus | Projects Found |
|---|--------|-------|----------------|
| 1 | [01_nextjs_supabase_deployments.md](01_nextjs_supabase_deployments.md) | Next.js + Supabase boilerplates, RLS, @supabase/ssr, auth flows | 8 projects, 25+ URLs |
| 2 | [02_fastapi_postgres_patterns.md](02_fastapi_postgres_patterns.md) | FastAPI + PostgreSQL, supabase-py v2, SSE, JWT/JWKS, connection pooling | 8 projects, 70+ URLs |
| 3 | [03_ai_chat_saas_applications.md](03_ai_chat_saas_applications.md) | AI chat apps — Open WebUI, LibreChat, LobeChat, Chatbot UI, artifacts | 7 projects, 50+ URLs |
| 4 | [04_deployment_railway_docker.md](04_deployment_railway_docker.md) | Railway multi-service, Docker multi-stage, CI/CD, CORS, SSL | 20 resources, 25+ error/fix pairs |
| 5 | [05_architecture_best_practices.md](05_architecture_best_practices.md) | State mgmt, auth, DB design, caching, RTL/Arabic, security, scalability | 25 resources, 30 decisions |

## Quick Reference by Topic

### Auth & JWT
- `01` → Supabase Auth + @supabase/ssr patterns, token refresh middleware
- `02` → PyJWT ES256/JWKS validation, FastAPI dependency injection
- `05` → Auth architecture decisions, getUser() vs getSession()

### Database & RLS
- `01` → RLS patterns from MakerKit, Supastarter, Catjam.fi case study
- `02` → Connection pooling (Supavisor port 6543 vs 5432), NullPool for serverless
- `05` → Multi-tenant RLS, `(SELECT auth.uid())` optimization, soft deletes, pgvector HNSW

### SSE Streaming
- `02` → sse-starlette patterns, disconnect detection, Nginx buffering fixes
- `03` → SSE vs WebSocket comparison across 7 chat apps, stream resumption
- `04` → SSE through Railway proxies, keep-alive headers, buffering config
- `05` → SSE decision matrix, typed event protocol, 30s heartbeat

### AI Chat & RAG
- `03` → Full architecture breakdowns of Open WebUI, LibreChat, LobeChat, Chatbot UI
- `05` → RAG pipeline evolution, multi-agent routing, semantic caching (0.92 threshold)

### Deployment
- `04` → Docker multi-stage builds, Railway config-as-code, env var management
- `04` → Railway vs Vercel vs Render comparison matrix
- `04` → 25+ common deployment errors with fixes

### Frontend / RTL / Arabic
- `01` → shadcn/ui + Supabase starters, SSR patterns
- `03` → Chat UI best practices (18 guidelines), artifact panel implementations
- `05` → CSS Logical Properties, Arabic typography (18px, 1.8 line-height), `<bdi>` for mixed content

### Error Handling & Debugging
- `02` → 10 typical FastAPI production errors with symptoms/causes/fixes
- `03` → 6 error tables (SSE, persistence, RAG, auth, deployment, frontend)
- `04` → 25+ deployment error/fix pairs across 6 categories
- `05` → Structured JSON error format with Arabic i18n, error boundary hierarchy
