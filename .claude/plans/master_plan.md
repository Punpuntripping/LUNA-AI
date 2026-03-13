# Luna Legal AI — Master Build Plan

> Primary plan document for @plan-reviewer and @execution-planner.
> All wave plans live in `.claude/plans/`. This is the index.

---

## Project Overview

ChatGPT-like legal AI app for Saudi lawyers. Arabic-first, RTL interface.
Two modes: **General Q&A** (broad legal questions) and **Case-specific** (documents + memories per case).

---

## Wave Summary

| Wave | Scope | Status | Plan File |
|------|-------|--------|-----------|
| Wave 1 | Auth fixes (AuthGuard, local JWT, refresh token, @supabase/ssr, DocumentType) | DONE | — |
| Wave 2 | Deployment files (2 Dockerfiles, railway.json) | DONE | — |
| Wave 3 | Sidebar + Cases CRUD + Conversations CRUD | DONE | `wave_3_sidebar_crud.md` |
| Wave 4 | Messages + SSE streaming + Documents + Memories + Mock RAG | DONE | `wave_4_messages_streaming.md` |
| Wave 5 | Polish + Testing + Deployment verification (68/70 pass) | DONE | `wave_5_polish_deploy.md` |
| Wave 6 | Integration — Agent framework + Artifacts + @ Parser + Artifact Panel | DONE | `wave_6_integration_overview.md` |
| Wave 6A | DB migrations (018-020) + shared types | DONE | `wave_6a_database_shared.md` |
| Wave 6B | Artifact + preferences backend APIs | DONE | `wave_6b_backend_services.md` |
| Wave 6C | Agent framework (5 families) + router wiring | DONE | `wave_6c_agent_framework.md` |
| Wave 6D | Frontend: types, hooks, @ parser, artifact panel | DONE | `wave_6d_frontend_integration.md` |
| Wave 7 | Production Hardening (best practices audit) | DONE | See 7A/7B/7C below |
| Wave 7A | SSE hardening: heartbeat, disconnect detection, CancelledError | DONE | `wave_7a_sse_hardening.md` |
| Wave 7B | Security & deployment: headers, Docker, healthcheck, rate limiting, error boundary | DONE | `wave_7b_security_deployment.md` |
| Wave 7C | Operational maturity: error codes, audit logging, CI, reconnect, optimistic updates | DONE | `wave_7c_operational_maturity.md` |

---

## Post-Wave Validation Rule

**Every wave MUST end with validation before the next wave starts.** This is mandatory.

After each wave's build agents finish:
1. **@integration-lead** — verify cross-layer contracts (types, URLs, SSE events)
2. **@validate** — run tests for the wave's scope
3. **@security-reviewer** — if auth/RLS/credential changes were made

Only proceed to the next wave when validation passes. If issues are found, fix them in a sub-wave (e.g., 6A-fix) and re-validate before moving on.

```
Build wave N → @integration-lead + @validate (parallel)
  ├─ PASS → proceed to Wave N+1
  └─ FAIL → fix sub-wave → re-validate → then proceed
```

---

## What's Built (Waves 1-5)

### Shared Layer (`shared/`)
- `config.py` — Pydantic Settings (Supabase, Redis, OpenRouter, Mistral, feature flags)
- `types.py` — 12 enums + dataclasses (ChatMessage, RetrievedContext, LLMUsage)
- `auth/jwt.py` — ES256+HS256 decode via JWKS, AuthUser, verify_request(), refresh_token()
- `db/client.py` — Supabase sync+async clients (service role + anon + per-user RLS)
- `cache/redis.py` — Sync+async Redis, session helpers, cache helpers, rate-limit sliding window
- `storage/client.py` — Supabase Storage upload/download/signed-URL helpers
- `db/migrations/001-017` — All 17 migration files applied

### Backend (`backend/`)
- `app/main.py` — FastAPI factory, lifespan, CORS, request-ID + rate-limit middleware
- `app/deps.py` — get_current_user (local JWT), get_supabase(), get_redis()
- `app/api/auth.py` — 5 endpoints (login, register, refresh, logout, me)
- `app/api/cases.py` — 6 endpoints (CRUD + status + delete)
- `app/api/conversations.py` — 6 endpoints (CRUD + end-session)
- `app/api/messages.py` — 2 endpoints (list + send with SSE streaming)
- `app/api/documents.py` — 5 endpoints (list, upload, detail, download, delete)
- `app/api/memories.py` — 4 endpoints (list, add, edit, delete)
- `app/services/` — case, conversation, message, document, memory, context services
- `app/models/` — Pydantic request + response models
- `app/middleware/rate_limit.py` — Sliding window, fail-open
- `Dockerfile` — Production-ready

