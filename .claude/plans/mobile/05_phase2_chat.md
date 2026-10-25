# Mobile — Phase 2: Chat core

**Status (2026-10-25):** 2a BUILT 2026-10-15/17 (`c45d75b` stream layer, `ebe792d` screens,
`4b2b584` «افتح ال…») → QA'd 2026-10-19 (`agents_reports/mobile_qa/2026-10-19_phase2a*/`) → fixes
`c8491c3`, `b330340`; action bar `711269e`, `e84e340` · 2b/2c BUILT 2026-10-20 (`c69eefb`) ·
workspace «إخفاء/حذف» removed (`e2fb613`) · Android dev builds `fb329f85` (expires 2026-11-03),
`d9bf7a13` (adds Sentry native) · 2b/2c emulator QA 2026-10-25 (`agents_reports/mobile_qa/2026-10-25_phase2bc*/`)
→ fixes `6207137` → retest PASS · Phase 1 deployed (`04`
§Status). Marks: ✅ decided ·
❓ owner decision · 🔎 not verified, check before relying on it. Scope: `01_app_scope.md`
(chat with **all** web features; citations option B). Phases: `03` §3.

## Owner decisions (2026-10-15) — override the sections below
- **R4 → (a):** «فتح ال… في ريحان» opens `https://rayhanai.com{library_url}` in the in-app browser
  (`expo-web-browser`). Known trade-off: no web session there → the visitor/anonymous view and gate.
  No native full-document screen in Phase 2 (E6 dropped).
- **D1 → yes:** permanent one-line footer under the composer «ريحان مساعد بالذكاء الاصطناعي وقد
  يخطئ — تحقق من المصادر» (plus the full disclaimer on the empty state and in agent viewers).
- **S3 / B4 → leave:** Stop = detach (as web). No server-side cancel.
- **NEW — auto-open artifacts over the chat (reverses W4 "no auto-open on the phone"):** when a turn
  produces an agent output (`workspace_item_created` with kind `agent_search` or `agent_writing`),
  it opens automatically as a **sheet/modal over the chat** (chat stays underneath; dismiss returns
  to the chat at the same scroll). Notes, attachments (user-uploaded) and `convo_context` /
  `references` never auto-open. Opens once per item (not again on refetch/resume); if the user is
  scrolled/typing it still opens (owner rule) — dismissible with swipe-down / «إغلاق». ⇒ a **basic
  artifact viewer** (agent_search markdown + `[n]` → reader + reference cards; agent_writing preview
  + disclaimer + copy) moves from 2b into **2a**; the full workspace list stays 2b.

---

## 1. Goal + exit criteria

Phase 2 turns the app into a working Rayhan chat, shipped in **three sub-phases**, each
releasable on its own (EAS Update / preview build).

**2a — Core chat (shippable alone).** On the Android dev build (iOS once the Apple account exists):
1. Home = conversation list: infinite scroll, search (titles + message text), «الكل / المميّزة»,
   star/unstar, rename, delete (confirm). Shared demo conversation read-only, «حذف» → «إخفاء».
2. «محادثة جديدة» → type → send → answer streams token by token as Arabic RTL markdown
   (headings, lists, **tables**, bold, links) in light + dark.
3. Deep-search turns: 4-stage progress tracker + status log. Writer turns: «القوالب المستخدمة».
   Latest settled answer: next-step chips (tap pastes into composer, never sends).
   `agent_question` turns: «السؤال» callout with read-only suggestions.
4. Tapping `[n]` opens the **native source reader** (metered reveal, same as web) with both exits:
   «فتح المصدر الرسمي» (in-app browser) and «فتح ال… في ريحان» (per R4).
5. **Stop** works; killing the network or backgrounding mid-answer never loses the answer — on
   return the bubble shows «ريحان يفكّر» and fills in when the run finishes. Never sends twice.
6. Quota / parallel-limit / duplicate refusals show the server's Arabic text; composer text is
   restored; no link to web checkout (📏).
7. Every bubble's copy offers «نسخ» + «النسخ لناجز». User message edit-and-resend; regenerate; retry.
8. AI disclaimer visible (D1).

