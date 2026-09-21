---
name: deploy
description: Redeploy Luna app to Railway (backend, frontend, or both)
user_invocable: true
allowed-tools: mcp__railway-mcp-server__deploy, mcp__railway-mcp-server__list-deployments, mcp__railway-mcp-server__get-logs
---

# /deploy — Redeploy to Railway

You are deploying the Luna Legal AI app to Railway.

## Argument: $ARGUMENTS

The argument specifies what to deploy:
- `backend` — deploy only luna-backend
- `frontend` — deploy only luna-frontend
- `both` or empty/blank — deploy both services

Parse `$ARGUMENTS` (case-insensitive, trimmed). If it doesn't match `backend` or `frontend`, deploy both.

## Deployment

Use `mcp__railway-mcp-server__deploy` with **`service_id`** and **`path`** —
those are the real parameter names. Anything else (`service`, `workspacePath`)
is silently dropped, and the call then deploys the LINKED service
(`luna-backend`) from the CURRENT directory. Pass `project_id`
(`a1e7045f-bd90-4f46-9cf4-f1a6c50f11d6`), `environment_id`
(`f9cf6025-7982-42d5-9b56-5f2945fcfd27`) and `service_id` explicitly, then
confirm in `list_deployments` that the deployment landed under the intended
service.

`path` must point at a CLEAN tree (`git worktree add --detach <scratch>/clean
HEAD`) — `deploy` tarballs the directory, not git, and this repo's root is
permanently dirty. If the work is already pushed, check `list_deployments`
first: the push's own build is the correct one and a snapshot deploy would
override it.

### Backend
- Service: `luna-backend`

### Frontend
- Service: `luna-frontend`

If deploying both, run them in parallel.

## After Deployment

1. For each deployed service, use `mcp__railway-mcp-server__list-deployments` (limit 1) to confirm the new deployment status.
2. Print a summary table:

| Service | Status |
|---------|--------|
| luna-backend | (status) |
| luna-frontend | (status) |

3. If any deployment shows an error, fetch logs with `mcp__railway-mcp-server__get-logs` for that service and show the relevant error lines.

## Rules
- Always confirm with the user before deploying (show which services will be deployed).
- Do NOT deploy Redis.
- Workspace path is always `C:\Programming\LUNA_AI`.
