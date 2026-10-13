# `lib/contracts` — code shared by web and mobile

This folder is imported by two apps:

| App | Import path | Bundler |
|---|---|---|
| Web (`frontend/`, Next.js) | `@/lib/contracts` | Next / webpack |
| Mobile (`mobile/`, Expo) | `@contracts` / `@contracts/<file>` | Metro (via `watchFolders`) |

Anything placed here must compile and run unchanged in **both**.

## Rules

1. **Pure TypeScript only.** Types, constants, enums, and pure functions (no side effects at import time).
2. **No React, no Next.** No `react`, `next/*`, `react-native`, JSX, hooks, or components.
3. **No platform globals.** No `window`, `document`, `localStorage`, `sessionStorage`, `navigator`, DOM types, or Node built-ins (`fs`, `path`, `process`, `Buffer`).
4. **No imports from outside this folder** — with one exception: `import type` from a module that itself follows these rules. Never import runtime code from `@/lib/...`, `@/components/...`, etc. (the `@/` alias means different things in each app).
5. **No third-party packages.** The two apps have separate `node_modules`; a package here would have to exist (at a compatible version) in both.
6. Use relative imports between files in this folder (`./types`), never `@/` aliases.
7. A breaking change to a contract must be made in one commit that updates both apps, and bump `CONTRACTS_VERSION` in `index.ts`.

## Verifying

```bash
cd frontend && npx tsc --noEmit
cd mobile && npx tsc --noEmit
```

Nothing has been moved here yet; what moves is an owner decision.
