# Luna Design System

**Luna (لونا)** is an Arabic-first legal AI assistant for Saudi lawyers — a ChatGPT-like workspace where lawyers manage cases, chat with specialized legal AI agents, upload and query case documents, and generate drafted legal artifacts (contracts, memos, legal opinions).

The interface is **RTL Arabic by default**, with English/code content isolated in LTR spans. Everything in this design system — tokens, components, copy — assumes Arabic first.

## Source

This system was reverse-engineered from a single attached codebase:

- **`frontend/`** — Next.js 14 App Router + TypeScript + Tailwind CSS + Radix UI + Zustand, mounted read-only via the Import menu. Core paths referenced:
  - `frontend/app/globals.css` — OKLCH channel tokens, light + dark themes
  - `frontend/tailwind.config.ts` — color + radius mapping
  - `frontend/app/layout.tsx` — `html lang="ar" dir="rtl"`, IBM Plex Sans Arabic via `next/font`
  - `frontend/components/{ui,sidebar,chat,artifacts,documents,auth}/` — shadcn-derived + product components
  - `frontend/components/auth/LoginForm.tsx`, `frontend/app/login/page.tsx` — auth shell
  - `frontend/components/chat/{ChatContainer,ChatInput,MessageBubble,AgentSelector,TemplateCards,CitationPills}.tsx` — chat surface
  - `frontend/components/sidebar/{Sidebar,SidebarHeader,SidebarFooter,CaseCard,ConversationItem}.tsx` — left nav
  - `frontend/components/artifacts/{ArtifactPanel,ArtifactCard}.tsx` — generated-document panel

No slide decks, Figma files, or brand guidelines were attached. This system is faithful to what's in code; any brand guidance beyond that is flagged as **inferred**.

## Products represented

One product, one surface: the **Luna web app** (`/login`, `/chat`, `/chat/[id]`, `/cases/[id]`, `/settings`). The sidebar has two tabs — **المحادثات** (Conversations) and **القضايا** (Cases) — and a chat view with an optional right-side **Artifacts** panel.

## Index

- `README.md` — this file (product context, content, visual foundations, iconography)
- `colors_and_type.css` — single source of truth for color + type tokens (OKLCH, both themes)
- `fonts/ibm-plex-sans-arabic.css` — Google Fonts import for IBM Plex Sans Arabic
- `assets/` — logo lockups, brand marks, icon notes
- `preview/` — Design System tab cards (registered via the review manifest)
- `ui_kits/web/` — Luna web app UI kit (React components + a click-through prototype)
- `SKILL.md` — Agent Skill manifest for cross-compatible use

---

## CONTENT FUNDAMENTALS

**Language.** Arabic (Modern Standard Arabic, RTL). Every user-facing string in the shipped app is Arabic; English only appears in code blocks, URLs, email/password fields, and occasional file metadata — all wrapped in LTR spans.

**Voice.** Professional, calm, respectful of the lawyer's expertise. Luna is an assistant, not a pundit. It never jokes and never hypes. When something works, it says so plainly (`تم النسخ` — "Copied"); when something fails, it apologizes without flourish (`حدث خطأ غير متوقع. حاول مرة أخرى.` — "An unexpected error occurred. Try again.").

**Person.** Second person, informal-polite (`أنت` implicit). Prompts are imperative and direct: `اكتب رسالتك هنا...` ("Type your message here..."), `اختر من القوالب:` ("Choose from templates:"). The product refers to itself as **لونا** / **لونا القانونية** ("Luna Legal") — first-person `أنا` is avoided in UI copy.

