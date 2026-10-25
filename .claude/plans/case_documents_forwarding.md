# Case documents → expanders + aggregator; responder loses the workspace list

**Status:** PLANNED 2026-10-25 — not built. Builds on the uncommitted `doc_type` change
(attachment summarizer labels every upload; `metadata.doc_type`).

**Scope:** deep_search_v4 (planner, orchestrator, case + reg expanders, aggregator), the unified
responder's context, the writer's source tags. No migration, no new table, no frontend change.

---

## 1. Decisions (owner, 2026-10-25 /reflect)

| # | Decision |
|---|---|
| D1 | The **responder** no longer sees the workspace-items list at all (titles, summaries, `doc_type`). The router keeps it unchanged. |
| D2 | Only the attachments the router attached to **this turn** (`deps.attached_items`, incl. reply attachments on resume) are forwarded — not every attachment in the conversation. |
| D3 | Forwarded types: `judgment`, `claim`, `hearing_minutes`, `memo`, `statement`, `expert_report`, `financial`, `contract`, `letter` — **plus attachments with no `doc_type`** (pre-2026-10-25 uploads, summarizer failures, summary not ready yet). Not forwarded: `chat`, `official_record`, `user_notes`, `reference`, `other`, and every non-attachment kind. |
| D4 | **Summary floor:** every forwarded document reaches the case expander, the reg expander and the aggregator with its WI alias, title, `doc_type` and summary — never dependent on the planner remembering anything. |
| D5 | **Aggregator** always gets the full text of every forwarded document (capped). |
| D6 | **Expanders** get full text only for the documents the planner names, per expander: `case_full_text_wis`, `reg_full_text_wis`. Empty = summaries only. No unfold tool for the expanders. |
| D7 | Caps: **12,000 chars per document**, **30,000 chars of full text per recipient per turn**. Over the per-doc cap → cut with a visible marker. Over the total → that document drops to summary + a note. |
| D8 | The **planner stays uncapped** (it reads every attachment in full today — largest live one 74k chars — and must, to choose). |
| D9 | The aggregator refers to a document by what it is («الحكم المرفق»), never `WI-N`, never as a `[n]` citation (`[n]` stays reserved for `<references>`). |
| D10 | Writer: selection stays with the writer planner; the writer additionally sees `doc_type` on its attachment sources. |
| D11 | The **sector_picker** does not receive case documents. Rerankers unchanged (they still read only `planner_brief`). |
| D12 | New prompt rules in the case expander, the reg expander, the aggregator, the planner, the responder and the writer (§5). |

Live data behind the caps (317 attachments, 2026-10-25): median `content_md` 1,087 chars, p90 12,242,
max 74,454; median summary 1,842 chars. The typical document costs ~1–3k chars per recipient.

---

## 2. Current state (verified in code)

- `_build_candidate_context_blocks` (`agents/deep_search_v4/orchestrator.py:965`) deliberately does
  **not** forward attachments; the planner is told to retell them in `planner_brief`
  (`planner/prompts.py:116`, `:144`, `:148`, `_PLANNER_BRIEF_DETAIL_RULES`; `planner/models.py`
  field descriptions; `shared/context.py` docstring).
- One shared `deps.context_blocks` list reaches three places: reg phase (`orchestrator.py:404`),
  case phase (`:626`), aggregator (`agg_input.context_blocks`, ~`:850`). The sector_picker gets its
  own list (`_select_forwarded_blocks(include_planner_brief=False)`, `:955`).
- Rerankers pick **only** `label == "planner_brief"` out of `state.context_blocks`
  (`case_search/loop.py:1113`, `reg_compliance_search/loop.py:558`) — a new label cannot leak in.
- Both expanders and the aggregator are single structured calls with no tools. Case expander runs
  1 round (`case_search/models.py:466`); reg expander up to 3 (`reg_compliance_search/models.py:422`).
