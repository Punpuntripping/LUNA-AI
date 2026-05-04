---
name: luna-design
description: Use this skill to generate well-branded interfaces and assets for Luna (لونا), an Arabic-first legal AI assistant for Saudi lawyers. Contains essential design guidelines, colors, type, fonts, assets, and UI kit components for prototyping.
user-invocable: true
---

Read the `README.md` file within this skill, and explore the other available files.

If creating visual artifacts (slides, mocks, throwaway prototypes, etc), copy assets out and create static HTML files for the user to view. Pull `colors_and_type.css` in with a `<link>` and you have tokens, fonts, and Arabic-first base styles for free. For React prototypes, load `ui_kits/web/Primitives.jsx` + peers the same way `ui_kits/web/index.html` does.

If working on production code (the Next.js + Tailwind + shadcn frontend), the tokens in `colors_and_type.css` mirror what `frontend/app/globals.css` ships, and the components in `ui_kits/web/` map to `frontend/components/{ui,chat,sidebar,artifacts,documents,auth}/`.

Two rules Luna never breaks: **Arabic first, RTL by default** (`<html lang="ar" dir="rtl">`, logical properties `ps-`/`pe-`/`ms-`/`me-`/`start-`/`end-`, Latin content inside `dir="ltr"` spans), and **no emoji** anywhere — Lucide icons only.

If the user invokes this skill without any other guidance, ask them what they want to build or design, ask some questions, and act as an expert designer who outputs HTML artifacts _or_ production code, depending on the need.
