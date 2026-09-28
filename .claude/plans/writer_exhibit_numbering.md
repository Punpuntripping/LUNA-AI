# Writer — exhibit numbering («مرفق رقم k») instead of `WI-N` in documents

**Status:** Phase 1 BUILT 2026-09-28 (not deployed, not live-validated) — §1–4. Phase 2 (§5–6) open.
**Trigger:** convo `a951fd93-912d-4040-8a2a-14347c60d25b` (nawarafallatah), item
`f682a16d` «تحديث مذكرة الدفوع — إثبات صفة الشريك والصلاحيات بعقد التأسيس».
The defense brief body carries 14 internal aliases — `(WI-13)`, `(WI-9)`,
`(WI-12)`, `(WI-8)` — which get copied verbatim into Najiz. The chat reply
repeats them but never tells the lawyer *what to attach, in what order*.

## Root cause

- The writer sees each package item as `<source kind="attachment" wi="WI-9">`
  (`agents/writer/prompts.py:376`). The ONLY citation rule it has is `(n)` for
  legal references (`prompts.py:59-65`). No rule says how to cite *evidence*,
  so it reuses the only handle it has — the alias.
- `WI-N` is a conversation-scoped handle, NOT a filing number: gappy and
  duplicated. In this convo: مخالصة = WI-2 **and** WI-8; عقود العمل = WI-3 **and**
  WI-9; عقد التأسيس = WI-11 **and** WI-13; bank docs = WI-4, WI-5, WI-12.
  «مرفق 8» would tell the judge there are seven exhibits before it. → Rejected
  "reuse the seq as the مرفق number"; exhibits are contiguous `1..K`.
- Chat reply = the writer's own `chat_summary` + `key_findings`
  (`orchestrator.py:2977-2994`, via `writer_planner/runner.py:756`). Nothing
  deterministic maps مرفق → card.

## Design

Two vocabularies, never mixed:

| Surface | Handle | Why |
|---|---|---|
| Document body (`content_md`, copied to Najiz / exported) | «(مرفق رقم k)», k = 1..K | what a court reads |
| Chat reply | «مرفق (k) ← WI-N» | WI-N is how the user finds the card (`WiBadge`) |

### 1. Writer output — new `exhibits` field (`agents/writer/models.py`)

```python
class ExhibitRef(BaseModel):
    n: int = Field(ge=1)          # 1..K, order of first mention in the body
    wi: str                       # "WI-N" of the attachment (primary copy)
    label_ar: str                 # clean court-facing description, NOT the WI title
    also_wi: list[str] = []       # duplicate uploads of the same document
```
`WriterLLMOutput.exhibits: list[ExhibitRef] = []`. Add to `tracking_output()`
(`exhibits` count).

- `label_ar` is written by the writer, because WI titles carry OCR typos
  («زاد الوقود», «زاد الوهود»). Example: «صورة من عقد تأسيس شركة زاد الوفود المحدودة».
- `also_wi` groups duplicate uploads so one document = one exhibit.

### 2. Writer prompt (`agents/writer/prompts.py`)

Add to `_SHARED_ROLE_AR` (general rules):
- User documents (items with `kind="attachment"`) are cited in the body ONLY as
  «(مرفق رقم k)», numbered 1..K in order of first mention. Same document
  uploaded twice → one number (list the extra alias in `also_wi`).
- Legal rules are supported ONLY by `(n)` from `<refs>`. Research items
  (`agent_search`), prior drafts and notes are never cited by alias.
- `WI-` must never appear in `title_ar`, `heading_ar` or `body_md`. (It is fine
  in `notes_ar`/`chat_summary`/`key_findings` — chat surfaces.)
- For `defense_brief`, `memo`, `legal_opinion`, `letter`: when `exhibits` is
  non-empty, the LAST section is `## المرفقات` — a numbered list `k. label_ar`
  matching `exhibits` one-for-one. (Contracts: skip — annexes are «ملحق», out of
  scope.)

Add `exhibits` to `_OUTPUT_CONTRACT_AR` JSON example + bullet.

### 3. Deterministic guard in the publisher (`agents/writer/publisher.py`)

Never refuses, never retries — substitutes, then logs. Runs on the assembled
`content_md` BEFORE `decode_for_persist`.

Kind map: build `{seq: (kind, item_id)}` from `input.research_items` (already
carries `wi_seq` + `kind` via `WriterInput.from_package`, `models.py:442`).
**Gap:** `from_package` drops `role="template"` items — widen it to pass a
separate `alias_kinds` list covering ALL `package.analyzed_items` so a cited
template-role attachment still resolves.

Regex: `\(?\s*WI-(\d+)\s*\)?` (case-insensitive), plus tolerate Arabic
parentheses and a leading «انظر/راجع».

| Alias resolves to | Replacement |
|---|---|
| `attachment` in `exhibits` (as `wi` or `also_wi`) | «(مرفق رقم k)» |
| `attachment` NOT in `exhibits` | assign next free k, append `ExhibitRef` (label = item title), emit «(مرفق رقم k)» |
| `agent_search` / `agent_writing` / `agent_writer` / `notes` | strip the marker (parenthetical) |
| unknown seq (hallucinated) | strip; separate counter |
| bare inline use («كما ورد في WI-6») | attachment → «المرفق رقم k»; else «ما سبق بيانه» |

After substitution: if the guard appended exhibits and the body has no
`## المرفقات` section, append/extend it from the final `exhibits` list. If the
LLM's section exists but disagrees with `exhibits`, rebuild it from `exhibits`
(the structured list is authoritative).

Telemetry: `_pub_span.set(wi_leaks_attachment=…, wi_leaks_stripped=…,
wi_leaks_unknown=…, exhibits=K)` + `logger.warning` when any leak > 0.

