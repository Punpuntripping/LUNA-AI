# «النسخ لناجز» — plain-text copy option

Status: PLANNED (2026-09-27). Frontend only — no backend, no DB, no migration.

## Goal

Lawyers paste Rayhan output into Najiz fields that accept NO formatting. Today
every «نسخ» writes raw markdown (`#`, `**`, `- `, `>`, table pipes). Add a
second copy mode that writes clean, readable plain text.

## Decisions (from /reflect, 2026-09-27)

| # | Decision |
|---|---|
| 1 | Target field has no formatting at all → strip ALL markdown, keep readable structure |
| 2 | Every list (bullet or numbered) → numbered lines `1.` `2.` … |
| 3 | References KEPT, as a plain «المراجع» block (`n-label` lines) |
| 4 | Inline citation markers `[n]` KEPT verbatim |
| 5 | UI = «نسخ» becomes a dropdown with 2 items, each with a grey explanatory line |
| 6 | Surfaces: ALL in-app copy buttons incl. chat bubbles (dropdown there too) |
| 7 | Public blog / public answer pages OUT of scope — keep their single button |
| 8 | Najiz copy starts directly with the body — no title line |
| 9 | Label is exactly «النسخ لناجز» |
| + | Bug fix: agent_writing «نسخ» must append «المراجع» (today it copies body only) |

## Menu copy

- **نسخ** — «يحتفظ بالعناوين والتنسيق»
- **النسخ لناجز** — «نص عادي بدون أي تنسيق — جاهز للصق في ناجز»

After either click: the trigger flips to «تم النسخ» ✓ for 1.5s (existing behaviour).

## Files

### New

1. **`frontend/lib/markdown/plain-text.ts`** — `markdownToPlainText(md: string): string`, pure, no deps.
   Line-based pass over the markdown:
   - `stripMarkdownImages` first (reuse `lib/markdown/images.ts`).
   - Fenced code: drop the ``` fence lines, keep the inner text.
   - ATX headings `#{1,6} x` → `x` on its own line (blank line before/after).
   - Setext underline lines (`===` / `---` under text) and horizontal rules → removed.
   - Blockquote `> ` prefix → removed (nested `>>` too).
   - Lists: `-`/`*`/`+`/`N.`/`N)` items → `N. text`. Counter restarts at each list block
     (a blank line + non-list line ends the block). Nested items get their own counter,
     indented 3 spaces per level.
   - Tables: drop the `|---|` separator row; each row → cells trimmed and joined with « — ».
   - Inline: `**x**` `__x__` `*x*` `_x_` `~~x~~` `` `x` `` → `x`; `[text](url)` → `text`;
     autolinks `<https://…>` → the URL; `<br>` → newline; other HTML tags stripped;
     backslash escapes `\*` → `*`.
   - `[n]` / `[n، m]` citation markers left untouched (not links — no `(` follows).
   - Trim trailing whitespace per line; collapse 3+ newlines → 2; trim ends.
   - Must NOT touch the «المراجع» block lines (`1-label` — no dot+space, so not a list).

2. **`frontend/components/common/CopyMenu.tsx`** — the shared dropdown.
   Props: `text` (markdown incl. refs), `variant` (`"bar" | "toolbar" | "icon"` to match the
   three existing button looks), `label?` (default «نسخ»; attachments pass «نسخ النص المستخرج»),
   `disabled?`, `onCopied?`.
   - Uses `components/ui/dropdown-menu` (already z-[70] — renders above the mobile overlay).
   - Item 1 writes `text`; item 2 writes `markdownToPlainText(text)`.
   - Owns the `copied` state + silent clipboard failure (same as today).
   - `dir="rtl"`, `align="start"`; items ≥36px tall under `md` (touch target, 3.4).

### Modified

3. **`WorkspaceItemActionBar.tsx`** — replace the نسخ `<Button>` (L298–318) with
   `<CopyMenu variant="bar" text={copyText} />`; delete local `copied`/`handleCopy`.
   Covers: writer docs, notes, templates, research answers, convo_context, references.

4. **`ArtifactPreview.tsx`** — toolbar button (L103–128) → `<CopyMenu variant="toolbar" …>`.
   Add prop `plainCopy?: boolean` (default `true`); when `false` render the old single button.
   **`PublicAnswerView.tsx`** passes `plainCopy={false}` (decision 7).
   Covers: attachments (`AttachmentRenderer`), `WorkspaceItemViewer`.

5. **`MessageBubble.tsx`** — both icon copy buttons (L361–380, L604–623) → `<CopyMenu variant="icon">`
   keeping the tooltip; `textToCopy` logic (streaming vs final) moves into the `text` prop.

6. **Writer refs fix — `NoteEditor.tsx` + `MarkdownDocEditor.tsx`.**
   - Hoist the duplicated "body + «المراجع» + `n-label`" builder into
     `ReferencePanel.tsx` as `export function appendReferencesForCopy(body, references)`
     (next to `referenceCopyLabel`). Use it in `AgentSearchViewer` (replace L107–117).
     Blog pages keep their inline copies (out of scope — don't touch).
   - `MarkdownDocEditor` gets `copyReferences?: Reference[]`; its action bar passes
     `copyText={appendReferencesForCopy(content, copyReferences ?? [])}` — uses the LIVE
     edited `content`, not the saved one.
   - `NoteEditor` passes `copyReferences={isShareable ? references : undefined}` (agent_writing only).

### Not touched
`ReferencePanel`'s per-source «نسخ المحتوى» (source dialog) — copies a regulation/ruling
text, not a Rayhan document. `CodeBlock` «نسخ الكود». Blog / public pages.

## Verification

- `npx tsc --noEmit`, `npm run lint` (Latin-numerals ESLint rule applies to new strings).
- No test runner in frontend → quick node check of `markdownToPlainText` against a fixture
  covering: headings, nested lists, bold inside list item, table, link, image card, `[3]`
  markers, «المراجع» block, code fence. Run from scratchpad, not committed.
- Browser (local dev): writer doc → نسخ includes «المراجع»; النسخ لناجز has zero `#`/`*`/`|`;
  research answer; attachment; chat bubble dropdown; mobile width (docked bar) menu opens above
  the overlay; RTL alignment of the menu.
