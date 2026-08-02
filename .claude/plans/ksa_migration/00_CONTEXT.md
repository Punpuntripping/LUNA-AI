# KSA Migration — Shared Context Brief

**READ THIS FIRST.** Every phase document in this directory builds on the measured facts below.
Do not re-derive them; do not contradict them. If your research contradicts something here, say so
explicitly in your document under a "Contradicts context brief" heading.

**Goal:** move Luna Legal AI from Supabase (ap-south-1 Mumbai) + Railway (no ME region) onto
Alibaba Cloud / SCCC `me-central-1` (Riyadh), so that all personal and privileged data is stored
and processed inside the Kingdom. Driver is Saudi PDPL data residency for a product serving Saudi
lawyers handling client-privileged material.

**Accepted out of scope:** LLM inference stays offshore. Alibaba Model Studio (Qwen) is only in
Singapore / US-Virginia / Beijing / Hong Kong / Tokyo / Frankfurt — no Riyadh. The user has
accepted this and it is NOT to be relitigated. It must, however, be *documented* as a cross-border
transfer in the PDPL paperwork (see Phase 7).

---

## Current state — measured 2026-08-01

### Supabase
| | |
|---|---|
| Project | `dwgghvxogtwyaxmbgjod` ("Legal_AI"), org `xnubuahfyivigoliotaw`, **Pro plan** |
| Region | `ap-south-1` (Mumbai) |
| Postgres | **17.6.1.084** |
| DB size | **9,909 MB** (~4.2 GB of that is indexes, which are not dumped) |
| Public tables | 44 |
| auth.users | **24** |
| Storage | **66 objects / 7,051,927 bytes** across 2 **private** buckets: `documents`, `regulation-images` |
| pg_cron jobs | **0** (nothing to recreate) |
| Second project cost | $10/month |

### Extensions actually installed (note the schemas — this is a migration trap)
| Extension | Version | Schema |
|---|---|---|
| `vector` | **0.8.0** | `extensions` |
| `pg_trgm` | 1.6 | **`public`** ⚠ |
| `dblink` | 1.2 | **`public`** ⚠ |
| `pg_net` | 0.20.0 | `extensions` |
| `pgcrypto` | 1.3 | `extensions` |
| `uuid-ossp` | 1.1 | `extensions` |
| `supabase_vault` | 0.3.1 | `vault` |
| `pg_stat_statements` | 1.11 | `extensions` |
| `pg_cron` | 1.6.4 | `pg_catalog` |
| `plpgsql` | 1.0 | `pg_catalog` |

A fresh install puts `pg_trgm`/`dblink` in `extensions`. Ours are in `public`. Preserve this or
trigram indexes and RPCs will not resolve.

### Largest tables
| Table | Total | Indexes | Est. rows |
|---|---|---|---|
| `case_topics` | 3,826 MB | 2,227 MB | 279,948 |
| `search_topics` | 1,950 MB | 1,137 MB | 137,268 |
| `cases` | 980 MB | 264 MB | 30,533 |
| `case_sections` | 892 MB | 491 MB | 60,425 |
| `services` | 92 MB | 44 MB | 4,717 |
| `seo_articles` | 61 MB | 8 MB | 50,931 |
| `circulars` | 36 MB | 10 MB | 1,843 |
| `retrieval_artifacts` | 17 MB | 0.2 MB | 309 |
| `seo_item_meta` | 7.8 MB | 4.5 MB | 9,933 |
| `reranker_runs` | 7.6 MB | 0.3 MB | 2,922 |
| `messages` | 3.7 MB | 2.3 MB | 1,343 |
| `workspace_items` | 3.2 MB | 0.4 MB | 479 |

### Vector / HNSW inventory
27 `vector` columns. Corpus tables are **1024-dim**; user-data tables (`case_documents`,
`case_memories`, `conversations`, `lawyer_cases`) are **1536-dim**.

HNSW indexes totalling ~2.7 GB — these are rebuilt, not dumped, and the rebuild is the long pole:

| Index | Table | Size |
|---|---|---|
| `idx_st_vec_regulation` | search_topics | 958 MB |
| `idx_ct_vec_basis` | case_topics | 840 MB |
| `idx_ct_vec_fact` | case_topics | 835 MB |
| `idx_ct_vec_principle` | case_topics | 470 MB |
| `idx_case_embedding` | cases | 162 MB |
| `idx_case_sections_facts_vec` | case_sections | 161 MB |
| `idx_case_sections_principle_vec` | case_sections | 161 MB |
| `idx_case_sections_basis_vec` | case_sections | 160 MB |
| `idx_st_vec_appendix` | search_topics | 74 MB |
| `idx_st_vec_service` | search_topics | 57 MB |
| `idx_svc_embedding` | services | 37 MB |
| `idx_st_vec_circular` | search_topics | 16 MB |
| 4 more (`idx_documents_embedding`, `idx_memories_embedding`, `idx_conversations_embedding`, `idx_cases_embedding`) | user tables | 16 kB each |

GIN indexes: `idx_cases_fts` 85 MB · `idx_circulars_fts` 9.4 MB · `idx_case_referenced_regs` 7.2 MB
· `idx_services_fts` 6.2 MB · `idx_messages_content_trgm` 1.9 MB · `idx_case_legal_domains` 1.9 MB
· plus ~10 small ones.

Known runtime trap already documented in project memory: **HNSW `ef_search` defaults to 40, which
caps effective `LIMIT`**. Verify the search RPCs set it themselves on the new instance.

---

## Coupling audit — how locked-in the code actually is

| Surface | Measured | Implication |
|---|---|---|
| Frontend → Supabase | **16 calls / 6 files, 100% auth** (`stores/auth-store.ts`, `components/auth/AuthSync.tsx`, `components/auth/LoginForm.tsx`, `app/auth/callback/route.ts`, `lib/api.ts`, `components/Settings/AccountSettingsDialog.tsx`) | Browser never queries data directly. No `supabase.from('table')` anywhere. |
| Backend → PostgREST | **357 call sites / 70 files**, of which **12 are `.rpc()`** | The bulk. Preserved for free if we self-host PostgREST. |
| Storage | **10 calls / 2 files** — `shared/storage/client.py` is the chokepoint, plus `backend/app/services/attachment_cleanup.py` | Trivially swappable to S3-compatible OSS. |
| Auth validation | `shared/auth/jwt.py` — validates JWTs itself, handles **both HS256 and ES256** via JWKS | Already provider-agnostic in shape. Self-hosted GoTrue defaults to HS256. |
| RLS | **95 `auth.uid()` refs / 14 migration files** | Backend runs on the **service-role key**, which bypasses RLS (`get_admin_client`/`get_supabase_client`, 84 usages / 30 files). RLS is defence-in-depth, NOT load-bearing on the hot path. |

**Consequence:** self-hosting the Supabase stack (Postgres + PostgREST + GoTrue + Storage + Kong)
means **zero application code changes**. That is the recommended route. Re-platforming to a plain
managed Postgres would mean rewriting ~335 `.table()` call sites.

### Env var surface (the cutover checklist)
Backend: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_KEY`, `SUPABASE_JWT_SECRET`,
`SUPABASE_DB_URL` (defined in `shared/config.py:59-63`).
Frontend: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` — these are **Docker build
args** baked into the image at `frontend/Dockerfile:27-32`, so the frontend must be **rebuilt**,
not merely restarted.

The project ref `dwgghvxogtwyaxmbgjod` is hardcoded **only** in docs/agent files (`CLAUDE.md`,
`.claude/agents/*`, `LOCAL_DEVELOPMENT.md`, `.env.example`), never in application code.

CSP at `frontend/next.config.mjs:44` uses wildcard `https://*.supabase.co` — will need editing to
point at the new self-hosted origin.

---

## Current Railway estate
Project `adaptable-generosity`; services: **Redis**, **luna-frontend**, **luna-backend**.
Frontend: builder RAILPACK, root `/frontend`, repo `Punpuntripping/LUNA-AI`.
Production domain `rayhanai.com` + `api.rayhanai.com`, fronted by Cloudflare (currently
grey-clouded — see `.claude/plans/cloudflare_navigation_hardening.md`).

**Railway has exactly four regions — US West (California), US East (Virginia), EU West
(Amsterdam), Southeast Asia (Singapore). No Middle East.** Railway therefore cannot be part of a
KSA-onshore architecture and must be replaced.

---

## Target: Alibaba Cloud / SCCC `me-central-1` (Riyadh)

