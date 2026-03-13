# Luna Legal AI

ChatGPT-like legal AI app for Saudi lawyers. Arabic-first, RTL interface.
Two modes: **General Q&A** (broad legal questions) and **Case-specific** (documents + memories per case).

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 14 App Router, TypeScript, Tailwind CSS, shadcn/ui |
| State | Zustand (client) + TanStack Query (server) |
| Backend | FastAPI, Python 3.11+ |
| Database | Supabase PostgreSQL + pgvector |
| Storage | Supabase Storage |
| Cache | Redis (Railway plugin) |
| Auth | Supabase Auth — email/password, JWT |
| Streaming | SSE via sse-starlette |

## Project Structure

```
frontend/       Next.js app — pages, components, hooks, stores, types
backend/        FastAPI app — routes, services, models, middleware
shared/         Python shared layer — config, db client, auth/JWT, cache, types
agents/         Mock RAG pipeline (agents/rag/)
.claude/agents/ Claude Code sub-agents (12 agents)
.claude/plans/  Wave-by-wave build plans (primary reference for @plan-reviewer)
agents_reports/ Agent output reports (validation, security, integration)
```

## Commands

| Task | Command |
|------|---------|
| Backend start | `cd backend && uvicorn app.main:app --port 8000 --reload` |
| Frontend dev | `cd frontend && npm run dev` |
| Redis | `docker compose up -d` |
| Type check | `cd frontend && npx tsc --noEmit` |
| Build | `cd frontend && npm run build` |
| Lint | `cd frontend && npm run lint` |

## Absolute Rules

1. **PyJWT**, NOT python-jose
2. **supabase-py v2+** (`client.auth.sign_in_with_password()`)
3. **@supabase/ssr**, NOT @supabase/auth-helpers-nextjs
4. Access token in **memory**, NOT localStorage
5. All error messages in **Arabic**
6. Every table has **RLS** enabled
7. User message saved **BEFORE** AI call (crash-safe)
8. **SSE**, NOT WebSocket

## Available Agents

| Agent | Purpose |
|-------|---------|
| @shared-foundation | Python shared layer — config, types, db, auth, cache |
| @sql-migration | PostgreSQL migrations, enums, RLS policies, triggers |
| @nextjs-frontend | Next.js pages, components, stores, hooks, RTL/Arabic UI |
| @fastapi-backend | FastAPI routes, services, models, middleware |
| @sse-streaming | SSE protocol, mock RAG pipeline, streaming display |
| @validate | API + DB + browser tests (comprehensive validation) |
| @security-reviewer | Security audit — RLS, JWT, XSS, CORS, credentials (read-only) |
| @rls-auditor | Dedicated RLS policy verification via live SQL |
| @plan-reviewer | Grand plan alignment check (only agent with Obsidian access) |
| @deploy-checker | Railway service status, env vars, health endpoints |
| @integration-lead | Frontend/backend contract matching — types, URLs, SSE events |
| @execution-planner | Conductor — reads plan-reviewer reports, invokes build/quality agents |

## Real Infrastructure

| Resource | Value |
|----------|-------|
| Supabase project | `dwgghvxogtwyaxmbgjod` (ap-south-1) |
| Supabase URL | https://dwgghvxogtwyaxmbgjod.supabase.co |
| Backend URL | https://luna-backend-production-35ba.up.railway.app |
| Frontend URL | https://luna-frontend-production-1124.up.railway.app |
| Redis (internal) | redis.railway.internal:6379 |
| Redis (public) | hopper.proxy.rlwy.net:11864 |

## Agent Reports

All agents write reports to `agents_reports/` using the naming convention:
- `validation_wave_{N}.md` — @validate post-wave results (bugs, fixes, pass/fail)
- `plan_review_{date}.md` — @plan-reviewer alignment audits
- `security_review_{date}.md` — @security-reviewer findings
- `integration_check_{date}.md` — @integration-lead contract mismatches

Agents MUST read existing reports before starting work to stay aligned on known bugs, completed fixes, and current project state.

| Report | Summary |
|--------|---------|
| `validation_wave_1_2.md` | Wave 1 auth fixes + Wave 2 deploy files. ES256/HS256 JWT bug found & fixed. 49/49 pass. |
| `validation_wave_3.md` | Wave 3 cases/conversations CRUD. 2 bugs + 6 missing UI features found. |
| `validation_wave_3_bugfix.md` | Wave 3 bug fixes confirmed. |
| `validation_wave_4.md` | Wave 4 messages + SSE + docs + memories. 5/5 checks pass. |
| `validation_wave_5.md` | Full quality gate. 68/70 pass (2 known limitations). |
| `security_review_2026-03-12.md` | RLS, JWT, XSS, CORS audit findings. |
| `integration_check_2026-03-12.md` | Cross-layer type/URL/SSE contract verification. |

## Project Status

| Wave | Scope | Status |
|------|-------|--------|
| Wave 1 | Auth fixes (AuthGuard, local JWT, refresh token, @supabase/ssr, DocumentType) | DONE |
| Wave 2 | Deployment files (2 Dockerfiles, railway.json) | DONE |
| Wave 3 | Sidebar + Cases CRUD + Conversations CRUD | DONE |
| Wave 4 | Messages + SSE streaming + Documents + Memories + Mock RAG | DONE |
| Wave 5 | Polish + Testing + Deployment verification (68/70 pass) | DONE |
| Wave 6A | DB migrations (018-020) + shared types | DONE |
| Wave 6B | Artifact + preferences backend APIs | DONE |
| Wave 6C | Agent framework (5 families) + router wiring | DONE |
| Wave 6D | Frontend: types, hooks, @ parser, artifact panel | DONE |
| Wave 7A | SSE hardening: heartbeat, disconnect detection, CancelledError | DONE |
| Wave 7B | Security & deployment: headers, Docker, healthcheck, rate limiting | DONE |
| Wave 7C | Operational maturity: error codes, audit logging, CI, reconnect | DONE |
| **Wave 8+** | **Real AI — swap mock agents for LLM, vector search, RAG, OCR** | **NOT STARTED** |

### Known Issues
- `frontend/.env.local` not present — dev env needs explicit env vars or symlink
- Supabase uses **ES256** JWTs (not HS256) — `shared/auth/jwt.py` handles both via JWKS
- Wave 5 validation: 68/70 pass (2 known limitations: stateless JWT logout, document upload test)
- All 5 agents are **mock** — return hardcoded Arabic text, no real LLM/RAG calls yet

## Plans (Source of Truth)

**Local plans** (primary): `.claude/plans/` — wave-by-wave breakdowns with agent assignments and file manifests.
- `master_plan.md` — Wave index, what's built vs missing, dependency chain
- `wave_4_messages_streaming.md` — 29 new files, 7 modified files, SSE protocol, success criteria
- `wave_5_polish_deploy.md` — Testing, security, deployment verification
- `wave_7a_sse_hardening.md` — Heartbeat + disconnect + CancelledError (3 files modified)
- `wave_7b_security_deployment.md` — Security headers, Docker, healthcheck, rate limiting (7 files)
- `wave_7c_operational_maturity.md` — Error codes, audit, CI, reconnect, optimistic updates (16 files)

**Grand plans** (Obsidian): Only `@plan-reviewer` reads them directly for detailed specs.
All other agents work exclusively within `C:\Programming\LUNA_AI\`.

Workflow: `@plan-reviewer` → alignment report → `@execution-planner` → invokes build agents
