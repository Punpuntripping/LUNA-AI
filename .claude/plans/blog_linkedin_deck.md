# Blog → LinkedIn Deck Generator (برندة العروض)

**Status:** P1 brand kit + P2 templates BUILT & render-verified 2026-07-17 · P3 skill + P4 pilot pending
**Date:** 2026-07-17
**Goal:** From any published Rayhan blog post (`rayhanai.com/blog/<token>`), an agent produces a **brand-matched, minimal, engaging PDF carousel** for LinkedIn document posts **plus the LinkedIn post copy** (hook, body, keywords, hashtags) — tuned for the general LinkedIn public to maximize reach, not just lawyers.

Inspiration: `marketing/pitch_deck/rayhan_pitch_deck.html` → `Rayhan_Pitch_Deck.pdf` (same brand, same headless-render pipeline).

---

## 1. Architecture decision — where the agent lives

**v1 (this plan): a Claude Code skill — `/blog-deck <token-or-url> [template]`.**
The "Sonnet-capable agent" is Claude itself running the skill interactively. Rationale:

- The pitch deck already proved this exact pipeline (hand-authored HTML → `msedge --headless=new --print-to-pdf`). Zero new infra, zero deploy.
- Publishing to LinkedIn is a human act anyway — the operator reviews/chooses a template and posts manually. Human-in-the-loop fits a skill, not a backend job.
- Template quality needs fast iteration; a skill lets us tune HTML/CSS per run without redeploys.

**v2 (later, optional):** promote to an internal API job (`backend/app/api/marketing_api/` following [[project_agents_api_namespace]] / the `deepsearch_api` pattern) driven by a pydantic-ai agent on tier_1, auto-triggered when a blog-post job publishes. Only worth it if blog volume makes the manual step a bottleneck. Out of scope here.

---

## 2. UX flow (one run of `/blog-deck`)

```
/blog-deck https://rayhanai.com/blog/ec47e5cc...   (or bare token)
 │
 1. FETCH   GET {BACKEND}/api/v1/public/blog/{token}  (public, no auth)
 │          → question, content_md, references[], created_at
 │
 2. ANALYZE blog anatomy → recommend a template
 │          (e.g. "7 numbered steps → recommend المسار/Steps template")
 │
 3. CHOOSE  AskUserQuestion with per-template previews (recommended first).
 │          Operator picks template — or "surprise me" accepts the recommendation.
 │
 4. PLAN    agent writes deck_plan.json (slide-by-slide content, ≤ 40 words/slide)
 │          + post.md (LinkedIn copy) — the "actual media (the word)"
 │
 5. RENDER  fill template → deck.html → msedge headless → Deck.pdf
 │
 6. VERIFY  python -m http.server + Playwright screenshots of every slide
 │          (Playwright MCP blocks file:// — known pitch-deck gotcha)
 │
 7. OUTPUT  marketing/linkedin_post/decks/<short>/  → operator posts to LinkedIn
```

---

## 3. Deck format — LinkedIn document post

| Property | Value | Why |
|---|---|---|
| File type | PDF (LinkedIn "document" post = swipeable carousel) | highest-dwell format on LinkedIn |
| Slide size | **1080×1350 px portrait (4:5)** → `@page{size:11.25in 14.0625in}` @96dpi | 4:5 fills the most mobile feed area; square 1080×1080 is the fallback variant |
| Slide count | **6–10** (hook + 4–7 content + references + CTA) | swipe-completion drops hard after ~10 |
| Text density | ≤ ~40 words per slide, one idea per slide | "engaging and minimal" — the deck teases, the blog delivers |
| Direction | Arabic RTL primary (`dir="rtl"`), English accents allowed in eyebrows/footers | matches brand + audience |
| Last slide | CTA: «جرّب ريحان مجاناً» + `rayhanai.com/blog/<token>` (+ optional QR) | traffic back to the blog |

