# Writer planner v2 — template-first planning

**Status:** BUILT (2026-10-07), not deployed. Migration 173 NOT applied. See §9 for what shipped vs the plan.
**Trigger:** Turki Alharbi convo `4e81bf72-948a-4c88-9d06-b20dad5b5845` (2026-09-29):
«اكتب لي صحيفة دعوى» → the planner paused **3 times** (party names → trade name →
plan approval) before writing a word.
**Companion work (other session):** writing the system templates + serving them
through the `/templates` endpoints. This plan defines the contract it must meet (§2).

---

## 0. Diagnosis (why Turki got 3 pauses)

| Pause | Cause | Fixed by |
|---|---|---|
| 1. «هل تصحّ الأطراف التالية؟» | Mandatory party check (`prompts.py` § Party and Position Validation) — legit gap (no names at all) but a separate pause | §4: parties folded into the ONE plan pause; missing names → placeholders |
| 2. «هل للمؤسسة اسم تجاري؟» | Partial answer re-asked. Prompt invites it: «plan from what you have, **or ask once more**» (`prompts.py:349`). Model quoted the "don't spread" rule and overrode it | §4: a reply is final; never re-ask |
| 3. Plan approval | `WriterSubtype` has no صحيفة دعوى → model judged "subtype ambiguous" → `present_plan_for_approval` trigger (`prompts.py:158`) | §3: `statement_of_claim` subtype + court-keyed template |

Side findings: user referenced the contract/addendum/notice for style mimicry but
never attached them — nobody noticed. Resume-leg history looked mis-ordered in the
model's reasoning (turn 2) — verify in §7.

---

## 1. The new mental model

The planner's job becomes: **pick the template(s), tell the user which, draft.**

```
request ─► subtype (+ court for statement_of_claim)
        ─► resolve template: attached > user's own (خاص) > ours (عام, not hidden) > none
        ─► ONE plan pause, headed by «النماذج المقترحة»
        ─► draft ─► record templates_used on the WI ─► chat line under the reply
```

Every subtype gets a system template, so "no template" becomes rare and explicit.

---

## 2. Template catalog (system = قالب عام)

### 2.1 Files are the source of truth
`agents/writer/templates/<subtype>/<name>.md`, YAML front-matter:

```yaml
title: نموذج صحيفة دعوى أمام المحكمة التجارية أو العمالية أو العامة
subtype: statement_of_claim
court: commercial_labor_general      # statement_of_claim only
sources:                             # optional — our blog posts the template is built from
  - https://rayhanai.com/blog/...
```

Existing (untracked, written in the other session):

| File | `court` | Courts covered |
|---|---|---|
| `statement_of_claim/commercial_labor_general.md` | `commercial_labor_general` | التجارية · العمالية · العامة (writer keeps the matching court block, deletes the other two) |
| `statement_of_claim/criminal_court.md` | `criminal` | الجزائية (حق خاص) |
| `statement_of_claim/diwan_al_mazalim.md` | `diwan_al_mazalim` | الإدارية — ديوان المظالم |
| `statement_of_claim/personal_status_court.md` | `personal_status` | الأحوال الشخصية |

To add — the current 3–5 line skeletons in `agents/writer/prompts.py::_SUBTYPE_BODIES_AR`
promoted to real templates:
`contract/default.md`, `memo/default.md`, `legal_opinion/default.md`,
`defense_brief/default.md`, `letter/default.md`, `summary/default.md`.
`_SUBTYPE_BODIES_AR` then shrinks to the no-template fallback only.

### 2.2 Serving contract (for the other session)

Option **(a) — shared rows + per-user hide list** (decided 2026-10-07):

- System templates are stored ONCE (scope `system`, no `user_id`), synced from the
  repo files by a seed script/deploy step. Editing a file + re-sync updates every user.
- **Stable id** per system template, derived from the file path
  (e.g. `uuid5(NAMESPACE, "statement_of_claim/criminal_court")`) — so links in
  old drafts survive re-seeding.
- `user_hidden_templates (user_id, template_id, hidden_at)` — RLS own-rows.
  "Deleting" a عام template = insert a hide row. Restore = delete the row.
- System templates are **read-only**. Editing one = «انسخ إلى قوالبي» → new خاص row.
- `GET /templates` returns both scopes, each row:
  `{template_id, title, scope: 'system'|'user', subtype, court, sources[], ...}`
  minus the caller's hidden system rows.
- `/templates/{id}` renders a system template read-only (copy button), a user
  template in the editor.

