# «سلسلة تعلّم ريحان» — Drip Education Series

**Status:** Phases **A + B + C + D BUILT and BROWSER-VALIDATED** 2026-08-16 against a
local dev stack. Not deployed. Phase A (onboarding retiming) is built but was NOT
exercised in the browser — the test account is already past both of its triggers.

### Validation — 2026-08-16, local stack, `test@luna-legal.dev`

| Check | Result |
|---|---|
| Settings popover shows «المكتبة القانونية», «اكتشف ريحان ›»; «جولة المخرجات» gone | PASS |
| Flyout opens `side=left`, on-screen, 8 lessons + جولة التعريف + المزيد | PASS |
| Lesson card renders RTL bottom-end, z-40, 320px, above composer | PASS |
| Live bar shows REAL data — «نقاطك هذا الشهر ٠٫١ / ٥ نقطة» | PASS |
| Binding-bar logic picks `points.monthly` for an expired→free account | PASS |
| Action button opens `UsageLimitsDialog` from the `SidebarDialogs` mount | PASS |
| Card and dialog agree (both report the free fallback) | PASS |
| Dismissal persists: `edu_usage_limits:true` + `edu_last_shown_at` written | PASS |
| Flat keys merge-safe — `detail_level`/`onboarding_seen`/`tour_*` all survive | PASS |
| **Auto-delivery: seeded turns=7, ONE real message → turn 8 → card fired** | **PASS** |
| Delivered `templates` — the next UNSEEN lesson, not lesson 1 again | PASS |
| `edu_turns` incremented 7→8 on the `done` event | PASS |
| Render ≠ seen: `edu_templates` still null while its card was on screen | PASS |
| «حدود الاستخدام» row works on `/templates/mine` (the SidebarPageShell fix) | PASS |

**ONE REAL BUG FOUND AND FIXED.** `showLesson()` gated on `aModalIsOpen()`, and Radix
`PopoverContent` renders with `role="dialog"` — so the settings popover the lesson is
launched FROM tripped the check. `closeMenus()` is a React state update, so the popover
was still in the DOM when `showLesson` ran synchronously after it. Every menu click was
silently swallowed: popover closed, card never appeared. The in-code comment asserting
"a Popover is not a dialog, so it does not trip this" was simply wrong. The gate is now
removed from the manual path — it exists to stop the AUTOMATIC series from firing under
a surface the user opened, which is the opposite of an explicit click.

**Env traps hit while validating** (both already in memory, both cost time):
- `npm run build` followed by `next dev` corrupts `.next` → every chunk 404s →
  `ChunkLoadError`. Fix: kill dev, `Remove-Item -Recurse -Force frontend\.next`, relaunch.
- Browsing `127.0.0.1:3000` fails CORS against a backend that allows `localhost:3000`.
  They are different origins. Use `localhost:3000`.

**Still NOT validated:** Phase A's two triggers (profession-alone at signup; full tour on
first render after `plan_id` turns paid) — both need a fresh/unpaid account. The
`privacy_masking` lesson's action and the five learn-more links were not clicked.
**Supersedes the trigger model in** `.claude/plans/edu_popups.md` (that design's card,
store, and persistence survive; its event-based triggers do not — see §2).

### Build log — 2026-08-16

**New:** `stores/edu-store.ts` (engine) · `stores/usage-dialog-store.ts` ·
`components/edu/edu-syllabus.tsx` · `components/edu/EduLessonCard.tsx` ·
`components/edu/EduLessonHost.tsx` · `components/edu/slots/UsageBarSlot.tsx`

**Modified:** `types/index.ts` (edu keys) · `stores/preferences-store.ts` (hydrate +
reset hookup) · `hooks/use-chat.ts` (`bumpTurn()` on `done`) ·
`components/chat/ChatLayoutClient.tsx` (two mounts) ·
`components/sidebar/SidebarFooter.tsx` (usage dialog → store)

**One departure from §3.** `UsageLimitsDialog` was mounted inside `SidebarFooter`. Below
`md` the sidebar body renders inside a Radix `Sheet`, which **unmounts its children when
closed** — so with the drawer shut the dialog was not in the tree and the lesson's action
button had nothing to open. It now mounts once in `ChatLayoutClient` and is driven by
`usage-dialog-store`; the settings row calls `open()`. Any future caller gets it free.