**Fixed slide skeleton (every template):**
1. **Hook** — the question, huge display type, minimal chrome. Must work as a static thumbnail (this is what non-swipers see).
2. **Promise/الخلاصة** — the one-paragraph answer, compressed.
3–8. **Body** — template-specific (steps / myth-fact / citations).
9. **المصادر** — 3–5 top references as citation chips (regulation name + المادة N) — this is Rayhan's differentiator ("نُظهر مصادرنا"), always gets its own slide.
10. **CTA**.

---

## 4. Template registry — 3 launch templates

Templates live as self-contained HTML files with `{{slot}}` placeholders + shared brand CSS. The agent never invents layout — it only fills slots (this is what keeps output on-brand and minimal).

| id | Name | Best for | Body-slide pattern |
|---|---|---|---|
| `masar` | **المسار** (The Path) | procedural/steps posts (like إفراغ عقار زراعي: 7 steps) | one numbered step per slide, big sage number dot (pitch-deck `.co .dot` scaled up), 1–2 line step text |
| `soal` | **سؤال وجواب** (Q&A) | conceptual "can I / what happens if" posts | Q on top in aubergine quote style, A beneath in cards; 2–3 sub-questions across slides |
| `mawthooq` | **موثّق** (Verified) | myth-busting / "generic AI gets this wrong" posts | ❌ الشائع (what people/ChatGPT believe) vs ✅ الموثّق (what the law actually says + citation chip) split per slide |

**Choice mechanism (answers the "will the agent give a choice?" question — yes, recommend-then-choose):**
- Agent scores the blog anatomy (numbered steps present? multiple sub-questions? correcting a misconception?) → picks a recommended template.
- `AskUserQuestion` with ASCII/mini previews per option, recommendation first. Optional arg skips the question (`/blog-deck <token> masar`).
- Every template shares the hook/خلاصة/المصادر/CTA slides, so switching template later only regenerates body slides.

---

> **Layout note (reorganized 2026-07-21):** the whole system now lives under
> `marketing/linkedin_post/` — `brand/` (kit), `templates/` (in brand/),
> `scripts/` (fetch_blog.py · render.ps1 · verify.py), `decks/<short>/` (output),
> and `CLAUDE.md` (operating doc). Paths below that say `marketing/brand/` or
> `marketing/linkedin_decks/` are pre-move; read them as `marketing/linkedin_post/brand/`
> and `marketing/linkedin_post/decks/`.

## 5. Brand kit — BUILT 2026-07-17 at `marketing/linkedin_post/brand/`

- `rayhan_brand.css` — pitch-deck tokens verbatim (`--canvas:#F7F2EC`, `--sage:#4A6B5F`, `--sage-deep:#2A4438`, `--aubergine:#4C4158`, `--forest:#242E29`, gold) + carousel-scale components (`.card`/`.pill`/`.chip`/`.dot`/`.foot`/`.swipe`/`.content`) + the finalized type ramp (§5b).
- `fonts/` — **vendored Noto Naskh Arabic variable woff2** (arabic + latin subsets, wght 400–700, downloaded from Google Fonts). ⚠️ The app font is **Noto Naskh Arabic** (`frontend/app/layout.tsx`), NOT IBM Plex Sans Arabic — older memory was stale. Georgia stays system-font for English accents only (no Arabic glyphs in Georgia — never use it for Arabic).
- `templates/masar.html · soal.html · mawthooq.html` — each is a **working demo** (realistic Saudi-legal content) AND the spec: a `SLOT CONTRACT` header comment lists every slot with hard char budgets. Rendered `*.pdf` siblings are the visual reference.
- ر-tile mark implemented as `.mark-tile` / `.foot .brand .t` CSS (no SVG needed).

The pitch deck itself is NOT refactored — it stays frozen as-shipped.

### 5b. Finalized design spec (research-backed, 2026)

