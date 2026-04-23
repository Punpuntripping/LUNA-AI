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

Use `mcp__railway-mcp-server__deploy` with `workspacePath: "C:\Programming\LUNA_AI"`.

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
