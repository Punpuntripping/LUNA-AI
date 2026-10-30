# WI preview lock — free users see ~40% of each new agent WI behind an upgrade wall

Status: PLANNED 2026-10-30 · Owner decisions locked 2026-10-30 · Migration `198_wi_preview_lock.sql`
(NOT written; 195 = client revoke APPLIED, 196 = renewal grace, 197 = deep search gate)

## 0. Locked decisions (owner, 2026-10-30)

1. **100% of free users** (effective plan = free, incl. paid plans that expired back to free). No A/B split.
2. **Preview + upgrade wall**: title + ~40% of the body readable, the rest replaced by an Arabic
   upgrade wall. The chat answer in the conversation stays fully visible.
3. **Every lockable WI is locked, including the first.**
4. **Only WIs created after launch** — everything older stays open.
5. **Router sees only the preview too** (option 1), with an explicit marker that the lock is
   DELIBERATE — never an error. Planner / writer keep full content (their output is a new WI,
   itself locked). No prompt-only or output-filter approach.

## 1. Blockers — must land before the lock means anything

1. ✅ **DONE 2026-10-30 — migration 195 applied; PostgREST now answers 42501 on all three tables.** **Direct PostgREST access (verified live 2026-10-30).** `anon` + `authenticated` hold ALL
   privileges on `workspace_items`, `workspace_item_references`, `retrieval_artifacts`; RLS lets a
   user SELECT / INSERT / UPDATE / DELETE their OWN rows (`artifacts_select` / `artifacts_update`
   / …). With the anon key from the web bundle a free user reads full `content_md` or rewrites
   `created_at` / `kind` / `created_by` to dodge the lock. No web/mobile code reads these tables
   directly (grep for `.from(` / `.rpc(` empty; backend is service-role, `backend/app/deps.py:123`)
   → REVOKE from anon + authenticated (keep RLS on, rule 6). ⚠ Re-grep at build time, and check
   views / RPCs over these tables (memory: views survive base revokes).
2. **Share copies the full body public.** `POST /workspace/{id}/share` (`backend/app/api/blog.py:501`,
   `:550-561`) → `/public/blog/{token}` (`:134`); «حفظ كمدونة» uses it (`SaveAsBlogDialog.tsx:90`,
   mobile `PublishSheet.tsx:145`, `:286`). Locked item → 402 + Arabic message.
3. **Editing a truncated body deletes the hidden 60%.** `note` / `agent_writing` are editable
   (`workspace_service.py:54`); web `MarkdownDocEditor.tsx:98,:293` autosaves the whole textarea after
   800 ms, also `WorkspaceItemViewer.tsx:69-99`, mobile `use-output-actions.ts:60-108`,
   `use-note-draft.ts:35-81`. Server: `update_workspace_item` (`workspace_service.py:362-369`)
   refuses a `content_md` change on a locked item (402, Arabic), title-only edits allowed. Clients:
   editor read-only when `preview.locked`.

## 2. What is lockable (live counts 2026-10-30)

- **Lock:** `agent_search` created by the agent (979 deep search, avg 5,061 chars; 22 simple
  search) · `agent_writing` (~105, except subtype `memory_file`).