**2b — Workspace, attachments, workspace copy.**
9. Workspace screen per conversation: every item kind with «WI-n» badge; viewers for بحث قانوني
   (clickable `[n]` + reference cards + disclaimer), تحليل قانوني (preview), notes (create/edit),
   attachment (OCR text first, then «الملف الأصلي»), ملخص المحادثة, مراجع; action bar: copy (incl.
   Najiz + «المراجع»), 👍/👎, hide, delete.
10. Attachments from the phone: Files (PDF), Photos, Camera (HEIC → JPEG) with upload progress and
    cancel; send blocked until uploads finish; a gate-refused send keeps its chips.

**2c — Parity tail.** «قالب» pick, «مدونة» by link, مستوى التفصيل, writer-draft edit,
مشاركة / حفظ كمدونة, the full «… في ريحان» reader if R4 (b) was deferred.

## 2. Dependencies

- **Android dev build** ✅ done (`expo-dev-client`, `d431c10`, build `ee2513fb`). Phase 2 adds native
  modules (keyboard-controller, maybe enriched-markdown, FlashList) → **Expo Go stops being enough**;
  each new native module needs a new dev build.
- **iOS dev build** waits on the Apple org account (D-U-N-S). iOS sign-off for 2a may lag Android;
  the same JS ships via EAS Update.
- **Contracts move (Phase 1 C1) only partly done** — `frontend/lib/contracts/` has
  `app-config/auth/errors/legal/numerals`. messages/conversations/SSE/agents move **here** (§3 C).
  ⚠ line refs in `04` §C1 are stale; current refs below.
- No backend dependency (§5).
- ❓ **Owner:** D1 (disclaimer placement), R4 («في ريحان» exit), S3 (true server-side Stop, later).

## 3. Work items

### C — Contracts move + pure helpers (`frontend/lib/contracts/`)
All additive; `CONTRACTS_VERSION` stays 0. The web keeps its import paths via
`export type {…} from "@/lib/contracts"` in `frontend/types/index.ts` and one-line re-exports in
`lib/*`. The web rebuilds (expected).

- **C1 Types, copied verbatim** (current `frontend/types/index.ts` lines):
  - `agents.ts`: `AgentFamily`…`WorkspaceItemSubtype` (`:642-668`), `WorkspaceItem`,
    `WorkspaceItemListResponse` (`:670-703`), `UploadInitResponse` (`:424-433`).
  - `conversations.ts`: `:73-125`.
  - `messages.ts`: `:131-246` (incl. `run_active`) + `SendMessagePayload` (`:1687-1690`).
  - `sse.ts`: existing `:439-460` (MessageStart/Token/Duplicate), `:472-534` (QuotaExceeded/
    ParallelLimit), `:616-636` (Done), `:1545-1681` (agent + workspace + next_steps +
    templates_used). **New:** `SSEError {detail}`, `SSEHeartbeat {}`, `SSEAgentSelected
    {agent_family}` (payload at `backend/app/services/message_service.py:1090-1093`; web ignores it),
    `SSEEventMap` (name → payload) and `SSEEventName` = 22 names (21 web cases in
    `use-chat.ts:500-934` + `agent_selected`).
  - `references.ts`: `ReferenceDomain`…`WriterMetadataReferenceView` (`:735-1108`),
    `ReferenceUnlockReason`…`ReferenceSourceError` (`:1713-1801`).
- **C2 Pure helpers moved verbatim** (web re-exports from the old path):
  `markdown/plain-text.ts` + `markdown/images.ts` (Najiz copy), `markdown/streaming.ts`
  (`splitStreamingContent`), `AGENT_OUTPUT_DISCLAIMER_AR`
  (`components/workspace/AgentOutputDisclaimer.tsx:19-21`) → `legal.ts`.
