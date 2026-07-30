# LinkedIn Post System — Blog → branded carousel

Turns a published Rayhan blog post (`rayhanai.com/blog/<token>`) into a
**brand-matched LinkedIn document post** — a portrait PDF carousel — **plus the
LinkedIn post copy** (hook, body, hashtags, first-comment link). Arabic-first,
RTL, tuned for the general LinkedIn public to maximize reach.

Plan of record: `.claude/plans/blog_linkedin_deck.md`. This folder is the
built-and-verified brand kit + templates + scripts + generated decks.

## Directory map

```
marketing/linkedin_post/
  CLAUDE.md                 ← you are here
  brand/
    rayhan_brand.css        design system: tokens + type ramp + components + @font-face
    fonts/                  vendored Noto Naskh Arabic (variable woff2, arabic+latin)
    templates/
      masar.html   (+.pdf)  numbered procedural steps
      soal.html    (+.pdf)  conceptual Q&A
      mawthooq.html(+.pdf)  الشائع ✕ vs الموثّق ✓  (myth-busting)
  scripts/
    fetch_blog.py           token → decks/<short>/source.md brief (+ source.json)
    render.ps1              decks/<short>/deck.html → deck.pdf (headless Edge)
    verify.py               deck.pdf → previews/*.png + geometry check (PyMuPDF)
  decks/
    <short>/                one folder per blog (short = first 8 chars of token)
      source.md source.json deck_plan.json deck.html deck.pdf post.md previews/
```

`brand/templates/*.html` are **working demos AND the spec**: each file's top HTML
comment is a `SLOT CONTRACT` listing every fillable slot with a hard character
budget. The `*.pdf` sibling is the visual reference. `pitch_deck/` (one level up)
is a separate, frozen asset — do not touch it.

## Pipeline (one deck)

```bash
# 1. FETCH — pull the blog, write the brief
python scripts/fetch_blog.py <token-or-blog-url>       # → decks/<short>/source.md

# 2. ANALYZE + 3. CHOOSE  (agent judgment — see "Template selection")
#    Read source.md + all three brand/templates/*.html SLOT CONTRACTs.

# 4. FILL — write decks/<short>/deck_plan.json, then deck.html by copying the
#    chosen template and replacing DEMO content with real slots (see rules).

# 5. RENDER
pwsh scripts/render.ps1 <short>                        # → decks/<short>/deck.pdf

# 6. VERIFY — rasterize + geometry check, then Read every previews/slide_NN.png
python scripts/verify.py <short>                       # → decks/<short>/previews/

# 7. COPY — write decks/<short>/post.md (LinkedIn wording, see below)
```

The agent's creative work lands entirely in `deck_plan.json` + `deck.html` +
`post.md`. The scripts are dumb and deterministic. If a slide overflows/clips,
shorten ONLY the offending slot's text and re-render (closed loop) — do not
resize type or edit the brand CSS.

## Brand kit essentials

- **Font is Noto Naskh Arabic** (the product font, `frontend/app/layout.tsx`) —
  vendored in `brand/fonts/`. NOT IBM Plex Sans Arabic (stale note elsewhere).
  Georgia is for **English accents only** — it has no Arabic glyphs, never set
  Arabic in Georgia.
- Palette (from the pitch deck): canvas `#F7F2EC`, sage `#4A6B5F` /
  deep `#2A4438`, aubergine `#4C4158`, forest `#242E29`, gold for secondary/
  «الشائع». Restrained — do not add new colors.
- Components live in `rayhan_brand.css`: `.slide .content .foot .swipe .card
  .pill .chip .dot .mark-tile .rule`. The ر brand mark is CSS (`.mark-tile`,
  `.foot .brand .t`) — no SVG.

## Templates & selection