- **Never lock:** user notes · memos (`note`/`memo`, the user's own words) · attachments + OCR ·
  `references` (library pages — already behind the library gate) · `convo_context` (hidden) ·
  `agent_search` created by the USER (blog imports, public anyway) · demo items.
- **Default exempt (owner to confirm, §9):** `statute_package` (verbatim law text from
  `articles_v2`) and the writer `summary` subtype built from the user's own attachment.

## 3. "Is this WI locked?" — decided at READ time

`locked = flag on AND kind lockable AND created_at >= WI_PREVIEW_LOCK_FROM AND NOT
created_while_paid AND NOT user_is_paid_now AND NOT demo`.

- `user_is_paid_now`: `shared/quota.library_state(...).is_paid` (same as the library), resolved
  once per request, ~60 s cache. Lookup fails → treat as free (lock), like the library.
- `created_while_paid`: new column, stamped by a BEFORE INSERT trigger from `user_subscriptions`
  (NOT the heavy `get_user_quota_state`) — no publisher code changes. Upgrading unlocks at once; a
  plan that expires leaves items made while paid open (default, §9).
- `created_at` is NOT NULL / default `now()` / no rewriting trigger → reliable once §1.1 is done.
- `WI_PREVIEW_LOCK_FROM` = the moment the flag is turned on (config, not a migration constant).

## 4. Truncation — one shared function

- `shared/` helper used by the backend AND `unfold_workspace_item`, so UI and router see
  byte-identical previews. Budget = 40% of the WHOLE body by chars, never exceeded. Reuse
  `GateBudget` / `free_budget` (`library_service.py:1222-1247`); do NOT use `gate_decision` (its
  min-withheld rule opens short items — contradicts "every WI is locked").
- Cut at the last top-level markdown block within budget; if that leaves < ~30%, cut inside the next
  block: paragraph → last sentence end (. ؟ ! ؛), list → item boundary, table → row boundary
  (keep header + separator). Never split a `[n]` marker; drop a trailing heading.
- Response: truncated `content_md` + `preview {locked, shown_chars, total_chars, hidden_ref_count}`
  — `backend/app/models/responses.py:315` + `frontend/lib/contracts/agents.ts:52`.
- Old app builds get truncated text with no wall → server appends one fixed Arabic markdown line
  that new clients strip.

## 5. Backend surfaces

- One choke point: `_to_response` (`backend/app/api/workspace.py:108-138`) — conversation + case
  lists (`:146-191`, which today ship full `content_md` for every item), single get (`:199-214`),
  PATCH / visibility / feedback responses (`:563-647`).
- References list (`:217-251`) + source reveal (`:376-560`): per §9 decision.
- Share 402 (§1.2), PATCH 402 (§1.3).
- Not leaks: export (`backend/app/api/export.py:143` renders client-sent markdown), copy
  (client-side), live-run SSE events (ids/titles only; clients refetch via the get endpoint), push
  bodies (fixed strings), message attachments (title + kind).

## 6. Router — deliberate lock (owner requirement)

- Lock policy lives on `RouterDeps` ONLY (`agents/router/router.py:126-168`), filled in `run_router`
  (`:772`) from paid/free resolved once in `_route`. `unfold_workspace_item`
  (`agents/tool_repository/unfold_workspace_item.py:790-815`) applies it only when the field exists
  → planner / writer deps keep full content. `_fetch_item` (`:291-312`) selects kind, created_by,
  created_at, metadata, `created_while_paid`.
- No-summary fallback (`router/context.py:59-64`, `:346-373`) uses the preview for locked items.
- Tool result wraps the preview in markers (router prompts are English; system tags use 〔[نظام] …〕,
  `router.py:378-382`):
  - Header: `〔[نظام] PLAN LOCK — DELIBERATE, NOT AN ERROR. WI-{n} is intentionally
    preview-locked on this user's free plan. You see the first {pct}% ({shown} of {total}
    characters), exactly what the user sees. The rest exists and is intact; it is withheld on
    purpose.〕`
  - Footer: `〔[نظام] END OF VISIBLE PART — {100-pct}% of WI-{n} is locked for the free plan.
    Answer only from the text above; never guess, reconstruct or summarise the hidden part. If asked
    for it, say plainly that the full text is on the paid plans and offer upgrading, e.g. «بقية هذا
    المستند متاحة في الباقات المدفوعة — يمكنك الترقية لعرضه كاملاً». Never apologise for a technical
    problem. Do not unfold WI-{n} again, do not dispatch a search or draft to regenerate it, and do
    not ask the user to paste it.〕`
  - No URL in the marker (iOS rules).
- One STATIC rule in `SYSTEM_PROMPT` after `router.py:368` explaining the marker; soften "full
  content" at `:239`, `:363`. Per-user lock state ONLY in the tool result — never in the cached
  prefix or the tool docstring (`:753-789`). Edit the `.py`, not prompts.md (reference only).
- Quality cost (accepted): the router cannot quote conclusions in the hidden 60%.
- Residual leak, ACCEPTED by the owner (§9.5): router context keeps WI summaries averaging 36%
  (search) / 28% (writing) of the body (`router/context.py:274-343`).

## 7. UI

- **Web:** wall after `AgentSearchViewer.tsx:117`, in `NoteEditor.tsx:292-318` (read-only; hide
  share / save-as-blog / export `:308`, `:317`) and `WorkspaceItemViewer.tsx:218`. New
  `WiPreviewWall` = the fade from `FullContentGate.tsx` + the «عرض الباقات» flow / campaign copy from
  `QuotaUpgradeDialog.tsx` (neither fits as-is). Dispatcher: `WorkspacePane.tsx:196-220`.
- **Mobile (mirror web):** `ArtifactBody.tsx:148,:201,:226`, `NoteViewer.tsx:62,:90`,
  `TextItemViewer.tsx:25`, `PublishSheet`, `export-file.ts:48`. `PaywallReason` `"wi_preview"`
  (`frontend/lib/contracts/payments.ts:79`). **App Store rule:** upgrade button only when
  `useIapEntryVisible()` (`mobile/src/purchases/entry.ts:12`); otherwise wall text, no button.

## 8. Analytics (does it drive upgrades?)

- Web: `gate_view` (IntersectionObserver, `useGateImpression`) + `gate_cta_click` with
  `gate_kind: "wi_preview"`, `cta: "upgrade"` — extend `GateKind` (`events.ts:131`), `GateCta`,
  `GATE_KINDS`, and the server allowlist (`analytics_service.py:129` — unlisted names are dropped).
- Mobile has no analytics client → server-side `wi_preview_served` (first serve per user × item) as
  the cross-platform count of free users who met the wall.
- `source=wi_preview` through to checkout for attribution.
- No control group (decision 1) → read it as a before/after on upgrade clicks + checkouts, not as
  a measured lift.

## 9. Decisions (owner, 2026-10-30)

1. ✅ `statute_package` + writer `summary` of the user's own attachment: **exempt**.
2. Items created while paid, after the plan expires: **stay open** (default, not contested).
3. ✅ Citation list on a locked WI: **only sources cited in the visible part + a count of the rest**
   («وN مصادر أخرى في الجزء المقفل», Latin digits). Deliberately overrides
   `access_tiers_gating_DECISIONS.md` §1.3 ("never gated") for locked WIs only — note it there. Same
   rule for the router's source list in `unfold`.
4. ✅ Share / save-as-blog / export on locked items: **all blocked** (copy of the visible part stays).
5. ✅ Router summaries (28–36% digest of the full body): **kept in full** — owner accepts the
   residual leak for better follow-up answers. Do NOT shorten `router/context.py:274-343` summaries.

## 10. Tests & copy

- Update: `backend/tests/test_wave_8c_workspace_endpoints.py`, `test_workspace_conversation_ownership.py`,
  `test_analytics_events.py`, `agents/tool_repository/tests/test_unfold_workspace_item.py`,
  `agents/router/tests/test_agent.py`, mobile `workspace-model.test.ts`, `action-bar.test.ts`.
- New: shown/total ∈ [0.30, 0.40] across real-shaped bodies (judgment-withholding test style);
  402 on PATCH content; 402 on share; reference filter; unfold markers present only for router deps
  + locked items; planner/writer still get full content; upgrade → unlocked on next read.
- Copy that becomes false: `product_docs` `pricing`, `usage_limits`, `workspace`, `guide`,
  `how_it_works` (the router reads them via `open_rayhan_page` — stale rows make it contradict the
  wall); `UsageLimitsView.tsx:154-155`, `:356-360`; `frontend/app/learn/workspace/page.tsx`;
  `frontend/lib/pricing.ts:127-166`; `mobile/src/purchases/catalog.ts`.

## 11. Release order

1. Migration 198: `created_while_paid` + trigger (the §1.1 REVOKE already shipped as 195).
2. Backend + agents deploy with the flag OFF (`WI_PREVIEW_LOCK_ENABLED=false`).
3. Web deploy + mobile OTA — both before the flag goes on.
4. `product_docs` rows updated.
5. Flag ON; set `WI_PREVIEW_LOCK_FROM` to that moment.

No ISR purge (WI views are authed-only). Risks: the paid lookup on every list call (cache it), the
demo tour (exempt demo items), old app builds (fixed Arabic line, §4).