Pure function `sanitize_wi_aliases(content_md, exhibits, alias_kinds) ->
(content_md, exhibits, stats)` in a new `agents/writer/exhibits.py` so it is
unit-testable without Supabase.

Persist: `metadata["exhibits"] = [{n, wi, item_id, label_ar, also_wi}]`
(`label_ar` passed through `decode_for_persist` — masked names under وضع السرية).

### 4. Chat attachment guide — deterministic

**As built:** appended in `publisher.py` (`_chat_summary_with_guide`) instead of
the planner runner — `WriterOutput.chat_summary` feeds BOTH the planner path and
the legacy path, so one call site covers both; `orchestrator.py` is unchanged.
Chat order is: summary → guide → key_findings bullets.

After publish, if `metadata.exhibits` non-empty, append a block to the
`SpecialistResult.chat_summary` (so it streams through the normal token path
and gets relay-decoded like the rest):

```
**ما تُرفقه مع المذكرة:**
• مرفق (1) — عقد تأسيس شركة زاد الوفود المحدودة ← WI-13
• مرفق (2) — عقود العمل الموسمية للموظفين العشرة ← WI-9
• مرفق (3) — المخالصة النهائية لعشرة موظفين ← WI-8 (مرفوع أيضاً كـ WI-2 — تكفي نسخة واحدة)
• مرفق (4) — الإفادات البنكية بصرف الشيكين ← WI-12
```
Optional second line for research items used (role source/reference, kind
agent_search): «استند التحرير أيضاً إلى: «…» (WI-7) — للاطلاع، لا يُرفق.»

Built in code from `exhibits`, not by the LLM → numbers can't drift from the body.
Also apply the WI-N guard's *mapping* (not stripping) to `key_findings`:
«(WI-9)» → «(مرفق 2 / WI-9)» so bullets and the guide agree.

Put the formatter in `agents/writer/exhibits.py` (`format_exhibit_guide_ar`).
The `chat_summary` 500-char validator runs before this, so the guide is not
truncated. Welcome-line logic untouched (guide is appended after).

Legacy (non-planner) writer path in the orchestrator: same append, via the same
helper, where `WriterOutput` becomes a result.

### 5. Revision stability (phase 2)

A revision (`revised_from`) must keep «مرفق 2» as «مرفق 2».
- Planner, when building the `prior_draft` AnalyzedItem, fetch the prior row's
  `metadata.exhibits`; carry it on the package (`WriterPackage.prior_exhibits`).
- Render inside `<prior_draft>` as
  `<exhibits><exhibit n="1" wi="WI-13">label</exhibit>…</exhibits>` + prompt
  rule: "keep existing numbers; new documents take the next number".
- Guard: seed its numbering from `prior_exhibits` before assigning new ones.

### 6. Duplicate-upload detection (phase 2)

Today duplicates are grouped only if the writer notices (`also_wi`). Later:
group by `attachments` file hash / identical OCR `content_md` in the planner and
pass groups to the writer. Out of scope for phase 1.

## Out of scope

- Frontend: none needed — body is plain text; chat `WI-N` already renders.
  (Optional later: turn «← WI-13» into a clickable chip.)
- Existing documents: not backfilled. Old memos keep `WI-N`; the next revision
  cleans them (the guard runs on every publish, and the prior draft's aliases
  get mapped).
- Contracts' «ملحق» numbering.

## Files

| File | Change |
|---|---|
| `agents/writer/models.py` | `ExhibitRef`, `WriterLLMOutput.exhibits`, widen `from_package` with `alias_kinds` |
| `agents/writer/prompts.py` | exhibit rules, `## المرفقات` rule, output-contract example |
| `agents/writer/exhibits.py` (new) | `sanitize_wi_aliases`, `format_exhibit_guide_ar`, `map_aliases_in_chat` |
| `agents/writer/publisher.py` | call sanitizer, persist `metadata.exhibits`, span attrs |
| ~~`agents/writer_planner/runner.py`, `agents/orchestrator.py`~~ | not needed — guide + key_findings mapping live in the publisher |
| `agents/writer/tests/test_exhibits.py` (new) | 11 unit tests |
| `agents/writer/tests/test_publisher.py` | +1 wiring test |

Pre-existing, unrelated failures (fail on the untouched tree too): `test_agent_instructions` ×2,
`test_package_rendering::test_render_parity_with_legacy_builder`,
`test_runner::test_package_path_user_message_is_minimal` — stale Arabic string asserts.

## Tests

`sanitize_wi_aliases`:
1. Anwar's body (fixture from `f682a16d`) → zero `WI-` left, 4 exhibits, WI-13→1
   (first mention), WI-9→2, WI-12→3, WI-8→4.
2. Search alias `(WI-7)` stripped, sentence punctuation intact (no «  .»).
3. Unknown `(WI-99)` stripped, counted as unknown.
4. `also_wi` alias maps to the same k.
5. Attachment cited but missing from `exhibits` → appended, `## المرفقات` extended.
6. Lower-case `wi-3`, Arabic parens, bare inline use.
7. Idempotent: running twice = same output.

`format_exhibit_guide_ar`: ordering, duplicate note, empty → "".

Publisher: `metadata.exhibits` persisted; span attrs set; `decode_for_persist`
applied to labels.

Live check: rerun a revision on a dev convo with 3+ attachments (one duplicated)
→ body has only «مرفق رقم k», `## المرفقات` matches, chat guide maps k → WI-N,
Najiz copy contains no `WI-`. Verify leak counters in Logfire.

## Order of work

1. `exhibits.py` + tests (pure).
2. Model field + prompt rules.
3. Publisher wiring + metadata.
4. Chat guide (planner runner + legacy path).
5. Dev live check → ship.
6. Phase 2: revision stability, dedupe.
