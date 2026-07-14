# Edu Popups («تلميحات تعليمية») — Design

**Status:** DESIGN — not built. Companion to the «اتعرف على ريحان» tour (see `frontend/components/onboarding/`).

## Concept

The onboarding tour teaches everything once, up-front, modally. Edu popups are the opposite: **one small lesson, at the exact moment it becomes relevant, non-blocking**. Example: the first time an agent publishes a workspace item, a small card slides in: "هذه أول بطاقة في مساحة العمل — كل الوكلاء يقدرون يرجعون لها".

Form: **NOT a modal Dialog** — a compact dismissible card (~320px) floating at the bottom-start of the main chat pane, above the composer. It never traps focus, never blocks typing, and disappears on dismiss. The tour stays the only modal in the app.

Each card = icon + title + 2–3 lines + optional action button (deep link, e.g. "افتح مساحة العمل") + «فهمت» dismiss.

## Architecture (mirrors the onboarding framework)

```
frontend/components/edu/
  edu-topics.tsx        ← the registry: ALL topics (id, content, trigger docs, priority, optional action)
  EduPopupCard.tsx      ← presentational card (RTL, theme tokens, slide-in animation)
  EduPopupHost.tsx      ← renders the active topic; mounted in ChatLayoutClient next to <OnboardingDialog/>
frontend/stores/
  edu-store.ts          ← the ENGINE: seen-state, gating rules, triggerEdu(), dismiss()
```

Integration sites call **one line**: `triggerEdu("topic_id")`. All gating logic lives in the store — call sites stay dumb and can over-fire safely (the engine dedupes/suppresses).

## Persistence

Rides the existing `/preferences` JSONB PATCH (zero backend changes), same as `onboarding_seen`.

**Trap — shallow merge:** the `merge_preferences` RPC merges top-level keys only. A nested `edu_seen: {a: true, b: true}` object would be *replaced wholesale* on every PATCH → two tabs clobber each other. **Use flat keys instead**: `edu_citations: true`, `edu_workspace: true`, … (prefix `edu_`). Each PATCH then touches exactly one key — merge-safe by construction.

Impression counts (see anti-nag) are client-only → `localStorage`, not preferences.

## Engine logic

### State (edu-store)
- `seen: Record<topicId, boolean>` — hydrated from preferences (reuse `preferences-store.hydrate()` payload; fail-closed: hydration failure ⇒ treat ALL as seen, never nag on API errors — same principle as the tour)
- `activeTopic: topicId | null` — at most ONE popup at a time
- `shownThisSession: topicId[]` — session cap tracking
- `lastShownAt: number | null` — spacing

### triggerEdu(topicId) — the gate chain (all must pass)
1. topic exists in registry
2. `seen[topicId] !== true`
3. `activeTopic === null` (one at a time; a losing trigger is DROPPED, not queued — if the moment passed, the popup shouldn't ambush later. It re-fires naturally next time the condition occurs)
4. onboarding tour not open (`useOnboardingStore.isOpen === false`)
5. preferences hydrated (else drop)
6. session cap: `shownThisSession.length < 2`
7. spacing: ≥ 3 minutes since `lastShownAt`
8. per-topic impressions < 3 (localStorage counter; at 3 ignored shows, auto-mark seen server-side — a popup the user keeps ignoring is answered)
9. optional per-topic `delayMs` (e.g. show 1.5s after the trigger so it doesn't flash mid-action)

### dismiss(topicId, reason)
- reasons: `"got_it"` («فهمت»), `"action"` (clicked the deep link), `"close"` (X)
- ALL reasons ⇒ `seen[topicId] = true` locally + PATCH `edu_<topicId>: true` (fire-and-forget, no rollback — worst case it shows once more next session)
- Mere *render* does NOT mark seen (reload mid-popup ⇒ it may show again; impressions cap bounds this)

### Suppression extras
- Never show while any Radix dialog is open (simplest proxy: tour store + a `document.querySelector('[role=dialog]')` check at show-time, or just the known dialog stores)
- `reset()` on logout (mirror preferences-store reset)

## Topic catalog v1 (each row = registry entry + one-line trigger call)

| id | Moment (trigger site) | Teaches | Action button |
|---|---|---|---|
| `workspace` | 1st `workspace_item_created` SSE — `use-chat.ts:556` | ما هي المخرجات، وين تلاقيها | افتح مساحة العمل (`toggleWorkspace`) |
| `citations` | 1st `done` whose message has references / 1st `referenced_existing_item` — `use-chat.ts:620` | أرقام [ن] قابلة للنقر → المصدر | — |
| `deep_search` | 1st `agent_progress` — `use-chat.ts:438` | البحث العميق يأخذ وقت، مراحله ظاهرة | — |
| `attachments_ocr` | 1st upload reaches `completed` — ChatInput/`use-resumable-upload` | الملف يُستخرج نصه ويصير بطاقة يقرؤها كل الوكلاء | — |
| `save_memo` | user's Nth message (e.g. 5th) in a convo with 0 memo items | «احفظ هذه المعلومة» تثبت معلومة للجلسة كلها | يعبّي القالب في صندوق الكتابة (`injectComposerText`) |
| `privacy_masking` | 1st send while وضع السرية ON (masking active) | الأرقام/الإيميلات تُقنّع قبل الوصول لأي نموذج خارجي | إعدادات المحادثة |
| `templates` | 1st `template_save_offer` — `use-chat.ts:636`, or 1st «+» menu open | قوالبي: احفظ واستخدم قوالبك | — |
| `agent_question` | 1st `agent_question` — `use-chat.ts:576` | ريحان يسأل للتوضيح — الرد يكمل نفس المهمة | — |

Catalog is data — adding a topic = one registry entry + one `triggerEdu()` line at the site. No engine changes.

## Priority when triggers race
Single-slot + drop-losers makes races mostly moot. If two fire in the same tick (e.g. `workspace` + `citations` on the same `done`), registry order = priority: first passing gate wins, other drops.

## Relationship to the tour
- Tour open ⇒ all edu suppressed (gate 4)
- Same-session after tour closes ⇒ edu allowed (the first session is when `workspace`/`citations` are most valuable)
- Topics deliberately overlap the tour's content — the tour is a skim; edu is reinforcement at the moment of use

## Build order (when approved)
1. `edu-store.ts` + preferences flat-key read/write (extend `preferences-store` hydrate to expose the raw prefs blob, or hydrate edu keys in edu-store from the same GET)
2. `EduPopupCard.tsx` + `EduPopupHost.tsx` + mount in `ChatLayoutClient`
3. Registry with 2 pilot topics: `workspace` + `citations` (both trigger from existing SSE cases — smallest integration)
4. Verify loop (Playwright, test account), then expand catalog
