# محادثة تجريبية + جولة المخرجات — Product Tour Plan

**Status:** planned, not built · **Authored:** 2026-08-10

A guided, click-through product tour that teaches the one thing a ChatGPT/Claude-fluent
user has never seen: **the workspace, the WI, and the reference layer.** It runs on a
single shared demo conversation that every account gets from signup.

The existing «اتعرف على ريحان» dialog (`components/onboarding/`) stays as-is — it is a
modal skim with *drawings* of المخرجات (`steps/StepWorkspace.tsx`). This is the second,
deeper thing: coach marks on the **real UI, with real data**.

---

## 1. Decisions locked

| # | Decision | Rationale |
|---|---|---|
| D1 | **One shared demo conversation**, not a per-user copy | Simplest. Backend runs on the service-role key, so this is a service-layer allowance — **no RLS migration** |
| D2 | Demo conversation is **read-only** for everyone | Every write path is already `.eq("user_id", …)`; non-owners are refused for free |
| D3 | Source reveals in the demo are **free** — no unlock, no ledger row | Reuses the existing `always_free` branch shape |
| D4 | **10 references**, covering all 3 domains present | Trimmed so every card supports the claims made about it |
| D5 | Tour advances on the **real click** («جرّب اضغط»), with a skip escape | User's call |
| D6 | **Desktop + mobile** both | User's call |
| D7 | Composer in the demo → **hint bar + «ابدأ محادثة جديدة»** | Simplest read-only treatment that still converts |
| D8 | Dismiss = **per-user hide** via a preference flag, never a shared delete | A soft-delete by one user would vanish it for all |
| D9 | **Edit the WI in place** (`ac478719…`), no clone | Simplest |

---

## 2. The fixture

| | |
|---|---|
| conversation | `f4804262-da8c-45eb-87c2-911025377d13` |
| workspace item | `ac478719-4897-48ee-a844-30bbb482da27` (`wi_seq=1`, `agent_search`, `subtype=legal_synthesis`) |
| owner | `c5f4cff0-0517-43f0-af59-a9905deab22c` (xl0rch@gmail.com) — stays the owner |
| assistant message | `3fac8a35…`, 845 chars, **no `[n]` markers** (verified — needs no edit) |
| WI body | 4,152 chars — the snippet→full-answer contrast is real and load-bearing for Act 1 |

The chip on the assistant message renders as **«افتح التحليل القانوني WI-1»** with no work:
`DEFINITE_SUBTYPE["legal_synthesis"] = "التحليل القانوني"` (`MessageBubble.tsx:714`).

### 2.1 Reference renumbering — 18 → 10

Selection rule: **every kept card must render both «فتح … في ريحان» and «فتح المصدر الرسمي»**,
except the compliance card, which is kept precisely *because* it can't — it is the exception
that teaches «كلها ماعدا الخدمات».

| new | old | domain | doc_type | إحالات | library page |
|---|---|---|---|---|---|
| 1 | 1 | regulations | نظام (العمل) | 0 | ✅ |
| 2 | 2 | regulations | دليل | 0 | ✅ |
| **3** | **5** | regulations | **نظام** | **2** | ✅ |
| 4 | 8 | cases | قضية | — | ✅ |
| 5 | 11 | cases | قضية | — | ✅ |
| 6 | 12 | regulations | دليل | 2 | ✅ |
| 7 | 13 | regulations | دليل | **5** | ✅ |
| 8 | 20 | cases | قضية | — | ✅ |
| 9 | 26 | cases | قضية | — | ✅ |
| 10 | 27 | compliance | خدمة حكومية | — | ❌ *by design* |

**Dropped:** old 3, 4, 6, 9, 10, 17, 22, 25 — the five dropped cases (9, 10, 17, 22, 25) are
dropped *because they have no library page*, which is the whole point of the trim.

**[3] is the Act 3 anchor.** It is the only card that is simultaneously a **نظام** (so the
button reads «فتح النظام في ريحان» — the exact phrasing wanted), has a library page, has an
official landing URL, **and** carries إحالات. Old [1] carries zero cross-refs and cannot
demo الإحالات at all.

### 2.2 Body rewrite (required, not optional)

`content_md` cites `[1][2][3][4][5][6][8][9][10][11][12][13][17][20][22][25][26][27]`.
Renumbering alone is not enough — §سادساً builds five قضائية principles on
`[10][25]`, `[9][20]`, `[22]`, `[8][26]`, `[17]`, and four of those cases are being cut.

**§سادساً must be rewritten to only the principles the four kept cases actually support:**

