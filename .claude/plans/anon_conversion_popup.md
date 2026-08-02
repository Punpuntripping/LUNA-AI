# Anon Conversion Popup («جرّب ريحان مجاناً») + Return-to-Page Auth

**Status:** PLAN — not built. Written 2026-08-01.
**Scope:** the five public content wings + a generic `?next=` return-to-page across all three auth paths.
**Companion:** `access_tiers_gating.md` (the gate CTA this must not collide with), `edu_popups.md` (the authed-side popup engine — different audience, different persistence, no shared code).

---

## 1. Concept

Today an anonymous reader on a public library page meets the conversion pitch exactly once: `BlogConversionCta` — a static block wedged between the content and the footer, which a reader who never reaches the footer never sees. It is the card in the screenshot, and it is fine; it is just passive.

This plan adds a **second, active surface**: the same pitch, as a popup, fired once the reader has demonstrably *read* something. Two rules define it:

1. **Engagement earns it.** The popup fires on scroll depth inside a document — never on arrival, never on a hub. A reader who bounced in 3 seconds is not asked for an account.
2. **Gating is irrelevant to it.** A free, never-gated مادة gets the same treatment as a gated نظام. The pitch is «this whole library has an AI on top of it», not «pay to see this paragraph» — so it does not care whether the current document is behind a gate.

The static `BlogConversionCta` **stays** exactly as it is. The popup is additive; §5 keeps them from stacking.

### Framing rule (inherited from `gate-copy.ts`)

> Gating must read as a curated feature, never a paywall slap.

The popup is the softest surface in the product — it interrupts a reader who is not being refused anything. So: no urgency, no countdown, no fake scarcity, no "you have read 3 of 5 free articles". One sentence of value, two buttons, an obvious X. If it cannot be dismissed in one click it is wrong.

---

## 2. Where it fires

### The five wings

| Wing | Hub route | Document routes |
|---|---|---|
| الأنظمة | `/regulations` | `/regulations/{slug}`, `/regulations/{slug}/{article}` |
| الامتثال | `/compliance` | `/compliance/{slug}` |
| التعاميم | `/circulars` | `/circulars/{slug}` |
| الأحكام | `/judgments` | `/judgments/{slug}` |
| المدونة | `/blog` | `/blog/{token}` |

**Documents only.** Hubs and their paginated siblings (`/{wing}/page/{n}`) are excluded: a directory grid has no reading depth, so a scroll trigger there measures nothing but a flick. Hubs already carry `HubCtaWall`, which is a full-page conversion surface of its own for anyone who tries to reach page 2.

```ts
// lib/anon-cta/eligibility.ts
const WINGS = ["regulations", "compliance", "circulars", "judgments", "blog"] as const;

/** A DOCUMENT under one of the five wings — not the hub, not a hub page. */
export function isEligibleDoc(pathname: string): boolean {
  const seg = pathname.split("/").filter(Boolean);      // "/regulations/x" → ["regulations","x"]
  if (seg.length < 2) return false;                      // the bare hub
  if (!WINGS.includes(seg[0] as (typeof WINGS)[number])) return false;
  if (seg[1] === "page") return false;                   // /{wing}/page/{n} is a hub
  return true;
}
```

### Mount points — two lines, total

Every one of the five wings already funnels through one of two shells:

- `components/library/blocks/LibraryPageShell.tsx` → regulations, compliance, circulars, judgments (docs **and** hub views, via `RegulationsHubView` et al.)
- `components/blog/BlogPageShell.tsx` → `/blog` and `/blog/{token}`

Mount `<AnonCtaPopup />` in both, right beside the existing `<BlogConversionCta />`. The component decides its own eligibility from `usePathname()`, so mounting it on ineligible routes costs nothing.

> **Deliberate consequence:** `LibraryPageShell` is also used by `/forms`, `/calculators`, and the `/library/{sector}` browse surfaces. Those mount the popup too but are filtered out by `WINGS`. Extending coverage later = adding a string to one array; no new mount, no new component.

**ISR safety.** `AnonCtaPopup` is a pure client component with zero server data — nothing it does can vary the shared ISR cache. This is the constraint `HubCtaWall`'s header comment spells out at length, and the reason the popup must never grow a server-side fetch.

---

## 3. The trigger — scroll engagement

### The core rule

