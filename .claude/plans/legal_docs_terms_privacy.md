# Plan — Terms & Privacy pages + signup consent (B) + settings links

**Goal:** Publish the Terms (`legal/terms-ar.md`) and Privacy (`legal/privacy-ar.md`) drafts as live, public pages in the ريحان app, capture **explicit mandatory-checkbox consent** at signup (option B), and expose both docs from Settings + footers.

**Status:** PLAN — not built. Drafts complete & reviewed in-session. Author = mhfallath (Saudi lawyer, reviews legal text himself).

---

## Locked decisions

- **Consent style = B** — mandatory checkbox at registration (blocks submit until checked). Strongest evidence vs. statement-by-action.
- **Placement = 4 surfaces:** (1) public pages `/terms` + `/privacy`, (2) signup checkbox, (3) Settings popover links, (4) footer on login (+ blog) pages.
- **Renderer = reuse** `frontend/components/chat/MarkdownRenderer.tsx` (react-markdown + remark-gfm; already handles tables/blockquotes/lists/hr/RTL).
- **Source of truth = `frontend/content/legal/*.md`** (relocated from repo-root `legal/`), imported as a baked string at build time.

---

## Architecture decisions (the two that matter)

### 1. How the page gets the markdown — `asset/source` import (build-time bake)
Repo-root `legal/` is OUTSIDE `frontend/`, so the frontend Docker build won't ship it, and runtime `fs` path-tracing in Next standalone is fragile. Therefore:
- **Relocate** the two `.md` into `frontend/content/legal/terms-ar.md` + `privacy-ar.md` (canonical home; delete repo-root copies to avoid drift).
- Add a webpack rule in `frontend/next.config.mjs` so `.md` imports resolve to their raw string:
  ```js
  // inside webpack(config) { ... return config }
  config.module.rules.push({ test: /\.md$/, type: "asset/source" });
  ```
- Page does `import termsMd from "@/content/legal/terms-ar.md";` → content baked into the bundle, fully portable, zero runtime fs. (Fallback if we dislike the webpack rule: store as `content/legal/terms-ar.ts` exporting a template-string constant.)

### 2. Consent must be RECORDED, not just gated (else B ≈ A)
A checkbox that only disables a button leaves no evidence. The persistence phase (Phase 5) stamps `terms_accepted_at` + `terms_version` on the user row. **Phase 5 is what gives option B its legal value** — recommended, but separable if we want UI first.

---

## Phase 0 — Content prep (do first; blocks go-live)
1. Relocate `legal/terms-ar.md` + `legal/privacy-ar.md` → `frontend/content/legal/`.
2. **Strip the internal admin note** from the top of each file (the `> ⚠️ عناصر يجب على المالك تعبئتها...` blockquote) — it must NOT render publicly. Keep the real `[placeholders]` only if still unfilled.
3. **Fill the `[placeholders]`** (or accept they render literally until filled): `[الاسم النظامي للشركة]`, `[رقم السجل التجاري]`, `[العنوان الوطني]`, contact + privacy emails. ⚠️ Going live with raw `[brackets]` looks unfinished — fill before deploy.
4. Add `frontend/lib/legal.ts` → `export const LEGAL_VERSION = "2026-06-22";` (single source for the version users consent to; bump when docs change to re-prompt).

## Phase 1 — Public pages
| File | Action |
|---|---|
| `frontend/app/terms/page.tsx` | NEW — server component; `import termsMd`; render via `<LegalPageShell title="الشروط والأحكام">`; `metadata` (title/desc); static. |
| `frontend/app/privacy/page.tsx` | NEW — same for سياسة الخصوصية. |
| `frontend/components/legal/LegalPageShell.tsx` | NEW — RTL shell: ريحان logo header, centered `max-w-3xl`, renders `<MarkdownRenderer content={md}/>`, back-link (`/login` if anon, `/chat` if authed — or simple "العودة"), bottom `LegalLinksFooter`. |
| `frontend/components/auth/AuthGuard.tsx` | EDIT line 15 → `const PUBLIC_PREFIXES = ["/blog", "/terms", "/privacy"] as const;` |
| `frontend/next.config.mjs` | EDIT — add `.md` asset/source webpack rule (see Arch §1). |

Polish note: `MarkdownRenderer` headings are sized "for chat" — wrap in `LegalPageShell` with heading-size overrides (or a `prose` container) so a full page reads well. Optional, low-priority.