### Frontend (`frontend/`)
- `app/layout.tsx` — Root layout, Arabic font, RTL, Providers
- `app/login/page.tsx` — Login page with login/register toggle
- `app/page.tsx` — Root redirect (auth guard)
- `app/chat/layout.tsx` — Chat layout with sidebar
- `app/chat/page.tsx` — Chat empty state with welcome
- `app/chat/[id]/page.tsx` — Chat conversation page (SSE streaming works)
- `components/auth/` — AuthGuard + LoginForm
- `components/sidebar/` — Full sidebar with conversation list, case cards, tabs
- `components/chat/` — ChatLayoutClient, ChatContainer, ChatInput, MessageList, MessageBubble, StreamingText, TypingIndicator, CitationPills, FilePreview
- `components/documents/` — DocumentBrowser, DocumentCard, UploadDropzone
- `components/memories/` — MemoryList, MemoryCard
- `components/ui/` — Full shadcn/ui component library
- `stores/` — auth-store, sidebar-store, chat-store (Zustand)
- `hooks/` — use-cases, use-conversations, use-messages, use-documents, use-memories, use-chat
- `lib/api.ts` — Full API client (auth, cases, conversations, messages, documents, memories)
- `lib/supabase.ts` — createBrowserClient
- `lib/utils.ts` — cn(), Arabic date helpers
- `types/index.ts` — All TypeScript interfaces
- `Dockerfile` — 3-stage standalone build

### Mock RAG
- `agents/rag/pipeline.py` — Simple mock: yields Arabic tokens + citations + done

### Infrastructure
- `railway.json` — Railway deployment config
- 17 SQL migrations applied to Supabase (all tables + RLS + triggers + indexes)
- Backend deployed: https://luna-backend-production-35ba.up.railway.app
- Frontend deployed: https://luna-frontend-production-1124.up.railway.app

---

## What Wave 6 Adds

### Database (3 migrations)
- `018_enums_agent.sql` — agent_family_enum, artifact_type_enum
- `019_artifacts.sql` — artifacts table + RLS + indexes
- `020_user_preferences_templates.sql` — user_preferences + user_templates tables + RLS

### Shared Layer (2 modified)
- `types.py` += AgentFamily, ArtifactType, AgentContext
- `config.py` += AGENT_AUTO_ROUTE_MODEL, AGENT_DEFAULT_MODEL

### Backend (5 new + 4 modified)
- `services/artifact_service.py` — Artifact CRUD
- `services/memory_md_service.py` — memory.md management
- `services/preferences_service.py` — Preferences + templates CRUD
- `api/artifacts.py` — 5 artifact endpoints
- `api/preferences.py` — 6 preferences/templates endpoints
- `main.py` += register new routers
- `models/requests.py` += new request models
- `models/responses.py` += new response models
- `services/message_service.py` — replace rag_query with route_and_execute
- `services/context_service.py` — load memory_md + preferences
- `api/messages.py` — pass agent_family + modifiers

### Agent Framework (17 new)
- `agents/base/` — BaseAgent protocol, context builder, artifact helper
- `agents/router/` — Router + mock classifier
- `agents/simple_search/agent.py` — Mock simple search (no artifact)
- `agents/deep_search/agent.py` — Mock deep search (creates report artifact)
- `agents/end_services/agent.py` — Mock end services (creates contract artifact)
- `agents/extraction/agent.py` — Mock extraction (creates summary artifact)
- `agents/memory/agent.py` — Mock memory (creates memory.md artifact + writes to case_memories)

### Frontend (11 new + 7 modified)
- `hooks/use-artifacts.ts` — Artifact CRUD hooks
- `hooks/use-preferences.ts` — Preferences + templates hooks
- `lib/commands.ts` — @ command registry + parser
- `components/chat/AtCommandPalette.tsx` — @ autocomplete popup
- `components/chat/TemplateCards.tsx` — Template card row
- `components/artifacts/ArtifactPanel.tsx` — Collapsible artifact sidebar
- `components/artifacts/ArtifactCard.tsx` — Artifact preview card
- `components/artifacts/ArtifactViewer.tsx` — Markdown viewer + editor
- `components/artifacts/ArtifactList.tsx` — Grouped artifact list
- `components/memory/MemoryEditor.tsx` — Case memory editor
- `app/cases/[case_id]/artifacts/page.tsx` — Case artifacts page
- `types/index.ts` += artifact, template, SSE event types
- `lib/api.ts` += artifactsApi, preferencesApi, templatesApi
- `hooks/use-chat.ts` += artifact_created + agent_selected handlers
- `stores/chat-store.ts` += agent selection + artifact panel state
- `components/chat/ChatInput.tsx` += @ parser integration
- `components/chat/ChatContainer.tsx` += template cards + panel toggle
- `components/chat/ChatLayoutClient.tsx` += artifact panel column

---

## Agent Roster

### Build Agents
| Agent | Model | Domain |
|-------|-------|--------|
| @shared-foundation | opus | Python shared layer |
| @sql-migration | opus | PostgreSQL migrations, RLS |
| @nextjs-frontend | opus | Next.js pages, components, stores, hooks |
| @fastapi-backend | opus | FastAPI routes, services, models |
| @sse-streaming | sonnet | SSE protocol, agent framework, streaming |