- Planner snapshots: `agents/orchestrator.py::_load_attached_items` selects
  `item_id, wi_seq, kind, title, content_md, metadata` — **not `summary`**, so
  `WorkspaceItemSnapshot.summary` is always `""` today. `_encode_attached_for_planner` already
  encodes `title`, `content_md` **and** `summary` (masking / وضع السرية) — once `summary` is
  selected it is masked for free.
- Live prompt keys: aggregator `prompt_mode_case | prompt_mode_reg_compliance | prompt_mode_full`
  (all built on `_SHARED_ROLE_AR`, v1 — owner keeps v1); reg expander `prompt_1` (only variant);
  case expander `prompt_3` in prod, `prompt_4` built uncommitted + A/B running
  (`.claude/plans/case_expander_prompt_4.md`).
- Responder: `agents/responder/context.py:96-104` appends `render_workspace_items_fence(...)` as
  "### Workspace items in this conversation"; rule 3 of `follow_up_question`
  (`agents/responder/prompts.py:112`) tells it to check attachments / workspace items.
- Writer: `walkers.build_analyzed_items_direct` passes the full `content_md` of planner-selected
  items; `AnalyzedItem` has no `doc_type`; `<source>` attrs built in `writer/prompts.py:~418`.

---

## 3. Design

### 3.1 The block — one `case_document` block per document

New module `agents/deep_search_v4/shared/case_documents.py`:

```python
CASE_DOCUMENT_TYPES: frozenset[str] = frozenset({
    "judgment", "hearing_minutes", "claim", "memo", "statement",
    "expert_report", "financial", "contract", "letter",
})
PER_DOC_FULL_TEXT_CAP = 12_000
TOTAL_FULL_TEXT_CAP = 30_000
NO_SUMMARY_EXCERPT_CHARS = 1_500

def forwardable_documents(attached_items) -> list[WorkspaceItemSnapshot]:
    """kind == 'attachment' and (doc_type in CASE_DOCUMENT_TYPES or doc_type unset),
    with a wi_seq. Order preserved (router order)."""

def parse_wi_aliases(aliases: list[str], docs) -> list[str]:
    """'WI-3' → item_id of a forwarded doc; anything else dropped (debug log)."""

def build_case_document_blocks(
    docs, full_text_ids: list[str], *, per_doc_cap=..., total_cap=...,
) -> list[ContextBlock]:
    """One ContextBlock(label='case_document', persistence='turn',
    source_item_id=item_id) per doc. Docs in full_text_ids come first, in that
    order, then the rest in router order. Full text is attached while the
    running total stays ≤ total_cap."""
```

A test asserts `CASE_DOCUMENT_TYPES ⊆ artifact_summarizer.models.DOC_TYPES` so the two lists can't
drift apart.

**Body format** (Markdown — block bodies are XML-escaped by every renderer, so no nested tags):

```
### WI-3 · judgment · حكم ابتدائي في نزاع عمّالي
#### الملخص
{summary}
#### النص الكامل
{content_md[:12000]}
[انتهى المعروض: 12000 حرف من أصل 48210 — بقية الوثيقة لم تُعرض]
```

Variants:
- Summary-only recipient → no `#### النص الكامل` section.
- Full text due but over the total cap → `#### النص الكامل` + `(لم يُرفق النص الكامل لتجاوز حد السياق — اعتمد على الملخص)`.
- No summary yet and no full text → `#### الملخص` + `(لم يُكتب ملخص بعد — هذا مطلع النص)` + first
  1,500 chars of `content_md`.
- No summary yet but full text attached → summary section omitted.
- No `doc_type` → middle segment omitted: `### WI-3 · {title}`.

All fields come from `deps.attached_items`, which are the planner's **already-encoded** copies →
the block is masked like `planner_brief`; nothing new to encode or decode.

### 3.2 Who gets what

`run_retrieval` builds three lists once per turn:

| Recipient | Documents | Full text for |
|---|---|---|
| case expander | all forwarded | `decision.case_full_text_wis` |
| reg expander | all forwarded | `decision.reg_full_text_wis` |
| aggregator | all forwarded | **all** (planner-named first, so they win the total cap) |
| sector_picker, rerankers | none | — |

`FullLoopDeps` gains `case_document_blocks: dict[str, list[ContextBlock]]` (keys `"case"`,
`"reg"`, `"aggregator"`). A helper `_with_case_documents(deps.context_blocks, extra)` inserts the
document blocks **right after `case_brief`** (facts of the matter → statute text → planner gloss →
lessons) and is used at the three call sites (`:404`, `:626`, aggregator). `_CANONICAL_LABEL_ORDER`
and `_select_forwarded_blocks` are **not** touched, so the sector_picker path is unchanged by
construction.

Decision is `None` (editorial / replay paths) → expanders summary-only, aggregator full. No attached
items → no blocks → every user message byte-identical to today.

### 3.3 Planner decision

`PlannerDecision` (`planner/models.py`) gains:

```python
case_full_text_wis: list[str] = Field(default_factory=list, description=(
    "WI-N aliases of forwarded case documents the RULINGS search must read in full "
    "(it always gets every forwarded document's summary). Empty = summaries only."))
reg_full_text_wis: list[str] = Field(default_factory=list, description=(
    "WI-N aliases of forwarded case documents the REGULATIONS search must read in full "
    "(it always gets every forwarded document's summary). Empty = summaries only."))
```

…and the `planner_brief` / `context_labels` descriptions are rewritten to match §5.1 (they reach
the LLM through the JSON schema, so leaving them would contradict the prompt).

`_render_attached_items` adds `forwarded="true"` on items that `forwardable_documents()` keeps, so
the planner never has to guess which ones travel.

### 3.4 Responder

`agents/responder/context.py`: delete the workspace-items block (and the
`render_workspace_items_fence` import). Prompt rule 3 edited (§5.5). The router, the compactor and
both planners keep their lists.

### 3.5 Writer

- `walkers._fetch_workspace_items_sync`: add `doc_type:metadata->>doc_type` to the select.
- `AnalyzedItem.doc_type: str = ""`, filled in `build_analyzed_items_direct`.
- `writer/prompts.py` attr builder: `doc_type="…"` when `kind == "attachment"` and non-empty.
- Exhibit rule (§5.6).

### 3.6 Snapshot loader

`agents/orchestrator.py::_load_attached_items`: add `summary` to the select. Side effect, intended:
the writer planner's `<attached_items>` lines start showing the summary they were always meant to
(`writer_planner/prompts.py::_render_attached_items` already renders it when non-empty).

### 3.7 Telemetry

On the existing `run_retrieval` path (logger + the `deep_search.aggregator` span attrs):
`case_docs.count`, `case_docs.full_text.case|reg|aggregator` (aliases), `case_docs.truncated`
(per-doc cut), `case_docs.over_total` (dropped to summary), `case_docs.full_text_chars.<recipient>`.

---

## 4. Files

| File | Change |
|---|---|
| `agents/deep_search_v4/shared/case_documents.py` | **NEW** — §3.1 |
| `agents/deep_search_v4/shared/context.py` | docstring: replace "Note on attached artifacts" with the `case_document` label row |
| `agents/deep_search_v4/orchestrator.py` | `FullLoopDeps.case_document_blocks`; build in `run_retrieval`; `_with_case_documents` at 3 call sites; rewrite the "attachments NOT forwarded" comments (`:976-982`, `:1055-1059`); telemetry |
| `agents/deep_search_v4/planner/models.py` | 2 new fields; 2 descriptions rewritten |
| `agents/deep_search_v4/planner/prompts.py` | static prompt edits §5.1; new dynamic `_CASE_DOCUMENTS_RULES`; `forwarded="true"`; `_PLANNER_BRIEF_DETAIL_RULES` edits |
| `agents/deep_search_v4/case_search/prompts.py` | `prompt_4` + `prompt_3` Context-blocks section §5.2 |
| `agents/deep_search_v4/reg_compliance_search/prompts.py` | `prompt_1` Context-blocks section §5.3 |
| `agents/deep_search_v4/aggregator/prompts.py` | `_SHARED_ROLE_AR` bullet §5.4 (mirrored into `_SHARED_ROLE_AR_V2` for parity; v2 is not live) |
| `agents/orchestrator.py` | `_load_attached_items` selects `summary` |
| `agents/responder/context.py` | drop the workspace fence |
| `agents/responder/prompts.py` | rule 3 §5.5 |
| `agents/writer_planner/walkers.py`, `agents/writer/models.py`, `agents/writer/prompts.py` | §3.5 + §5.6 |