**Two smaller deviations, both deliberate:**
- The card sits at bottom-**end** (left in RTL), not bottom-start as §6 sketched — start
  is where the always-mounted `w-64` sidebar lives.
- Entry motion reuses the existing `.animate-fab-in` utility from `globals.css` rather
  than `tailwindcss-animate` classes, which this project does not have. It already
  carries a `prefers-reduced-motion` guard.

**Verified:** `npx tsc --noEmit` clean · `next lint` clean (one pre-existing
`SidebarHeader` warning, untouched) · `next build` compiles and passes type validation.
The build then fails prerendering `/circulars` against `localhost:8000` with no backend
running — the known local ISR-bake condition ([[project_isr_bake_docker_cache_trap]]),
unrelated to this work.

**NOT verified:** no browser run. The delivery path (4 real turns → card → live bar) has
never executed. See §10.

### Build log — 2026-08-16, second pass (Phases A + D)

**Phase A** — `components/onboarding/OnboardingDialog.tsx`, `components/tour/TourOverlay.tsx`,
`stores/onboarding-store.ts`.

- Signup now opens `"profession"` alone; the full tour opens on
  `isPaid && !onboardingSeen && professionGroup !== null`, derived from `user.plan_id`.
- The profession branch is tested FIRST, so a paid account still owing the question
  answers it and gets the tour on the next pass instead of both stacking.
- **Trap 2 solved:** `finish()` reads `useOnboardingStore.getState().mode` and calls
  `markOnboardingSeen()` **only for the full tour**. Marking it from the profession run
  would have retired the flag before the tour ever ran and killed A2 for every user.
- **A race the plan missed.** `TourOverlay`'s `onboardingOpen` gate is a render-time
  selector, and `OnboardingDialog` is mounted before it — so `open()` lands in the same
  passive-effect flush and the tour would read a stale `false` and start underneath the
  modal. Closed with `professionPending` (`profession_group === null`), which is written
  by the dismissal itself and is optimistic, so it flips exactly when the tour should
  take over. `undefined` is NOT pending (degraded read; the dialog does not prompt on it
  either).

**Phase D** — all seven remaining lessons added to `EDU_SYLLABUS`. Order:
`usage_limits → templates → citations → deep_search → save_memo → privacy_masking →
library → judgments`. All `learnMore` routes verified to exist on disk.

- `templates` ships with **no action button**: the composer «+» menu is local state in
  `ChatInput` with no programmatic opener, and the plan's rule is to ship good copy
  rather than fragile plumbing. It links to `/templates` instead.
- `save_memo` uses `injectComposerText` (fills, never sends — the `STEP_QUESTIONS`
  contract). `privacy_masking` opens `ConversationSettingsDialog` via its new store.

**A REGRESSION THE FIRST PASS INTRODUCED, now fixed.** Mounting `UsageLimitsDialog` in
`ChatLayoutClient` fixed the chat routes and silently broke `/templates`, `/blogs` and
`/library/mine`: they render the same `Sidebar` — with the same «حدود الاستخدام» row —
through `SidebarPageShell`, where nothing mounted the dialog. Clicking it there did
nothing at all.

Both store-driven dialogs now mount in **`components/sidebar/SidebarDialogs.tsx`**,
rendered in both of `Sidebar`'s branches OUTSIDE the `Sheet`/`aside`. They travel with the
menu that opens them, and any future shell gets them free. **Do not mount either dialog
anywhere else** — `ChatLayoutClient` deliberately no longer does.

**Known behaviour worth a decision:** manually reopening «اكتشف ريحان» from the settings
popover opens mode `"full"`, so dismissing it sets `onboarding_seen`. A free user who
explores that menu will therefore not get the tour again after paying. Consistent with
"once ever", but it is a real path to the tour being spent early.

**Verified:** `npx tsc --noEmit` clean · `next lint` clean (one pre-existing
`SidebarHeader` warning). **Still no browser run of anything.**

### Build log — 2026-08-16, third pass (settings surface)

Owner request: put the lessons in the settings popover, behind the «اكتشف ريحان» row.

- **`edu-store.showLesson(id)`** — manual entry point. Bypasses the cadence, session and
  day gates (the user clicked it; making them wait for a milestone would be absurd) but
  keeps the single-slot rule and still refuses while a modal owns the screen, because the
  card renders at z-40 and would otherwise be invisible underneath it.
