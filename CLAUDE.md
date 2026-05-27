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
agents/         Real agent pipeline — orchestrator, router, deep_search_v4, agent_search, agent_writer, memory
.claude/agents/ Claude Code sub-agents (build, deploy, review)
agents/.claude/agents/ Pydantic AI agent builder pipeline (6 agents)
.claude/plans/  Wave-by-wave build plans (primary reference for @plan-reviewer)
agents_reports/ Agent output reports (validation, security, integration)
```

## Commands

| Task | Command |
|------|---------|
| Backend start | `uvicorn backend.app.main:app --port 8000 --reload` (run from repo root) |
| Frontend dev | `cd frontend && npm run dev` |
| Redis | `docker compose up -d` |
| Type check | `cd frontend && npx tsc --noEmit` |
| Build | `cd frontend && npm run build` |
| Lint | `cd frontend && npm run lint` |

## Vocabulary — "Layer" vs "Tier" (do NOT confuse these)

Two completely different concepts. Plans and discussions must keep them separate.

| Word | Meaning | Where defined | Values |
|---|---|---|---|
| **Layer** (Layer 1–4) | Architectural position in the agent call graph. Determines who can talk to the user, who can write `workspace_items`, what context surface each agent gets. | `.claude/plans/wave_9_agent_runs.md` § "Agent Hierarchy" | Layer 1 Conductor (Router) · Layer 2 Major (planners, top-level user-facing agents) · Layer 3 Task (transformers, no user talk — aggregator, agent_writer) · Layer 4 Memory (summarize/compact/distill — system-side) |
| **Tier** (tier_1, tier_2) | Model cost/capability bucket. Drives which family + provider chain `get_agent_model(slot)` returns. | `agents/utils/agent_models.py:32-45` (`Tier = Literal["tier_1", "tier_2"]`) | tier_1 = qwen3.6-plus / deepseek-v4-pro (capable) · tier_2 = qwen3.5-flash / deepseek-v4-flash (cheap/fast) |

An agent has **both**: a Layer (where it sits) and a Tier (which model it bills against). Example: `item_analyzer` is **Layer 4 Memory** running on model **tier_2** (deepseek-flash). `writer_planner_decider` is **Layer 2 Major** running on model **tier_1** (qwen3.6-plus).

If you find an older plan or report using "Tier 1–4" for the architectural concept, it's pre-rename — read it as "Layer 1–4".

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
| @deploy-checker | Railway deploy + verify — triggers deploys, watches builds, verifies services/env/health/CORS, post-deploy Playwright screenshots |
| @integration-lead | Frontend/backend contract matching — types, URLs, SSE events |
| @execution-planner | Conductor — reads plan-reviewer reports, invokes build/quality agents |
| @frontend-planner | Frontend sprint conductor — drives build-evaluate loop via @nextjs-frontend + @frontend-dev-loop, deploys, iterates |
| @frontend-dev-loop | Frontend evaluator (read-only) — state discovery, benchmark vs Claude.ai/ChatGPT, Playwright grading |

### Pydantic AI Agent Builder Pipeline (`agents/.claude/agents/`)

| Agent | Purpose |
|-------|---------|
| @pydantic-ai-planner | Autonomous requirements analysis → produces INITIAL.md |
| @pydantic-ai-prompt-engineer | System prompt design → produces prompts.md |
| @pydantic-ai-tool-integrator | Tool implementation → writes tools.py |
| @pydantic-ai-dependency-manager | Deps + agent assembly + runner → writes deps.py, agent.py, runner.py, __init__.py |
| @pydantic-ai-validator | Test creation → writes tests/ with TestModel + FunctionModel |
| @luna-wiring | Wires finished agent into orchestrator.py, agent_models.py, router |

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
- `agents/memory/agent.py` still uses **mock summary text** (`_mock_item_summary`, `_mock_compaction_summary`) — real DB logic, placeholder LLM output pending Wave 10

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