- **C3 New pure helpers**, each with a test file (`npx tsx` / `node --test` 🔎 — whatever
  `frontend/` already uses):
  - `sse-parser.ts`: `createSSEParser(onEvent)` → `feed(chunk)` / `end()`. `\n` + `\r\n`, joins
    multi-line `data:`, dispatches on blank line, ignores `:` comments, keeps a partial line across
    chunks. Backend frame is always `event: X\ndata: <one-line JSON>\n\n`
    (`message_service.py:1566-1568`, `ensure_ascii=False`). Plus `parseSSEData<K>(name, json)` that
    never throws.
  - `utf8.ts`: incremental UTF-8 decoder — **fallback only** if
    `TextDecoder.decode(…,{stream:true})` is missing on Hermes 🔎. A split Arabic codepoint must
    never become `�`.
  - `next-steps.ts` / `templates-used.ts`: **zod-free** ports of `frontend/lib/next-steps.ts` /
    `templates-used.ts` (max 3 chips, unique kind, trimmed non-empty, http(s)-only sources,
    `undefined` ≠ `[]`). ✅ The web files become re-exports after an equivalence test on the same
    fixtures — one boundary parser for both apps.
  - `citations.ts`: `CITATION_RE` + `toAsciiDigits` (`lib/markdown/remark-citations.ts:47-70`),
    `splitCitations(text)`, `linkifyCitations(md)` (`[4،17]` → `[[4]](cite:4)[[17]](cite:17)`,
    skipping fenced/inline code).
  - `placeholder.ts`: `PLACEHOLDER_MAX_AGE_MS`, live cap, `placeholderPollMs(...)`
    (`hooks/use-messages.ts:17-62`), `isDeadPlaceholder(...)` (`MessageList.tsx:495-502`).
  - `reference-labels.ts`: `officialUrl(view)` + the «فتح {X} في ريحان» label out of
    `components/workspace/ReferencePanel.tsx:1540-1655` 🔎 purity.
  - `library-refusal.ts`: `parseRefusal` (`lib/library/full-content.ts:257-280`) +
    `refusalCardCopy` (`lib/library/gate-copy.ts:226`) 🔎 purity; any CTA pointing at web pricing
    must be overridable by the app (📏).
  - `references-copy.ts`: `appendReferencesForCopy` (ReferencePanel) 🔎 purity.
- **Stays on web / re-implemented in the app:** `use-chat.ts`, `chat-store.ts`,
  `use-messages.ts`, `rich-html.ts`, `remark-citations.ts`.
- ✔ **Acceptance:** web `tsc` + lint + build; mobile `tsc`; grep: no `@/`, `react`, `zod` under
  `contracts/`; helper tests green.

### X — Streaming transport (`mobile/src/api/stream.ts`, `src/chat/run-send.ts`)
**✅ `import { fetch } from "expo/fetch"`** (WinterCG, `response.body.getReader()`; Expo docs show
this exact use with `Accept: text/event-stream`). Import it explicitly (SDK 57 global behaviour 🔎).
Rejected: `react-native-sse` (EventSource on XHR; its **auto-reconnect re-POSTs** = the duplicate
trap in memory `project_resend_dedup`); XHR `onprogress` (O(n²) `responseText`, Android buffering);
RN built-in fetch (no body streaming).

- **X1 `openMessageStream(conversationId, payload, signal)`:** `appHeaders()` + `Accept:
  text/event-stream` + bearer. **401** = refused by `get_current_user` *before* any write
  (`backend/app/api/messages.py:51-64`) → one refresh + one re-POST is safe there, and only there.
  426 → update gate. HTML / `cf-mitigated` → `MSG_EDGE`. Other 4xx → Arabic `detail`. No 15 s total
  timeout; **idle watchdog aborts after 45 s without bytes** (heartbeat every 15 s,
  `message_service.py:1031-1038`).
- **X2 Decode loop:** `reader.read()` → `TextDecoder("utf-8").decode(v,{stream:true})` (fallback C3
  `utf8.ts`) → `createSSEParser` → typed `switch` over `SSEEventMap`.