**Tone by surface.**
- **Nav / buttons** — one or two words. `تسجيل الدخول` (Sign in), `حذف` (Delete), `إرسال` (Send), `إعادة التوليد` (Regenerate), `المخرجات` (Artifacts).
- **Confirmations** — a direct question + plain consequence. `هل أنت متأكد من حذف هذه القضية؟ سيتم حذف جميع المحادثات المرتبطة بها.`
- **Empty states** — offer the next action. `ابدأ محادثة جديدة أو اختر من القوالب:`
- **Errors** — short, specific, no blame. `الحد الأقصى 5 ملفات`, `الملفات المقبولة: PDF، PNG، JPG فقط`.
- **Placeholders** — hint the expected input. `example@email.com`, `••••••••`, `أدخل اسمك الكامل`.

**Casing.** Arabic has no case; all labels are written naturally. In English-inside-Arabic contexts (model names, code, emails) keep the original casing (`Bot`, `PDF`). Avoid ALL CAPS — not idiomatic for Arabic and visually harsh.

**Numerals.** Latin digits (`0–9`) are the default — this matches what ships (`5 ملفات`, `50 ميجابايت`, `2 دقائق`). Arabic-Indic digits are acceptable in prose but **not** in counts or form fields. Dates use `getRelativeTimeAr` — `منذ 3 دقائق`, `أمس`, `الأسبوع الماضي`.

**Emoji.** **None.** The shipped app uses zero emoji. Lucide icons carry all visual cues. Do not introduce emoji into Luna designs.

**Unicode glyphs as icons.** Not used — everything is a Lucide component. Exception: bullet points, ellipses, and RTL mark characters inside copy.

**Content examples from the product.**
- Conversation tab: `المحادثات` · `القضايا`
- Case types: `عقاري` (real estate), `تجاري` (commercial), `عمالي` (labor), `جنائي` (criminal), `أحوال شخصية` (personal status), `إداري` (administrative), `تنفيذ` (execution), `عام` (general)
- Priorities: `عالية` · `متوسطة` · `منخفضة`
- Agents: `تلقائي` (auto), `بحث معمّق` (deep search), `استخراج` (extraction), `ذاكرة` (memory), `خدمات` (services/drafting)
- Artifact types: `تقرير`, `عقد`, `مذكرة`, `ملخص`, `رأي قانوني`, `ذاكرة`
- Template card: `عقد إيجار تجاري — إنشاء مسودة عقد إيجار`

---

## VISUAL FOUNDATIONS

**The vibe.** Serious legal-tech. Closer to Notion or Linear than to a consumer chatbot. The system reads as trustworthy, document-forward, and uncluttered — warm only where it needs to be (the primary button, the active nav state). You are never more than one subtle shade of gray away from white.

**Color.** A single brand hue — deep slate blue `oklch(42% 0.07 195)` — carries every primary action, brand mark, focus ring, and active state. Everything else is tinted near-neutral on a cool-gray axis (hue ≈ 260). There are no gradients in the product. There is no brand accent beyond primary + destructive-red. Case-type badges are the only place where saturated pastels appear, and even those are `100/700` tints (soft bg / deep fg).

**Type.** **IBM Plex Sans Arabic** throughout — a single family serves Arabic and Latin together. Weights in active use: 400 (body), 500 (UI labels, nav), 600 (titles, emphasis), 700 (the "لونا" logomark, H1). Sizes skew small and dense: sidebar items `14px`, meta `12px`, badges `10px`. Arabic needs extra leading — body is set at `1.5–1.7`. Letter-spacing is tight at large sizes (`-0.01em` to `-0.02em`) and default at body.

**Spacing.** Tailwind's 4px base scale. Common rhythm: `gap-2` (8px) between inline controls, `p-3`/`p-4` (12/16px) card padding, `px-4 py-2.5` on inputs. Page padding is 16px mobile, ~24–32px desktop. The chat column max-width is `3xl` (768px).

**Corner radii.** `8px` is the house default (`--radius`). Buttons and cards = 8px; chat input = 12px (`rounded-xl`); message bubbles = 16px (`rounded-2xl`); pills and avatars = full. Square-cornered surfaces never appear.