---

## 5. Prompt edits (verbatim)

### 5.1 Planner decider — `PLANNER_DECIDER_SYSTEM_PROMPT` + dynamic rules

**(a) Injected-context bullet**

OLD
```
- `<attached_items>` — attached items the router selected, with their full content (memos / notes / highly relevant documents).
```
NEW
```
- `<attached_items>` — attached items the router selected, with their full content (memos / notes / highly relevant documents). An uploaded document carries `doc_type` — what it is (`judgment`, `contract`, `claim`, …) — and `forwarded="true"` when it travels downstream as a case document (below).
```

**(b) "Only three blocks flow …" paragraph**

OLD
```
Only three blocks flow to the executors and the aggregator: `case_brief`, `planner_brief` (which you write), and `prior_search_lessons` (a summary of `<prior_searches>`). `<conversation_summary>`, `<recent_messages>` and `<attached_items>` are for you alone — if you find in them a fact the search needs, carry it into `planner_brief`.
```
NEW
```
Four kinds of block flow to the executors and the aggregator: `case_brief`, `planner_brief` (which you write), `prior_search_lessons` (a summary of `<prior_searches>`), and `case_document` — the user's own case papers.

Case documents are forwarded for you: every attached item marked `forwarded="true"` (an uploaded judgment, claim, hearing minutes, memo, statement, expert report, financial record, contract or letter — or an upload with no type yet) reaches both executors with its title, type and summary, and reaches the aggregator **with its full text**. The one thing you decide about them is which documents each executor also reads in full (`case_full_text_wis` / `reg_full_text_wis`).

Everything else — `<conversation_summary>`, `<recent_messages>`, and every attached item without `forwarded="true"` (a chat, an official record, the user's notes, a reference text, a draft, a prior card) — is for you alone: if you find in it a fact the search needs, carry it into `planner_brief`.
```

**(c) `planner_brief` section**

OLD
```
A field passed to the executors and the aggregator. **Empty is the default** for ordinary questions. Write it only when the attachments or the case context carry an explicit fact necessary to steer the search that will not arrive via the question — and in particular: the content of `<attached_items>` reaches the search only through this field. (The one exception: articles you fetched with `fetch_article` are forwarded downstream in full on their own channel, so they do not need to be quoted here.) Descriptive, not directive: state the discovered facts, not the suggested angles. (The detailed editing rules are injected into the dynamic instructions when attachments or prior searches are present.)
```
NEW
```
A field passed to the executors and the aggregator. **Empty is the default** for ordinary questions. Write it only when the attachments or the case context carry an explicit fact necessary to steer the search that will not arrive via the question. Two channels carry text downstream on their own, so never retell them here: the articles you fetched with `fetch_article` (forwarded verbatim), and the forwarded case documents (summary to the executors, full text to the aggregator). For a forwarded document, the brief adds only what no single summary says — how the documents relate to each other and to the question (e.g. «الحكم المرفق قضى بعدم الاختصاص، وصحيفة الدعوى المرفقة سابقة له»). The content of every other attached item reaches the search only through this field. Descriptive, not directive: state the discovered facts, not the suggested angles. (The detailed editing rules are injected into the dynamic instructions when attachments or prior searches are present.)
```

**(d) `context_labels` section — last sentence**

