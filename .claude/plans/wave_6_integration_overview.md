# Wave 6 — Integration Plan (Mock Study) — Overview

> **Source:** Obsidian `Wave6_Integration_Plan.md` (2026-03-13)
> **Goal:** Wire up the full pipeline — frontend → backend → agents → database — with mock agents.
> Prove every interface works end-to-end before implementing real AI logic.
> **Prerequisite:** Waves 1-5 DONE (68/70 tests passing per `validation_wave_5.md`)

---

## Scope

**IN SCOPE:**
- Database: 3 new migrations (enums, artifacts, user preferences/templates)
- Backend: artifact CRUD API, preferences/templates API, router wiring
- Agents: BaseAgent protocol, router + classifier, 5 mock family agents
- Frontend: artifact panel, `@` parser, template cards, new hooks/stores
- Full E2E integration tests (6 acceptance scenarios)

**OUT OF SCOPE:**
- Real AI (LLM calls, embeddings, vector search, OCR, prompt engineering)
- All agents return hardcoded Arabic mock data
- Agent internals (retrieval logic, memory extraction, document processing)

---

## Sub-Wave Breakdown

| Sub-Wave | Scope | Build Agents | Quality Agents | MCP Servers |
|----------|-------|--------------|----------------|-------------|
| **6A** | DB migrations + shared types | @sql-migration, @shared-foundation | @rls-auditor | Supabase MCP |
| **6B** | Artifact + preferences APIs | @fastapi-backend | @integration-lead | Supabase MCP |
| **6C** | Agent framework + router wiring | @sse-streaming, @fastapi-backend | Backend import tests | — |
| **6D** | Frontend types, hooks, UI | @nextjs-frontend | @validate, @security-reviewer | Playwright, shadcn, ESLint MCP |

---

## Dependency Graph

```
Wave 6A: DB Migrations + Shared Layer ─────────────────────────────────┐
  │                                                                     │
  ├─► Wave 6B: Artifact + Preferences APIs ──────────────────────┐     │
  │     │                                                         │     │
  │     └─► Wave 6C: Agent Framework + Router Wiring ────────┐   │     │
  │           │                                                │   │     │
  │           └─► Wave 6D: Frontend Integration ──────────────┤   │     │
  │                 │                                          │   │     │
  │                 └─► FINAL VALIDATION (E2E) ───────────────┘   │     │
  │                                                                │     │
  └────────────────────────────────────────────────────────────────┘     │
                                                                         │
  Parallel opportunity: 6B backend work + 6C agent work (after 6A)      │
  Sequential requirement: 6D MUST wait for 6B + 6C                      │
  ──────────────────────────────────────────────────────────────────────┘
```

### Parallelism Opportunities

| Can Run in Parallel | When |
|---------------------|------|
| @sql-migration + @shared-foundation | Wave 6A start |
| @fastapi-backend (artifacts) + @fastapi-backend (preferences) | Wave 6B start |
| Agent framework (@sse-streaming) + Frontend types (@nextjs-frontend) | After 6B |
| @validate + @security-reviewer + @integration-lead | Final validation |

---

## Agent Assignment Matrix

### Build Agents

| Agent | Wave | Responsibilities | Files |
|-------|------|------------------|-------|
| **@sql-migration** | 6A | Migrations 018-020 (enums, artifacts, preferences/templates) | 3 new SQL files |
| **@shared-foundation** | 6A | AgentFamily, ArtifactType enums, AgentContext dataclass, config vars | 2 modified files |
| **@fastapi-backend** | 6B | artifact_service, memory_md_service, artifacts API, preferences_service, preferences API, register routers | 6 new + 3 modified files |
| **@sse-streaming** | 6C | BaseAgent protocol, context builder, artifact helper, router, classifier, 5 mock agents | ~15 new files |
| **@fastapi-backend** | 6C | Wire router into message_service, update context_service, update messages API | 3 modified files |
| **@nextjs-frontend** | 6D | Types, API client, hooks, @ parser, template cards, artifact panel, case artifacts page | 12 new + 8 modified files |

### Quality Agents

| Agent | Wave | What It Validates | MCP Tools Used |
|-------|------|-------------------|----------------|
| **@rls-auditor** | 6A | RLS policies on artifacts, user_preferences, user_templates tables | `mcp__supabase__execute_sql` |
| **@integration-lead** | 6B, 6D | Backend ↔ frontend type alignment, API URL contracts, SSE event shapes | — |
| **@validate** | 6D | Full E2E: 6 acceptance tests (API + DB + browser) | `mcp__playwright__*`, `mcp__supabase__execute_sql` |
| **@security-reviewer** | 6D | New artifact/preferences endpoints, agent input validation | — |
| **@deploy-checker** | Post-deploy | Railway health, env vars, CORS headers | `mcp__railway-mcp-server__*` |

### Orchestration

| Agent | When | Role |
|-------|------|------|
| **@plan-reviewer** | Before 6A | Reads this plan + Obsidian source, confirms alignment |
| **@execution-planner** | Each sub-wave | Invokes build agents in dependency order, tracks progress |

---

## MCP Server Usage Strategy

### Supabase MCP (`mcp__supabase__*`)