**Borders.** `1px solid var(--border)` — a very soft cool gray. Used everywhere structure is needed: cards, inputs, dividers, sidebar edge. Never colored except for destructive (`border-destructive/50`) or active case (`border-primary/30`).

**Shadows.** Extremely restrained. Assistant message bubbles use `shadow-sm`; cards gain `shadow-sm` on hover. No large elevation, no colored shadows, no inner shadows. Modals layer via a `bg-black/50` backdrop, not a shadow.

**Backgrounds.** Flat. No images, no patterns, no textures, no illustrations. The login page is a plain `bg-background` centered card. The chat canvas is `bg-background`; the sidebar is a hair darker at `--sidebar-background`. Section separation comes from a 1px border, never a tint gradient.

**Gradients.** Not used anywhere in the shipped app. Do not introduce them.

**Transparency & blur.** Subtle opacity modifiers on state (`hover:bg-primary/90`, `bg-destructive/10`, `bg-primary/5`). No backdrop-blur, no frosted glass. Modal backdrop is a solid `rgb(0 0 0 / 0.5)`.

**Cards.** White surface + 1px border + `rounded-lg` (8px) + `p-3` or `p-4` padding + optional `shadow-sm`. On hover: `bg-accent/40` or `hover:shadow-sm`. No colored left borders, no emoji thumbnails, no heavy drop shadows.

**Buttons.** shadcn-variants (`default` / `destructive` / `outline` / `secondary` / `ghost` / `link`); heights `h-9/h-10/h-11` sm/default/lg; `icon` is a square at the same heights. Default button is `bg-primary text-primary-foreground`, hover `bg-primary/90`. Ghost is transparent with `hover:bg-accent`. Focus ring is a 2px primary ring + 2px background offset.

**Forms.** `h-10` inputs with 1px border, `rounded-md`, `px-3 py-2` padding. Error state swaps border to `border-destructive`. The focus treatment is `focus:ring-2 focus:ring-ring focus:border-transparent` — a soft glow, not a hard outline.

**Pills / badges.** `rounded-full`, `10–11px` font, `500` weight, `100/700` (bg/fg) tint pairs. Used for: case types (per-type color), priorities (green/yellow/red), agent families (blue/purple/orange/yellow/gray), artifact types (blue/purple/indigo/orange/yellow/emerald), extraction status. Never outlined — always filled.

**Animation.** Small and fast. `transition-colors` on hover (150–200ms), `animate-in slide-in-from-start duration-200` on the artifact panel, `animate-spin` on Loader2, `animate-blink` (1s step-end infinite) on the streaming cursor, `animate-bounce-dot` on the typing indicator. No parallax, no scroll-linked effects, no spring easing. Default easing is `ease-in-out`.

**Hover states.** Backgrounds fade to `accent/50` or `muted/50`; primary buttons drop to `primary/90`; destructive to `destructive/90`; text links gain an underline. No scale, no shadow growth, no glow.

**Press / active states.** State carried by Radix data attributes (`data-[state=active]`, `data-[state=open]`). Pressed primary buttons stay at `primary/90`. Tabs swap to `bg-accent text-accent-foreground`. Active conversation item swaps to `border-primary/30 bg-accent`. No scale-down.

**Layout.** Left sidebar (`w-72` = 288px, collapsible to 0), flexible chat column centered on `max-w-3xl`, optional right-edge artifact panel (`w-[400px]`). Everything is `h-screen` with an internal `min-h-0 + flex-1` scroll pattern so only the message list scrolls.

**Fixed / sticky elements.** The sidebar is always full-height. The chat input is sticky to the bottom of its column. The theme toggle floats top-`start` on the login page.

**RTL details.** Logical properties (`ps-*`, `pe-*`, `ms-*`, `me-*`, `start-*`, `end-*`) everywhere — never `left`/`right`. Chevrons flip direction (`ChevronLeft` is used where a Latin layout would use right). Code blocks force `dir="ltr"`. Password/email inputs are `dir="ltr"` inside an RTL form.