OLD
```
Attachments are not a label — their facts go via `planner_brief`.
```
NEW
```
Attachments are not a label — forwarded case documents travel on their own (you only pick their full-text readers), and facts from any other attached item go via `planner_brief`.
```

**(e) Output schema** — two lines added after `"planner_brief"`:

```
  "planner_brief": "<فارغ افتراضاً؛ حقائق المرفقات/القضية اللازمة للبحث عند وجودها>",
  "case_full_text_wis": ["<WI-N لوثيقة مُمرَّرة يقرأ بحثُ الأحكام نصّها كاملاً — فارغ افتراضاً>"],
  "reg_full_text_wis": ["<WI-N لوثيقة مُمرَّرة يقرأ بحثُ الأنظمة نصّها كاملاً — فارغ افتراضاً>"],
  "context_labels": ["prior_search_lessons"]
```

**(f) NEW dynamic `_CASE_DOCUMENTS_RULES`** — appended by `build_decider_instructions` only when
`forwardable_documents(deps.attached_items)` is non-empty (same token discipline as
`_PLANNER_BRIEF_DETAIL_RULES`):

```
## `case_full_text_wis` / `reg_full_text_wis` — which case documents each executor reads in full (this turn carries forwarded documents)

Both executors already receive every forwarded document's title, type and summary, and the aggregator receives every one in full. Name a document here, by its `WI-N` alias, only when an executor needs the document's own text — not just its summary — to search well:

- `case_full_text_wis` — the rulings search. It looks for how courts decided disputes like this one, so it gains most from the papers of the dispute itself: a `judgment` (what it decided and on what ground), `hearing_minutes`, a `claim` or a `memo` (the grounds each party raised), an `expert_report`. Name a court paper whenever the question turns on what it says («هل أعترض على الحكم؟»، «ما رأيك في دفوع الخصم؟»).
- `reg_full_text_wis` — the regulations search. It needs a document's text when the question turns on its exact terms: a `contract` clause, the wording of a `letter` or notice, the items of a `financial` record. A court paper rarely helps it beyond its summary.

Leave both lists empty when the summaries are enough — the document was attached as background, or the question does not turn on its wording. A document is shown up to 12,000 characters; each executor reads at most 30,000 characters of full text per turn, in the order you list them — put the decisive document first. Only aliases of items marked `forwarded="true"` count; any other alias is ignored.
```

**(g) `_PLANNER_BRIEF_DETAIL_RULES`** — two bullets

OLD
```
- **Excerpt, do not copy.** For long attachments: pick out the facts the search needs (parties, dates, amounts, conditions, quoted rulings), and do not copy a whole text.
```
NEW
```
- **Excerpt, do not copy.** For a long attached item that is **not** forwarded: pick out the facts the search needs (parties, dates, amounts, conditions, quoted rulings), and do not copy a whole text. A forwarded case document is never excerpted here — its summary and text travel on their own.
```

OLD
```
- **Length:** usually 3–15 sentences after excerpting, even for large attachments. The final test: "the executors read `query_restatement` + `planner_brief` only (they do not see the attachments) — is that enough for them to steer their queries precisely?" If the answer is no, fill out `planner_brief`. Write `planner_brief` in Arabic.
```
NEW
```
- **Length:** usually 3–15 sentences after excerpting, even for large attachments — and often empty when every attachment is a forwarded case document. The final test: "the executors read `query_restatement` + `planner_brief` + the forwarded documents' summaries (and the full text of those I named) — they see no other attachment. Is that enough for them to steer their queries precisely?" If the answer is no, fill out `planner_brief`. Write `planner_brief` in Arabic.
```

### 5.2 Case expander — `## Context blocks` (append)

**`prompt_4`** (Step 1 / Step 3 / `basis` vocabulary exists in this prompt):