- **X3 Send state machine** (port of `use-chat.ts:109-490`), per conversation, keyed by `sendId`
  (parallel conversations isolated):
  - Optimistic user row prepended to TanStack `["messages","list",id]` (web `:261-278`).
  - Composer chips released **only on `message_start`** (`:210-236, :514`).
  - **✅ Never auto-re-POST once the request went out.** Before `message_start`: network error / 5xx
    / idle → wait 1.5 s → refetch page 1 → if newest rows are our user text (same content,
    `created_at ≥ sendStart−5 s`) + an assistant placeholder → **adopt** (poll to completion); else
    mark failed with «إعادة المحاولة» (manual retry = new POST; server dedup still guards). After
    `message_start`: finish streaming, invalidate, poll (web Layer 2, `:437-448`).
  - `quota_exceeded` / `parallel_limit`: remove optimistic row, restore composer, notice
    (`:555-615`). `duplicate`: remove, refetch, show `detail` (`:535-554`).
  - `done`: flush, write final content + `artifact_ids` / `referenced_item_ids` + `next_steps` /
    `templates_used` into the cached row **before** clearing the stream (no flash, `:660-765`);
    invalidate lists (server sets the title).
  - `agent_question`: finish + refetch (`:822-842`). `error`: failed + banner (`:934-949`).
- **X4 Token pacing:** per-conversation ref buffer flushed to zustand every **≈80 ms** (≤12
  renders/s); only the streaming row subscribes; `done` flushes first.
- **X5 Stop** = `abort()` + invalidate (web `chat-store.ts:701-709`). ⚠ Server **detaches and keeps
  running** (`message_service.py:1516-1551`) — answer still lands via polling, run still bills. UX
  shows that honestly (bubble back to «ريحان يفكّر»). ❓ **S3** real server cancel = backend
  change, out of Phase 2.
- **X6 Polling / background:** `useMessages` `refetchInterval = placeholderPollMs(...)`, foreground
  only (`src/lib/query-client.ts` focusManager ↔ AppState). Authed routes are keyed per user+path
  60/min (`rate_limit.py:509-520`) → a 4 s poll = 15/min, CGNAT-safe. Backgrounded mid-answer: OS
  kills the socket, backend detaches (and schedules web push «إجابتك جاهزة»,
  `message_service.py:1381-1394`). On `AppState → active`: stream registered and no bytes >10 s →
  abort + recover (refetch → «ريحان يفكّر» while `run_active`) → poll. Deep-search progress is
  **not replayed** after resume (accepted for v1; ❓ attach endpoint §5 B3).
  **Phase 3 hand-off:** extend `push_service.schedule_turn_ready` to Expo tokens; payload =
  `conversation_id` only (PDPL); tap → `rayhan://c/{id}` (M1 route).
- ✔ **Acceptance:** unit tests (parser fixtures: split frames, split UTF-8 Arabic, CRLF, heartbeat,
  malformed JSON; state machine with a mocked stream). Device: airplane mode before/after
  `message_start` → no second user row (SQL); background 60 s during deep search → answer appears;
  Stop → answer arrives later; two conversations stream in parallel (max-plan test account).

### L — Conversation list (2a)
- **L1** `app/(app)/index.tsx` replaces the placeholder home; header «محادثة جديدة» + ⚙.
  `src/chat/use-conversations.ts`: index + search as `useInfiniteQuery`, offset 30
  (`frontend/hooks/use-conversations.ts:11,38-82`).
- **L2** Search: 300 ms debounce → `q` (server BM25), message matches show `snippet`; «المميّزة» adds
  `starred=true`.
- **L3** Row: `title_ar ?? «محادثة جديدة»`, relative time via `src/lib/format.ts` (Latin digits), ★,
  «يعمل» dot while that id streams/polls.
- **L4** Long-press menu (`@expo/ui` Menu; ❓ swipe actions too): تمييز (PATCH `{starred}`,
  optimistic), إعادة تسمية (sheet, PATCH `{title_ar}`), حذف (confirm, DELETE); demo: «إخفاء» 🔎
  DELETE on demo = per-user hide in `conversation_service.delete_conversation`.
- **L5** Pull-to-refresh, empty state (D1 disclaimer + CTA), offline = cached list + banner.