**Imagery.** There is no product photography, no illustrations, no mascot. The Luna brand mark is a **primary-colored rounded square with the word `لونا` set in bold IBM Plex Sans Arabic**. No other imagery appears in the app. If you need to fill a hero, use whitespace + the wordmark.

---

## ICONOGRAPHY

**Library.** [**Lucide React**](https://lucide.dev) (`lucide-react` in `package.json`). Every icon in Luna is Lucide. Sizes are always in Tailwind pixel steps: `h-3 w-3` (12px), `h-3.5 w-3.5` (14px), `h-4 w-4` (16px), `h-5 w-5` (20px). Stroke weight is Lucide's default (2px). Colors are `text-muted-foreground` by default, `text-foreground` on hover, `text-primary` when active, `text-destructive` when destructive.

**Icons in active use** (pulled from the codebase):
- **Chat** — `Send`, `Square` (stop), `Plus` (add menu), `Paperclip` (attach), `LayoutGrid` (templates), `Terminal` (commands), `Bot`, `Copy`, `Check`, `RefreshCw`, `ThumbsUp`, `ThumbsDown`, `Pencil`, `AlertCircle`, `X`, `FileText`, `ImageIcon`, `Scale` (citations)
- **Agent selector** — `Sparkles`, `Search`, `FileSearch`, `Brain`, `FileEdit`, `ChevronDown`
- **Sidebar** — `Menu`, `PanelRightClose`, `PanelRightOpen`, `MessageSquare`, `Briefcase`, `ChevronDown`, `ChevronLeft` (used *because* of RTL), `MoreHorizontal`, `Archive`, `XCircle`, `LogOut`, `User`, `Loader2`
- **Documents** — `FileText`, `Image`, `Download`, `Trash2`, `File`, `ArrowRight`

**No icon fonts.** Lucide ships as per-icon React components / tree-shakeable SVGs.

**No emoji.** See Content Fundamentals. Do not add any.

**No unicode glyph icons.** No ▲ ◆ ★. If you need a mark, import a Lucide component.

**CDN fallback.** For HTML prototypes outside the React codebase, use the official Lucide CDN build via `<i data-lucide="icon-name">` + `lucide.createIcons()`, or paste individual SVGs from [lucide.dev](https://lucide.dev). The UI kit in `ui_kits/web/` loads Lucide this way.

**Logo.** The Luna logomark is the Arabic wordmark `لونا` set at bold IBM Plex Sans Arabic, centered in a primary-filled rounded square. Two lockups are provided in `assets/`:
- `assets/luna-mark.svg` — square glyph (used as sidebar avatar + login hero)
- `assets/luna-lockup.svg` — horizontal: mark + `لونا القانونية` wordmark

No alternative brand illustrations exist in the source. If a design needs imagery beyond the logo, treat it as a **deliberate gap to fill with the user**, not something to invent.

---

## CAVEATS & GAPS

- **No brand guidelines document** was provided — voice and imagery guidance above is inferred from ~40 shipped UI strings + component code. It's internally consistent but not canonical.
- **No slide template, no marketing site, no mobile app, no docs site** — only the web app exists in the codebase. One UI kit: `ui_kits/web/`.
- **No custom illustrations or marketing imagery.** The logomark is the only brand asset; it's reconstructed here from CSS (primary-filled rounded square + Arabic wordmark) — there's no source SVG/PNG in the repo.
- **Fonts are CDN-loaded from Google Fonts** (same source the codebase uses via `next/font`). No self-hosted `.woff2` files ship with this system. If you need offline fonts, download IBM Plex Sans Arabic weights 300/400/500/600/700 into `fonts/` and swap the `@import` for `@font-face` rules.
- **Lucide icons are CDN-loaded in the UI kit HTML.** In React projects use the `lucide-react` package the codebase depends on.
