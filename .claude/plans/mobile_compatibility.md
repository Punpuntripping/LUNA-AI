# Plan — Mobile compatibility: library + main chat

**Goal:** Make the public library (regulation/judgment doc pages above all) and the main chat genuinely usable on phones. Three phases: a mechanical sweep with zero design decisions, the floating compressed TOC (the user's centerpiece idea), and chat structural work. Each phase ships independently.

**Evidence base (2026-08-13):** live probe of prod at 390×844 (screenshots in session scratchpad) + two code audits of `frontend/` at HEAD `43dc167`. All file:line refs verified against the working tree on that date.

## Decisions (locked with user)

- **TOC goes compressed + floating.** A fixed pill that opens a ~5-row window centered on the current مادة, with open/close; «عرض الكل» expands to a full bottom sheet. The inline `TocList` `<details>` at the top of the page **stays** (first impression + SEO); the widget takes over once the reader scrolls.
- **Font strategy = stop opting out of the existing scale.** `globals.css:163-171` already bumps `text-xs/sm/base/lg/xl` to 13/15/17/19/21px under 640px. The fix is replacing hardcoded `text-[Npx]` with tokens, not inventing new sizes.
- **Tablet (768–1023px) is a non-goal for this plan.** No `md:` layer exists anywhere in the library and the split/overlay switch is at 768px; leave that gap for a later pass. Phones first.

## Key facts from the audits

| Fact | Where |
|---|---|
| Mobile TOC = closed-by-default `<details>`, inline at page top, no scrollspy, no floating access | `components/library/blocks/TocList.tsx`; wired `app/regulations/[slug]/page.tsx:225-235`, `app/judgments/[slug]/page.tsx:289-298` |
| Desktop TOC has scrollspy + missing-anchor fallback that mobile lacks | `components/library/blocks/TocRail.tsx:36-72` |
| نظام المعاملات المدنية = ~156,000px tall at 390px, 716 TOC entries | live probe |
| Phone type scale exists but is bypassed by ~75 `text-[Npx]` uses in library + chat chrome | `app/globals.css:163-171`, `tailwind.config.ts:178-198` |
| Every chat input is ≤15px → iOS Safari zooms on focus and stays zoomed | `ChatInput.tsx:700`, `SearchBar.tsx:190`, `MessageBubble.tsx:289`, `ConversationHeaderMenu.tsx:188`, `MarkdownDocEditor.tsx:216,263`, `ConversationItem.tsx:185`, `ComposerPlusMenu.tsx:336` |
| `viewportFit:"cover"` set, but composer / FAB / sheets never use `env(safe-area-inset-bottom)` | `app/layout.tsx:35-44`, `ChatInput.tsx:645`, `AskRayhanWidget.tsx:187,208`, `AnonCtaPopup.tsx:365-374` |
| Enter always sends — no newline possible on phone keyboards | `ChatInput.tsx:233-241` |
| Workspace auto-opens full-screen overlay mid-stream on mobile | `hooks/use-chat.ts:576-584` → `ChatLayoutClient.tsx:118-133` |
| Overlay/drawer have no history integration — Android back exits the conversation | `ChatLayoutClient.tsx:118-133`, `Sidebar.tsx:126-147` |
| No `Sheet` primitive exists; bottom-sheet pattern lives only in `AnonCtaPopup` / `AskRayhanWidget` | `components/ui/` inventory |
| Two overlapping sidebar-open buttons; the always-rendered one collides with the chat header | `ChatLayoutClient.tsx:64-74` (no `md:hidden`), `Sidebar.tsx:126`, `shell/SidebarPageShell.tsx:30-40` |
| Zero mobile e2e coverage | `frontend/e2e/` has one desktop spec |

---

# Phase 1 — Mechanical sweep (no design decisions)

All items are independent; land as one wave. Agent: **@nextjs-frontend**.

## 1.1 Kill iOS zoom-on-focus globally

One rule in `app/globals.css` under `@media (max-width: 639px)`:

```css
input, textarea, select {
  font-size: max(1rem, 1em);
}
```

Fixes the composer (15px), library `SearchBar` (13px), message edit, rename inputs, doc editor — every current and future input — without touching component classes. Verify the composer and library search visually after; the +1–3px is acceptable and consistent.

## 1.2 Safe-area insets

`pb-[calc(theme(spacing.3)+env(safe-area-inset-bottom))]`-style additions:

| Surface | File |
|---|---|
| Composer wrapper (`px-4 py-3`) | `components/chat/ChatInput.tsx:645` |
| اسأل ريحان FAB (`bottom-5 left-5`) | `components/library/blocks/AskRayhanWidget.tsx:187` |
| AskRayhan bottom sheet | `AskRayhanWidget.tsx:208` |
| AnonCtaPopup bottom sheet | `components/marketing/AnonCtaPopup.tsx:365-374` |
| Sidebar drawer footer | `components/sidebar/SidebarFooter.tsx` root padding |

## 1.3 Enter = newline on touch

`ChatInput.tsx:233-241`: gate the send-on-Enter branch behind a coarse-pointer check (`matchMedia("(pointer: coarse)")`, computed once per mount — do NOT use `useIsMobile`, a 768px iPad with a keyboard is not the issue; input modality is). On coarse pointers Enter inserts a newline; send is the button only. Desktop unchanged.

## 1.4 Font-token sweep

Rule: reading surfaces and anything the phone bump should reach use tokens; `text-[Npx]` is reserved for genuinely fixed chrome (none identified). The sweep:

**Chat:**
- `MessageBubble.tsx:318,325` user bubble `text-[15px]` → `text-sm` — matches assistant container; both then follow the scale. (NOT `text-base`: the assistant *container* is `text-sm`; markdown `p` inside is `text-base`. Making the user bubble `text-sm` equalizes the two containers.)
- `MessageBubble.tsx:266` `text-[11px]` → `text-xs`; `:270` `text-[10px]` → `text-xs`.
- `MarkdownRenderer.tsx:229-312` chat heading scale — restore hierarchy: h1 `text-xl` bold, h2 `text-lg` bold, h3 `text-base` bold, h4 `text-sm` bold. Today h2–h4 render ≤ body size on phones.
- `MessageBubble.tsx:455-456` — drop justification on phones: prefix the three justify classes with `sm:` so `max-sm` gets ragged-right. Arabic justify at ~340px produces rivers.
- `MarkdownRenderer.tsx:126-137` inline code `text-[13px]` → `text-xs`; `CodeBlock.tsx` body `text-[13px]` → `text-xs`.
- `Sidebar.tsx:56` `text-[9px]` → `text-xs`; `WorkspaceCard.tsx:*` / `WorkspaceItemActionBar.tsx:227,240,257` / `ReferencePanel.tsx` card meta `text-[11px]` → `text-xs`.
- `DeepSearchProgress.tsx` labels `text-[11px]` → `text-xs`.

**Library:**
- `blocks/ArticleBody.tsx:49,54` `text-[15px] … sm:text-base` → `text-base` throughout (yields 17px phone / 16px desktop, joining the app's other reading surfaces).
- `blocks/LeadSummary.tsx:30` keep (already token-led).
- `TocRail.tsx:83,123,156` `text-[13px]` → `text-sm`; `:91,132,167` `text-[11px]` → `text-xs`.
- Badges/counts across cards: `text-[11px]` → `text-xs` in `MetadataCard.tsx:47`, `SectorBrowseGrid.tsx:56,59`, `CourtBrowseGrid.tsx:66,81,85`, `SectorPills.tsx:26`, `LibraryTypeChips.tsx:75`, `JudgmentCard.tsx:39,48,56`, `RegulationCard.tsx:36`, `ComplianceCard.tsx:33`, `CitedRegulations.tsx:85`, `SectorPreviewStrip.tsx:58`, `JudgmentsFilterBar.tsx:142,179`, `LibrarySearchPanel.tsx:361`, `LibrarySearchResultRow.tsx:75,80`, `SearchBar.tsx:190,220`, `NestedArticles.tsx:45` (`text-[10px]`).

After the sweep, `grep -r "text-\[1[0-5]px\]" frontend/components/{chat,library,workspace,sidebar}` should return zero hits.

## 1.5 Text overflow guard

`blocks/ArticleBody.tsx:49` — add `break-words` (long decree numbers / URLs in `whitespace-pre-line` legal text currently overflow a 360px viewport).

## 1.6 Sidebar-button collision + header

- `ChatLayoutClient.tsx:64-74` and `shell/SidebarPageShell.tsx:30-40`: add `max-md:hidden` to the `absolute top-3 start-3 z-30` reopen button — `Sidebar.tsx:126` already renders the mobile one. Removes the double-button stack that intercepts taps.
- `ChatContainer.tsx:74-86`: give the header row `max-md:ps-12` so «المحادثة» + ⋯ clear the fixed hamburger (verified colliding in the live probe).

## 1.7 Anchor offset

Library doc pages: `scroll-mt-24` → `scroll-mt-20` at `app/regulations/[slug]/page.tsx:270,308` and `app/judgments/[slug]/page.tsx:332,357`. Headers are 60–64px, not 96px; jumps currently land 32px short.

## 1.8 Sidebar drawer flash

`stores/sidebar-store.ts:29` — lazy-initialize `isOpen` from `window.matchMedia("(min-width: 768px)").matches` (store is client-only). Kills the 288px drawer flashing open on first mobile paint, and lets the two raw `window.innerWidth < 768` checks in `Sidebar.tsx:97,109` collapse to `useIsMobile()`.

---

# Phase 2 — Floating TOC for library doc pages

Agent: **@nextjs-frontend**. New client widget on regulation + judgment doc pages, `lg:hidden` (desktop keeps `TocRail`).

## 2.1 `frontend/hooks/use-toc-scrollspy.ts` (new)

Extract from `TocRail.tsx:36-72`:
- IntersectionObserver scrollspy (`rootMargin: "-96px 0px -60% 0px"` — retune top inset to the real 64px header when touched) returning `activeId`.
- The anchor-click handler with the missing-anchor fallback (`#` target absent → scroll to `#library-doc-gate`), from `TocRail.tsx:36-46`.
- Refactor `TocRail` to consume the hook. Behavior-identical on desktop.

## 2.2 `frontend/components/library/blocks/TocFloating.tsx` (new)

Client component. Receives the same `entries` prop as `TocList` (already computed server-side at `regulations/[slug]/page.tsx:145` / judgments equivalent).

**Pill (collapsed):** `fixed z-40 start-4` + `bottom-[calc(1.25rem+env(safe-area-inset-bottom))]` — opposite corner from the اسأل ريحان FAB (`left-5` = physical left; the pill uses logical `start` = right in RTL). `rounded-full border bg-card shadow-md px-3 h-11`, list icon + live label «المادة ٤٥». Visible only while the scrollspy has an active entry (i.e., reader is inside the مواد) — hidden at page top where the inline `TocList` is on screen.

**Compact panel (tap):** anchors above the pill, `w-72 max-w-[calc(100vw-2rem)] rounded-xl border bg-card shadow-lg`. Exactly **5 rows**: active ±2, clamped at list ends. Rows `h-11` (44px touch floor), `truncate`, active row highlighted (`bg-primary/10 font-medium`). Footer row: «عرض الكل (٧١٦)» start-aligned, X end-aligned. Transparent `fixed inset-0` backdrop at z-30 closes on outside tap. Row tap = jump (with gate fallback from the hook) + close.

**Full sheet («عرض الكل»):** bottom sheet per the `AskRayhanWidget.tsx:208` pattern — `fixed inset-x-0 bottom-0 z-50 max-h-[82vh] rounded-t-2xl border-t bg-card` + safe-area padding, scrim `z-40 bg-black/50`. Header: title + count + close; a filter input (≥16px — covered by 1.1) that narrows entries by مادة number/title; body = the grouped entry list, `overflow-y-auto overscroll-contain`. Escape + scrim tap close.

**Gated entries:** on panel open, lazily check `document.getElementById` per visible entry; absent target → subtle lock glyph on the row, tap goes to `#library-doc-gate` (existing fallback). No server changes.

## 2.3 Wiring

- `app/regulations/[slug]/page.tsx` — render `<TocFloating entries={…}>` in a `lg:hidden` block alongside the existing `TocList` at `:225-235`.
- `app/judgments/[slug]/page.tsx` — same at `:289-298`.
- Not on the مادة page (`/regulations/{slug}/{article}`) — it has no TOC data today; adjacent work.

**Z coordination:** pill z-40 (same tier as FAB, opposite corners — no overlap), sheet z-50/scrim z-40 (matches `SiteMobileNav`; the two are never open simultaneously). Nothing touches the portalled-Radix z-70 tier.

---

# Phase 3 — Chat structural

Agent: **@nextjs-frontend**; each item independently shippable, listed in build order.

## 3.1 `frontend/components/ui/sheet.tsx` (new)

shadcn sheet primitive (Radix Dialog based), side `bottom` + `start` variants, overlay/content at **z-[70]** per the documented convention (`ChatLayoutClient.tsx:103-117`), safe-area padding built into the bottom variant. This is the shared foundation for 3.2, 3.5, and the library sheet can later migrate to it.

## 3.2 Sidebar → Sheet on mobile

`Sidebar.tsx` below `md`: render inside `Sheet` (focus trap, scroll lock, Escape, swipe-close for free) instead of the hand-rolled `max-md:fixed` aside + scrim (`:126-147`). Desktop rendering unchanged. Removes the unthrottled resize listener (`:95-104`).

## 3.3 Workspace overlay behavior

- **No auto-open on mobile.** `hooks/use-chat.ts:576-584`: gate `openWorkspaceItem` on `!isMobile` — the assistant bubble already renders the «افتح التحليل …» WI chip, so discovery is covered without yanking the user away from a streaming answer. Desktop unchanged.
- **Back button closes the overlay.** `ChatLayoutClient.tsx:118-133`: on overlay open, `history.pushState({wsOverlay:true})`; `popstate` closes it. Add Escape handler + body-scroll lock while open (or fold the overlay into a full-screen `Sheet` variant and get all three free).

## 3.4 Touch-target pass (44px floor on interactive chrome)

| Surface | File | Change |
|---|---|---|
| Conversation rows `py-1.5` (~30px) | `ConversationItem.tsx:163-173` | `max-md:py-2.5` |
| Row ⋯ button `h-6 w-6` | `ConversationItem.tsx:245` | `max-md:h-9 max-md:w-9` |
| Footer icons `h-7 w-7` | `SidebarFooter.tsx:90,114` | `max-md:h-9 max-md:w-9` |
| Dialog close = bare 16px icon | `ui/dialog.tsx:58-61` | add `p-2 -m-2` hit area |
| Workspace pane header `h-8` | `WorkspacePane.tsx:130-162` | `max-md:h-10 max-md:w-10` |
| Item action bar `h-7`/`h-6` buttons | `WorkspaceItemActionBar.tsx:227,240,257` | see 3.6 |
| Reference-card actions `h-6` ×3 wrapping | `ReferencePanel.tsx:474-530` | `max-md:h-9`, allow 2-col wrap |
| Bubble action bar `h-7 w-7` ×6 | `MessageBubble.tsx:334-384,564-676` | `max-md:h-9 max-md:w-9`, drop the tooltip wrapper on coarse pointers |

## 3.5 Chat dialogs → bottom sheets on mobile

Add a `mobileSheet` presentation to `DialogContent` (classes per `AnonCtaPopup.tsx:365-374`: `max-sm:bottom-0 max-sm:top-auto max-sm:translate-y-0 max-sm:rounded-t-2xl max-sm:rounded-b-none max-sm:max-h-[85dvh]`), then opt in: source-reveal dialog (`ReferencePanel.tsx:291-316`), add-item picker (`WorkspaceAddMenu.tsx:209-242`), settings/account dialogs, `QuotaUpgradeDialog`. While there: sweep remaining `vh` → `dvh` (`ReferencePanel.tsx:701`, `WorkspaceAddMenu.tsx:211`, `SidebarFooter.tsx:129`, `AccountSettingsDialog.tsx:495`, `DeleteAccountDialog.tsx:92`, `PaymentHistoryDialog.tsx:261`, `OnboardingDialog.tsx:152`).

## 3.6 Workspace item action bar on mobile

`WorkspaceItemActionBar.tsx:182-215`: below `md`, replace the draggable floating cluster with a **fixed full-width bottom bar** (`fixed inset-x-0 bottom-0` inside the overlay, safe-area padded, `h-12`, buttons `h-9`). Dragging via a 20×28px handle competes with page scroll and the floating position covers the last lines of the artifact + المراجع. Desktop keeps the draggable bar.

## 3.7 iOS keyboard resilience

- `MessageList.tsx:249-284`: re-run the pin-to-bottom write on `window.visualViewport` `resize` (the keyboard shrinks the visual viewport; iOS does not honor `interactiveWidget:"resizes-content"`).
- `ChatInput` textarea `onFocus`: `scrollIntoView({block:"nearest"})` after a frame, so the composer surfaces above the keyboard.

## 3.8 Tooltips on touch

Wrap `ui/tooltip.tsx` trigger: on coarse pointers render children without the tooltip (today every bubble/action-bar tooltip costs a dead first tap).

---

# Verification (each phase)

**@frontend-dev-loop** (or manual Playwright) at **390×844**, prod or local:

1. Focus the composer and library search on an iOS UA — no viewport zoom (1.1); composer visible above keyboard with home-indicator clearance (1.2, 3.7); Enter produces a newline (1.3).
2. Regulation page نظام المعاملات المدنية: pill appears after scrolling into the مواد, shows the current مادة, 5-row window jumps correctly, «عرض الكل» sheet filters 716 entries, tap on a gated مادة lands on the gate CTA — not a dead tap (Phase 2).
3. Chat header shows «المحادثة» un-clipped with one hamburger (1.6); sidebar opens with no flash, closes via Android back / Escape / scrim (1.8, 3.2); workspace overlay closes on back button and does not auto-open mid-stream (3.3).
4. Type check + `npm run build`; then `grep` gate from 1.4.
5. Add one mobile e2e spec (`frontend/e2e/mobile-chat.spec.ts`, viewport 390×844) covering: send with newline, WI chip → overlay → back closes, TOC pill jump. Today's mobile e2e coverage is zero.

# Out of scope / adjacent

- Tablet layer (768–1023px `md:` treatment, split-view minimums, `ResizableHandle` touch size).
- مادة page TOC (`/regulations/{slug}/{article}` has no TOC data).
- `SectorBrowseGrid` truncated 2-up tiles and `HubPagination` 3-row wrap — real but cosmetic; fold into a later library polish pass.
- `GateBanner` absolute-overlay overflow (latent — suppressed on all current library paths).
- `/compliance/{slug}` 404 while `ComplianceCard` links to it (wing is intentionally empty; tracked in compliance-wing plan).
- PDF `<iframe>` blank on iOS Safari (`AttachmentRenderer.tsx:190-197`) — needs a viewer/download fallback; separate small plan.
- Unifying the two library shells' header heights/z-indexes.