## Phase 2 — Signup consent (B)
Edit `frontend/components/auth/LoginForm.tsx`:
- New state `agreedToTerms` (reset in `toggleMode`).
- In `mode === "register"` only: checkbox row —
  `☐ أوافق على [الشروط والأحكام](/terms) و[سياسة الخصوصية](/privacy)` (links `target="_blank"`).
- Gate: add to `validate()` for register → if `!agreedToTerms`, error `"يجب الموافقة على الشروط وسياسة الخصوصية"`; also disable the "إنشاء حساب" button while unchecked.
- **Google OAuth nuance (must handle):** Google auto-creates an account on first sign-in, and the Google button shows in both modes — a first-time user in *login* mode would bypass the checkbox. Solution = **always-visible fine-print line** under the form covering Google by action: `"بالمتابعة عبر Google، فإنك توافق على الشروط وسياسة الخصوصية."` + (in register mode) also require the checkbox before the Google button fires. This hybrid keeps consent clean for both email and Google paths.
- Pass `LEGAL_VERSION` into `register()` (for Phase 5).

## Phase 3 — Settings links
Edit `frontend/components/sidebar/SidebarFooter.tsx` settings `PopoverContent`:
- Add a `<Separator/>` + two ghost buttons (icons `FileText`, `ShieldCheck` from lucide) → "الشروط والأحكام" / "سياسة الخصوصية", each `router.push` (or `<a target="_blank">`) to `/terms` `/privacy`. Match the existing `data-testid` + justify-between `›` pattern.

## Phase 4 — Footer links
- `frontend/components/legal/LegalLinksFooter.tsx` — NEW tiny component: "الشروط والأحكام · سياسة الخصوصية" muted links.
- Render on `frontend/app/login/page.tsx` (under the form) and inside `LegalPageShell`. Optional: blog pages.

## Phase 5 — Consent persistence (recommended; makes B meaningful)
| Layer | Change |
|---|---|
| DB | Migration `075_user_terms_consent.sql` — `ALTER TABLE users ADD COLUMN terms_accepted_at timestamptz, ADD COLUMN terms_version text;` |
| Backend (email register) | Accept `terms_version` in the register payload; stamp `terms_accepted_at = now()`, `terms_version`. (Find register route in `backend/app/.../auth`.) |
| Backend (Google) | Stamp consent on first-time account creation in the OAuth upsert path (trace from `frontend/app/auth/callback/route.ts` → backend user-bootstrap). Trickiest bit — Google users never hit the email register route. |
| Frontend | `register()` in `auth-store` sends `LEGAL_VERSION`; (optional) re-prompt when stored version ≠ current. |

⚠️ Verify live `users` schema via Supabase MCP before writing 075 (see [[project_migration_drift]] — migration files ≠ prod).

---

## File manifest
**New:** `frontend/app/terms/page.tsx`, `frontend/app/privacy/page.tsx`, `frontend/components/legal/LegalPageShell.tsx`, `frontend/components/legal/LegalLinksFooter.tsx`, `frontend/content/legal/{terms,privacy}-ar.md`, `frontend/lib/legal.ts`, (P5) `shared/db/migrations/075_user_terms_consent.sql`.
**Edit:** `frontend/components/auth/AuthGuard.tsx`, `frontend/next.config.mjs`, `frontend/components/auth/LoginForm.tsx`, `frontend/components/sidebar/SidebarFooter.tsx`, `frontend/app/login/page.tsx`, (P5) `auth-store` + backend register route + auth callback path.

## Test checklist
- [ ] `/terms` + `/privacy` load logged-OUT (AuthGuard) and render md (headings/lists/blockquotes/tables RTL).
- [ ] Register submit blocked until checkbox checked; error shows; toggle resets it.
- [ ] Google fine-print visible in both modes; register-mode Google requires checkbox.
- [ ] Settings popover → both links open correct pages.
- [ ] Login footer links work logged-out.
- [ ] `npx tsc --noEmit` clean; `npm run build` OK (asset/source rule).
- [ ] (P5) email + Google signups write `terms_accepted_at`/`terms_version`; verify rows in Supabase.

## Open items for the user
1. **Phase 5 now or later?** Build consent persistence (DB+backend) this round, or ship UI (Phases 0–4) first and add the record after?
2. **Fill placeholders now?** Provide entity name / CR / national address / emails — or ship with `[brackets]` temporarily (not recommended for go-live).
3. **Back-link target** from a legal page for anon vs authed users — `/login` vs `/chat` vs just history-back.

Related memory: [[project_legal_docs_terms_privacy]], [[project_new_convo_attachments]] (create-on-attach nav pattern), [[project_blog_share_links]] (public-page + AuthGuard allow-list precedent).