- **«اكتشف ريحان» is a FLYOUT submenu** (nested `Popover`, `side="left"`), not an inline
  accordion — the pattern Claude's own «Learn more ›» uses. Inline would have pushed 10
  rows into a popover that already holds 8, forcing it to scroll on any short viewport.
  `side` is physical and the sidebar is on the RIGHT in this RTL layout, so left is away
  from it; `collisionPadding` lets Radix flip to the right on a narrow viewport, which is
  what keeps it usable on a phone.
- The settings popover is now **controlled** (`settingsOpen`) so a lesson click can close
  the whole menu stack. Order matters at the call site: close FIRST, then `showLesson` —
  the card renders at z-40, underneath the popover's own z-70.
- **«جولة المخرجات» row deleted** per request. The tour itself is untouched and still
  auto-runs once over the demo conversation; only its manual re-entry is gone.
- **«المكتبة القانونية» row added**, reversing the documented decision at the old
  `SidebarFooter:219`. Rationale recorded inline: the global header that used to be the
  library's entry point is not visible from inside the chat shell. «مكتبتي» stays out.
- The «اتعرف على ريحان» dialog kept a manual re-entry as **«جولة التعريف السريعة»**, the
  first entry inside the expansion. Deleting the row wholesale would have orphaned the
  post-payment tour, which was not what was asked.

**Behaviour worth knowing:** reading a lesson from this menu dismisses it like any other
delivery — it marks `edu_<id>` seen and stamps `edu_last_shown_at`. So browsing the menu
consumes lessons out of the series, and clicking through all 8 retires it entirely. That
is consistent ("a dismissal is a dismissal") but it is a real way to spend the syllabus
early, and it is the same shape as the «اكتشف ريحان» reopen note above.

## 0. The decision in one paragraph

Stop teaching everything at once, up front, to a user who has done nothing yet. Teach
**one lesson every 4 messages**, in a fixed order, non-blocking, once each — on top of a
**lesson zero that already exists**. Lesson 1 at turn 4 is حدود الاستخدام with the
user's *real* meters. Lesson 2 at turn 8 is قوالبي. The syllabus is data — adding lesson
10 is a registry entry, not an engineering task.

**Lesson zero is المحادثة التجريبية** (+ its «جولة المخرجات» coach-marks), already built
and already shipping at signup. It is not a registry entry and the engine never touches
it — but it *is* the first rung of the ladder, and the syllabus is written on the
assumption that it has already run. See §7.0.

Two smaller retimings ride along (Phase A): the profession question moves to signup
**alone**, and the 3-step «اتعرف على ريحان» tour moves to **after a successful payment**.

---

## 1. Scope

| Phase | What | Depends on |
|---|---|---|
| **A** | Retime onboarding: profession-alone at signup; full tour post-payment | — |
| **B** | The series engine + registry + card (the *structure*) | — |
| **C** | Lessons 1–2 (`usage_limits`, `templates`) as pilots | B |
| **D** | Remaining syllabus (lessons 3–9) | C verified |

Phases A and B are independent and can be built in parallel.

---

## 2. Why not just use `edu_popups.md` as designed

