# Luna Legal AI — Local Development

This project runs entirely on your machine for day-to-day development. Supabase
(database, auth, storage) stays cloud-hosted — only the backend, frontend, and
Redis run locally.

```
┌─────────────┐     ┌─────────────┐     ┌─────────────────────┐
│  Frontend   │────▶│   Backend   │────▶│  Supabase (cloud)   │
│  :3000      │     │  :8000      │     │  Postgres + Auth    │
└─────────────┘     └──────┬──────┘     └─────────────────────┘
                           │
                           ▼
                    ┌─────────────┐
                    │  Redis      │  (Docker, :6379)
                    └─────────────┘
```

## One-time setup

1. Install Docker Desktop, Python 3.11+, Node 20+.
2. From the repo root: `docker compose up -d` (starts Redis on :6379).
3. Backend deps: `cd backend && pip install -r requirements.txt`
4. Frontend deps: `cd frontend && npm install`

Env files already exist and are gitignored:

- `.env` (root) — backend secrets (Supabase, Redis, LLM keys, Logfire)
- `frontend/.env.local` — frontend public env (Supabase URL/anon key, API URL)

## Daily workflow

Three terminals:

```bash
# 1. Redis (only needed once, runs in background)
docker compose up -d

# 2. Backend
cd backend && uvicorn app.main:app --port 8000 --reload

# 3. Frontend
cd frontend && npm run dev
```

Open http://localhost:3000.

## Local-mode env values

These are the values that differ from Railway. Both files are gitignored so you
can edit freely.

### `.env` (root)

| Var | Local | Railway |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379` | `redis://default:<pwd>@hopper.proxy.rlwy.net:11864` |
| `ENVIRONMENT` | `development` | `production` |
| `DEBUG` | `true` | `false` |
| `CORS_ORIGINS` | `http://localhost:3000` | `https://luna-frontend-production-1124.up.railway.app` |

### `frontend/.env.local`

| Var | Local | Railway |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | `https://luna-backend-production-35ba.up.railway.app` |
| `NEXT_PUBLIC_SUPABASE_URL` | (same — cloud) | (same — cloud) |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | (same — cloud) | (same — cloud) |

The previous Railway Redis URL is preserved as a comment in `.env` for quick switch-back.

## Files that stay as-is (don't delete)

These exist for Railway and are harmless in local dev:

- `backend/Dockerfile`, `frontend/Dockerfile` — image build for Railway
- `railway.json` — declares Dockerfile builder + healthcheck
- `frontend/next.config.mjs` — `output: "standalone"` is ignored by `npm run dev`
- `shared/observability.py` — reads `RAILWAY_ENVIRONMENT`, falls back to `APP_ENV`
- `frontend/next.config.mjs` CSP allows `*.railway.app` — harmless when not used

## Switching back to Railway

Two routes:

### Route A — keep developing locally, just push to deploy

You don't need to change local env files at all. Railway has its own env vars
set in the dashboard. Just commit and push to `master`:

```bash
git push origin master
```

Railway auto-deploys on push (watch patterns: `/backend/**`, `/shared/**`,
`/agents/**` for backend; `/frontend/**` for frontend).

### Route B — point your local frontend at the Railway backend

Useful when you want to test the production backend with a local frontend, or
when migrating fully back to Railway.

1. **`frontend/.env.local`** — set:
   ```
   NEXT_PUBLIC_API_URL=https://luna-backend-production-35ba.up.railway.app
   ```
2. **`.env`** (only if you want backend to use Railway Redis too):
   ```
   REDIS_URL=redis://default:CNjbXKzNprDIQvblxCHMIQtdYGOixYPC@hopper.proxy.rlwy.net:11864
   ```
3. Restart `npm run dev` (Next.js only reads `.env.local` at startup).

## Verify local setup is healthy

```bash
# Redis is up
docker ps | grep luna-redis

# Backend is up + can reach Supabase
curl http://localhost:8000/api/v1/health

# Frontend is up
curl http://localhost:3000
```

Backend health response should include `{"status": "ok"}` and Redis status.

## Known caveats

- **Hot reload**: backend uses `--reload`, frontend uses Next.js fast refresh.
  Migrations are NOT auto-applied — run them manually via Supabase dashboard or
  `supabase db push`.
- **Logfire**: stays enabled locally if `LOGFIRE_TOKEN` is set in `.env`.
  Traces tag as `dev` (via `APP_ENV` fallback) since `RAILWAY_ENVIRONMENT` is unset.
  Unset the token to silence telemetry.
- **Rate limiting**: still active in local dev (fails-open if Redis unreachable).
  Defaults: 60 req/min/IP, 10 req/min on auth, 20 msg/min.
- **CORS**: backend only allows `http://localhost:3000` per `.env`. Add more
  origins (comma-separated) if you run the frontend on a different port.
- **Supabase**: still the cloud project (`dwgghvxogtwyaxmbgjod`). No local DB.
  All your data lives in Supabase, shared between local and Railway environments.

## Cost note

Going local-only means Railway build minutes and runtime are no longer being
consumed. Supabase free tier and your Logfire/LLM API keys are the only ongoing
costs. The Railway services are still deployed and reachable — if you don't
push for a while they sit idle but the project may eventually pause if usage
drops below thresholds. To stop Railway spending entirely, pause the project
from the Railway dashboard (Settings → Danger Zone → Pause Project). Resume
later by un-pausing — no code changes needed.