| Decision | Value | Source/why |
|---|---|---|
| Canvas | 1080×1350 (4:5) · `@page 11.25in×14.0625in` @96dpi | portrait fills most mobile feed area |
| Slide count | 6–10 (masar 7–10 · soal 6–9 · mawthooq 5–8) | <5 feels incomplete, >12 drop-off |
| Text blocks/slide | **max 2** (headline + one support block); chips/footers are metadata | carousel readability research |
| Hook headline | 88px w700, lh 1.5 | ≥40px at LinkedIn's ~½-scale feed preview |
| Slide headline | 56px w700, lh 1.45 · 3–8 words · must narrate standalone in sequence | skim path |
| Body | 36px w400, **lh 1.75** | Arabic needs 1.6–1.8 lh; ≥30px floor (LinkedIn re-rasterizes PDFs) |
| Quote (soal Q) | 46px w600 aubergine | slide's de-facto headline must dominate |
| Arabic typography | letter-spacing **0** always · bold only on short headings · char budgets not word budgets (Arabic runs 20–30% longer) | connected-script rules |
| Layout | body slides: eyebrow top + `.content` (flex, vertically centered) + footer — kills dead-space-above-footer | verified in render |
| Swipe cue | hook only: forest pill «اسحب ←» bottom-left — arrow matches LinkedIn's advance-swipe direction, which is also RTL-natural | |
| Page counter | «١ / ٩» Arabic-Indic, bottom-left; brand mark + rayhanai.com bottom-right | |
| Palette | canvas + sage accents; gold reserved for الشائع/secondary pills — no new colors | restrained 1–2 accents |

### 5c. Agent-reliability contract (why templates are shaped this way)

Constrained-generation findings applied: the agent NEVER writes HTML/CSS — it fills a fixed schema (`deck_plan.json`) whose slots have hard `maxLength` budgets; the renderer is a dumb slot-filler; validation is a **closed loop**: (1) schema-validate the plan (lengths, slide-count ranges, enum kinds) → (2) render → (3) measure per-slide `scrollHeight-clientHeight` overflow (0 required; proven check) → (4) on failure, agent shortens ONLY the offending slot and re-renders. Template-guided filling + verifiable repair beats free-form generation on reliability.

---

## 6. deck_plan.json — the agent's contract

The agent's creative work lands in one reviewable JSON before any HTML is touched:

```json
{
  "token": "ec47e5cc…",
  "template": "masar",
  "language": "ar",
  "keywords": ["إفراغ عقار", "السجل العقاري", "كتابة العدل", "ضريبة التصرفات العقارية"],
  "slides": [
    {"kind": "hook",   "eyebrow": "عقارات · توثيق", "headline": "عندك أرض زراعية بدون سجل عقاري وتبي تفرغها لشريكك؟", "sub": "الإجراء النظامي كامل في ٧ خطوات"},
    {"kind": "summary","headline": "الخلاصة", "body": "…≤40 words…"},
    {"kind": "step",   "n": 1, "title": "توثيق التصرف لدى كتابة العدل", "body": "…", "cite": "اللائحة التنفيذية لنظام التوثيق · المادة ٢"},
    {"kind": "sources","refs": [{"name": "نظام التسجيل العيني للعقار", "article": "٧–٩"}]},
    {"kind": "cta",    "url": "rayhanai.com/blog/ec47e5cc…"}
  ],
  "post": {
    "hook": "أكثر سؤال يوصلنا عن الأراضي الزراعية: …",
    "body": "…3–5 short lines, line breaks for skimmability…",
    "engagement_question": "واجهت هالإجراء من قبل؟",
    "hashtags": ["#عقارات", "#قانون_سعودي", "#توثيق", "#ريحان", "#SaudiLaw", "#LegalTech"],
    "first_comment": "التحليل الكامل بالمصادر النظامية: https://rayhanai.com/blog/…"
  }
}
```

Renderer = dumb slot-filler (small Python script or direct agent authoring of final HTML, pitch-deck style). Keeping plan ≠ render means the operator can edit `deck_plan.json` and re-render without re-running the LLM step.