### M — Chat screen (2a)
- **M1** `app/(app)/c/[id].tsx` (deep-linkable for Phase 3) + `c/new` (lazy: first send →
  `POST /conversations` → `router.replace("/c/{id}")` → send; picking an attachment on `new` creates
  the conversation first, web `ChatInput.tsx:856-874`).
- **M2** ✅ **FlashList v2** (`npx expo install @shopify/flash-list` 🔎 SDK 57 version), **not
  inverted** (inverted + RTL + pagination breaks subtly); `maintainVisibleContentPosition:
  {startRenderingFromBottom, autoscrollToBottomThreshold}` 🔎 v2 API; `onStartReached` → older pages
  (`before` cursor, 30); «↓» pill. One item per message; settled rows memoized. Fallback: FlatList.
- **M3** User bubble (inline-end): long-press نسخ / النسخ لناجز / تعديل («حفظ وإرسال» = new send,
  `MessageBubble.tsx:239-258`); failed rows «فشل إرسال الرسالة» + «إعادة المحاولة».
- **M4** Assistant row (port `MessageBubble.tsx:450-660`): unframed markdown (K); «افتح التحليل» +
  referenced-item chips hidden in 2a, wired in 2b; `TemplatesUsedLine`; next-step chips (latest
  settled only, `:189-199`); `agent_question` callout; failed + retry; actions نسخ + «إعادة التوليد».
  Web 👍/👎 on bubbles is local-only (`:146, :235`) → ✅ skipped.
- **M5** Placeholder: `isDeadPlaceholder` → FailedResponseBubble, else «ريحان يفكّر» (port
  `TypingIndicator` / `ThinkingRow`, `MessageList.tsx:470-529, 638-653`).
- **M6** Deep-search progress: stage tracker (planning/searching/evaluating/aggregating/writing,
  `types :1559-1598`) from `agent_progress`, `status` log, sealed summary chip; isolated store slice.
- **M7** Composer: multiline, `textAlign:"right"` + `writingDirection:"rtl"` 🔎 Android; Enter =
  newline (web touch rule, `ChatInput.tsx:405-416`); send ↔ **Stop**; 10,000 chars
  (`ChatInput.tsx:88`); disabled at `max_parallel_runs` (`/usage`). ✅
  `react-native-keyboard-controller` (`KeyboardStickyView` + list inset).
- **M8** Notices: `quota_exceeded` → `message_ar` (or `detail` for `parallel_reserve`),
  `parallel_limit` → `detail`. 📏 no upgrade buttons / web links (Phase 4 adds the paywall).
- **M9** Header menu: rename / star / delete + «مساحة العمل (n)» (2b). Demo conversation: composer
  replaced by «هذه محادثة تجريبية للاطّلاع» + «ابدأ محادثة جديدة» (`hooks/use-demo-conversation.ts`).

### K — Markdown, Arabic RTL + tables (2a)
Web: `react-markdown` + `remark-gfm` + `remarkCitations` + `rehype-highlight`
(`components/chat/MarkdownRenderer.tsx:374-412`), prefix/tail streaming (`StreamingText.tsx`).
None of it runs on RN.
- **K0 Spike (1 day, ❓→✅ by result)** behind one adapter `src/components/markdown/Markdown.tsx`
  `{content, streaming?, onCitationPress?, variant:"chat"|"reader"}`:
  - **(A) `react-native-enriched-markdown`** (Software Mansion, native md4c, GFM; docs claim full RTL
    incl. tables, selection/copy, `onLinkPress`, regex `linkVariants`, streaming). New arch + dev
    build only. 🔎 RN 0.86 compatibility, custom font per weight, Android `writingDirection`, dark
    styles, perf at 30k chars.
  - **(B) `marked` lexer + own renderer** (~300 lines): nested `AppText` (one family per weight),
    RTL list markers, `borderStartWidth` blockquotes, tables = horizontal `ScrollView` of rows (min
    column 120, start-aligned), code LTR monospace, citations as pressable inline `Text`. Works in
    Expo Go, full control.
  - **Recommendation:** (A) if it passes the Arabic fixtures (deep-search answer, 6-column table,
    nested lists, mixed Latin, `[n]` in a table cell) in both themes on Android + iOS; else (B).
    Rejected: `react-native-marked` / `-markdown-display` (tables via
    `react-native-reanimated-table`, known RTL problems).