**Two thresholds per document** — `ENGAGE_RATIOS = [0.35, 0.80]` of the document's scrollable distance. Each fires at most once per document, so an engaged reader meets the pitch twice on one long نظام: about a third of the way in, and again near the end. This is the «{n+1} period» applied *within* a document as well as across documents.

A fire additionally requires:

- `MIN_DWELL_MS` (8s) elapsed since the document mounted — the floor on the first impression, and
- `MIN_GAP_MS` (5s) since the previous impression on the same document, and
- no popup currently open.

The dwell floor is not decoration. Without it, a fling-scroll to the bottom on a phone — the single most common gesture on a long نظام — fires the popup in under two seconds, which reads as an ambush and converts nobody. `MIN_GAP_MS` exists because that same fling crosses **both** thresholds in one gesture; without it the reader gets two popups back to back.

```ts
const progress = (window.scrollY + window.innerHeight) / document.documentElement.scrollHeight;
```

Measured on a passive, `requestAnimationFrame`-throttled `scroll` listener. Detach it the moment the popup fires or the document becomes ineligible.

### The short-page fallback (this is the case the trigger gets wrong)

A مادة page is often shorter than the viewport. Its scroll progress is `1.0` on load, so a naive scroll trigger fires instantly on exactly the pages where the reader has read the least.

**Guard:** if the document is not meaningfully scrollable —

```ts
const scrollable = document.documentElement.scrollHeight > window.innerHeight * 1.2;
```

— abandon the scroll signal entirely and substitute a `SHORT_PAGE_DWELL` (20s) timer. Twenty seconds on a page that fits one screen is a reader who finished it and is thinking.

Re-evaluate `scrollable` on `resize` and once after any late reveal (`FullContentGate` swapping in a full document changes the page height dramatically — a page that was short can become long mid-visit).

### Constants — one file, `lib/anon-cta/config.ts`

```ts
export const ENGAGE_RATIOS    = [0.35, 0.80]; // of scrollable distance, one fire each
export const MIN_DWELL_MS     = 8_000;   // floor before the first impression
export const MIN_GAP_MS       = 5_000;   // between the two impressions on one doc
export const SHORT_PAGE_DWELL = 20_000;  // non-scrollable pages (ONE impression)
export const MAX_PER_SESSION  = 3;       // ROUNDS (documents that showed it), not raw impressions
export const QUIET_DOCS       = 2;       // docs skipped after a round — the {n+1} period
```

⚠ `MAX_PER_SESSION` counts **documents that showed the popup**, not impressions. With two thresholds a raw-impression cap of 3 would be spent halfway through document 2, which is not what the cross-document ladder in §4 means. 3 rounds ⇒ at most 6 impressions per session.

---

## 4. Cadence and dismissal — the `{n+1}` period

Impressions are bounded on two independent axes, and **both** must be satisfied.

**Axis 1 — the hard cap.** At most `MAX_PER_SESSION` (3) **rounds** per session, where a round is one document that showed the popup (up to two impressions — §3). A fourth round is never shown regardless of behaviour.

**Axis 2 — the quiet period.** An impression arms a cooldown of `QUIET_DOCS` (2) further eligible documents. The counter decrements on each *new* eligible document opened, not on scroll and not on a re-render of the same path.

```
doc 1   scroll 35% + 8s   →  POPUP  ┐ round 1
        scroll 80% + 5s   →  POPUP  ┘
                          →  quietFor armed (on the FIRST impression)
doc 2                     →  silent
doc 3                     →  silent
doc 4   scroll 35% / 80%  →  POPUP ×2       ← the {n+1} period, round 2
doc 5, 6                  →  silent
doc 7   scroll 35% / 80%  →  POPUP ×2       ← round 3 = last of the session
doc 8 …                   →  silent forever (cap reached)
```

**The quiet period arms on a document's FIRST impression and does not block its second.** A document that fires at 35% becomes the active round and stays permitted for its 80% threshold regardless of `quietFor` — otherwise the 35% fire would arm the cooldown, block the 80% fire, and silently collapse the feature back to one popup per document.

Two corrections to this section, both found during implementation and both already in the code:

- **⚠ The ladder is off by one if implemented literally.** Decrementing on open and firing when `quietFor === 0` silences exactly ONE document, not two: the document that decrements the counter *to* zero is then itself allowed to fire (doc 3 above). The stored value must therefore be `QUIET_DOCS + 1`, and `quietFor` is documented in the code as "documents to skip, **counting the current one**". The ladder above — impressions on 1, 4, 7 — is the intent and is what the code now produces; it is covered by a runtime test against a fake storage.
- **Arm on the impression, not on the dismissal.** Arming on dismiss cannot see a reader who navigates away with the popup still open: `quietFor` stays 0 and the very next document pitches immediately. Same ladder, one fewer failure mode.