| id | shape it fits | body pattern | slides |
|----|---------------|--------------|--------|
| `masar` | answer is a **sequence of steps/إجراءات** | one numbered step per slide | 7–10 |
| `soal` | answer splits into **2–4 sub-questions** | quote-Q + card-A per slide | 6–9 |
| `mawthooq` | answer **corrects a misconception** the asker holds | الشائع ✕ card + الموثّق ✓ card | 5–8 |

Every template shares fixed **hook · (الجواب المختصر) · المصادر · CTA** slides;
only the body slides differ, so switching template later only re-fills the body.

**Recommend, then let the operator confirm** (`AskUserQuestion` with previews;
an explicit template arg skips the ask). ⚠️ **Guard: mawthooq over-selects.** In
the 2026-07-17 E2E test both blogs auto-picked mawthooq (both genuinely had a
misconception structure, but still). Before defaulting to mawthooq, check
honestly whether the answer is really steps (masar) or sub-questions (soal).

## Hard rules

- **Geometry:** 1080×1350 px portrait (4:5). Set by `@page` in the CSS — the
  render step passes **no** paper-size flag. `verify.py` asserts 810×1013 pt.
- **Slides:** 6–10 total (per-template ranges above).
- **≤ 2 text blocks per slide** (headline + one support block; chips/footers are
  metadata). Headlines must read standalone as a skim path across slides.
- **Char budgets, not word budgets** (Arabic runs 20–30% longer). Obey the
  SLOT CONTRACT. Body text floor ~30px — LinkedIn re-rasterizes PDFs and blurs
  thinner text.
- **Arabic typography:** letter-spacing **0** always (connected script). Page
  counters «n / N» in **Arabic-Indic numerals** (٠١٢٣٤٥٦٧٨٩).
- **Faithful to the blog.** This is legal marketing — a wrong article number is
  a serious defect. Only cite what the blog states. Article numbers often live
  in the answer PROSE while `references[]` has `article_num=null` — extract from
  the متن.
- **PDPL / anonymization:** blogs are already anonymized snapshots; keep decks
  generic — no client names, party names, or judgment numbers. CTA slide keeps
  the «محتوى توعوي عام — ليس استشارة قانونية» disclaimer.

## Post copy (`post.md`)

- **Hook line:** scroll-stopping, general-public phrasing first, legal term
  second (e.g. «أفرغ أرض», not only «نقل ملكية عقارية»).
- **Body:** short skimmable lines, one concrete fact/number, ends with an
  **engagement question**.
- **Hashtags:** 4–6, mix broad-Arabic + niche + 1–2 English. Never more.
- **Blog link goes in the FIRST COMMENT**, not the post body — LinkedIn
  suppresses reach on posts with external links in the body. The CTA slide says
  «الرابط في أول تعليق».

## Render / verify gotchas

- **CSS path discipline** (the #1 breakage): `deck.html` sits at
  `decks/<short>/`, two levels under `brand/`, so its link must be
  `../../brand/rayhan_brand.css`. Templates under `brand/templates/` use
  `../rayhan_brand.css`. Font `@font-face` urls are relative to the CSS file, so
  they resolve from either location automatically.
- **PyMuPDF (`fitz`) is the reliable verify path.** Playwright MCP screenshots
  proved flaky here (browser locks; recover by killing `chrome.exe` whose
  command line contains `ms-playwright-mcp`). True DOM-overflow measurement needs
  a browser — serve the folder (`python -m http.server`) and read
  `scrollHeight-clientHeight` per `.slide`; `verify.py` does the visual check.
- **Edge profile locks:** `render.ps1` uses a per-deck `--user-data-dir` so
  parallel renders don't collide. A stray handle can lock the deck folder for
  moves — kill `msedge.exe` with `edge_deck_*` in its command line.

## Related

- Plan: `.claude/plans/blog_linkedin_deck.md`
- Memory: `project_blog_linkedin_deck` (build status, E2E test finding)
- Blog source API: `GET /api/v1/public/blog/{token}` (public, no auth) —
  see `project_blog_share_links`, `project_blog_post_api_built`.