```
### `case_document` — the user's own case papers

A block labelled `case_document` holds one paper of the user's own dispute — a ruling, a statement of claim, a memorandum, hearing minutes, a contract, a letter… It opens with a heading carrying its type and title, then its summary and, when the planner judged it pivotal, its full text (possibly cut at a marked point).

These blocks are not background: they are the facts of **this** dispute, and they come first in Step 1 — who the parties are and in what capacity, what was claimed, which defences were raised, what a court already decided and on what ground. The question still decides *what* you search; the papers tell you what that question means here. When the user asks about a ruling («هل أعترض على الحكم؟»), the points it decided and the grounds it rested on are your Step-3 decision points; a defence a party raised in a memo or in the minutes is exactly what a `basis` query looks for.

Write every query in the courts' wording, never in the document's: do not copy a sentence, a party name, an amount or a date into a query. A law or article a document cites counts as named in the input — the one-query allowance applies to it.
```

**`prompt_3`** (prod until prompt_4 ships; no Step vocabulary):

```
A block labelled `case_document` holds one paper of the user's own dispute (a ruling, a statement of claim, a memorandum, hearing minutes, a contract…): its type, title, summary and sometimes its full text. Unlike the other blocks, it states the facts of this dispute — read it to know the parties, what was claimed, the defences raised and what a court already decided, and let the points actually disputed there shape which principles, grounds and facts you query. Write every query in the courts' wording: never copy a sentence, a party name, an amount or a date from a document into a query.
```

### 5.3 Reg expander `prompt_1` — `## Context blocks` (append)

```
A block labelled `case_document` holds one paper of the user's own matter — a contract, a letter or notice, a financial record, a ruling, a statement of claim… It opens with a heading carrying its type and title, then its summary and, when the planner judged it pivotal, its full text (possibly cut at a marked point). Unlike the other blocks, it states the facts of the user's situation: read it to settle what the question is actually about — which contract, which clause, which relationship, which step has already happened — before you frame the issues. The clause, notice or payment the question turns on tells you which legal concepts to query («الشرط الجزائي في عقد المقاولة»، «إنهاء عقد العمل بإشعار من صاحب العمل»). Query the legal concept, never the document's wording: do not copy a sentence, a party name, an amount or a date into a query. A law or article the document cites counts as named by the user — the rules above on cited articles apply to it.
```

### 5.4 Aggregator — `_SHARED_ROLE_AR` (insert after the `<context_blocks>` bullet; mirror in `_SHARED_ROLE_AR_V2`)

```
- **`case_document` blocks are the exception — they are the user's own case papers** (a ruling, a statement of claim, a memorandum, hearing minutes, a contract, a letter…). Each opens with a heading carrying its type and title, then its summary and its full text; a long document is cut at a marked point — never guess past it. They are the facts of the user's matter, and when the question is about one of them («هل أعترض على الحكم؟»، «هل الشرط الجزائي في عقدي نافذ؟») the answer must work from what it actually says: the ruling's grounds, the clause's wording, the dates and amounts it states.
  - The legal rule still comes from `<references>` only, cited `[n]`. A document is a source of facts, never a legal authority, and never a `[n]` citation.
  - Refer to a document by what it is — «الحكم المرفق»، «العقد المرفق»، «مذكرة الخصم المرفقة» — never by its `WI-N` alias, which the user never sees.
  - When a document came without its full text, do not state its facts more precisely than its summary does.
  - If the part of a document that was cut off likely holds what the answer needs, say so in the gaps section.
```

### 5.5 Responder — `follow_up_question` rule 3

OLD
```
3. **Nobody has answered it already.** Never ask what the message history, the case context, an attachment or a workspace item already answers. Check them first.
```
NEW
```
3. **Nobody has answered it already.** Never ask what the message history or the case context already answers, or what the producing agent reports as known. Check them first.
```

(Safe because rule 2 already restricts questions to gaps the producing agent reports, and the
aggregator now reads the documents itself, so its reported gaps already account for them.)

### 5.6 Writer — exhibits section (insert after the `label_ar` bullet)

