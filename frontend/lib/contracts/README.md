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

## Tests

Pure helpers have `node:test` files in `__tests__/` (no runner is installed in
either app; `tsx` comes via npx):

```bash
cd frontend && npx tsx --test lib/contracts/__tests__/*.test.ts lib/__tests__/contracts-parsers-equivalence.test.ts
```

The zod-equivalence test lives outside this folder because it imports zod.
Tests may use Node APIs (`node:test`, `TextDecoder`); the modules may not.

## What lives here

| File | Contents | Web keeps importing from |
|---|---|---|
| `auth.ts` | `User`, `AuthTokens`, `AuthResponse` (moved) + native auth bodies (`SignupRequest`, `SignupResponse`, `OtpRequestResponse`, …) + `ProfessionGroupKey`, `ProfessionAnswer`, `UpdateProfessionRequest`/`Response` (`PATCH /auth/profession`, added) | `@/types` (re-export; the profession types are not used by the web yet) |
| `app-config.ts` | `AppConfig` (`GET /app/config`), `AppPlatform`, `APP_HEADERS` | — (native only) |
| `errors.ts` | `ErrorCode` union (mirror of `backend/app/errors.py`), `ApiErrorEnvelope` | — |
| `legal.ts` | `LEGAL_VERSION`, `LEGAL_ROUTES`, `AGENT_OUTPUT_DISCLAIMER_AR` (moved) | `@/lib/legal`, `@/components/workspace/AgentOutputDisclaimer` (re-export) |
| `numerals.ts` | `AR_NUM_LOCALE`, `AR_DATE_LOCALE`, `toLatinDigits` (moved) + `arNumber`, `arResetDate` (moved from gate-copy) | `@/lib/format/numerals`, `@/lib/library/gate-copy` (re-export) |
| `agents.ts` | `AgentFamily`, `WorkspaceItem*`, `UploadInitResponse` (moved) + `CreateNoteRequest`, `CreateReferenceRequest`, `UpdateVisibilityRequest`, `WorkspaceFileUrlResponse` (added, identical shapes) | `@/types` (re-export; the added four are still declared by the web itself) |
| `conversations.ts` | `ConversationSummary`, `ConversationDetail`, list/create shapes (moved) | `@/types` (re-export) |
| `messages.ts` | `Message`, `MessageMetadata`, `NextStep`, `TemplateUsed`, `MessageListResponse` (incl. `run_active`), `SendMessagePayload` (moved) | `@/types` (re-export) |
| `references.ts` | `Reference`, `SourceView`, `ReferenceSourceResponse`, unlock/balance shapes (moved) | `@/types` (re-export) |
| `sse.ts` | every SSE payload (moved) + `SSEError`, `SSEHeartbeat`, `SSEAgentSelected`, `SSEDeepSearchGated` (added — deep-search plan gate, migration 197), `SSEEventMap`, `SSEEventName`, `SSE_EVENT_NAMES` (23), `isSSEEventName` | `@/types` (re-export) |
| `sse-parser.ts` | `createSSEParser` (feed/end), `parseSSEData` (never throws), `toTypedSSEEvent` | — (native only) |
| `utf8.ts` | `createUtf8Decoder` — streaming UTF-8 fallback (WHATWG-exact) | — (native only) |
| `markdown/plain-text.ts`, `markdown/images.ts`, `markdown/streaming.ts` | `markdownToPlainText` (النسخ لناجز), `stripMarkdownImages`, `splitStreamingContent` (moved) | `@/lib/markdown/*` (re-export) |
| `next-steps.ts`, `templates-used.ts` | zod-free `parseNextSteps` / `parseTemplatesUsed` (URL check injectable) | `@/lib/next-steps`, `@/lib/templates-used` (re-export / wrap) |
| `citations.ts` | `CITATION_RE`, `toAsciiDigits`, `splitCitations`, `linkifyCitations` | — (web keeps its remark plugin) |
| `placeholder.ts` | `PLACEHOLDER_MAX_AGE_MS`, `placeholderPollMs`, `isDeadPlaceholder` | — (web keeps its own copy in use-messages / MessageList) |
| `share.ts` | `ShareDraftResponse`, `ShareArtifactRequest`, `ShareArtifactResponse`, `UpdateWorkspaceItemRequest`, `ExportFormat`, `ExportDocumentRequest` (added, not moved — identical shapes) | — (web still declares its own in `@/types` / `@/lib/api`) |
| `templates.ts`, `blogs.ts`, `preferences.ts` | composer «+» extras: `UserTemplate`, `TemplateListResponse`, `PendingTemplate`, `withTemplateDirective` (the `استخدم القالب: «…»` line) · `MyBlogItem`, `BlogItemResponse`, `extractBlogToken`, `planBlogQueue`, `MAX_BLOG_CHIPS` · `DetailLevel`, `DETAIL_LEVEL_OPTIONS`, `coerceDetailLevel`, `/preferences` shapes incl. `profession_reask_done` (added, not moved — identical to the web's) | — (web keeps its own in `@/types`, ChatInput, ComposerPlusMenu, DetailLevelToggle) |
| `push.ts` | `AppPushTokenRequest`, `AppPushTokenDeleteRequest` (`/push/app-token`), `TurnReadyPushData` (`data.conversation_id` of the «إجابتك جاهزة» push) — types only, added | — (native only; web push stays in `@/lib/push`) |
| `reference-labels.ts`, `references-copy.ts` | `referenceLabel`, `referenceCopyLabel`, `referenceLibraryExitLabel`, `officialUrl`, `appendReferencesForCopy`, … (moved) | `@/components/workspace/ReferencePanel` (re-export) |
| `library-refusal.ts` | `parseRefusal`, `refusalCardCopy(input, ctas)` — CTAs are a parameter (the app must never point at web pricing) | `@/lib/library/full-content`, `@/lib/library/gate-copy` (re-export / wrap) |
| `wi-preview.ts` | WI preview lock (`.claude/plans/wi_preview_lock.md`): `WI_PREVIEW_COPY` (THE wall copy, both apps), `WI_PREVIEW_LEGACY_SUFFIX`, `isWiPreviewLocked`, `wiPreviewVisibleContent`, `wiPreviewShownPct`, `wiPreviewHiddenRefsLine`, `WorkspaceItemReferencesResponse` (added) — plus `WorkspaceItem.preview` / `WorkspaceItemPreview` in `agents.ts` | — (new) |

Moved files are re-exported from their old web paths so no web consumer changed.
A new backend
`ErrorCode` member must be added to `errors.ts` in the same change.
