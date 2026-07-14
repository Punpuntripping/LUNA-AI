---
name: ship
description: Commit → push → deploy → verify → memory-sync for built-but-undeployed Luna features
user_invocable: true
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Agent, mcp__railway-mcp-server__deploy, mcp__railway-mcp-server__list_deployments, mcp__railway-mcp-server__get_logs, mcp__railway-mcp-server__list_variables
---

# /ship — take built features to production, end to end

You take features that are BUILT but not yet in production and ship them:
discover what's shippable, confirm scope, preflight, commit, **push to master
first**, snapshot-deploy the affected Railway services, verify health, then
flip the feature's status in the persistent memory files. One run = one
coherent ship, fully verified, with the paper trail updated.

## Argument: $ARGUMENTS

Parse `$ARGUMENTS` (trimmed):
- Empty / `all` → ship everything currently uncommitted + unpushed (after
  showing the manifest and confirming).
- A feature hint (e.g. `onboarding`, `composer menu`) → ship only the files
  belonging to that feature; leave unrelated dirty files uncommitted. Match
  the hint against `git status` paths and the memory backlog (Step 1).
- `backend` / `frontend` → restrict the deploy to that one service (still
  commit + push everything staged for it).
- `--build` → also run the full `npm run build` preflight (slow; default is
  tsc-only).
- `--dry-run` → do Steps 1–2 only (manifest + plan), deploy nothing.

## Hard facts (read once — these are constraints, not suggestions)

- Repo root: `C:\Programming\LUNA_AI`. Remote: `origin` = GitHub, branch
  `master` tracks `origin/master`. Railway services are **GitHub-linked to
  master**.
- Services: `luna-backend`, `luna-frontend`. **Never deploy Redis.**
- Path → service mapping: `frontend/**` → luna-frontend; `backend/**`,
  `shared/**`, `agents/**` → luna-backend; both sets touched → both services.
  Docs/plans/reports-only changes (`.claude/`, `agents_reports/`, `*.md` at
  root) need **no deploy** — commit + push only.
- **Master-pull trap:** any Railway env-var change auto-triggers a redeploy
  that pulls from GitHub *master*, not your local snapshot. That is why the
  order is always **push → then deploy**. If this run must also change env
  vars, change them only AFTER the push, and follow with a fresh snapshot
  deploy (or use skipDeploys) so the master-pull doesn't stomp anything.
- **Frontend build-arg trap:** `NEXT_PUBLIC_API_URL` is a Docker build ARG —
  changing it needs a *rebuild*, not a restart. Any CSP change in
  `next.config.mjs` must be committed and pushed BEFORE flipping frontend
  env vars (the env flip triggers a master-pull rebuild that must already
  contain the CSP fix).
- Prod URLs: frontend `https://rayhanai.com`, backend
  `https://api.rayhanai.com`, backend health `GET /api/v1/health`.
- Memory dir (status sync target):
  `C:\Users\mhfal\.claude\projects\C--Programming-LUNA-AI\memory\`.
- Commit style: conventional commits matching the existing log —
  `feat(scope): …` / `fix(scope): …`, Arabic feature names welcome in the
  subject. Never `--no-verify`, never force-push, never amend published
  commits.

## Workflow

### Step 1 — Discover the shippable state
Run in parallel:
- `git status -sb` and `git diff --stat` (uncommitted work)
- `git log origin/master..HEAD --oneline` (committed but unpushed)
- Grep the memory dir's `MEMORY.md` for `NOT deployed|NOT committed` — this
  is the ship backlog with feature names.

Build a **ship manifest**: for each feature (or the one matching the
argument hint) list its files, the commit(s) it needs, the affected
service(s), and the memory file that tracks it. If the working tree mixes
several features and the user asked for one, plan a scoped `git add` of only
that feature's paths.

### Step 2 — Confirm
Show the manifest (files → commit message draft → services to deploy →
memory files to update) and **ask the user to confirm before deploying**.
`--dry-run` stops here.

### Step 3 — Preflight
- Frontend files in the manifest → `cd frontend && npx tsc --noEmit`
  (mandatory). With `--build`, also `npm run build`.
- Backend/agents/shared `.py` files → `python -m py_compile <each file>`.
- Any failure → report the errors and STOP. Nothing ships on a red preflight.

### Step 4 — Commit and push (push BEFORE deploy — see master-pull trap)
- `git add` the manifest paths (scoped, not `-A`, when shipping one feature).
- One conventional commit per feature. End the message with the session's
  standard co-author trailer if the harness requires one.
- `git push origin master`. If push is rejected (remote ahead), `git pull
  --rebase origin master`, re-run the frontend tsc preflight if the rebase
  touched frontend files, then push. Never force-push.

### Step 5 — Deploy
For each affected service, call `mcp__railway-mcp-server__deploy` with
`workspacePath: "C:\Programming\LUNA_AI"`. Both services → launch in
parallel. Skip the deploy entirely for docs-only ships (say so).
- A snapshot deploy reported **SKIPPED** usually means no watched-path
  change — check `list_deployments`: the GitHub master-pull deploy from your
  push may already be the live one carrying the same commit. That counts as
  shipped; verify it like any other deploy.

### Step 6 — Verify
- Poll `mcp__railway-mcp-server__list_deployments` (limit 1 per service)
  until the new deployment is `SUCCESS` — re-check every ~60s, give up after
  ~10 min and report. On `FAILED`/`CRASHED` → pull
  `mcp__railway-mcp-server__get_logs`, show the error lines, STOP (do not
  update memory).
- Backend shipped → `curl -s https://api.rayhanai.com/api/v1/health` must
  return healthy JSON.
- Frontend shipped → `curl -s -o /dev/null -w "%{http_code}" https://rayhanai.com`
  must be `200`.
- For UI-visible features, optionally invoke **@deploy-checker** for a
  post-deploy Playwright screenshot pass — do this when the user asked for
  visual confirmation or the feature is user-facing UI.

### Step 7 — Memory sync (only after Step 6 is green)
For each shipped feature that has a memory file:
- Edit the memory file body: `NOT deployed` / `NOT committed` →
  `DEPLOYED <today YYYY-MM-DD>` (keep the rest of the line).
- Edit the matching one-line entry in `MEMORY.md` the same way.
- Touch ONLY the features in this ship's manifest — never batch-flip others.

### Step 8 — Report
Summarize: commit hash(es) + subjects, services deployed + deployment
status, health-check results, memory files updated. If anything was skipped
(e.g. deploy SKIPPED and covered by the master-pull), say so explicitly.

## Rules
- **Push before deploy, always.** The GitHub link makes master the fallback
  truth; a snapshot deploy over an unpushed tree is how prod silently
  reverts later.
- Never deploy Redis. Never change env vars mid-ship without the trap
  protocol in Hard facts.
- Red preflight or failed deploy = full stop; report, don't "fix forward"
  without the user.
- Memory status flips happen only for verified-green ships, only for
  manifest features.
- If the working tree is clean AND master == origin/master AND the backlog
  grep finds nothing, say there is nothing to ship and stop.