| principle | old refs | survives? |
|---|---|---|
| وجوب وجود سبب مشروع للفصل | [10], [25] | ✗ both cut |
| العذر المرضي يمنع الفصل المشروع | [9], [20] | ✅ via old [20] → new [8] |
| التأخير الجزئي لا يبرر طي القيد | [22] | ✗ cut |
| المخالصة النهائية تمنع المطالبة | [8], [26] | ✅ via new [4] + [9] |
| الانقطاع دون عذر مشروع | [17] | ✗ cut |

**Do not invent principles to backfill.** Rewrite §سادساً around the two surviving ones
(plus old [11] → new [5], already cited in ثالثاً for شهادة الخدمة/الوثائق), and shorten the
section heading accordingly. Everything above §سادساً only needs marker renumbering.

### 2.3 Migration `127_demo_conversation.sql`

Next free number (last on disk is `126_product_docs.sql` in `shared/db/migrations/`).
Idempotent, data-only, **no schema change**:

1. `update conversations set title_ar = 'محادثة تجريبية' where conversation_id = …`
2. `delete from workspace_item_references where wi_id = … and n in (3,4,6,9,10,17,22,25)`
3. Renumber the survivors per §2.1 — **two-phase** (`n = n + 1000`, then down to 1–10) to
   dodge any unique constraint on `(wi_id, n)`
4. `update workspace_items set content_md = $$…$$, metadata = metadata || '{"ref_count":10,"cited_count":10}'` — the rewritten body from §2.2
5. `update workspace_items set feedback = null` — the column is shared; it must not ship with one user's thumb

⚠ **Irreversible against live prod data.** Snapshot the 18 rows + `content_md` into the
migration file as a commented-out restore block before running it.

⚠ Per `[[project_moyasar_payments]]` — **migration runs before the deploy**, or the frontend
renders 18 cards against a body that cites 10.

---

## 3. Backend — the demo allowance

**No RLS migration.** `shared/db/client.py:130` — the backend uses `SUPABASE_SERVICE_KEY`
and bypasses RLS entirely; ownership is enforced in Python. So sharing the demo is purely a
service-layer decision, and every *write* path stays refused with zero work.

### 3.1 `backend/app/services/demo_service.py` (new)

The single source of truth. Two ids, two predicates:

```python
DEMO_CONVERSATION_ID = "f4804262-da8c-45eb-87c2-911025377d13"
DEMO_ITEM_ID = "ac478719-4897-48ee-a844-30bbb482da27"

def is_demo_conversation(conversation_id: str | None) -> bool: ...
def is_demo_item(item_id: str | None) -> bool: ...
```

Constants, not env vars — an env var that drifts between services silently turns the tour
into a 404 (`[[project_isr_bake_rate_limit_bypass]]` is the same failure shape).

### 3.2 Read paths that gain the allowance

Each ownership check becomes "…or it's the demo":

| file | function | change |
|---|---|---|
| `services/conversation_service.py:25` | `list_conversations` | prepend the demo row when `offset == 0` and neither `case_id` nor `starred` is filtering |
| `services/conversation_service.py` | `get_conversation` | allow when `is_demo_conversation` |
| `services/message_service.py` | list messages | same |
| `services/workspace_service.py` | `get_workspace_item`, list by conversation | allow when `is_demo_item` / `is_demo_conversation` |
| `api/workspace.py:451` | references payload | rides on `get_workspace_item` — no separate change |

`_enrich_conversation` gains a derived **`is_demo: bool`** field so the frontend never has to
know the id.

### 3.3 Free source reveal — `api/workspace.py:337` `get_reference_source`

Step 1 (ownership) gets the demo allowance. Step 4 (entitlement) gains a branch **above**
`resolve_access`, mirroring the existing `always_free` shape at :404:

```python
if is_demo_item(item_id):
    decision = library_service.AccessDecision(
        may_unlock=True, charged=False, reason="open"
    )
```

and step 6 (`_record_library_use`) is **skipped** for the demo — a tutorial must not write
rows into the user's library shelf.

⚠ The docstring at :362 warns that `surface` must never change the charge or the endpoint
becomes the bypass it was built to close (migration 104). This branch is keyed on a
**hardcoded item id**, not on caller-supplied input, so it is not that bypass — but the
guard must be `is_demo_item(item_id)` and nothing looser. Never accept a client-supplied
"demo" flag.

### 3.4 Writes stay refused

No changes to any POST/PATCH/DELETE. A non-owner attempting to send a message, edit the WI,
rate it, share it, or delete the conversation is already refused by the `.eq("user_id", …)`
filters. §4.2 makes those refusals *visible* instead of silent 404s.

---

## 4. Frontend — dressing the demo conversation

