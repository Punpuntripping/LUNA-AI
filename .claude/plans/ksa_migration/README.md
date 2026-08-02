# KSA Migration — Supabase + Railway → Alibaba Cloud (Riyadh)

**Status:** PLANNING. Nothing provisioned, nothing committed, no spend.
**Created:** 2026-08-01

---

## Why

Luna serves Saudi lawyers handling client-privileged material. Saudi PDPL conditions the transfer
of personal data outside the Kingdom, SDAIA has published no adequacy list, and localization is
expected for sensitive/PII data absent an exemption.

Today:
- **Supabase** is in `ap-south-1` (Mumbai). Supabase runs only on AWS and offers **no Middle East
  region** — so managed Supabase can never be onshore.
- **Railway** has exactly four regions — California, Virginia, Amsterdam, Singapore. **No Middle
  East** — so the compute tier can never be onshore either.

Neither current provider can be fixed in place. Both must be replaced to achieve residency.

**Target:** Alibaba Cloud / SCCC `me-central-1` (Riyadh) — a JV of Alibaba Cloud, stc Group, eWTP
Arabia Capital, SCAI and SITE, whose stated position is that data stays within Saudi Arabia in
Riyadh-based data centres.

## Why now

**24 users.** 1,343 messages. 66 stored files. This migration is nearly free today and becomes
progressively harder with every month of growth. The window is the point.

## The strategic finding that makes this tractable

The codebase is far less locked into Supabase than a typical Supabase app:

- The **frontend touches Supabase for auth only** — 16 calls across 6 files, zero `supabase.from()`
  data queries. The browser never talks to the database.
- The **backend authorizes in FastAPI**, running on the service-role key. RLS is defence-in-depth,
  not load-bearing.
- **Storage is one file** (`shared/storage/client.py`).
- `shared/auth/jwt.py` already validates JWTs itself and handles both HS256 and ES256.

Therefore the chosen route is **self-hosting the Supabase stack** (Postgres + PostgREST + GoTrue +
Storage + Kong) on Alibaba ECS, which means **zero application code changes** — all 357 PostgREST
call sites keep working unmodified. The alternative, re-platforming to a plain managed Postgres,
would mean rewriting ~335 `.table()` calls.

We trade a rewrite for an ops burden. That trade is the central bet of this plan.

## Explicitly out of scope

**LLM inference stays offshore.** Alibaba Model Studio (Qwen) has no Riyadh region — Singapore,
US-Virginia, Beijing, Hong Kong, Tokyo and Frankfurt only. This is accepted and settled. It must be
*documented* as a cross-border transfer (Phase 8), not solved.

---

## Documents

Read `00_CONTEXT.md` first — every phase builds on its measured facts.

| # | Document | Covers |
|---|---|---|
| 00 | [`00_CONTEXT.md`](00_CONTEXT.md) | Shared fact brief — measured current state, coupling audit, data classification |
| 01 | [`01_account_and_procurement.md`](01_account_and_procurement.md) | SCCC vs Alibaba International entity, KSA account prerequisites, billing, RAM baseline, **capability verification gate**, cost model |
| 02 | [`02_network_and_infrastructure.md`](02_network_and_infrastructure.md) | VPC, security groups, ECS sizing, ESSD, OSS, Redis, SLB, DNS/TLS, Cloudflare |
| 03 | [`03_self_hosted_supabase_stack.md`](03_self_hosted_supabase_stack.md) | Postgres 17 + pgvector 0.8, PostgREST, GoTrue, Storage API, Kong, JWT scheme, secrets |
| 04 | [`04_schema_and_data_migration.md`](04_schema_and_data_migration.md) | Dumps, 9.5 GB corpus load, HNSW rebuild, storage objects, auth.users, verification |
| 05 | [`05_logical_replication_dual_run.md`](05_logical_replication_dual_run.md) | Publication/subscription, live-table set, drift detection, the one-way trapdoor |
| 06 | [`06_application_deployment.md`](06_application_deployment.md) | FastAPI + Next.js off Railway, CI/CD, env inventory, build-arg trap, CSP |
| 07 | [`07_validation_and_cutover.md`](07_validation_and_cutover.md) | Shadow testing, validation matrix, cutover runbook, rollback |
| 08 | [`08_decommission_and_operations.md`](08_decommission_and_operations.md) | Teardown, backups/PITR, monitoring, DR, PDPL documentation |

## Dependency order

```
01 Account ──► 02 Infra ──► 03 Stack ──► 04 Data ──┬──► 05 Replication ──┐
                                                    │                     ├──► 07 Cutover ──► 08 Ops
                                     06 App Deploy ─┘                     │
                                     (parallel with 04/05) ───────────────┘
```

Phase 06 can run in parallel with 04 and 05 — the application tier does not depend on the data
being fully loaded, only on the stack from 03 being reachable.

---

## Decision gates

The plan has three points where it should be allowed to stop.

**Gate A — after Phase 01. Can Alibaba actually host this?**
The two questions that decide everything:
1. Is RDS for **PostgreSQL** available in `me-central-1`, or only MySQL?
2. What **pgvector version** ships — we need 0.8.0 with HNSW on PG17?

The working assumption throughout is **self-hosted Postgres on ECS**, precisely so a NO on both
does not kill the project. But if ECS itself can't provide a suitable memory-heavy instance family
in Riyadh, stop here.

**Gate B — after Phase 05. Is the replica sound?**
Drift within threshold, vector-search golden queries matching production, full validation matrix
green on the shadow stack. If not, extend the dual-run — there is no deadline pressure.

**Gate C — the cutover itself. Point of no return.**
Supabase supports outbound logical replication but **not inbound**. The moment Riyadh is promoted,
Supabase is stale and rollback becomes restore-from-snapshot rather than a switch-flip. Phase 07
defines this marker precisely; treat it as irreversible in planning.

---

## Open questions carried across phases

1. **Contracting entity.** Does buying through Alibaba Cloud International grant `me-central-1` with
   the in-Kingdom residency guarantee, or is an SCCC contract required for it to hold legally?
   (Phase 01 — this is the single most consequential unknown.)
2. **Cloudflare.** The domain sits behind Cloudflare today. Proxied traffic egresses the Kingdom,
   which partly undermines the migration's purpose. Decide before cutover, not after. (Phases 02, 07)
3. **Residual cross-border transfers.** Beyond the LLM: Logfire telemetry, Mistral OCR, SMTP,
   Cloudflare. Each needs documenting. (Phase 08)
4. **Backups may not be allowed to leave the Kingdom** — constrains backup destination choice.
   (Phase 08)

## What this costs

Two things, and the second is the one that gets underestimated:

1. **Run-rate.** Alibaba ECS + OSS + Redis + SLB versus today's Supabase Pro + Railway. Phase 01
   models it.
2. **Ops burden.** We take ownership of backups, PITR, patching, upgrades, monitoring, TLS and DR —
   all of which Supabase and Railway currently do for free. Phase 08 quantifies this in hours/month.
   It does not go away after cutover.

---

*Not legal advice. The PDPL reading here is the engineering team's working interpretation; Saudi
counsel should review the residency posture and the residual-transfer documentation before it is
relied upon.*