- **K1** Preprocess (contracts): `stripMarkdownImages` → `linkifyCitations`.
- **K2** `cite:N` links → pill, LTR-isolated `[N]` (web `CitationMarker.tsx:20-45`), pressable only
  when the message has an `agent_search` artifact.
- **K3** Links: http(s) only via `expo-web-browser`; everything else dropped.
- **K4** Streaming: `splitStreamingContent`; prefix memoized, tail re-parsed per 80 ms flush.

### R — Citations → native source reader (2a)
- **R1** Target = first `agent_search` id in `artifact_ids` (rule of `MessageList.tsx:568-570`);
  references from `GET /workspace/{item}/references` (free, cached).
- **R2** `app/(app)/source/[itemId]/[n].tsx`: card from the free list immediately, then
  `GET /workspace/{item}/references/{n}/source` — **metered, fired on open = the user gesture**
  (web `ReferencePanel.tsx:201-217, 274-290`); never prefetched. Plain fetch, not `apiFetch` (a 401
  shows «انتهت جلستك» + login here, never ejects mid-read; web `lib/api.ts:951-1031`). 402 →
  `parseRefusal` card (📏 no web purchase link); 404/429/5xx → Arabic per `ReferenceSourceError`.
- **R3** `SourceView` variants (`types :769-977`): `chunk` (segments → markdown; tables via `md`,
  never `html`; images via `expo-image` + caption), `case`, `gov_service`, `circular` (bodies up to
  168k chars → block-chunked FlashList), `article_full`, `regulation_summary` («ملخص»), legacy;
  «الإحالات», «تم فتح …» line + balance chip, «نسخ المحتوى».
- **R4** Exits: «فتح المصدر الرسمي» = `officialUrl(view)` → `expo-web-browser`. «فتح ال… في ريحان» =
  `library_url` (null → no button, never guessed). ❓ **owner:**
  - (a) open `https://rayhanai.com{library_url}` in the in-app browser — no web session there → the
    reader hits the **anonymous gate**. Not recommended.
  - **(b) recommended:** native full-document screen via authed
    `GET /library/full/{content_type}/{key}` (🔎 map `library_url` → `(type,key)`; check it is free
    once unlocked by the reveal).
  - (c) v1: share/copy the URL only.
  All options keep «نسخ الرابط / مشاركة». If (b) slips, 2a ships (c) and (b) moves to 2c.

### D — AI disclaimer
- **D1** ✅ New-chat empty state shows `AGENT_OUTPUT_DISCLAIMER_AR` in full; ✅ every agent-output
  viewer shows it (2b). ❓ Also a persistent one-line footer under the composer («ريحان مساعد
  بالذكاء الاصطناعي وقد يخطئ — تحقق من المصادر»)? Web has none in chat; recommended for store review.

### Y — Copy (2a bubbles, 2b workspace)
- **Y1** `src/components/CopyMenu.tsx`: «نسخ» → `Clipboard.setStringAsync(markdown)`; «النسخ لناجز» →
  `markdownToPlainText` (C2); haptic + «تم النسخ».
- **Y2** While streaming copy takes live content (`MessageBubble.tsx:283-285`); agent outputs append
  «المراجع» (C3 `references-copy`).
- **Y3** ❓ Rich-HTML «نسخ» deferred (`expo-clipboard` `StringFormat.HTML` exists).

### W — Workspace (2b)
- **W1** `c/[id]/workspace.tsx` (grouped in the web's `KIND_ORDER`: المسودات، نتائج البحث، الملاحظات،
  المرفقات، المراجع، ملخص المحادثة; «WI-n» badge; hidden toggle) + `workspace/[itemId].tsx`.