Operated by Saudi Cloud Computing Company — JV of Alibaba Cloud, stc Group, eWTP Arabia Capital,
SCAI and SITE. Two availability zones. Public residency claim: *"Your data stays securely within
Saudi Arabia in our Riyadh-based data centers"* and *"Data stays in-country, encryption is on by
default, and configurations align with local regulations and CIS benchmarks."*

**Confirmed in catalog:** ECS · Container Service for Kubernetes (ACK) · RDS · ApsaraDB for Redis ·
ApsaraDB for MongoDB · OSS / Storage · SLB + VPC · VPN Gateway · Express Connect · Transit Router ·
API Gateway · Cloud Backup · Cloud Firewall / WAF · CloudMonitor · Auto Scaling · Security Center ·
CDN · MaxCompute · DataWorks · ApsaraMQ for Kafka.

**Absent:** any managed identity/auth service (⇒ self-host GoTrue), and Model Studio.

**UNVERIFIED — must be confirmed with SCCC before committing budget:**
1. Is **RDS for PostgreSQL** (not just MySQL) offered in `me-central-1`?
2. What **pgvector version** does it ship? Alibaba's docs reference PG14/PG15; we need **PG17 +
   pgvector 0.8.0 with HNSW**.

Because of (2), the working assumption across these plans is **self-hosted Postgres 17 on ECS**,
not managed RDS. Any phase doc that assumes managed RDS must justify it.

---

## Dual-run strategy and its one-way trapdoor

Supabase **supports outbound logical replication** to an external Postgres via
publication/subscription over a **direct** connection (not the pooler).

Supabase **does not support inbound** logical replication — `CREATE SUBSCRIPTION` needs replication
privileges the managed platform does not expose.

**Therefore:** Alibaba can run as a live replica while Supabase is primary. The moment we promote
Alibaba, Supabase goes stale and rollback becomes a restore-from-snapshot, not a switch-flip. Every
plan must treat cutover as a genuine point of no return and size the rollback window accordingly.

Known traps for the replication leg:
- Supabase direct connections are **IPv6-only** unless the IPv4 add-on is purchased. Alibaba ECS
  needs IPv6 egress, or buy the add-on.
- Logical replication does not carry DDL, and sequence values need explicit handling.
- Whether the `auth` schema can be published needs verifying; the 24 `auth.users` rows may need a
  separate dump at cutover.
- Copy the **pgsodium root key** out of the old project before it is paused or deleted
  (`supabase_vault` is installed) or any encrypted secrets are lost.

---

## Data classification — drives what must move

**Personal / privileged (MUST be onshore):** `auth.users`, `users`, `conversations`, `messages`,
`workspace_items`, `case_documents` (uploaded client documents — lawyer-client privileged),
`case_memories`, `lawyer_cases`, `llm_calls`, `user_subscriptions`, `audit_logs`. Total: a few MB.

**Public corpus (no residency obligation — Saudi published legal texts are not personal data):**
`case_topics`, `search_topics`, `cases`, `case_sections`, `services`, `seo_articles`, `circulars`,
`regulations_v2`, `chunk_titles_v2`, `seo_item_meta`. ~9.5 GB of the 9.9 GB.

This asymmetry is the key scheduling lever: the 9.5 GB is static and can be loaded at leisure well
ahead of cutover; the few MB that actually changes is what needs a maintenance window.

---

## Phase documents in this directory

| File | Phase |
|---|---|
| `00_CONTEXT.md` | This brief |
| `01_account_and_procurement.md` | Alibaba/SCCC account, KSA entity, RAM/IAM, billing, capability verification |
| `02_network_and_infrastructure.md` | VPC, security groups, ECS sizing, OSS, Redis, SLB, DNS/TLS, Cloudflare |
| `03_self_hosted_supabase_stack.md` | Postgres 17 + pgvector 0.8, PostgREST, GoTrue, Storage, Kong, secrets, JWT |
| `04_schema_and_data_migration.md` | Dumps, corpus load, HNSW rebuild, storage objects, auth.users, migration history |
| `05_logical_replication_dual_run.md` | Publication/subscription, table set, drift detection, trapdoor management |
| `06_application_deployment.md` | FastAPI + Next.js off Railway, CI/CD, env vars, Redis, build args |
| `07_validation_and_cutover.md` | Test matrix, shadow testing, cutover runbook, rollback |
| `08_decommission_and_operations.md` | Supabase/Railway teardown, backups, monitoring, PDPL documentation |