**Instant mute.** Clicking either CTA sets `muted: true` — the reader is navigating to `/login`, and if they come back without signing up, being pitched again in the same session is nagging.

### Persistence — `sessionStorage`, key `luna_anon_cta_v1`

```ts
interface AnonCtaState {
  v: 1;
  shown: number;      // impressions so far this session
  quietFor: number;   // eligible docs still to skip
  muted: boolean;     // CTA clicked → done for this session
  lastDoc: string;    // last eligible pathname, so a re-render ≠ a new doc
}
```

`sessionStorage`, not `localStorage`, and not the auth store:

- a new tab starts fresh, which is the behaviour chosen ("session only, then re-arm");
- it survives same-tab navigation between documents, which is exactly the lifetime the counter needs;
- it dies with the tab, so nothing persists about a visitor who never created an account — a PDPL-friendly default and one less thing to declare in `/privacy`.

Every accessor is `try/catch`-guarded and **fails closed** (storage unavailable ⇒ treat as muted ⇒ never show). Same principle as `post-login-intent.ts`: a popup is best-effort, and a broken read must never produce an unbounded loop of impressions.

---

## 5. Suppression — never two calls to action at once

The popup is the **fourth** conversion surface an anonymous reader can meet on a single document (`FullContentGate`'s anon reveal panel, `HubCtaWall`, `BlogConversionCta`, and now this). The gate chain below is what keeps it from ever being the second one *visible at the same moment*. All must pass:

1. **Anonymous only.** `isAuthenticated === false && isLoading === false`. Identical rule to `BlogConversionCta` — a signed-in reader is already converted, and the session probe must resolve first or the popup flashes at returning users on every page load.
2. **Route eligible** (§2).
3. **Not muted, `shown < MAX_PER_SESSION`, `quietFor === 0`** (§4).
4. **No other dialog open.** Proxy: `document.querySelector('[role="dialog"]') === null`. The reference-source dialog, the usage-limits dialog and the onboarding tour all register as Radix dialogs; stacking a pitch on top of one is the worst version of this feature. (Same proxy `edu_popups.md` gate 8 specifies.)
5. **No anon CTA already on screen.** Tag the existing surfaces with `data-anon-cta` — the anon branch of `FullContentGate`'s `RevealPanel`, the `Wall` in `HubCtaWall`, and `BlogConversionCta` — and skip the fire if any tagged element is intersecting the viewport. A reader looking at «سجّل مجاناً لعرض النص كاملاً» does not need a modal that says the same thing. Cheap `IntersectionObserver`, evaluated only at fire time.
6. **Trigger satisfied** (§3).

A blocked fire is **dropped, not queued**. If the moment passed, the popup should not ambush the reader ten seconds later — it re-arms naturally on the next document. (`edu_popups.md` gate 3, same reasoning.)

### Googlebot

Googlebot renders JavaScript but does not scroll and does not dwell. **The trigger therefore never fires for the crawler** — not because we detect it, but because it never performs the gesture. Same JS ships to every client; no cloaking, no branch on user-agent, and no interstitial in the indexed render.

This is the specific reason the scroll trigger is safer than a timer on these pages: a time-based popup *would* fire in a headless render and would sit squarely inside Google's intrusive-interstitial guidance for mobile search landings.

---

## 6. The card

Content is the existing `BlogConversionCta` pitch, verbatim — one pitch, one wording, and any future rewrite touches one string table.

```
        ✦   (icon tile, bg-primary, text-primary-foreground)

        جرّب ريحان مجاناً

  المساعد القانوني الذكي للمحامين السعوديين — أنشئ تحليلاتك
      القانونية ومذكراتك مدعومة بالأنظمة والسوابق.

   [ ✦ ابدأ الآن ]        [ تسجيل الدخول ← ]
```

Copy lives in `lib/anon-cta/copy.ts` (mirroring the `gate-copy.ts` discipline: no Arabic string hardcoded in a component).

### Form factor

| Viewport | Shape |
|---|---|
| `≥ sm` | Centred modal `Dialog`, `max-w-md`, overlay, Esc + overlay-click + X to close |
| `< sm` | **Bottom sheet**, `max-h-[60vh]`, content still visible above it |

The mobile constraint is not aesthetic. An interstitial that covers the main content on a search landing is the exact pattern Google demotes; a sheet occupying the bottom 60% leaves the article readable and sits outside that definition. Combined with §5's "never on arrival", the surface stays compliant on both counts.

Built on the existing `components/ui/dialog.tsx` (Radix — focus trap, Esc, `aria-modal`, and the RTL-aware `start-[50%]` positioning are already handled). The X already renders `<span className="sr-only">إغلاق</span>`.

`prefers-reduced-motion` → fade only, no slide.

### ⚠ Overriding the shared dialog is not a plain class merge (found during implementation)

Three things bite when restyling `DialogContent` from a caller:

1. **`tailwind-merge` has no class groups for `tailwindcss-animate`.** An unprefixed override of `slide-in-from-left-1/2` or `zoom-in-95` survives alongside the base class and then loses on CSS source order (verified by generating the real CSS: `slide-in-from-left-0` is emitted *before* `slide-in-from-left-1/2`). Only media-variant utilities are guaranteed to come later — hence the sheet's entrance is expressed with `max-sm:` / `sm:` variants, and reduced motion is handled with inline `--tw-enter-*` / `--tw-exit-*` custom properties rather than `motion-reduce:`. Positional and sizing classes (`top`, `bottom`, `max-w`, `translate-*`, incl. the `rtl:` variant) *do* merge correctly and can be passed directly.
2. **RTL + transform centring.** shadcn centres with a transform, and the enter keyframe *replaces* `transform` — so in RTL the element slides in by its own width. Tolerable on a `max-w-lg` modal, a full-screen swipe on a full-width sheet. Horizontal transform centring is therefore dropped at both breakpoints (`start-0 end-0 mx-auto`) and the enter X pinned to 0.
3. **The overlay cannot be restyled from a caller at all.** `DialogContent` renders its own `<DialogOverlay className="bg-black/80">`. So on mobile the article behind the 60vh sheet is *visible but dimmed to 20%*, and Radix's modal mode locks body scroll while the sheet is open.

Point 3 is worth a decision rather than a silent default. Note that it is **not** an SEO question: per §5, Googlebot never scrolls and therefore never fires the popup, so no interstitial exists in anything Google observes. It is purely about how heavy the interruption feels to a reader mid-article. Options: leave as is (heaviest), add an `overlayClassName` prop to the shared `ui/dialog.tsx` and use a lighter dim below `sm`, or render the mobile sheet non-modal so the page keeps scrolling behind it.

---

## 7. Return-to-page after auth — including the email-verification path

**Answering the question directly: yes, the email-confirmation link can return the reader to the page they were reading, and it is not complex.** It is the same one-parameter change that fixes the Google-OAuth path. **Production needs no Supabase config change; local dev does** — verified empirically, §7.5. There is one genuine edge case (§7.4) and it degrades gracefully.

### 7.1 Why a URL parameter, not `sessionStorage`

The codebase already has a return-to mechanism: `lib/post-login-intent.ts` stashes an intent in `sessionStorage` and `AuthGuard` consumes it after login — that is how `claim_anon_answer` returns a visitor to `return_to`.

**It cannot carry the verification path.** A confirmation email is frequently opened on a different device or browser than the one that signed up (phone mail app vs desktop). `sessionStorage` is per-tab and per-origin-per-browser; there is nothing to read on the other side.

So this feature uses **`?next=<site-relative-path>` on the URL** as the single carrier for all three paths. `post-login-intent` is left completely untouched — it still owns the three richer intents that need to *do* something after login (create a conversation, claim an answer, copy a form). Plain "put me back where I was" does not need a state machine.

### 7.2 The three paths

| Path | Round-trip | Carrier | Change needed |
|---|---|---|---|
| **A.** Email + password login | none (same tab) | `?next=` on `/login` | `LoginForm` redirects to `next` instead of `/chat` |
| **B.** Google OAuth | Supabase → Google → `/auth/callback` | `?next=` on the callback URL | `redirectTo` carries it; the route reads it |
| **C.** Email verification | GoTrue `/auth/v1/verify` → `/auth/callback` | `?next=` on the callback URL | `emailRedirectTo` carries it; the route reads it |

B and C converge on the **same** file — `app/auth/callback/route.ts:63`, currently a hardcoded `NextResponse.redirect(\`${base}/chat\`)`. One change serves both.

Flow for C, concretely:

```
popup CTA on /regulations/labor-law
   → /login?next=%2Fregulations%2Flabor-law&mode=register
   → supabase.auth.signUp({ options: {
         emailRedirectTo: `${origin}/auth/callback?next=%2Fregulations%2Flabor-law` } })
   → email link: https://<ref>.supabase.co/auth/v1/verify?token=…&redirect_to=<that URL>
   → 302 → /auth/callback?next=%2Fregulations%2Flabor-law&code=…
   → exchangeCodeForSession(code)   [writes the session cookie]
   → 302 → /regulations/labor-law   [signed in, back where they started]
```

GoTrue preserves query parameters already present on `redirect_to` and appends its own `code`. Nothing custom is required on the Supabase side beyond §7.5.

### 7.3 The open-redirect guard — mandatory

`next` arrives from a URL and is therefore attacker-controlled. `//evil.com` and `/\evil.com` are both browser-valid protocol-relative redirects. Validate on **every** read, server and client:

```ts
// lib/safe-next.ts — one implementation, imported by LoginForm and the callback route.
const ALLOWED = ["/regulations", "/compliance", "/circulars", "/judgments", "/blog", "/chat"];

export function safeNext(raw: string | null): string {
  if (!raw) return "/chat";
  let path: string;
  try {
    path = decodeURIComponent(raw);
  } catch {
    return "/chat";
  }
  if (!path.startsWith("/")) return "/chat";        // absolute URL / scheme
  if (path.startsWith("//") || path.startsWith("/\\")) return "/chat";  // protocol-relative
  if (!ALLOWED.some((p) => path === p || path.startsWith(`${p}/`))) return "/chat";
  return path;
}
```

Allowlist, not a denylist. `/chat` is the fallback and is itself allowed, so a stale or hostile `next` silently produces today's exact behaviour.

**⚠ One check the snippet above is missing — added during implementation.** Reject every C0 control character and DEL *before* the prefix tests:

```
/%09/evil.com   →  decodes to  /<TAB>/evil.com
```

Browsers strip tab, LF and CR out of a URL **before resolving it**, so that resolves as the protocol-relative `//evil.com` and walks straight past `startsWith("//")`. Rejecting all control characters is simpler and safer than modelling the stripping rules, and it also closes CR/LF header injection on the server side. Implement it with char codes rather than a regex literal so no raw control byte ever lands in the source.

**The allowlist is wider than the popup's `WINGS`, deliberately.** It carries `/forms`, `/calculators` and `/library` in addition to the five wings, because `LibraryPageShell` mounts `BlogConversionCta` on those routes too — without them a CTA click there drops `next` and silently lands on `/chat`. The two lists answer different questions (*where does the popup fire* vs *where may auth return someone*) and must stay separate; both files carry a comment saying so.

### 7.4 The one real limitation — cross-device verification

`lib/supabase.ts` uses `createBrowserClient` from `@supabase/ssr`, which defaults to **PKCE**. The `code_verifier` is a cookie in the browser that ran `signUp()`. If the confirmation link is opened in a *different* browser, `exchangeCodeForSession` fails — **today, already, before this plan**. The current code then redirects to `/login?error=oauth`, which shows «تعذّر تسجيل الدخول عبر Google» — a Google error message for an email confirmation. Confusing, and pre-existing.

This plan does not fix PKCE (the alternatives — implicit flow, or a server-side verify endpoint — are a much larger change and weaken the auth model). It **degrades properly** instead:

- on exchange failure **or a missing `code`**, redirect to `/login?next=<next>&notice=verify_elsewhere`;
- `LoginForm` renders «تم تأكيد بريدك. سجّل الدخول للمتابعة.» for that notice — not the Google error;
- after the manual login, `next` is still on the URL, so the reader lands on the page they were reading.

The "missing `code`" case is deliberately folded in here, because per §7.5 the route handler **cannot see the reason**: an expired or already-used email link arrives with its `error` in the URL *fragment*, which the browser never transmits. From the server the two situations are indistinguishable — `?next=…`, no `code`. Hence one reason-neutral notice covering both, and hence the copy says «سجّل الدخول للمتابعة» rather than naming a cause it cannot know.

Distinguishing them is still possible if it ever matters — the fragment survives into the browser, so a client component could read `window.location.hash` — but that means converting `/auth/callback` from a route handler into a page, which is a real restructuring for a marginal copy improvement. Out of scope.

Net: same-browser verification returns them automatically; cross-device verification returns them after one manual login. Both are strictly better than today's unconditional `/chat`.

### 7.5 ⚠ Supabase Redirect URLs allowlist — prod is fine, LOCAL DEV IS NOT

Supabase's *Redirect URLs* allowlist is matched against the **full** `redirect_to` URL, query string included. An exact entry therefore rejects `…/auth/callback?next=…`; GoTrue falls back to the Site URL and the `next` vanishes with no error anywhere.

**Probed against the live project, 2026-08-01** — `GET /auth/v1/verify` with a deliberately invalid token, comparing the `Location` header (an invalid token still exercises the allowlist, so this is a safe read-only probe):

| Origin | bare `/auth/callback` | with `?next=…` | Allowlist entry |
|---|---|---|---|
| `https://rayhanai.com` | ✅ honoured | ✅ **honoured, query preserved** | **wildcard** |
| `https://www.rayhanai.com` | ✅ honoured | ❌ → `https://rayhanai.com` | exact |
| `http://localhost:3000` | ✅ honoured | ❌ → `https://rayhanai.com` | exact |
| `https://luna-frontend-production-1124.up.railway.app` | ✅ honoured | ❌ → `https://rayhanai.com` | exact |
| `https://evil.example.com/x` | ❌ → `https://rayhanai.com` | — | absent (control) |

Anything under the apex — `…/random-path-xyz`, `…/auth/callback/deeper`, any query — is honoured, so that entry is a broad wildcard. Every other origin is an exact string.

**Consequences, in order of importance:**

1. **Production works with no config change.** The apex wildcard already accepts `?next=…`. This is the deployed path and it needs nothing.
2. **`www` is a non-issue.** `https://www.rayhanai.com/` returns `308 → https://rayhanai.com` at the Railway edge, so the browser is never *on* `www` when the app's JS reads `window.location.origin`. The exact entry is never exercised with a query.
3. **Local dev is broken and fails confusingly.** On `localhost:3000`, `emailRedirectTo` becomes `http://localhost:3000/auth/callback?next=…`, which is rejected → the developer is redirected to **`https://rayhanai.com`**. Testing signup on localhost throws you into production, which reads as a code bug and is not one.

**Required for dev (not for prod):** add wildcard entries —

```
http://localhost:3000/auth/callback*
https://luna-frontend-production-1124.up.railway.app/auth/callback*
```

Scoped to `/auth/callback*` rather than `/**`: the apex entry is already broader than it needs to be (see T3 — it means the allowlist provides no meaningful redirect defence, and `safeNext()` is the only real guard), and there is no reason to replicate that.

Re-run the probes if the allowlist is edited or GoTrue is upgraded:

```bash
curl -sS -o /dev/null -D - \
  "https://dwgghvxogtwyaxmbgjod.supabase.co/auth/v1/verify?token=x&type=signup&redirect_to=<url-encoded target>" \
  | grep -i "^location"
```

> **Second finding from the same probe, and it matters for §7.4:** the failure reason comes back in the URL **fragment** (`#error=access_denied&error_code=otp_expired`), not the query string. A fragment is never transmitted to the server, so `app/auth/callback/route.ts` — a route handler — **cannot** read why an email link failed. All it observes is `?next=…` with no `code`. The notice copy must therefore stay reason-neutral (§7.4); do not write a branch that expects `?error=` on an expired email link, because it will never arrive.

### 7.6 ⚠ Build trap — `useSearchParams()` needs Suspense

`app/login/page.tsx` is a server component. Calling `useSearchParams()` inside `LoginForm` forces the route into client rendering and fails `next build` with *"useSearchParams() should be wrapped in a suspense boundary"*.

Do not reach for `useSearchParams`. `LoginForm` already reads a query parameter — the `?error=oauth` handler at `LoginForm.tsx:79-84` — via `new URLSearchParams(window.location.search)` inside a `useEffect`. **Read `next` and `notice` the same way**, in the same effect. Zero new imports, no Suspense boundary, and consistent with the file's existing idiom.

Note `useSearchParams` appears nowhere in the frontend today, so this trap has never been hit — it will be, the first time someone reaches for the idiomatic hook here.

### 7.7 `mode=register`

The popup's primary button says «ابدأ الآن», which promises signup, but `/login` opens in login mode and the reader has to find the toggle. Carry `&mode=register` and have the same effect seed `useState<"login"|"register">`. Small, but it is the difference between the button meaning what it says and not.

---

## 8. Trap list

| # | Trap | Consequence | Guard |
|---|---|---|---|
| T1 | Supabase Redirect URLs entry is exact, not wildcard | `next` silently dropped, no error. **Prod is safe** (apex is wildcarded); **localhost is not** — a dev testing signup is redirected into production | §7.5 — add `http://localhost:3000/auth/callback*` before testing locally |
| T1b | Expecting `?error=` on a failed email link | Branch never fires; reader gets the Google error message | §7.5 / §7.4 — the reason lives in the **fragment**, invisible to a route handler. Treat "no `code`" as the signal |
| T2 | `useSearchParams()` in `LoginForm` | `next build` fails on the Suspense rule | §7.6 — `window.location.search` in an effect, like the existing `?error=oauth` |
| T3 | Unvalidated `next` | Open redirect from a public, indexed page | §7.3 — `safeNext` allowlist, applied server **and** client |
| T4 | Short مادة page | Scroll progress is 1.0 on load → popup fires instantly | §3 — `scrollable` check → 20s dwell instead |
| T5 | `FullContentGate` reveal lengthens the page | A "short" page becomes long; stale `scrollable` reading | §3 — re-evaluate on `resize` and after a reveal |
| T6 | Popup over the gate's own anon CTA | Two signup pitches, one modal over the other | §5 gate 5 — `data-anon-cta` intersection check |
| T7 | Session probe not settled | Popup flashes at signed-in readers on every page load | §5 gate 1 — require `isLoading === false` |
| T8 | Counting re-renders as documents | `quietFor` drains without the reader opening anything | §4 — `lastDoc` path comparison, not a render counter |
| T9 | Any server fetch added to the popup | Poisons the shared ISR cache for every visitor | §2 — pure client component, permanently |
| T10 | Timer-only trigger | Fires in a headless render → intrusive interstitial in the indexed page | §5 — scroll is the primary signal; the dwell fallback needs a real viewport |
| T11 | `sessionStorage` unavailable (privacy mode, SSR) | Throw on read, or unbounded impressions | §4 — `try/catch`, fail **closed** to muted |
| T12 | Cross-device email confirmation | PKCE exchange fails → misleading Google error | §7.4 — `notice=verify_elsewhere`, preserve `next` |

---

## 9. File manifest

### New — 5 files

| File | Purpose |
|---|---|
| `frontend/lib/anon-cta/config.ts` | The five constants (§3) |
| `frontend/lib/anon-cta/copy.ts` | Every Arabic string the popup renders |
| `frontend/lib/anon-cta/session.ts` | `sessionStorage` state: read / arm / record / mute, all fail-closed |
| `frontend/lib/safe-next.ts` | `safeNext()` — the open-redirect guard, shared by client and route handler |
| `frontend/components/marketing/AnonCtaPopup.tsx` | Eligibility + trigger + gate chain + the Dialog/sheet |

### Modified — 8 files

| File | Change |
|---|---|
| `frontend/components/library/blocks/LibraryPageShell.tsx` | Mount `<AnonCtaPopup />` beside `<BlogConversionCta />` |
| `frontend/components/blog/BlogPageShell.tsx` | Same |
| `frontend/components/blog/BlogConversionCta.tsx` | Add `data-anon-cta` (T6) |
| `frontend/components/library/FullContentGate.tsx` | Add `data-anon-cta` to the anon `RevealPanel` branch (T6) |
| `frontend/components/library/hub/HubCtaWall.tsx` | Add `data-anon-cta` to `Wall` (T6) |
| `frontend/components/auth/LoginForm.tsx` | Read `next` / `mode` / `notice` in the existing effect; redirect to `safeNext(next)`; pass `next` into `redirectTo` and `register()` |
| `frontend/stores/auth-store.ts` | `register(..., returnTo?)` → `emailRedirectTo: /auth/callback?next=…` |
| `frontend/app/auth/callback/route.ts` | Redirect to `safeNext(next)`; on exchange failure `→ /login?next=…&notice=verify_elsewhere` |

Five of the eight are one-liners: two mounts and three `data-anon-cta` tags. The real work is `LoginForm`, `auth-store`, and the callback route.

### Zero backend changes

No API, no migration, no new table. Impressions are deliberately **not** tracked server-side: an anonymous-visitor counter is a personal-data question the product does not need to answer to ship this.

### Config — dev only

**Production: none.** The apex `rayhanai.com` allowlist entry is already a wildcard and accepts `?next=…` (§7.5, probed live).

**Local dev + Railway preview: two allowlist entries** (§7.5). Not a deploy blocker — a *testing* blocker. Add them before step 2 of the build order or path C cannot be verified locally.

---

## 10. Build order

1. **`safe-next.ts` + the callback route + `LoginForm` + `auth-store`.** Return-to-page shipped and verifiable on its own, using the CTAs that already exist — `BlogConversionCta`'s two buttons become `/login?next=…`. No popup yet. This half is independently useful and independently testable.
2. **Add the two dev allowlist entries** (§7.5 — localhost + Railway), then **verify path C end to end** with a throwaway address, same browser and cross-browser. Skipping the entries makes local verification redirect into production and look like a code bug.
3. **`config.ts` + `copy.ts` + `session.ts`.** Pure logic. Unit-testable without a DOM: feed `session.ts` a fake storage and assert the `{n+1}` ladder in §4 exactly.
4. **`AnonCtaPopup.tsx`** — eligibility and gate chain first, rendering `null` always; confirm with a temporary `console.debug` that it arms on the right routes and never on hubs. Then the Dialog.
5. **`data-anon-cta` tags** (T6) and the intersection gate.
6. **Mount in both shells.** Coverage of all five wings lands in this one step.
7. Playwright verification (§11), then tune `ENGAGE_RATIO` / `QUIET_DOCS` from real behaviour — they are in one file for exactly that reason.

---

## 11. Verification

### Auth return-to (ship and verify this half first — it stands alone)

| # | Check | Pass |
|---|---|---|
| 1 | `/login?next=%2Fblog%2Fx`, log in with email+password | Lands `/blog/x`, signed in |
| 2 | Same URL, Google OAuth | Lands `/blog/x`, signed in |
| 3 | Sign up from `/regulations/{slug}`, open the email link **in the same browser** | Lands `/regulations/{slug}`, signed in |
| 4 | Same, open the link in a **different** browser | Lands `/login` with «تم تأكيد بريدك…» — *not* the Google error — and completing login lands on `/regulations/{slug}` |
| 5 | `/login?next=//evil.com`, `/login?next=https://evil.com`, `/login?next=/admin` | All three land `/chat` |
| 6 | `/login` with no `next` | Lands `/chat` — unchanged |

### Popup behaviour

| # | Check | Pass |
|---|---|---|
| 7 | Anon, `/regulations/{long-slug}`, scroll to 40% within 3s | **No** popup (dwell floor, T4) |
| 8 | Same, scroll to 40% after 10s | Popup (35% threshold) |
| 8b | Dismiss it, keep reading to 85% | Popup again (80% threshold) — two per document |
| 8c | Fling-scroll 0 → bottom in one gesture | **One** popup, not two stacked (`MIN_GAP_MS`) |
| 8d | Document between 1.2 and 1.8 viewports, no scrolling at all | No popup — resting progress already exceeds 0.35 |
| 9 | Anon on a one-screen مادة, wait 20s without scrolling | Popup **once** (short-page path yields one, not two) |
| 10 | Anon on `/regulations` and `/regulations/page/2` | Never, at any scroll depth |
| 11 | Signed-in, any document | Never — and no flash during the session probe (T7) |
| 12 | Finish a document's round, then open 2 more documents | Silent on both; popup returns on the 3rd (§4 ladder) |
| 13 | Continue past 3 rounds / 6 impressions | Never shown again (cap) |
| 14 | Click «ابدأ الآن», return with the back button, keep browsing | Never shown again this session (mute) |
| 15 | New tab, same site | Counter fresh — popup available again |
| 16 | Open the reference-source dialog, then scroll | No popup over it (gate 4) |
| 17 | Gated نظام, scroll so the reveal panel is on screen | No popup while it is visible (T6) |
| 18 | Mobile viewport | Bottom sheet ≤ 60vh, article still visible behind it |
| 19 | Popup open → Esc, overlay click, X | All three close it; focus returns to the document |
| 20 | `curl` the page HTML | No popup markup in the SSR output — client-only, crawler never fires it (§5) |

`cd frontend && npx tsc --noEmit && npm run lint && npm run build` must pass — the build is what catches T2.