- **W2** Viewers: `agent_search` (markdown + `[n]` → R2, reference cards with «عرض المصدر» + both
  exits, disclaimer); `agent_writing` (preview + disclaimer, lock badge); `note` (create `POST
  …/workspace/notes`, edit PATCH); `attachment` (`content_md` OCR first, «الملف الأصلي» via
  `GET /workspace/{id}/file`, `ocr_status` banner); `convo_context` / `references` view only.
- **W3** Action bar: نسخ menu, 👍/👎 (`PATCH /workspace/{id}/feedback`, hidden on demo). مشاركة / حفظ
  كمدونة → 2c. ❌ إخفاء/إظهار and حذف are **removed** (owner, 2026-10-20): the web has no such
  buttons and the owner never approved them. `manageSpec` returns all-false; the code stays dormant.
  Rule: the app matches the web; nothing new that touches agents, agent context or the backend
  without the owner's explicit OK.
- **W4** `workspace_item_created/updated` invalidate; ✅ **no auto-open on the phone**
  (`use-chat.ts:791-801`); «افتح التحليل» / «راجع البطاقة السابقة» chips open W2.

### F — Attachments from the phone (2b)
- **F1** `expo-document-picker` (PDF + images) and `expo-image-picker` (library + camera);
  **HEIC/WebP → JPEG** via `expo-image-manipulator` (backend allows only pdf/png/jpeg, magic-byte
  check, `workspace_service.py:39,640-646`). ≤5 files, ≤50 MB (`ChatInput.tsx:89-91, 838-851`).
- **F2** `page_count`: ✅ byte scan for `/Type /Page` (not `/Pages`), fallback 1 — estimate only;
  post-OCR settle bills the truth (memory `project_ocr_gate_page_count`).
- **F3** Upload = web's 3 steps: `POST …/workspace/attachments/init` → TUS to Supabase `upload_url`
  (6 MB chunks, `x-upsert`, bearer = our Supabase JWT 🔎) → `POST
  /workspace/attachments/{id}/finalize`; cancel → `…/cancel`. **F0 spike:** `tus-js-client` on RN 🔎
  vs. a ~120-line own TUS PATCH loop (`expo-file-system` byte ranges + `expo/fetch`,
  `Tus-Resumable: 1.0.0`, `Upload-Offset`). ⚠ not the deprecated multipart `/attachments/upload`.
- **F4** Composer chips with progress/cancel/failed; send blocked while uploading; chips released on
  `message_start`; background mid-upload → resume from offset on `active`.

### E — Parity tail (2c)
E1 «قالب» (directive line `استخدم القالب: «…»`, `ChatInput.tsx:350-361`) · E2 «مدونة» by link
(`POST /conversations/{id}/blog-items`) · E3 مستوى التفصيل · E4 writer-draft edit (lock-aware) ·
E5 مشاركة / حفظ كمدونة · E6 R4 (b) if deferred.

## 4. File map (new, mobile)
```
app/(app)/index.tsx                     conversation list (replaces placeholder)
app/(app)/c/[id].tsx · c/new            chat
app/(app)/c/[id]/workspace.tsx · workspace/[itemId].tsx            (2b)
app/(app)/source/[itemId]/[n].tsx       reader
src/api/{stream,conversations,messages,workspace,references,uploads}.ts
src/chat/{run-send,stream-store,use-messages,use-conversations,keys}.ts
src/components/markdown/{Markdown,adapters/*}.tsx · CopyMenu.tsx
src/components/chat/{MessageRow,UserBubble,AssistantBody,Composer,DeepSearchProgress,
  NextStepChips,TemplatesUsedLine,AgentQuestion,FailedBubble,Thinking,NoticeBanner}.tsx
src/components/source/{SourceReader,variants/*,RefusalCard,Exits}.tsx
```
Packages (`npx expo install`): `@shopify/flash-list`, `react-native-keyboard-controller`,
`expo-clipboard`, `expo-haptics`, K0 winner (+`marked`). 2b: `expo-document-picker`,
`expo-image-picker`, `expo-image-manipulator` (+`tus-js-client`?). `expo-doctor` clean.

## 5. Backend changes
**None required** — all routes exist (conversations CRUD + `q`/`starred`
`backend/app/api/conversations.py:38-176`; messages + SSE; workspace / references / source / file /
attachments `workspace.py:146-1004`; blog-items, templates, preferences).