That design fires each topic on the **first occurrence of a matching event** ("first
time a workspace item is published"). Eight independent listeners, each racing, losers
dropped. It is a good design for contextual reinforcement and a wrong one here, on two
counts:

1. **Order is the point.** A curriculum has a sequence. Event triggers deliver whatever
   happens to happen first, so the user's syllabus is a function of which features they
   stumbled into. You asked for lesson 1 = حدود الاستخدام and lesson 2 = templates.
2. **Drop-the-loser loses lessons.** In that design a suppressed trigger is discarded
   and only re-fires if the condition recurs. For a series that is a silently skipped
   chapter. Here a blocked lesson **waits** (§5, gate 3) and lands at the next eligible
   turn.

**Kept verbatim from it:** the non-modal card form, one-popup-at-a-time, flat `edu_*`
preference keys (and the shallow-merge trap they solve), dismissal semantics,
fail-closed hydration, `reset()` on logout, the impression cap.

---

## 3. Files

```
frontend/components/edu/
  edu-syllabus.tsx      ← ORDERED registry: every lesson, its copy, action, learn-more
  EduLessonCard.tsx     ← presentational card (RTL, theme tokens, slide-in)
  EduLessonHost.tsx     ← renders the active lesson; mounted in ChatLayoutClient
  slots/UsageBarSlot.tsx← live-data slot for lesson 1 (compact bar from /usage)
frontend/stores/
  edu-store.ts          ← the ENGINE: counter, pointer, gate chain, deliver/dismiss
```

**Modified:**
- `frontend/stores/preferences-store.ts` — hydrate + expose the `edu_*` keys
- `frontend/hooks/use-chat.ts` — one line: `bumpTurn()` on `done`
- `frontend/components/chat/ChatLayoutClient.tsx` — mount `<EduLessonHost/>`
- `frontend/components/onboarding/OnboardingDialog.tsx` — Phase A retiming
- `frontend/types/index.ts` — `UserPreferencesData` gains the `edu_*` keys

---

## 4. Persistence — flat keys only

Rides the existing `/preferences` JSONB PATCH. **Zero backend changes.**

| Key | Type | Meaning |
|---|---|---|
| `edu_turns` | `number` | Lifetime user messages sent |
| `edu_last_shown_at` | `string` (ISO) | Spacing anchor |
| `edu_<lesson_id>` | `true` | One per delivered lesson |

> **⚠ Trap — shallow merge.** `merge_preferences` merges *top-level keys only*. A nested
> `edu: { seen: {...}, turns: 12 }` would be replaced wholesale on every PATCH and two
> tabs would clobber each other. Flat keys mean each PATCH touches exactly one key —
> merge-safe by construction. Same rule that governs `tour_workspace_seen`.

> **⚠ Trap — counter drift across tabs.** The increment is a client-side
> read-modify-write, so two tabs sending concurrently can lose one. The counter drifts
> *low*, meaning lessons arrive slightly late — an acceptable failure direction, and the
> only one available without backend work. **If drift ever matters**, the fix is to
> increment server-side in the message-send path (the backend already writes the user
> message before the AI call — rule 7 — so the bump is atomic and free there). Do not
> "fix" it client-side with locks.

Fire-and-forget PATCH, no await, no rollback — the `markOnboardingSeen` contract. A lost
write costs one repeated lesson, never a lost one.

---

## 5. The engine

### State (`edu-store`)
```
turns: number              // from edu_turns
seen: Record<id, boolean>  // from edu_<id> keys
lastShownAt: number | null
activeLesson: id | null    // at most ONE
shownThisSession: id[]
impressions: Record<id, number>   // localStorage, client-only
```

### `nextLesson()`
> **The first lesson in syllabus order whose `seen` flag is false.**

Deliberately *not* an index pointer. A pointer computed as "count of seen lessons" would
mis-target the moment anyone inserts a lesson mid-syllabus. This formulation makes
insertion at any position safe: a user who has seen 1, 2, 4 gets 3 next, and appending
lesson 10 never disturbs anyone.

### `bumpTurn()` — called once per completed answer
```
turns += 1  →  PATCH edu_turns
maybeDeliver()
```

Fires on the **`done` SSE event**, not on send. A card sliding in while the answer is
still streaming competes with the thing the user actually asked for.

### `maybeDeliver()` — the gate chain (all must pass)

| # | Gate | Why |
|---|---|---|
| 1 | preferences hydrated | Fail-closed. Never teach off a failed hydrate. |
| 2 | `nextLesson()` exists | Syllabus exhausted → silent forever. |
| 3 | `floor(turns / 4) > seenCount` | **The milestone rule.** Not `turns % 4 === 0` — see below. |
| 4 | `activeLesson === null` | One at a time. |
| 5 | onboarding dialog closed AND tour closed | Never stack on a modal. |
| 6 | no other open Radix dialog | `document.querySelector('[role=dialog]')`. |
| 7 | `shownThisSession.length < 1` | **Max one lesson per session.** |
| 8 | `now - lastShownAt >= 24h` | **Max one lesson per day.** |
| 9 | `impressions[id] < 3` | Three ignored shows ⇒ auto-mark seen. A lesson they keep dismissing has been answered. |

**Gate 3 is why lessons never get skipped.** Using `floor(turns/4) > seenCount` rather
than an exact modulo means a lesson blocked at turn 4 by gates 5–8 is still owed at turn
5, 6, 20 — whenever the user is next eligible. The syllabus waits; it does not drop.
A user who sends 40 messages on day one has earned 8 lessons (the whole syllabus) and
will receive them over 8 days, in order.

`CADENCE = 4` is a single constant in `edu-store.ts`. Changing it retimes every future
lesson and, because gate 3 compares against `seenCount` rather than a stored schedule,
never re-teaches or skips one for users already mid-course.

### `dismiss(id, reason)`
Reasons: `"got_it"` («فهمت») · `"action"` (used the button) · `"learn_more"` · `"close"`.
**All** reasons ⇒ `seen[id] = true` + PATCH `edu_<id>: true` + set `edu_last_shown_at`.
A bare render does **not** mark seen (a reload mid-card may re-show it; gate 9 bounds it).

---

## 6. The card

Non-modal. ~320px, bottom-start of the chat pane, above the composer, slide-in. Never
traps focus, never blocks typing. **The tour stays the only modal in the app.**

```
┌──────────────────────────────┐
│ ⚙  حدود الاستخدام            ✕│
│ نص من سطرين أو ثلاثة…         │
│ ▓▓▓▓▓░░░░░  ← optional slot   │
│ [إجراء]  [اعرف أكثر]   [فهمت] │
└──────────────────────────────┘
```

Registry entry shape:
```ts
{
  id: "usage_limits",
  icon: Gauge,
  title: string,
  body: string[],              // 2–3 lines
  slot?: "usage_bar",          // optional live-data widget
  action?: { label, run },     // in-app: open a dialog, toggle a pane, inject composer text
  learnMore?: { label, href }, // a /learn route — opens in a new tab
  delayMs?: number,
}
```

---

## 7. Syllabus

### 7.0 · Lesson zero — المحادثة التجريبية (already built, do not rebuild)

The ladder starts before the engine does. At signup the user already gets the shared
**محادثة تجريبية** in their sidebar plus the **«جولة المخرجات»** coach-mark tour over it
([[project_demo_conversation_tour]], migrations 127/128). That is lesson zero: a real
conversation with real مخرجات they can open and poke.

It is **not** a registry entry, it has no `edu_*` key, and `bumpTurn()` explicitly does
not count its messages (§9 trap 7). It appears here because the syllabus below is written
on the assumption that it has already run — which is why there is no `workspace` lesson.

> **Consequence — the dropped lesson.** «جولة المخرجات» *is* the workspace lesson. The
> earlier draft of this plan had `workspace` at #3; it is cut, because teaching مساحة
> العمل at turn 12 to someone who was coach-marked through it at turn 0 is the exact
> "overwhelm the user" failure this whole plan exists to avoid.

> **⚠ Open decision (copy, not structure).** The post-payment tour (§8, A2) is still
> `agents → workspace → questions`. Its workspace step now overlaps lesson zero too.
> Left at 3 steps deliberately — cutting or rewriting it is a copy call for the owner,
> and it does not block any phase here.

### 7.1 · The registry (proposed — reorder or cut freely)

Cadence 4. Turn numbers are the *earliest* possible delivery; gates 7–8 (one per session,
one per day) push them later for a heavy user.

| # | Turn | id | Teaches | Action | اعرف أكثر |
|---|---|---|---|---|---|
| 1 | 4 | `usage_limits` | يُحاسَب بالنقطة لا بعدد الرسائل؛ هذا استهلاكك الآن | فتح «حدود الاستخدام» (`UsageLimitsDialog`) | `/learn/usage-limits` |
| 2 | 8 | `templates` | قوالبي: احفظ قالبك واستخدمه لاحقاً | فتح قائمة «+» | — |
| 3 | 12 | `citations` | أرقام المراجع قابلة للنقر وتفتح المصدر | — | — |
| 4 | 16 | `deep_search` | البحث المعمّق يأخذ وقتاً ومراحله ظاهرة | — | `/learn/how-it-works` |
| 5 | 20 | `save_memo` | «احفظ هذه المعلومة» تثبّت معلومة للجلسة كلها | `injectComposerText` | — |
| 6 | 24 | `privacy_masking` | وضع السرية يُقنّع الأرقام قبل أي نموذج خارجي | إعدادات المحادثة | `/learn/data-protection` |
| 7 | 28 | `library` | المكتبة القانونية ومكتبتي | — | `/library` |
| 8 | 32 | `judgments` | الأحكام القضائية وملخص ريحان | — | `/judgments` |

**Why `usage_limits` is first at turn 4 and not turn 5:** the free plan's whole allowance
is 5 points ([[project_free_monthly_window_upgrade_ladder]], migration 129). At four
messages a free user is at the wall, not past it — the lesson is still actionable rather
than a post-mortem on a window they already exhausted.

### Lesson 1 in detail
The one lesson with live data. Card shows **one** compact real bar — the *binding*
window: `points.monthly` for a free plan, `points.session` for a paid one — sourced from
the existing `/usage` payload via `useUsageLimits`. Two exits, both requested:
- **فتح «حدود الاستخدام»** → the existing `UsageLimitsDialog`, all five bars, live.
- **اعرف أكثر** → `/learn/usage-limits` («سياسة حد الاستخدام»), which explains what a
  نقطة actually buys.

> `useUsageLimits(enabled)` currently fetches only while the dialog is open. The card
> passes `enabled: true` for its own lifetime; the 10s `staleTime` means opening the full
> dialog right after reuses the same cached row rather than refetching.

---

## 8. Phase A — onboarding retiming

### A1 · Profession alone at signup
`OnboardingDialog.tsx:104-113` currently opens the **full** 4-step tour when
`onboarding_seen` is falsy. Change: on first run open `"profession"` — the mode the store
already supports (`onboarding-store.ts:15`). The profession answer keeps its own gate
(`users.profession_group === null`), so it is still asked exactly once and never re-nags.

### A2 · Full tour after payment
`onboarding_seen` is repurposed to mean *"the 3-step intro tour has been shown"*. Open
the full tour when:

```
isPaid && !onboardingSeen && professionGroup !== null
```

where `isPaid = user.plan_id != null && user.plan_id !== "free"` — **already on the auth
store user** (`types/index.ts:19`), so this needs no new request and no flag passed
across the 3DS redirect.

This is deliberately *derived state*, not an event handler on the payment callback, and
that buys three things:
- `/pay/callback` is a **cold boot** after a full-page 3DS redirect and lives outside
  `ChatLayoutClient` — the dialog isn't even mounted there. Deriving from `plan_id` on
  the next `/chat` render sidesteps the whole problem.
- It covers the `processing` phase, where the money is in but the grant lands later via
  webhook. An event-on-success handler would silently miss those users.
- Existing paid users get it once, on their next visit.

Fires **once ever** — `onboarding_seen` is set on any dismissal.

> **⚠ Check before building A2:** «جولة المخرجات» (`TourOverlay`) is currently chained to
> open only after the onboarding dialog is dismissed. Once the intro tour no longer fires
> at signup, that chain must be re-pointed at the profession step's dismissal, or free
> users will never get the workspace tour at all.

---

## 9. Traps

1. **Shallow merge** — flat `edu_*` keys only. §4.
2. **Counter drift across tabs** — drifts low; accepted. §4.
3. **`nextLesson()` is a search, not an index** — keeps mid-syllabus insertion safe. §5.
4. **Gate 3 is `floor(turns/CADENCE) > seenCount`**, never `turns % CADENCE === 0`. An
   exact-modulo test silently deletes a chapter every time gates 5–8 block one.
5. **Fail-closed hydration** — a failed `/preferences` GET must mean "all seen", never
   "teach everything". Same principle as `onboardingSeen`'s `true` default.
6. **Never stack on a modal** — gates 5 and 6. Portalled Radix content sits at `z-[70]`
   ([[project_radix_layer_z70]]); the card is chrome and must stay at or below `z-60`.
7. **Demo conversation messages must not count** — it is lesson zero (§7.0), not a turn
   the user spent. Exclude at the `bumpTurn()` call site, or a user who merely reads the
   demo arrives at lesson 1 having sent nothing.
8. **Quota-blocked sends must not count** — bump on `done`, never on submit.

---

## 10. Success criteria

- [ ] A fresh account is asked its profession alone at signup — no 4-step modal
- [ ] المحادثة التجريبية + «جولة المخرجات» still fire at signup, unchanged (lesson zero)
- [ ] Reading the demo conversation advances `edu_turns` by **zero**
- [ ] The 3-step tour appears once, on the first `/chat` render after `plan_id` becomes paid
- [ ] Lesson 1 appears at turn 4 with a **live** bar matching `UsageLimitsDialog`
- [ ] Both of lesson 1's exits work: the full dialog, and `/learn/usage-limits`
- [ ] Turn 8 delivers lesson 2 — and only after ≥24h and a new session
- [ ] 40 messages in one day delivers exactly **one** lesson that day, and the rest in
      order on subsequent days — none skipped
- [ ] A lesson blocked by an open dialog is re-delivered later, not lost
- [ ] Dismissal persists across reload and across devices
- [ ] `npx tsc --noEmit` clean