### Quality Agents
| Agent | Model | Domain |
|-------|-------|--------|
| @validate | opus | API + DB + browser tests |
| @security-reviewer | opus | Security audit (read-only) |
| @rls-auditor | sonnet | RLS policy verification |
| @integration-lead | opus | Cross-layer contract matching |
| @deploy-checker | haiku | Railway deployment health |

### Orchestration
| Agent | Model | Domain |
|-------|-------|--------|
| @plan-reviewer | opus | Plan alignment (reads Obsidian + .claude/plans/) |
| @execution-planner | opus | Conductor — invokes all other agents |

---

## Wave 6 Dependency Chain

```
Wave 6A (parallel):
  @sql-migration        → 018, 019, 020 migrations
  @shared-foundation    → types.py, config.py updates
    │
    └─► GATE 6A: @rls-auditor + Supabase MCP verification
          │
          └─► Wave 6B (parallel):
                @fastapi-backend  → artifact_service, preferences_service, APIs
                  │
                  └─► GATE 6B: @integration-lead + curl tests
                        │
                        ├─► Wave 6C (sequential):
                        │     @sse-streaming      → agent framework (base, router, 5 mocks)
                        │     @fastapi-backend     → wire router into message_service
                        │       │
                        │       └─► GATE 6C: import tests + mock execution tests
                        │             │
                        │             └─► Wave 6D:
                        │                   @nextjs-frontend → types, hooks, @ parser,
                        │                                      template cards, artifact panel,
                        │                                      case artifacts page
                        │                     │
                        │                     └─► GATE 6D (FINAL):
                        │                           @integration-lead (parallel)
                        │                           @security-reviewer (parallel)
                        │                           @validate → 6 acceptance tests
                        │                             (Playwright MCP for browser tests)
                        │                             (Supabase MCP for DB verification)
```

---

## Wave 7: Production Hardening

Source: 5 best-practice audit reports (`agents_reports/best_practices/`) consolidated into `EXECUTION_PLAN.md`.
Scoped for MVP (~100 users). Items not needed at this scale are deferred with specific triggers.

```
Wave 7A + 7B can run in PARALLEL (zero shared files):

  Wave 7A: @sse-streaming + @fastapi-backend + @nextjs-frontend
    ├── Heartbeat (15s keep-alive pings)
    ├── Disconnect detection (request.is_disconnected)
    └── CancelledError handling

  Wave 7B: @fastapi-backend + @nextjs-frontend
    ├── Security headers (CSP, HSTS, X-Frame-Options)
    ├── Non-root Docker + multi-stage build
    ├── Railway healthcheck
    ├── Per-user rate limiting + X-Forwarded-For
    └── Error boundary + smart query retry

Wave 7C runs AFTER 7A (reconnect depends on heartbeat):

  Wave 7C: @fastapi-backend + @nextjs-frontend + @sse-streaming
    ├── SSE auto-reconnect with exponential backoff
    ├── Structured error codes (~111 HTTPExceptions → LunaHTTPException)
    ├── Audit logging (wire existing audit_logs table)
    ├── GitHub Actions CI pipeline
    └── Optimistic updates for conversations
```

### Wave 7 Validation Chain
```
7A + 7B build (parallel) → @integration-lead + @validate + @security-reviewer + @deploy-checker
  ├─ PASS → proceed to 7C
  └─ FAIL → fix sub-wave → re-validate
7C build → @integration-lead + @validate (Supabase MCP for audit logs, Playwright MCP for optimistic UI)
  ├─ PASS → Wave 7 complete
  └─ FAIL → fix sub-wave → re-validate
```

---

## Future Waves (Post Wave 7)

| Wave | Scope | What Changes |
|------|-------|--------------|
| 8 | LLM-based intent classification | Mock classifier → Claude Haiku |
| 9 | Legal DB vector search | Simple Search mock → pgvector RAG + hybrid search |
| 10 | Full RAG pipeline | Deep Search mock → multi-step retrieval |
| 11 | Document processing | Extraction mock → Mistral AI OCR |
| 12 | Document generation | End Services mock → template engine |
| 13 | Memory extraction | Memory mock → auto-extraction |

Each wave swaps ONE mock agent. Interfaces don't change — only internals.

### Deferred Items (Scale Later — with triggers)

| Item | Trigger | Source Report |
|------|---------|---------------|
| Gunicorn + Uvicorn workers | Request queuing, Railway 2+ vCPU | Infrastructure |
| Active Redis caching | Supabase latency >200ms | Architecture |
| Semantic LLM caching | Real LLM + repeated queries >$50/mo | AI Chat SaaS |
| Celery background tasks | Doc processing >10s blocking HTTP | Infrastructure |
| Separate Supabase envs | Before real user data in production | Next.js/Supabase |
| Real SMTP (Resend/SendGrid) | Supabase email rate limits hit | Next.js/Supabase |
| MFA for lawyers | Saudi legal compliance requirement | Next.js/Supabase |
| Vitest frontend tests | Frontend regression bugs appearing | Architecture |
| Playwright E2E tests | Before launch with paying users | Architecture |
| Sentry error tracking | Production launch (5-min setup) | Infrastructure |
| k6 load testing | Before launch or first perf complaint | Architecture |