| # | Optional, additive | Why |
|---|---|---|
| B1 | Heartbeat right after `message_start` | First byte after a slow start; app watchdog 45 s makes it optional |
| B2 | `classify_client` → `rayhan_app` | With analytics (`04` §5) |
| B3 ❓ | `GET /conversations/{id}/stream` attach | Restore tokens/progress after background; v1 polls |
| B4 ❓ (S3) | True server-side Stop | Stop = detach today, run keeps billing — product decision for web + app |
| B5 | Expo tokens in `push_service.schedule_turn_ready` | Phase 3 hand-off |

## 6. Not in Phase 2
Push (Phase 3; only the deep-link route is prepared) · paywall (Phase 4) · settings scope (Phase 5)
· offline cache ❓ · share-into ❓ · library browsing (v2) · rich-HTML copy (Y3) · Cases.

## 7. Test plan
- **Contracts:** web tsc/lint/build + mobile tsc; helper tests (SSE parser fixtures, next-steps /
  templates equivalence vs old zod parsers, `linkifyCitations` incl. Arabic-Indic digits / `،` /
  code / table cell, `markdownToPlainText`, `placeholderPollMs` time table).
- **Mobile unit:** X3 state machine with a fake `ReadableStream`: every event order incl.
  `agent_progress(done)` before/after `done`, `agent_question` then `done`, duplicate, quota,
  error, drop before/after `message_start`.
- **Device** (`@mobile-qa`, Android emulator then physical iPhone): exit criteria 1–10; RTL/dark
  screenshots (list, chat, 6-column table, reader); deep-search ≥4k words without jank (JS FPS ≥ 50
  🔎); keyboard (composer above keys, iOS swipe-dismiss).
- **No-duplicate SQL** after airplane tests (user rows per send, account `enterprise@rayhanai.com`);
  Logfire: no `duplicate_send_rejected` caused by the app.
- **Metering:** opening the reader charges once; reopening = `already_unlocked`; scrolling the list
  charges nothing (`library_unlocks`).
- **Uploads:** 30-page PDF, iPhone HEIC, 49 MB, cancel mid-upload, background mid-upload; OCR text in W2.

## 8. Risks / unknowns
- 🔎 `expo/fetch` streaming on SDK 57 Hermes (`getReader`, `AbortSignal`, `TextDecoder` stream);
  test against prod through Cloudflare (`Cache-Control: no-transform`, `messages.py:81`); carrier
  proxy buffering.
- 🔎 K0: enriched-markdown on RN 0.86, custom fonts, citation links; fallback (B) ≈ +3 days.
- Expo Go loses chat (dev build required); iOS blocked on the Apple account.
- Stop does not stop billing (B4 ❓) — UX must not imply otherwise.
- After a background resume only «ريحان يفكّر» shows for 3–7 min of deep search; Phase 3 push helps.
- Pre-`message_start` adopt heuristic can misjudge two identical rapid sends (server dedup bounds it).
- 🔎 TUS on RN; token expiry mid-upload (re-init on 401).
- 🔎 Contracts purity (ReferencePanel labels, refusal copy, `appendReferencesForCopy`) — if impure,
  re-implement in the app with a shared fixture test.
- Payload drift: check every `SSEEventName` against `message_service.py` emit sites (incl.
  `agent_selected`); keep the integration-lead §6 check.
- Very long circular bodies (168k chars) need chunked rendering.

## 9. Order of execution
1. ✅ Android dev build. **K0** + **F0** spikes in parallel with **C1–C3** (contracts + tests; ship
   the web build).
2. **X1–X6** transport + state machine, unit-tested before any UI.
3. **L** → **M** → **K1–K4** → **R1–R3** (R4 per owner) → **D1**, **Y1–Y2** → 2a device walk →
   EAS Update to preview.
4. **2b:** W1–W4 → F1–F4 → Y2 → device walk → preview.
5. **2c:** E1–E6 → update `mobile/CLAUDE.md` + a status block here.