```
- A user document may carry `doc_type` — what it is (`judgment`, `contract`, `claim`, `memo`, …). Use it to describe the exhibit correctly in `label_ar` (a `judgment` is «صورة من صك الحكم …», not «مستند»); never write the label itself in the document.
```

---

## 6. Tests

| File | Covers |
|---|---|
| `deep_search_v4/tests/test_case_documents.py` **NEW** | type filter (9 in + unlabeled in; `chat`/`official_record`/`user_notes`/`reference`/`other` out; non-attachment kinds out; no `wi_seq` out); `CASE_DOCUMENT_TYPES ⊆ DOC_TYPES`; per-doc cut + marker; total cap → summary + note; planner-named first; no-summary excerpt; summary omitted when full text present; unknown/foreign alias ignored |
| `deep_search_v4/tests/test_context_block_forwarding.py` | per-recipient lists (case ≠ reg ≠ aggregator full-text sets); inserted after `case_brief`; sector_picker gets no `case_document`; no attachments → lists byte-identical to today |
| `deep_search_v4/planner/tests/test_attached_doc_type.py` | `forwarded="true"` only on forwardable items; `_CASE_DOCUMENTS_RULES` injected only when one exists; `PlannerDecision` new fields default `[]` |
| case / reg / aggregator prompt tests | each live prompt carries its `case_document` rule; user message renders the block inside `<context_blocks>` |
| `responder/tests` | responder context contains no `<workspace_items>`; router still does (`test_router_context_render.py:269` comment updated — the renderer test itself stays) |
| `writer` tests | `<source kind="attachment" … doc_type="judgment">`; no attr for other kinds / empty |
| `agents/tests` (orchestrator) | `_load_attached_items` select includes `summary` |

Run: `python -m pytest agents/deep_search_v4 agents/responder agents/writer agents/writer_planner agents/memory -q`.
Known pre-existing failures to ignore: `agents/router/tests/test_agent.py::test_inject_case_context_with_case_memory`,
`::test_inject_user_preferences_with_prefs` (stale Arabic-heading assertions, unrelated).

---

## 7. Sequencing & risks

1. **In-flight work in the same files** — `case_search/prompts.py` (prompt_4, A/B running),
   `aggregator/prompts.py` + `runner.py` (aggregator v2), `shared/query_cap.py`, plus the uncommitted
   `doc_type` change. Diff every file before `git add` (memory: git-add-dirty).
2. **Do not edit `prompt_4` while its A/B is running** — it changes the system prompt of every
   replay. Edit `prompt_3` now; apply §5.2-prompt_4 after the A/B verdict (or ship it with prompt_4).
3. Ship together with (or after) the `doc_type` commit — this plan reads `metadata.doc_type`.
   Recommended first: the lenient `doc_type` validator from the review (unknown label → `"other"`),
   so a stray label can't cost the whole attachment summary.
4. **Cost:** only turns with forwarded documents pay. Aggregator ≤ +30k chars (~10k tokens) worst
   case, ~1–3k chars typical. Reg expander full text is re-sent each round (≤3) — another reason it
   is planner-gated.
5. **Expanders copying document text into queries** — guarded by the prompt rule; check in the live
   validation (queries must not carry party names / amounts).
6. Prefix caching: the new blocks sit in the **user** message (like every context block), so system
   prompts change once at deploy and stay stable after.

## 8. Live validation

1. Upload a real judgment (or reuse a demo conversation), ask «هل أعترض على الحكم؟» → deep search.
2. Logfire (via `@logfire-monitor-agent`): planner output has `case_full_text_wis=["WI-n"]`; case
   expander user message carries the `case_document` block with `#### النص الكامل`; reg expander
   carries summary only; aggregator carries full text; no `case_document` in sector_picker or reranker
   messages.
3. Answer refers to «الحكم المرفق», no `WI-` string, every `[n]` maps to `<references>`.
4. Responder system context has no "Workspace items in this conversation".
5. A turn with **no** attachment: expander + aggregator user messages byte-identical to before.