### 2.3 Planner-side loading
`load_user_template_titles` (`backend/app/services/writer_planner_context.py:159`)
→ `load_template_catalog`: system (minus hidden) + user. Also filter out **empty**
user templates (10 of 12 live rows today are blank) — they're noise in the prompt.

---

## 3. Planner decision changes (`agents/writer_planner/models.py`)

- `WriterSubtype` += `"statement_of_claim"` (صحيفة دعوى) — `agents/writer/models.py:26`,
  plus a `_SUBTYPE_BODIES_AR` fallback entry.
- New `court: Literal["commercial","labor","general","criminal","personal_status","administrative"] | None`
  — required iff `subtype == "statement_of_claim"`. Six real courts; the runner maps
  commercial/labor/general → `commercial_labor_general` and passes the specific
  court to the writer so it keeps the right block.
- **System template is resolved by code, not picked by the LLM**: (subtype, court) →
  file. The LLM can't name a template that doesn't exist.
- `chosen_template` (TPL-n) stays for the user's **own** templates only. If a خاص
  template matches the same subtype (+court), it wins over the عام one.
- Court comes from the conversation first (prior search WI usually says it — Turki's
  said المحكمة التجارية). If genuinely ambiguous (e.g. عامة vs تجارية when one side
  isn't a trader), the plan lists both candidates and the user picks — same single pause.
- If the matching عام template is **hidden** by this user → fallback skeleton, and
  the plan says «لم يُستخدم قالب». Never silently use a hidden one.

### 3.1 Drop «احفظ كقالب» from the planner
- Remove prompt section "Offering to save a new template" (`prompts.py:213-240`).
- Remove `offer_save` / `offer_item_id` (`models.py:135-152`) + `tracking_output` keys.
- Remove the `template_save_offer` emit (`runner.py:718-752`).
- Frontend: remove `TemplateSaveOfferChip` + the SSE case in `use-chat.ts` / `chat-store.ts` / `types/index.ts`.
- **Keep** `/templates/ingest` + `template_ingester` (still callable from the UI).

---

## 4. The one pause — the plan, headed by «النماذج المقترحة»

For a **new document**, the planner presents exactly one plan (`present_plan_for_approval`).
Its first section names the template(s):

```markdown
## النماذج المقترحة
- نموذج صحيفة دعوى أمام المحكمة التجارية (قالب عام)

## الأطراف
- المدعي: [اسم المدعي] — فرد
- المدعى عليه: مصنع … (مؤسسة فردية)

## الطلبات
1. …

## ما سيُترك فارغاً لتعبئته
- رقم هوية المدعي، العنوان الوطني، رقم السجل التجاري للمدعى عليه

## مستندات أشرت إليها ولم تُرفق
- العقد الأساسي، العقد الإلحاقي، الإخطار — أرفقها إن أردت محاكاة صياغتك

هل أبدأ الكتابة؟
```

Rules (replace the current party-validation + strategy-alignment sections):

1. **Party check no longer pauses on its own.** Inferred parties go in `## الأطراف`;
   unknown names become template placeholders listed under «ما سيُترك فارغاً».
   The user corrects them in their approval reply if they want.
2. **A reply is final.** After any pause: fold the answer in, unknowns → placeholders.
   Delete «or ask once more». Never a second clarifying question in the same request.
3. **`ask_user` survives only for a request that can't be planned at all**
   (no document type inferable). Not for names, amounts, dates.
4. **Referenced-but-missing documents** (user mentions files not in the workspace)
   are flagged in the plan, not asked separately.
5. Two plausible templates (court ambiguous, or خاص vs عام both fit) → list them
   under «النماذج المقترحة» as numbered choices; the approval reply picks.
6. Plan language: Arabic; template titles verbatim; scope tag «(قالب عام)» / «(قالب خاص)».

**Skip the plan** (draft directly) only when: tone/length tweak of an existing draft,
or the user already approved a plan this request. Revisions of a delivered draft go
through the item editor, which does not change structure — no template carry-over needed.

Turki's case under v2: one pause (the plan above) instead of three.

---

## 5. Recording + showing which templates were used

### 5.1 Source of truth = the draft WI
Runner (not the LLM) writes, at publish:

```json
workspace_items.metadata.templates_used = [
  {"template_id": "…", "title": "…", "scope": "system", "subtype": "statement_of_claim",
   "court": "commercial", "sources": ["https://rayhanai.com/blog/…"]}
]
```
`[]` = no template used. Never inside `content_md` (would leak into نسخ لناجز / exports).

### 5.2 Chat line under the reply
- Copied onto the assistant message `metadata.templates_used` + SSE event
  `templates_used` (same pattern as `next_steps`: live event + persisted for reload).
- Rendered under the message, distinct from the next-step chips:

```
القوالب المستخدمة في الكتابة:  نموذج صحيفة دعوى — المحكمة التجارية · عام ↗   المصادر: [1] [2]
```
  - title → `/templates/{template_id}`; scope badge عام / خاص.
  - المصادر → the template's `sources` URLs (our blog posts), plain links.
  - Empty list → «لم يُستخدم قالب — بُني الهيكل تلقائياً».
  - Template since deleted/hidden → title shown, no link, «(محذوف)».
- An attached file used as structure is **not** shown here (not a قالب).

---

## 6. Writer side

- `TemplateRef` (`agents/writer/models.py:327`) += `scope`, `court`, `sources`.
- `<template source="library" …>` rendering (`agents/writer/prompts.py:482`) carries
  `scope` + `court`; for `commercial_labor_general` the writer is told the specific
  court so it keeps one block.
- Templates contain drafting guidance (`<!-- … -->`, and an «إرشادات الصياغة» section
  in diwan_al_mazalim). Writer prompt: follow it, never reproduce it.
  **Publisher guard**: strip any `<!-- … -->` that survives; flag a leaked
  «إرشادات الصياغة» heading (same pattern as the WI-N exhibit guard).
- Template placeholders `[…]` the planner had no value for stay as placeholders.

---

## 7. Open items / to verify while building

1. Resume-leg history order — turn 2 reasoning placed the party question *before*
   «نعم اكتب لي صحيفة دعوى». Check what `<recent_messages>` the resumed planner gets.
2. Planner model is `_FLASH` (`agents/utils/agent_models.py:226`); CLAUDE.md says
   tier_1. Re-evaluate after v2 — the simpler decision may make flash sufficient.
3. diwan_al_mazalim's guidance is a visible `## إرشادات الصياغة` section, not a
   comment — ask the template session to wrap it in `<!-- -->` like the others.
4. Template seed script location + when it runs (deploy hook vs manual).

## 8. Acceptance

- Replay Turki's convo: «نعم اكتب لي صحيفة دعوى» → exactly ONE pause, plan headed
  «النماذج المقترحة: نموذج صحيفة دعوى أمام المحكمة التجارية (قالب عام)», unknown
  IDs as placeholders, missing contracts flagged.
- Approve → draft follows the commercial block only; no `<!--` in `content_md`;
  WI + message carry `templates_used`; chat line links to `/templates/{id}` + 2 blog sources.
- Hide the commercial عام template → same request → plan says «لم يُستخدم قالب»,
  chat line says the same.
- User owns a خاص صحيفة دعوى template → it is proposed over the عام one.
- Memo request → `memo/default.md` named in the plan and the chat line.
- No `template_save_offer` event anywhere.

---

## 9. As built (2026-10-07)

Deviations from §1–§6, decided while building:

- **No `court` field on `PlannerDecision`.** Courts come from each template's
  front-matter and are shown in `<templates_catalog>` (`TPL-n | scope | subtype |
  court | title`); the planner picks a TPL alias. Keeps the code free of a court
  list while the templates are being rewritten. Safety net: when the planner names
  no template and exactly ONE visible system template matches the subtype, the
  runner uses it (`_sole_system_template_for`).
- **System templates are served from the repo files at runtime**
  (`agents/writer/system_templates.py`, uuid5 ids from the path) — no DB table,
  no seed step. Only the hide list is in the DB (`user_hidden_templates`, 173).
- **Resume-order bug fixed** (§7.1): `_render_recent_messages` reversed an
  already-chronological list → the planner read every chat newest-first.
- `/templates` search over system templates = plain substring (not BM25).

Files: `agents/writer/system_templates.py` (new), `shared/db/migrations/173_user_hidden_templates.sql` (new),
`agents/writer_planner/{prompts,tools,models,runner,deps}.py`, `agents/writer/{models,prompts,publisher}.py`,
`backend/app/services/{writer_planner_context,templates_service,message_service}.py`,
`backend/app/api/templates.py`, `backend/app/models/responses.py`, `backend/app/errors.py`, frontend (chat line,
save-offer removal, system-template UI).

Not done: `<subtype>/default.md` files for memo/contract/… — left to the template-writing
session (any `.md` dropped under `agents/writer/templates/<subtype>/` is picked up).