| Tool | When | Purpose |
|------|------|---------|
| `apply_migration` | Wave 6A | Apply 018, 019, 020 migrations to Supabase |
| `list_tables` | 6A validation | Verify `artifacts`, `user_preferences`, `user_templates` tables created |
| `execute_sql` | 6A validation | Test RLS policies, verify enum types, check constraints |
| `execute_sql` | 6B validation | Test artifact CRUD queries, verify ownership isolation |
| `generate_typescript_types` | 6D | Generate fresh TS types from DB schema for frontend |
| `get_project` | Any time | Check project status/health |
| `list_migrations` | 6A pre-check | Verify current migration state before applying new ones |

### Playwright MCP (`mcp__playwright__*`)

| Tool | When | Purpose |
|------|------|---------|
| `browser_navigate` | 6D validation | Navigate to chat page for E2E tests |
| `browser_type` | 6D validation | Type `@بحث_معمق` in chat input, test @ parser |
| `browser_click` | 6D validation | Click template cards, artifact panel items |
| `browser_snapshot` | 6D validation | Verify DOM state (artifact panel open, template cards visible) |
| `browser_take_screenshot` | 6D validation | Visual verification of RTL layout, Arabic text rendering |
| `browser_wait_for` | 6D validation | Wait for SSE streaming to complete |

### Railway MCP (`mcp__railway-mcp-server__*`)

| Tool | When | Purpose |
|------|------|---------|
| `list-services` | Post-deploy | Verify backend + frontend services running |
| `list-variables` | Post-deploy | Confirm env vars set correctly |
| `get-logs` | Post-deploy | Check for runtime errors after deployment |
| `check-railway-status` | Post-deploy | Overall platform health |

### shadcn MCP (`mcp__shadcn__*`)

| Tool | When | Purpose |
|------|------|---------|
| `search_items_in_registries` | 6D pre-build | Find components needed for artifact panel |
| `get_add_command_for_items` | 6D | Install new shadcn components if needed |

### ESLint MCP (`mcp__eslint__*`)

| Tool | When | Purpose |
|------|------|---------|
| `lint-files` | 6D validation | Lint all new frontend files before commit |

---

## Validation Gates

Each sub-wave has a **mandatory validation gate** that must pass before proceeding.

### Gate 6A: Database + Shared
```
@rls-auditor → verify RLS on 3 new tables
Supabase MCP → execute_sql: SELECT * FROM artifacts LIMIT 1
Supabase MCP → execute_sql: test enum values exist
Python import check: from shared.types import AgentFamily, ArtifactType, AgentContext
```

### Gate 6B: Backend APIs
```
@integration-lead → verify Pydantic models match plan spec
curl tests → CRUD on /api/v1/artifacts, /api/v1/preferences, /api/v1/templates
Supabase MCP → execute_sql: verify RLS isolation (cross-user test)
Backend import: from backend.app.main import app (no import errors)
```

### Gate 6C: Agent Framework
```
Backend import: from agents.router.router import route_and_execute
Backend import: from agents.base.agent import BaseAgent
Mock test: route_and_execute("ما هي حقوق العامل؟", ...) → yields token/citations/done events
Mock test: route_and_execute("@عقد ...", ..., explicit_agent="end_services") → yields artifact_created
```

### Gate 6D: Frontend Integration (FINAL)
```
TypeScript: cd frontend && npx tsc --noEmit (zero errors)
ESLint MCP: lint all new files (zero errors)
@integration-lead → full cross-layer contract check
@security-reviewer → audit new endpoints + agent input validation
@validate → run all 6 acceptance tests (API + DB + browser via Playwright MCP)
```

---

## Estimated File Count

| Sub-Wave | New Files | Modified Files | Total |
|----------|-----------|---------------|-------|
| 6A | 3 | 2 | 5 |
| 6B | 6 | 3 | 9 |
| 6C | 15 | 3 | 18 |
| 6D | 12 | 8 | 20 |
| **Total** | **36** | **16** | **52** |

---

## Acceptance Criteria (6 E2E Tests)

All 6 must pass at Gate 6D:

1. **Plain text → Simple Search** — auto-routed, streaming tokens + citations, no artifact
2. **@بحث_معمق → Deep Search** — explicit agent, streaming + report artifact created
3. **@خطة @عقد → End Services + plan** — plan modifier runs first, then contract artifact
4. **Template card click → End Services** — chatbox populated, correct agent_family sent
5. **@ذاكرة → Memory Agent** — creates/updates memory.md artifact, writes to case_memories
6. **Artifact editing** — open artifact, edit markdown, PATCH saves, viewer refreshes

---

## What Comes After Wave 6

Once all 6 acceptance tests pass, the integration layer is proven. Next waves swap mocks for real implementations:

| Wave | Scope | Agent Replaced |
|------|-------|----------------|
| 7 | LLM-based intent classification | Mock classifier → Claude Haiku |
| 8 | Real Legal DB vector search | Simple Search mock → pgvector RAG |
| 9 | Full RAG pipeline | Deep Search mock → multi-step retrieval |
| 10 | Document processing | Extraction mock → Mistral AI OCR |
| 11 | Real document generation | End Services mock → template engine |
| 12 | Memory extraction + management | Memory mock → auto-extraction |

Each wave swaps ONE mock agent. Interfaces don't change — only internals.