### 4.1 Sidebar

`Conversation` type gains `is_demo?: boolean`. The demo pins to the top of the list with a
small «تجريبية» chip. It is **excluded** from `/chats` search, star, and the trigram index —
it is furniture, not the user's content.

### 4.2 Read-only treatment (in `ChatContainer` / `ChatInput`)

| surface | treatment |
|---|---|
| composer | replaced by a hint bar: «هذه محادثة تجريبية للاطّلاع» + a primary «ابدأ محادثة جديدة» that routes to a fresh chat (D7) |
| 👍/👎 on the WI | **hidden** — `workspace_items.feedback` is one shared column; one user's thumb would be everyone's |
| مشاركة / حفظ كمدونة | **rendered but disabled**, with a hover hint «متاح في محادثاتك». Act 4 explains them; it does not invoke them |
| «+» add-item menu | **rendered but disabled**, same hint. Act 5 explains it |
| conversation menu (rename/delete) | delete replaced by **«إخفاء»** → sets the D8 preference flag |

### 4.3 Preference keys

Flat keys only — `merge_preferences` is a **shallow** merge, and a nested object clobbers
across tabs (`[[project_edu_popups]]`):

- `tour_workspace_seen: true` — the tour has run
- `demo_conversation_hidden: true` — D8 dismiss

---

## 5. The tour engine

No tour library is installed (no driver.js / shepherd / joyride in `package.json`) and none
gets added — the requirement is ~11 anchored steps that also *drive* app state, which is a
thin thing to own and a thick dependency to import.

### 5.1 `stores/tour-store.ts` (new)

`{ isOpen, stepIndex, open(), close(), next(), prev() }`. Open state lives in the store, not
in component state, so the sidebar settings popover can reopen it — the same reason
`onboarding-store.ts` exists.

### 5.2 `components/tour/TourOverlay.tsx` (new)

- Mounted in `ChatLayoutClient.tsx` beside `<OnboardingDialog />`; self-gating, renders
  `null` when closed.
- **Anchors** resolve by `data-tour="<id>"` attributes added to the real components (§6).
  Rect via `getBoundingClientRect()`, recomputed on scroll/resize/step change.
- **Spotlight = four divs**, not an SVG mask: top/bottom/start/end panels framing the anchor
  rect, leaving the target itself uncovered. Clicks pass through natively — no
  `pointer-events` juggling, which is the usual source of the "tour ate my click" bug.
- **Card** positioned beside the rect on desktop, above/below on mobile.
- Always shows «تخطّي الجولة». Steps that advance on `next` also show «التالي».

### 5.3 Advancing on the real click (D5)

Prefer **state transitions over DOM click listeners** — the app already models every
navigation beat in `chat-store.ts`, and a store subscription cannot miss a click the way a
listener on a re-rendered node can:

| beat | watch |
|---|---|
| open the WI | `workspaceByConversation[id].openItemId` becomes non-null |
| click `[n]` | `focusedReferenceN` becomes `3` |
| back to list | `openItemId` becomes null |
| close the pane | `isOpen` false |

Only the `عرض المصدر` dialog has no store state — that one advances on the dialog's own
open state, lifted through a callback.

**Stall guard:** if the expected transition hasn't happened after ~8s, reveal a «التالي»
that performs the transition itself. A tour that can only be escaped by reloading is worse
than no tour.

### 5.4 z-index and the Radix dialog

App chrome ceilings at **60** (the mobile workspace overlay is `z-[60]`), portalled Radix
layers sit at **`z-[70]`** (`[[project_radix_layer_z70]]`). The tour root is **`z-[80]`**.

⚠ Act 3 points *inside* the عرض المصدر dialog (for الإحالات). Radix sets
`pointer-events: none` on `<body>` while a dialog is open, so the tour root needs an
explicit `pointer-events: auto` or its own buttons go dead. Verify on mobile, where this
exact class of bug previously froze the whole app with no tappable way out.

### 5.5 Scroll

The reference panel lives inside `ArtifactPreview`'s scroll viewport. Every Act 3 step must
`scrollIntoView` its anchor **and wait for the scroll to settle** before measuring, or the
spotlight lands on a stale rect. `ReferencePanel` already scrolls a focused card into view
(`ReferencePanel.tsx:214-231`) — reuse that path rather than adding a second scroller.

---

## 6. The tour script

Anchors are `data-tour` attributes to be added. Copy below is the intent; final Arabic
wording lives in **`components/tour/tour-content.ts`** — one file, like
`onboarding-content.ts`. Never edit copy in components.

### Act 1 — طبقة المحادثة (2 steps, deliberately the shortest)