### Copy rules ("maximize viewership" without clickbait)
- Hook slide + post hook carry the **search keywords** (regulation names, the everyday phrasing of the problem — «أفرغ أرض», not only «نقل ملكية عقارية»). General-public wording first, jargon second.
- Post body: short lines, no wall of text, one concrete number or step count («٧ خطوات», «١١ مصدر رسمي»).
- Blog link goes in **first_comment** (LinkedIn suppresses external links in post body); post body ends with the engagement question.
- Hashtags: 4–6, mixed broad-Arabic + niche + 1–2 English. Never more.
- **PDPL/anonymization rule inherited from the pitch deck:** no client names, judgment numbers, or party identifiers — blogs are already anonymized snapshots; the deck must not re-introduce specifics.
- Disclaimer footer on CTA slide (align with [[project_ai_disclaimer_ui]] wording): «محتوى توعوي — ليس استشارة قانونية».

---

## 7. Render + verify pipeline

1. `deck.html` = brand CSS + template + filled slots. `@page{size:11.25in 14.0625in;margin:0}`, slides `1080×1350`, `-webkit-print-color-adjust:exact`.
2. `msedge --headless=new --disable-gpu --no-pdf-header-footer --print-to-pdf="<out>" file:///…/deck.html` (identical to pitch-deck re-render command).
3. Verify: `python -m http.server` in the deck folder → Playwright MCP screenshots per slide → agent self-checks: RTL not mangled, no text overflow/clipping, Arabic font actually applied (not fallback), contrast on canvas bg.
4. Output manifest per run — `marketing/linkedin_post/decks/<short>/`:
   - `source.md` · `source.json` · `deck_plan.json` · `deck.html` · `deck.pdf` · `post.md` (copy + hashtags + first comment, paste-ready) · `previews/slide_NN.png`
   - Committed scripts do steps 1/5/6: `scripts/fetch_blog.py <token>` · `scripts/render.ps1 <short>` · `scripts/verify.py <short>`.

---

## 8. Build phases

| Phase | Work | Status |
|---|---|---|
| **P0 — Consolidation** | everything under `marketing/linkedin_post/` (brand + scripts + decks + CLAUDE.md); inline fetch/render/verify turned into committed scripts | ✅ DONE 2026-07-21 |
| **P1 — Brand kit** | `marketing/linkedin_post/brand/` (tokens CSS, vendored Noto Naskh Arabic woff2, ر-mark) | ✅ DONE 2026-07-17 |
| **P2 — Templates** | 3 template demos rendered to PDF at 1080×1350; slide geometry, font load, RTL, and zero-overflow verified (Playwright measurements + pymupdf page rasterization) | ✅ DONE 2026-07-17 |
| **P3 — Skill** | `.claude/skills` `/blog-deck`: fetch → analyze → AskUserQuestion → deck_plan.json → render → verify loop (§5c) → output | pending |
| **P4 — Pilot** | Run on the إفراغ عقار زراعي post (`ec47e5cc…`) end-to-end with its REAL content; private LinkedIn test upload | pending |

Ship gate: pilot PDF swipes cleanly in LinkedIn's document viewer on mobile (upload as a private test post first — LinkedIn re-rasterizes PDFs and can blur thin text; body floor 30px at 1080 width).

**Verification gotchas (hit during P2):** Playwright MCP viewport screenshots proved flaky (browser crashed mid-session; recover by killing `chrome.exe` processes whose command line contains `ms-playwright-mcp\mcp-chrome-*`). The robust verify path is: render PDF → rasterize pages with **pymupdf** (`fitz`, installed) → Read the PNGs. Keep Playwright only for the live overflow measurement (`scrollHeight-clientHeight` per `.slide` over a temp `python -m http.server`).

---

## 9. Open questions (defaults chosen, flag to change)

1. **Bilingual variant?** Default: Arabic-only deck. An English mirror deck doubles reach on LinkedIn but doubles copy work — could be a later `--lang en` flag.
2. **Square 1080×1080 fallback** as a second render of the same plan? Default: no (keep v1 minimal), trivial to add later since it's a CSS-size swap.
3. **QR code on CTA slide** to the blog URL? Default: plain URL text in v1 (no QR dependency).
