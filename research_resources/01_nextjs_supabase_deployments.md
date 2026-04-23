# Next.js + Supabase: Real-World Projects, Patterns & Best Practices

**Research Date:** 2026-03-13
**Purpose:** Catalog successful Next.js + Supabase deployments, architecture decisions, pitfalls, and best practices relevant to the Luna Legal AI project.

---

## Summary Table of Projects Found

| # | Project | Type | Open Source | Key Stack | URL |
|---|---------|------|-------------|-----------|-----|
| 1 | Vercel nextjs-subscription-payments | SaaS Subscription Starter | Yes | Next.js, Supabase Auth, Stripe, Webhooks | [GitHub](https://github.com/vercel/nextjs-subscription-payments) |
| 2 | Supabase Community Vercel AI Chatbot | AI Chat App | Yes | Next.js, Supabase Auth + Postgres, Vercel AI SDK, SSE Streaming | [GitHub](https://github.com/supabase-community/vercel-ai-chatbot) |
| 3 | MakerKit Next.js Supabase Turbo | SaaS Boilerplate (Monorepo) | Free Lite version | Next.js 15, Supabase, Stripe, Turborepo, shadcn/ui, i18next | [Site](https://makerkit.dev/next-supabase) / [GitHub Lite](https://github.com/makerkit/nextjs-saas-starter-kit-lite) |
| 4 | Supastarter | SaaS Boilerplate | No (paid, $299) | Next.js, Supabase, Stripe/LemonSqueezy, Multi-tenancy | [Site](https://supastarter.dev/) |
| 5 | KolbySisk next-supabase-stripe-starter | SaaS Starter | Yes | Next.js, Supabase, Stripe, shadcn/ui, React Email | [GitHub](https://github.com/KolbySisk/next-supabase-stripe-starter) |
| 6 | Vercel/Supabase Official Next.js Starter | Auth Template | Yes | Next.js App Router, @supabase/ssr, Tailwind CSS | [Vercel Template](https://vercel.com/templates/next.js/supabase) / [GitHub](https://github.com/vercel/next.js/tree/canary/examples/with-supabase) |
| 7 | Catjam.fi Production Case Study | Production SaaS (blog post) | N/A (case study) | Next.js, Supabase, RLS, Server Components | [Article](https://catjam.fi/articles/next-supabase-what-do-differently) |
| 8 | Supabase chatgpt-your-files | Document Chat (RAG) | Yes | Next.js, Supabase, pgvector, OpenAI | [GitHub](https://github.com/supabase-community/chatgpt-your-files) |

---

## Detailed Project Profiles

### 1. Vercel nextjs-subscription-payments

**URL:** https://github.com/vercel/nextjs-subscription-payments
**Type:** Full SaaS subscription application template
**Stars:** 6k+ (one of the most-referenced Next.js + Supabase projects)

**What it does:**
A clone-and-deploy SaaS subscription app with Stripe Checkout, Stripe customer portal, and Supabase-powered auth + database. Webhook listeners auto-sync Stripe product/price updates into Supabase Postgres.

**Architecture decisions:**
- **Auth:** Supabase Auth with cookie-based sessions via `@supabase/ssr`
- **Payments:** Stripe Checkout + webhooks. Webhook endpoint at `/api/webhooks` listens for product updates and propagates them to Supabase.
- **Database:** Supabase Postgres for user profiles, products, prices, and subscriptions.
- **RLS:** Enabled on all user-facing tables. Service role key used only server-side for webhook handlers.
- **SSR vs CSR:** Uses Next.js App Router with Server Components for data fetching, client components for interactive UI.
- **Deployment:** Vercel integration auto-provisions Supabase env vars (`NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`).

**Key patterns:**
- Stripe webhook -> Supabase service role client -> upsert products/prices/subscriptions
- Environment variables split: `NEXT_PUBLIC_*` for client-safe, no prefix for secrets
- `SUPABASE_SERVICE_ROLE_KEY` set manually (never auto-exposed)

**Pitfalls documented:**
- Missing env vars is the #1 deployment failure
- Stripe webhook URL must match production domain exactly (`/api/webhooks`)

---

### 2. Supabase Community Vercel AI Chatbot

**URL:** https://github.com/supabase-community/vercel-ai-chatbot
**Type:** Full-featured AI chatbot with chat history persistence
**Relevance to Luna:** Very high -- similar AI chat + Supabase auth + streaming pattern

**What it does:**
A production-ready AI chatbot forked from Vercel's official chatbot template, "Supabaseified" with Supabase Auth and Postgres DB replacing the original Vercel KV/Auth.js setup.

**Architecture decisions:**
- **Auth:** Supabase Auth (supports GitHub OAuth, email/password)
- **Streaming:** Vercel AI SDK for streaming chat UI responses (SSE-based)
- **Server Components:** React Server Components (RSCs) + Suspense + Server Actions for data fetching
- **Edge Runtime:** Supports edge runtime for streaming routes
- **Database:** Supabase Postgres for chat history, user data
- **UI:** shadcn/ui + Tailwind CSS + Radix UI + Phosphor Icons

**Key patterns:**
- Server Actions for mutations (save chat, delete chat)
- RSCs for initial data load (chat history list)
- Streaming via Vercel AI SDK's `useChat` hook on client, AI SDK `streamText` on server
- Model-agnostic: switch between OpenAI, Anthropic, Hugging Face via AI SDK

**Pitfalls documented:**
- Must configure Supabase RLS for chat tables
- GitHub OAuth requires callback URL configuration in Supabase dashboard
- Edge runtime has limitations with some Node.js APIs

---

### 3. MakerKit Next.js Supabase Turbo

**URL:** https://makerkit.dev/next-supabase (paid) / https://github.com/makerkit/nextjs-saas-starter-kit-lite (free lite)
**Type:** Production-grade multi-tenant SaaS boilerplate
**Relevance to Luna:** High -- production deployment patterns, RLS, multi-tenancy

**What it does:**
A monorepo-based SaaS starter with authentication, team management, subscription billing, admin dashboard, internationalization (i18n), and real-time notifications via Supabase Realtime.

**Architecture decisions:**
- **Monorepo:** Turborepo-based. Packages split into `@kit/supabase`, `@kit/ui`, `@kit/billing`, etc.
- **Auth:** `@supabase/ssr` cookie-based auth. Middleware refreshes tokens.
- **RLS:** Comprehensive RLS policies per table. Team-based access patterns with role hierarchies.
- **Database:** Supabase Postgres with migrations managed via `supabase migration` CLI.
- **Multi-tenancy:** Tenant-aware RLS, billing per tenant, custom domains.
- **UI:** shadcn/ui, Tailwind CSS, React Query (TanStack Query) for server state.
- **Validation:** Zod schemas for all data structures. `z.infer` for TypeScript types.
- **i18n:** Built-in via i18next -- relevant for Luna's Arabic-first requirement.

**Key patterns:**
- Route groups: `(marketing)` for public pages, `(auth)` for auth flows, `(app)/home` for protected app
- Service role client created only in server-side utilities
- Zod schema validation at API boundaries
- `supabase test db` with pgTap for automated RLS policy testing

**Production checklist they publish:**
1. Enable RLS on all tables
2. Configure real SMTP provider (not Supabase default)
3. Set environment variables per deployment environment
4. Never prefix secrets with `NEXT_PUBLIC_`
5. Use connection pooling (Supavisor) for serverless
6. Load test with k6 on staging before production

**Pitfalls documented:**
- Supabase default email service has low rate limits and poor deliverability
- Default email templates cause auth failures when users open links in different browsers
- Connection exhaustion in serverless environments without pooling

---

### 4. Supastarter

**URL:** https://supastarter.dev/
**Type:** Premium SaaS boilerplate ($299 lifetime)

**What it does:**
Production-grade SaaS template with multi-tenancy, payment integration (Stripe + Lemon Squeezy), blogging, documentation, AI integrations, and CLI tools.

**Architecture decisions:**
- **Auth:** Comprehensive -- password login, magic links, social logins
- **Multi-tenancy:** Data isolation per tenant, tenant-aware routing, per-tenant billing, custom domain support, feature flags per tenant
- **Payments:** Dual provider support (Stripe and Lemon Squeezy)
- **AI Features:** Dedicated AI SaaS boilerplate variant for Next.js
- **No Vendor Lock-in:** Modular architecture allows switching between Supabase, Drizzle, or Prisma

**Key patterns:**
- Tenant routing with Next.js dynamic routes
- Database isolation per tenant via RLS
- Subscription model management per tenant
- CLI tooling for scaffolding and configuration

---

### 5. KolbySisk next-supabase-stripe-starter

**URL:** https://github.com/KolbySisk/next-supabase-stripe-starter
**Type:** High-quality open-source SaaS starter

**What it does:**
A focused, well-architected SaaS starter emphasizing code organization, accessibility, and developer experience. Includes auth, subscriptions, email, and prebuilt pages.

**Architecture decisions:**
- **Auth:** Supabase Auth
- **File structure:** "Group by feature" pattern -- code colocated with features, UI in `app/` dir, reusable components in `components/` dir
- **UI:** shadcn/ui components in `components/ui/`
- **Email:** React Email + Resend for transactional emails
- **Payments:** Stripe Checkout + webhooks + subscription management
- **Type safety:** Dynamic product metadata with type-safe schema parsing

**Key patterns:**
- Feature-based file organization (not layer-based)
- Webhook sync between Stripe and Supabase
- Migration scripts for database schema evolution
- Prebuilt accessible pages with responsive design

---

### 6. Vercel/Supabase Official Next.js Starter Template

**URL:** https://vercel.com/templates/next.js/supabase / https://github.com/vercel/next.js/tree/canary/examples/with-supabase
**Type:** Official reference implementation for auth

**What it does:**
The canonical example of cookie-based auth using `@supabase/ssr` with Next.js App Router. This is the template Supabase and Vercel jointly recommend.

**Architecture decisions:**
- **Auth:** `@supabase/ssr` with `createBrowserClient` (client) and `createServerClient` (server)
- **Middleware:** Token refresh proxy pattern -- middleware calls `supabase.auth.getUser()` to refresh tokens, passes refreshed tokens via `request.cookies.set` to Server Components and `response.cookies.set` to browser
- **SSR:** Server Components for initial data fetching, client components for interactive forms
- **Cookie handling:** Uses `getAll()` and `setAll()` (NOT the deprecated `get()`/`set()`/`remove()` methods)

**Key patterns:**
- Two utility files: `utils/supabase/client.ts` (browser) and `utils/supabase/server.ts` (server)
- Middleware must be configured to refresh tokens on every request
- `supabase.auth.getUser()` (NOT `getSession()`) for server-side auth verification
- `Cache-Control: private, no-store` on auth-related responses to prevent CDN caching of tokens

---

### 7. Catjam.fi Production Case Study

**URL:** https://catjam.fi/articles/next-supabase-what-do-differently
**Type:** Post-mortem / lessons learned from a production Next.js + Supabase app

**Key lessons:**

**Data Loading:**
- Stream data from Server Components instead of fetching from route handlers after page mount
- Use React `use()` hook with promises passed from Server Components + `<Suspense>` boundaries
- Keep data loading calls near where data is consumed

**RLS Performance:**
- RLS follows O(n) complexity and can cause N+1-like problems
- Simple RLS conditions cause extra joins on queries returning many rows
- Cache bulk queries in RLS to speed up reads by an order of magnitude
- Index all columns used in RLS policies

**RLS Mutation Strategy:**
- Use RLS only for SELECT operations (reads)
- Route ALL mutations through the server using service role
- Reasoning: mutations often need external API validation, complex business logic, or multi-step transactions that are awkward in RLS policies

**Testing:**
- Don't mock Supabase calls -- mocks are painful to maintain and miss real DB edge cases (FK constraints, function corner cases)
- Spin up a local Supabase instance with seeded test data for e2e tests
- Run e2e tests on built (not dev mode) Next.js to reduce flakiness

---

### 8. Supabase chatgpt-your-files

**URL:** https://github.com/supabase-community/chatgpt-your-files
**Type:** Document chat with RAG (Retrieval-Augmented Generation)
**Relevance to Luna:** Very high -- document upload + vector search + chat

**What it does:**
A production-ready MVP for securely chatting with your documents using pgvector. Users upload files, documents are chunked and embedded, and the AI references relevant chunks when answering questions.

**Architecture decisions:**
- **Vector Search:** pgvector extension on Supabase Postgres for semantic similarity search
- **Document Processing:** File upload -> chunking -> embedding -> storage in pgvector
- **Auth:** Supabase Auth for secure per-user document access
- **RLS:** Documents and embeddings protected by RLS policies tied to user ID

---

## Common Patterns & Best Practices

### Authentication

1. **Always use `@supabase/ssr`** -- the `@supabase/auth-helpers-nextjs` package is deprecated. Do not use both packages simultaneously.

2. **Client migration mapping:**
   | Old (auth-helpers) | New (@supabase/ssr) |
   |---------------------|---------------------|
   | `createMiddlewareClient` | `createServerClient` |
   | `createClientComponentClient` | `createBrowserClient` |
   | `createServerComponentClient` | `createServerClient` |
   | `createRouteHandlerClient` | `createServerClient` |

3. **Token refresh in middleware:** Since Server Components cannot write cookies, middleware must:
   - Call `supabase.auth.getUser()` to refresh the token
   - Pass refreshed token to Server Components via `request.cookies.set`
   - Pass refreshed token to browser via `response.cookies.set`

4. **Never trust `getSession()` server-side.** Always use `getUser()` -- it makes a round-trip to Supabase Auth to validate the token. `getSession()` reads from cookies which can be spoofed.

5. **Cookie API:** Use `getAll()` and `setAll()` (NOT the deprecated `get()`/`set()`/`remove()` methods that will break your app).

6. **Prefetch pitfall:** Next.js Link prefetching and `Router.push()` can send server requests before the browser processes new access/refresh tokens, resulting in requests with stale or missing cookies.

### Row-Level Security (RLS)

1. **Enable RLS on every table.** No exceptions. A table without RLS is publicly readable and writable.

2. **RLS for reads, service role for writes:**
   - Use RLS policies for SELECT operations (leveraging `auth.uid()`)
   - Route INSERT/UPDATE/DELETE through server-side code using service role client
   - This gives you validation flexibility and avoids complex RLS write policies

3. **Performance:**
   - Index all columns referenced in RLS policies
   - RLS has O(n) cost per row -- watch for N+1 patterns on queries returning many rows
   - Consider caching bulk lookups within RLS functions

4. **Pair UPDATE policies with SELECT policies:** PostgreSQL reads existing rows before updating. Without a matching SELECT policy, UPDATE USING clauses fail silently.

5. **Custom JWT claims:** Store tenant IDs or roles in JWT claims to avoid heavy subqueries in RLS policies.

6. **Test RLS automatically:** Use `supabase test db` with pgTap for repeatable RLS policy verification.

### API Keys & Security

1. **Anon key** (`NEXT_PUBLIC_SUPABASE_ANON_KEY`): Safe for client-side. All access is gated by RLS via the `anon` and `authenticated` Postgres roles.

2. **Service role key** (`SUPABASE_SERVICE_ROLE_KEY`): Bypasses RLS completely. NEVER expose to the client. NEVER prefix with `NEXT_PUBLIC_`. Use only in:
   - Server Actions (with explicit auth checks)
   - Route Handlers
   - Webhook endpoints
   - Edge Functions

3. **Server Actions are not automatically secure.** Even though they run on the server, they can be called by anyone. Always verify user identity and permissions before executing admin logic.

### SSR & Data Fetching

1. **Two client utilities:** Every project uses this pattern:
   - `utils/supabase/client.ts` -- `createBrowserClient()` for Client Components
   - `utils/supabase/server.ts` -- `createServerClient()` for Server Components, Server Actions, Route Handlers

2. **Stream data from Server Components** rather than fetching in `useEffect` after mount. Use React `use()` hook with promises + `<Suspense>` boundaries.

3. **Set `Cache-Control: private, no-store`** on any response that touches authentication. If a CDN caches an auth response, different users may receive each other's sessions.

4. **Force dynamic routes** for SSE/streaming: `export const dynamic = 'force-dynamic'`

### SSE / Streaming

1. **Use ReadableStream API** in Next.js Route Handlers for SSE endpoints.

2. **Return the Response immediately** and start async streaming work after return -- this ensures Next.js sends headers to the client so chunks flow incrementally.

3. **Set `X-Accel-Buffering: no`** header for nginx/reverse proxy compatibility.

4. **Browser EventSource limitation:** Native EventSource only supports GET without custom headers. Use the `sse.js` library for POST requests or custom auth headers.

5. **Heartbeat messages** every 15-30 seconds keep connections alive through proxies and load balancers.

### Connection Pooling (Serverless)

1. **Use Supavisor** (Supabase's connection pooler) for all serverless deployments (Vercel, Next.js API routes, Server Actions).

2. **Transaction mode (port 6543)** is mandatory for serverless -- prevents connection exhaustion.

3. **Start with `connection_limit=1`** per serverless function, increase cautiously.

4. **Keep pooler usage under 40%** of available connections if using the REST API.

5. **Prepared statements not supported** in transaction mode -- if using Prisma, add `pgbouncer=true` to the connection string.

### Deployment

1. **Environment variables checklist:**
   - `NEXT_PUBLIC_SUPABASE_URL`
   - `NEXT_PUBLIC_SUPABASE_ANON_KEY`
   - `SUPABASE_SERVICE_ROLE_KEY` (server-only, no `NEXT_PUBLIC_` prefix)

2. **Separate Supabase projects** per environment (dev, staging, production).

3. **Real SMTP provider** for production auth emails (not Supabase's built-in, which has low rate limits and poor deliverability).

4. **Load test on staging** with k6 or similar before production launch.

5. **MFA on Supabase account** -- enforce MFA on your organization.

---

## Typical Errors & Debugging

### Authentication Errors

| Error / Symptom | Cause | Fix |
|----------------|-------|-----|
| User appears logged out on page refresh | Middleware not refreshing tokens | Ensure middleware calls `supabase.auth.getUser()` and sets cookies on both request and response |
| "Invalid JWT" or "JWT expired" | Token not refreshed server-side | Check middleware is running on all protected routes (not excluded by `matcher`) |
| Wrong user session served | CDN caching auth response | Set `Cache-Control: private, no-store` on auth-related responses |
| Auth works in dev, fails in production | Email link opened in different browser | Configure a real SMTP provider; customize email templates with proper redirect URLs |
| `getSession()` returns stale data | Using `getSession()` instead of `getUser()` | Replace all server-side `getSession()` calls with `getUser()` |
| Cookies not updating | Using deprecated cookie API | Migrate from `get()`/`set()`/`remove()` to `getAll()`/`setAll()` |
| Prefetched pages have no auth | Next.js Link prefetching runs before cookies are set | Use `supabase.auth.getUser()` in Server Components (not just middleware) |

### RLS Errors

| Error / Symptom | Cause | Fix |
|----------------|-------|-----|
| "new row violates row-level security policy" | INSERT/UPDATE policy missing or wrong | Add appropriate INSERT/UPDATE policies, or route mutations through service role |
| UPDATE silently returns 0 rows | No matching SELECT policy for UPDATE | Add a SELECT policy with same USING clause as UPDATE |
| Query extremely slow with RLS enabled | RLS conditions causing joins on every row | Index columns used in policies; cache bulk lookups; consider moving logic to server |
| Data visible to wrong users | RLS policy references wrong auth function | Ensure policies use `auth.uid()` and match against correct FK column |

### Deployment Errors

| Error / Symptom | Cause | Fix |
|----------------|-------|-----|
| Build fails on first deploy | Missing environment variables | Check build logs for Zod validation errors; add missing vars in hosting provider |
| "supabaseUrl is required" | `NEXT_PUBLIC_SUPABASE_URL` not set | Add to hosting provider's env vars and redeploy |
| Connection timeout in serverless | Too many direct connections | Switch to Supavisor connection pooler (port 6543) |
| Prisma errors in serverless | Prepared statements in transaction mode | Add `pgbouncer=true` to connection string |
| Webhook not firing | Stripe endpoint URL mismatch | Set webhook URL to `https://your-domain.com/api/webhooks` exactly |

### SSE / Streaming Errors

| Error / Symptom | Cause | Fix |
|----------------|-------|-----|
| SSE responses arrive all at once (buffered) | Nginx or proxy buffering | Set `X-Accel-Buffering: no` header; set `Cache-Control: no-cache` |
| SSE connection drops after 30s | No heartbeat; proxy timeout | Send heartbeat comment (`:\n\n`) every 15-20 seconds |
| Cannot send auth headers with EventSource | Browser API limitation | Use `sse.js` library or `fetch()` with ReadableStream instead |
| SSE route returns cached response | Next.js static optimization | Add `export const dynamic = 'force-dynamic'` to the route |

---

## Relevance to Luna Legal AI

Based on this research, the following patterns are directly applicable to the Luna project:

1. **Auth:** Luna already uses `@supabase/ssr` correctly. The middleware token refresh pattern documented by the official template and MakerKit matches Luna's architecture.

2. **RLS strategy:** The catjam.fi recommendation of "RLS for reads, service role for writes" aligns well with Luna's backend pattern (FastAPI service layer doing mutations via service role).

3. **SSE streaming:** Luna uses `sse-starlette` on FastAPI (not Next.js Route Handlers), which avoids the Next.js SSE buffering issues. The frontend `EventSource` approach should work since the backend handles auth differently.

4. **Document chat (RAG):** The `chatgpt-your-files` project validates Luna's architecture of document upload -> chunking -> pgvector -> retrieval in chat context.

5. **Arabic/i18n:** MakerKit's built-in i18n support (i18next) is a reference for Luna's Arabic-first requirement, though Luna handles this differently via hardcoded Arabic strings.

6. **Connection pooling:** Luna's Railway-hosted FastAPI backend should use Supavisor for database connections to avoid exhaustion under load.

7. **Testing:** The recommendation to use local Supabase + seeded data for e2e tests (not mocks) is highly relevant for Luna's validation workflow.

---

## Sources

- [Vercel nextjs-subscription-payments](https://github.com/vercel/nextjs-subscription-payments)
- [Supabase Community Vercel AI Chatbot](https://github.com/supabase-community/vercel-ai-chatbot)
- [MakerKit Next.js Supabase SaaS Boilerplate](https://makerkit.dev/next-supabase)
- [MakerKit Free Lite Starter](https://github.com/makerkit/nextjs-saas-starter-kit-lite)
- [Supastarter](https://supastarter.dev/)
- [KolbySisk next-supabase-stripe-starter](https://github.com/KolbySisk/next-supabase-stripe-starter)
- [Vercel/Supabase Official Next.js Starter Template](https://vercel.com/templates/next.js/supabase)
- [Next.js with-supabase example (GitHub)](https://github.com/vercel/next.js/tree/canary/examples/with-supabase)
- [Catjam.fi: Next.js + Supabase in production -- what would I do differently](https://catjam.fi/articles/next-supabase-what-do-differently)
- [Supabase chatgpt-your-files](https://github.com/supabase-community/chatgpt-your-files)
- [Supabase Docs: Setting up Server-Side Auth for Next.js](https://supabase.com/docs/guides/auth/server-side/nextjs)
- [Supabase Docs: Creating a Supabase client for SSR](https://supabase.com/docs/guides/auth/server-side/creating-a-client)
- [Supabase Docs: Migrating to SSR from Auth Helpers](https://supabase.com/docs/guides/auth/server-side/migrating-to-ssr-from-auth-helpers)
- [Supabase Docs: Row Level Security](https://supabase.com/docs/guides/database/postgres/row-level-security)
- [Supabase Docs: Production Checklist](https://supabase.com/docs/guides/deployment/going-into-prod)
- [Supabase Docs: Troubleshooting Next.js Auth Issues](https://supabase.com/docs/guides/troubleshooting/how-do-you-troubleshoot-nextjs---supabase-auth-issues-riMCZV)
- [MakerKit: Supabase RLS Best Practices](https://makerkit.dev/blog/tutorials/supabase-rls-best-practices)
- [MakerKit: Production Deployment Checklist](https://makerkit.dev/docs/next-supabase-turbo/going-to-production/checklist)
- [Supabase Docs: Advanced Server-Side Auth Guide](https://supabase.com/docs/guides/auth/server-side/advanced-guide)
- [Supavisor: Scalable Postgres Connection Pooler](https://supabase.com/blog/supavisor-postgres-connection-pooler)
- [Stripe & Supabase SaaS Starter Kit (Vercel)](https://vercel.com/templates/next.js/stripe-supabase-saas-starter-kit)
- [Pedro Alonso: Real-Time Notifications with SSE in Next.js](https://www.pedroalonso.net/blog/sse-nextjs-real-time-notifications/)
- [Fixing Slow SSE Streaming in Next.js and Vercel](https://medium.com/@oyetoketoby80/fixing-slow-sse-server-sent-events-streaming-in-next-js-and-vercel-99f42fbdb996)
- [Next.js + Supabase Cookie-Based Auth Workflow (2025 Guide)](https://the-shubham.medium.com/next-js-supabase-cookie-based-auth-workflow-the-best-auth-solution-2025-guide-f6738b4673c1)
- [Supabase RLS Complete Guide 2026](https://vibeappscanner.com/supabase-row-level-security)
- [StarterIndex: Next.js + Supabase Boilerplates](https://starterindex.com/nextjs+supabase-boilerplates)