Assume ChatGPT fluency. Do **not** explain the composer, the message bubbles, or copy/regenerate.

| # | anchor | says | advance |
|---|---|---|---|
| 1 | `chat-thread` — the question + reply pair | محادثتك والردود، تمامًا كما تتوقع. الفرق يبدأ في السطر التالي | التالي |
| 2 | `artifact-chip` — «افتح التحليل القانوني WI-1» | ما تقرأه بالأعلى **مقتطف** من الإجابة. التحليل الكامل — بمصادره — خلف هذا الزر. **جرّب اضغط** | click → `openItemId` |

### Act 2 — طبقة الـ WI (3 steps)

| # | anchor | says | advance |
|---|---|---|---|
| 3 | `wi-body` | التحليل الكامل: أطول من الرد، ومقسوم بعناوين، وكل رقم فيه مرجع | التالي |
| 4 | `wi-badge` + pane header | **WI-1** هو اسم هذه البطاقة — وريحان يناديها بهذا الاسم داخل المحادثة | التالي |
| 5 | `pane-close` (X) + `pane-back` (←) | **X** يرجّعك للمحادثة · **←** يرجّعك لقائمة المخرجات | التالي |

### Act 3 — المراجع (4 steps — the core)

| # | anchor | says | advance |
|---|---|---|---|
| 6 | `citation-3` — the `[3]` marker in the body | كل رقم في النص مرجع. تقدر تفتحه من الرقم نفسه، أو من قائمة المراجع بالأسفل. **جرّب اضغط [3]** | click → `focusedReferenceN === 3` |
| 7 | the open reveal dialog body | المرجع يحمل **القسم من النظام** الذي استرجع منه ريحان — وإذا كان قضية، فهو يحمل **ملخص القضية** | التالي |
| 8 | `الإحالات` disclosure | الإحالات: المواد التي يشير إليها هذا النص. **جرّب افتحها** | click → expanded |
| 9 | the dialog's two exits | «فتح المصدر الرسمي» → موقع الجهة · «فتح النظام في ريحان» → داخل مكتبتنا. كل المصادر موثّقة بمصدرها الرسمي | التالي |

Then one card-level step, anchored on the **compliance card [10]**:

| # | anchor | says | advance |
|---|---|---|---|
| 10 | `ref-card-10` | أربعة أنواع من المصادر: نظام · قضية · تعميم · خدمة حكومية. كلها تفتح في مكتبة ريحان — **ما عدا الخدمات الحكومية**، فهي عند جهتها | التالي |

⚠ Step 10 is the one place the copy is *shown* rather than *told*: card 10 visibly lacks the
library button that the nine cards above it carry. This only holds because of the §2.1 trim —
if the ref set changes, this step's claim breaks. **تعميم appears in no card in this
conversation** and is named in the legend only.

### Act 4 — النشر (1 step)

| # | anchor | says | advance |
|---|---|---|---|
| 11 | `wi-action-bar` — مشاركة + حفظ كمدونة | «مشاركة» تعطيك رابطًا للتحليل · «حفظ كمدونة» يحفظه في مدوناتك | التالي |

Both buttons are disabled here (§4.2) — this step explains, it does not invoke.

### Act 5 — مساحة العمل (2 steps)

| # | anchor | says | advance |
|---|---|---|---|
| 12 | `pane-back` (←) | ارجع لقائمة المخرجات. **جرّب اضغط** | click → `openItemId === null` |
| 13 | `workspace-add` («+») | كل مخرجات المحادثة تتجمّع هنا — وتقدر تضيف ملاحظة أو ترفع ملفًا من «+» | إنهاء |

**13 steps.** On finish: `tour_workspace_seen: true`.

---

## 7. Mobile (D6)

The same `data-tour` ids work — `WorkspacePane` is the identical component in both layouts.
Three differences:

1. **No split pane.** Below `md` the workspace is a full-viewport `z-[60]` overlay
   (`ChatLayoutClient.tsx:117`) and the chat is completely covered. Acts 2–5 therefore run
   *inside* the overlay, and Act 1's anchors are gone from the screen — the engine must not
   try to measure them.
2. **X and ← carry more weight** — they are the only way back. Step 5's copy stays as-is;
   it is more important here, not less.
3. **Card placement** flips to above/below the anchor, and the spotlight must respect
   `env(safe-area-inset-*)` — the page paints under the notch (`viewportFit: "cover"`).

---

## 8. Trigger and re-entry

- A brand-new account lands on the demo conversation, and the tour auto-starts **after** the
  «اتعرف على ريحان» dialog is dismissed — never both on screen at once.
