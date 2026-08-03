# اكتشف ريحان — Piece 2: The Workspace (مساحة العمل)

Second lesson of the اكتشف ريحان hub (`/learn`) — a NEW endpoint
`/learn/workspace` (owner decision 2026-08-02: separate page, not a section
appended to how-it-works). Long-form version of onboarding `STEP_WORKSPACE`
the same way `/learn/how-it-works` is the long form of `STEP_AGENTS`.

**STATUS: BUILT 2026-08-02** (local):
- `frontend/components/learn/WorkspaceView.tsx` — the lesson body.
- `frontend/app/learn/workspace/page.tsx` — indexable, Article JSON-LD.
- `frontend/lib/nav/site-nav.ts` — new enabled child «مساحة العمل» + hub
  label/description for the اكتشف ريحان slot. Second enabled child ⇒ the slot
  auto-promotes from flat link to dropdown (resolve-nav rule) and the /learn
  hub grid gains the card automatically (it reads SITE_NAV).
- `frontend/app/learn/page.tsx` — hub `noindex` LIFTED (the piece-1 note said
  to do exactly this once a second lesson lands) + canonical added.
- `frontend/lib/seo/sitemap.ts` — `/learn` + `/learn/workspace` added to the
  static section; header comment updated.

## Brief (owner, 2026-08-02)

Guide the user to the workspace: the types of docs, the references and what
they contain, and WHY the workspace exists — to store the important facts and
prevent our open-source models from hallucinating.

## Sources & voice

Same hard voice rules as piece 1 (`discover_rayhan_agents.md`):
- Reader vocabulary only: الموجّه، الباحث، الكاتب، المكتبة القانونية، مساحة
  العمل، تقرير موثّق. No internal names (workspace_items, kinds enum, agents).
- Models are **open-source, never named**.
- خطاب مباشر بصيغة «أنت»، same register as the onboarding popup.
- The onboarding popup's `STEP_WORKSPACE` (3 bullets) stays the SUMMARY of
  this page — if copy drifts, update the popup, never diverge this page.

Grounding facts came from the code, not invention:
- The six group cards use the EXACT labels the pane shows
  (`WorkspaceList.tsx` `KIND_LABELS`): المسودات، نتائج البحث، الملاحظات،
  المرفقات، المراجع، ملخص المحادثة.
- Reference behaviour matches `reference_library_integration`: numbered
  markers → official-text popup → «فتح في ريحان» library page.
- Control actions match shipped UI: WorkspaceAddMenu (note / upload / link
  from case docs), router save_memo («احفظ هذه المعلومة»), MarkdownDocEditor,
  action-bar share-link / save-as-blog / 👍👎 feedback.

## Structure

```
Hero      مساحة العمل — ذاكرة محادثتك الموثّقة
§1        لماذا مساحة العمل؟          ← 3 cards: hallucination risk /
                                        grounding on saved facts / context
                                        survives long chats
§2        ماذا تجد في مساحة العمل؟    ← 6 cards = the pane's own groups
§3        المرجع قبل الإجابة          ← 3 numbered steps: marker → official
                                        text → library page
§4        مساحتك — وأنت تتحكم بها     ← add / save-memo / edit+rate / share
CTA       جرّب + cross-link to /learn/how-it-works
```

## Notes

- OCR is described reader-level only: «يقرأ ريحان نصّها حتى لو كانت صورة
  ممسوحة» — no Mistral/OCR vocabulary.
- Feedback claim is deliberately modest («تقييمك يساعدنا على تحسين
  المخرجات») — ratings are persisted, not used for online learning.
- ملخص المحادثة copy promises the originals stay intact — matches compaction
  keeping items, only the transcript is summarized.