- Gate: `tour_workspace_seen !== true` **and** a successful preferences hydrate. Fail-closed
  on hydrate failure (treat as seen), exactly like `OnboardingDialog.tsx:104-113` — an API
  blip must never re-nag an existing user.
- Re-openable any time from the sidebar settings popover (`SidebarFooter.tsx`), beside
  «اتعرف على ريحان». Reopening always resets to step 0.
- Existing users: they get the demo conversation in their sidebar too, and the tour offered
  once. Same accepted one-time cost as the original tour rollout.

---

## 9. File manifest

**New (8)**
```
shared/db/migrations/127_demo_conversation.sql
backend/app/services/demo_service.py
frontend/stores/tour-store.ts
frontend/components/tour/TourOverlay.tsx
frontend/components/tour/TourCard.tsx
frontend/components/tour/TourSpotlight.tsx
frontend/components/tour/tour-content.ts
frontend/hooks/use-tour-anchor.ts
```

**Modified (~12)**
```
backend/app/services/conversation_service.py   demo allowance + is_demo
backend/app/services/message_service.py        demo allowance
backend/app/services/workspace_service.py      demo allowance
backend/app/api/workspace.py                   free reveal + no record_use
frontend/types/index.ts                        Conversation.is_demo
frontend/stores/preferences-store.ts           2 flat keys
frontend/components/chat/ChatLayoutClient.tsx  mount TourOverlay
frontend/components/chat/ChatInput.tsx         demo hint bar
frontend/components/chat/MessageBubble.tsx     data-tour on chip + thread
frontend/components/workspace/WorkspacePane.tsx        data-tour on X / ← / badge
frontend/components/workspace/ReferencePanel.tsx       data-tour on card / إحالات / exits
frontend/components/workspace/WorkspaceItemActionBar.tsx  data-tour + disabled-on-demo
frontend/components/workspace/WorkspaceAddMenu.tsx     data-tour + disabled-on-demo
frontend/components/sidebar/*                  pin + chip + إخفاء
```

---

## 10. Traps

1. **Migration before deploy.** 10 cards vs an 18-marker body is the visible failure.
2. **The `[3]` anchor is data-coupled.** If the ref set is ever re-trimmed, steps 6–10 break
   silently. Assert in a test that new `[3]` is `domain=regulations`, `doc_type=نظام`, has a
   library page, and has ≥1 cross-ref.
3. **`workspace_items.feedback` is shared.** Ship it `null` and keep the thumbs hidden.
4. **Radix `pointer-events: none` on body** while the reveal dialog is open (§5.4).
5. **Never a client-supplied demo flag** on the free-reveal branch (§3.3).
6. **`workspace_item_references` joins on `wi_id`, not `item_id`** — `item_id` on that table
   is the *source row* PK (`library_items_service.py:940-947`), not the WI. Getting this
   backwards returns zero references.
7. **`_record_library_use` must be skipped**, not just the charge — otherwise the tutorial
   pollutes «استُخدم مؤخرًا» in the library.
8. **Don't reuse the `references` kind.** `ReferencesRenderer.tsx` is a «قيد التطوير» stub;
   the refs this tour teaches live in `ReferencePanel` inside the `agent_search` viewer.

---

## 11. Success criteria

1. A fresh account signs up → محادثة تجريبية is in the sidebar, pinned, chipped.
2. The tour runs all 13 steps end-to-end on desktop **and** on a 390px viewport.
3. Every one of the 10 reference cards renders «فتح المصدر الرسمي»; nine render
   «فتح … في ريحان»; card 10 renders neither library button.
4. Opening a source in the demo writes **no** `library_unlocks` row and **no** `record_use`
   row, and the balance chip does not move.
5. A non-owner cannot send, edit, rate, share, or delete anything in the demo conversation —
   and sees a disabled affordance with a hint rather than an error.
6. «إخفاء» removes it from that user's sidebar only; a second account still sees it.
7. `[n]` markers in the body resolve 1→10 with no dead markers.

---

## 12. Open / deferred

- **The 5 dropped قضايا could be kept** instead of cut, by adding `seo_item_meta` slugs for
  them (10,000 of 30.5k judgments already have one). That trades a content trim for an SEO
  surface change and a re-bake — deferred, and gated on
  `[[project_judgments_court_sections]]`'s "ranked view REQUIRED before publishing" rule.
- **The demo item stays owned by xl0rch**, who therefore sees it as an ordinary editable
  conversation. Acceptable; worth a note so a future edit doesn't surprise anyone.
- Whether the demo conversation should also be reachable **anonymously** as a marketing
  surface — out of scope here; `/blog/{token}` snapshots already cover that shape.
