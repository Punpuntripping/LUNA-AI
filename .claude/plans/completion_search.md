# Completion Search — one lookup helper for the planners and for attachments

**Status:** BUILDING — the owner approved the build on 2026-11-22 ("let's start building the plan").
Written 2026-11-21 from the owner's /reflect answers (2026-11-14 → 11-21); the answers of 2026-11-22
are folded in (C23–C25). **Phases 0–6 are built** (§7.1–7.4), 2026-11-23. Nothing is committed,
deployed or applied to the database, and every flag is off. Left: Phase 7 (the owner's rollout).
Build copy: `C:\Programming\LUNA_AI_completion_search` (branch `feat/completion-search`, from
`origin/master` @ `5dc2f18`).

**Why now:** agents are left with gaps they cannot close. Similarity search cannot return *the* article
56 of a named law, a ruling by its case number, or the sentence where a law defines a term. Today only
the search planner can fetch an article (`fetch_article`), only by an exact law title, and what an
attachment cites is never looked up at all.

**Vocabulary:** the helper is a **Layer 3 Task** agent when a planner calls it (it never talks to the
user) and runs as a **Layer 4** helper-frame step for attachments. Model **tier_2** (flash). See
CLAUDE.md "Layer vs Tier".

**Code base for line numbers:** `origin/master` @ `2d9754f` (2026-11-21). The build runs in a fresh
worktree from `origin/master` (now `5dc2f18`) — the main checkout is on an unrelated branch with
uncommitted edits. The read-only planning copy was removed on 2026-11-22.

---

## 0. In one paragraph

**Completion Search** is one small agent that finds a *named* legal text and hands back its official
wording: an article of a law by number, a phrase inside a law, a ruling by case number, a judicial
principle by number, a circular, a government service. A caller sends plain words
(«نظام المعاملات المدنية المادة 56 والمادة 234»); the helper splits them into searches, tries up to 3
rounds on the ones still unsatisfied, and returns each result with a confidence level (high / medium /
low). It uses word match only — no similarity search — and is built from simple search's existing
lookups and its model-free opener, as one agent. The two planners call it as a tool (it replaces
`fetch_article` on the search planner). For attachments, a **model-free scan** lists every «نظام /
لائحة / مادة» mention; the helper resolves them in the background; each official article is then
printed under the attachment **segment that cites it**, and the user gets **one card per conversation**
listing what was pinned.

---

## 1. Today on master (verified 2026-11-21)

### 1.1 `fetch_article` and the pinned card

- Tool `fetch_article(ctx, regulation_title, article_number) -> str`
  (`agents/tool_repository/fetch_article.py:1529-1615`), registered **only** on the deep-search planner
  decider (`agents/deep_search_v4/planner/agent.py:279`). No model inside it.
- It resolves the law only on a unique exact title; otherwise it returns a shortlist of up to 3 titles.
  Planner text is cut at `_PLANNER_ARTICLE_CAP = 4_000` (`:154`); the full text is accumulated on the
  planner deps (`accumulate_fetched_article`, `:1365`).
- `flush_statute_package` (`:1422-1501`) writes a workspace item `kind="note"`,
  `metadata.subtype="statute_package"`, `created_by="agent"`. **A new card on every flush**, never an
  update; no dedup across turns. Prod holds 203 such cards in 177 conversations.
- The full text travels as the `statute_articles` context block
  (`agents/deep_search_v4/orchestrator.py:1135-1143`) to the sector picker (`:1034`), the reg expander
  (`:479`), the case expander (`:705`) and the aggregator (`:938`). No size cap on it.
- The writer planner has no article lookup (`agents/writer_planner/agent.py:91-106`).
- The module cannot be deleted: simple search and `regulation_versions.py` import its resolver
  internals (`manual_search.py:92,333`, `searcher.py:64`, `compliance.py:77`, `regulation_versions.py:34`).

### 1.2 Simple search parts that are reused

- Plain functions callable without a model run:
  `resolve_regulation_id(supabase, title)` (`fetch_article.py:1057`),
  `regulation_versions.resolve / open_article` (`regulation_versions.py:229, 547`),
  `fetch_article_identity` (`searcher.py:690`),
  `manual_search_core(supabase, query, data_type, *, embedding_fn=…)` (`manual_search.py:725`).
- **`embedding_fn=None` switches off every similarity call** in `manual_search_core`
  (`manual_search.py:302-318`): `search_topics`, `hybrid_search_cases`, the house embedder.
- Number reading already exists: `article_number_keys` (`fetch_article.py:591-613`) =
  raw → Arabic-Indic digits → ordinal words 1–999 (`_arabic_ordinal_to_int`, `:563`).
- The model-free opener `unfold(supabase, resolved, *, judgment_access=None)`
  (`agents/simple_search/unfold.py:2220`) renders an article, ruling, circular or service as text under
  fixed budgets. It needs only a service-role Supabase client.
- A ruling's full text is gated: `judgment_entitled` (`unfold.py:1234`) with a resolver built by
  `judgment_access_resolver` (`agents/simple_search/runner.py:261-319`), which calls the library's
  `resolve_access(…, surface="reference")` and **charges an unlock**.
- Every simple-search lookup tool records a search fee first (`searcher.py:810,865`,
  `manual_search.py:1343-1348`).
- **Missing in simple search:** a case by its number (the number is display only), judicial principles
  (not one of its six levels), a word inside a document, retrying with other wording (its prompt says
  pass the name verbatim and ask the user).

### 1.3 The attachment pipeline

- Helpers start with the user's **message**, not at upload: OCR → summary → dependents
  (`agents/helpers/turn.py:258-281`). An item not filled in this turn only runs when a *critical*
  consumer calls `ensure_ready` (`agents/helpers/status.py:422-489`).
- The summary writes `workspace_items.metadata.doc_type` (`agents/memory/summarize.py:341-344`); 14
  values (`agents/memory/artifact_summarizer/models.py:83-98`).
- The segmenter (`registry.py:223-242`) depends on (ocr, summary) and is critical for the case
  expander, reg expander, aggregator and writer; ceiling 300 s. It runs only for attachments over 5,000
  characters or small files bundled in one message.
- Segments live in `attachment_segments` (item_id, seq, from_p, to_p, page_from, page_to, char_start,
  char_end, label, value, what, content_md, owner_class, owner, brought_by). A re-map **deletes and
  re-inserts** every row (`store.py:90-106`; FK `ON DELETE CASCADE`).
- Since migration 223 (applied to prod 2026-11-22) the segment maps and `helper_runs` hang on the
  **file** (`user_files.file_id`), not on the conversation's item; the column is still named `item_id`.
- Delivery: `wait_and_load_sections` (`agents/utils/attachment_sections.py:54-106`) →
  `render_sections` (`agents/utils/attachment_delivery.py:173-212`). Deep search gets `core` sections
  only; the writer gets metadata + core + supporting. Caps 100k per document / 300k per recipient;
  tags, markers, summaries and headings are not counted.
- Deep search only receives attachments whose type is in `CASE_DOCUMENT_TYPES`
  (`agents/deep_search_v4/shared/case_documents.py:65-68`): judgment, hearing_minutes, claim, memo,
  statement, expert_report, financial, contract, letter.
- Nothing records which laws an attachment cites — not the summary, not the segment map.

### 1.4 Data facts (prod, read-only, 2026-11-14 → 11-21)

| Fact | Value |
|---|---|
| Regulations / with article text | 3,982 / 1,837 (52,811 articles) |
| Article length | p50 325 · p90 1,327 · p99 5,079 · max 244,419 chars |
| Regulation status | in_force 2,745 · in_force_amended 195 · consultation_ended 981 · cancelled 56 |
| Titles shared by more than one regulation | 40 titles, 86 regulations |
| Regulations linked to a parent law (`parent_law_id`) | 331 |
| Word search inside the largest law (721 articles, تشكيل ignored) | 55 ms, no new index |
| Rulings / in the library index | 37,066 / 35,773; case number stored as an index facet |
| Exact case-number lookup through the index | under 1 ms |
| Case numbers shared by more than one ruling | 2,081 (some old rulings carry placeholders `unk01…`) |
| Judicial principles | 2,304, unique `principle_no`; **not** in the library index |
| Circulars | 1,844, no number or date column, 5 issuing bodies, word-search column filled |
| Government services | 4,985 (4,866 distinct names); word-search column `fts` filled on all; library index holds only 925 |
| Attachments with text | 661; 246 mention «المادة» (2,448 mentions) |
| How the article number is written | digits 52% · words 33% («المادة السابعة والسبعون»); 440 attachments use ٠-٩ |
| Mentions that name the law in the same phrase | 774 of 2,448 (about a third) |
| Contracts | 216 of 376 «المادة» are the contract's own clause headings; 16 name a law |
| Mentions by segment value (non-contract) | core 1,956 · repeat 31 · metadata 7 · boilerplate 6 · supporting 6 |
| Citing non-contract attachments without any segment map | 30 of 209 |
| Segments tile the text | 8,628 neighbouring pairs: no overlap, gaps of 2–4 characters (line breaks) |

### 1.5 Existing defects this plan removes

1. **Articles fetched after an `ask_user` pause are lost** — the resume branch never flushes
   (`planner/runner.py:615-630`; fresh deps at `agents/orchestrator.py:4658`). No card, no
   `statute_articles` block.
2. **The "card created" signal never reaches the screen** — `_progress_from_planner_event` drops it
   (`agents/orchestrator.py:3692-3758`).
3. The pinned card is an **editable note**, counts toward the 15-item cap, and is sent to the LLM
   summariser.
4. The tool's docstring (what the model reads) contradicts the planner prompt
   (`fetch_article.py:1561-1576` vs `planner/prompts.py:155,157`).

---

## 2. Vocabulary

| Word | Meaning |
|---|---|
| **Request** | The plain words a caller sends. No fields. May hold several searches. |
| **Search** | One thing to find inside a request (one article, one case, one phrase). |
| **Round** | One attempt at a search. At most 3 per search; a satisfied search is not repeated. |
| **Confidence** | high / medium / low, decided by code from checkable facts (§4.3), never by the model's own opinion. |
| **Pin** | A found official text that is saved and travels to the working agents. |
| **Card** | The one workspace item per conversation that shows the user what was pinned. |
| **Scan** | The model-free pass over an attachment's text that lists every mention. |
| **Mention** | One place in an attachment that names a law, a regulation or an article. |

---

## 3. Owner decisions

| # | Decision | Date |
|---|---|---|
| C1 | A new LLM helper agent, called on demand; the answer goes back to the agent that asked; it never writes a report and never talks to the user. | 11-14 |
| C2 | The request is plain words — "it shouldn't be filling item, just send the request". One request can hold several searches. | 11-14 |
| C3 | Two dimensions: first the document (the library's word search, retried with other wording), then the article number or word inside it. | 11-14 |
| C4 | Best guesses are allowed; each result carries a confidence level (high / medium / low, tied to hard facts). | 11-15 |
| C5 | At most 3 rounds; each round repeats only the searches still unsatisfied; then it returns the best result. | 11-15 |
| C6 | Mode A (article in a document) and Mode B (word in a document) first. Mode C (find a law through its sector) later. | 11-15 |
| C7 | All five source types in the first version, in two groups "just like in the deepsearch": regulations + circulars + government services; cases + judicial principles. | 11-15 |
| C8 | High-confidence results are saved as a **new workspace item type** that replaces the `statute_package` card and takes both its jobs. | 11-15 |
| C9 | Medium results go back to the asking agent "and the agent will decide". A medium result the agent accepts is saved and passed on like a high one (my suggestion 11-16, not objected). | 11-15 |
| C10 | Callers in the first stage: the two planners and the attachment flow. The aggregator waits for a performance review. | 11-15 |
| C11 | "All what we want to do is embedded in the simple search, but instead of 2 agents we want it into 1 agent." A **new** agent that reuses simple search's parts; simple search itself stays untouched. | 11-16, 11-21 |
| C12 | No similarity search anywhere — "even the gov services". | 11-21 |
| C13 | No per-search fee on the helper's lookups. | 11-21 |
| C14 | Attachments: a **model-free** tool finds «نظام / لائحة / مادة» and returns what it found to the agent. | 11-21 |
| C15 | It runs automatically on every attachment; the list goes to Completion Search. | 11-21 |
| C16 | Contracts are excluded — wait for the summary's document type. | 11-21 |
| C17 | A law that is not in our data gets a "not applicable" result, with no further tries. | 11-21 |
| C18 | What is pinned is the **official article text**. A law cited as a whole, with no article, is not pinned. | 11-21 |
| C19 | The article is pinned **within the segment that references it**. | 11-21 |
| C20 | Citations go in a **new table**, not a new column on the segment table. | 11-21 |
| C21 | The user still gets **one card per conversation** listing the articles found. | 11-21 |
| C22 | An article cited in several segments is printed **in full under each one** — "each segment should have its own text, because it might get sent to different places". | 11-21 |
| C23 | Pinned text under a segment: each article is cut at **10,000 characters** ("make it 10k per article"); pinned text does not count toward the 100k / 300k attachment caps; a 30,000-character safety net per document. | 11-22 |
| C24 | Rulings: **no unlock gate** on the helper — "no, do not make it complicated". A ruling found by its number is opened without using one of the user's ruling unlocks. | 11-22 |
| C25 | The card's name on screen: «النصوص المثبّتة». | 11-22 |
| C26 | Attachments **use the agent**: "the attachment should use a model" · "the agent should be the responsible for fetching". Code decides what it can prove; everything else goes to the Completion Search agent, which fetches the article itself. What it fetches **is delivered** when code confirms the law is in the library, in force, and has that article. This answers open question 3 and reverses the model-free build of §7.3. | 11-23 |

Standing rules that also apply: every background agent's model use counts toward the user's usage
(owner, 2026-10-16); one shared output-token cap; all error text in Arabic; Latin digits only; existing
Arabic strings are copied, never retyped.

**Earlier decisions this plan reverses on purpose** (`.claude/plans/fetch_article_bm25_resolution.md`):
"only a unique exact title resolves" becomes "best guess with a confidence level" (C4); "dedup within
the turn only, one card per search" becomes "one card per conversation" (C21). Kept: gate on title
coverage and never on the BM25 score; text only, never an `[n]` citation; the normal search still runs;
errors return a plain string, never `ModelRetry`; `manual_search.py` is not edited.

---

## 4. Design

### 4.1 The pieces

```
 search planner ─┐                         ┌─ card (one per conversation, user-visible)
 writer planner ─┼─ complete_search(req) ──┤
                 │        │                └─ statute_articles block → sector picker, expanders, aggregator
                 │        ▼                   writer block          → writer
                 │  Completion Search agent (tier_2, word match only, ≤3 rounds)
                 │        ▲
 attachment ─ scan (no model) ─ mentions ──┘──► attachment_citations (new table)
                                                     │
                     segments + citations ───────────┴─► article text printed under each citing segment
```

Three new things: **the helper agent** (§4.2–4.4), **the card** (§4.5), **the attachment flow**
(§4.6–4.7). Each is behind its own flag (§4.10).

### 4.2 The helper agent

New package `agents/completion_search/` — `agent.py`, `deps.py`, `models.py`, `prompts.py`,
`lookups.py`, `confidence.py`, `runner.py`, `tests/`.

**Input.** `request: str` (plain words) plus `caller` (`deep_planner` / `writer_planner` /
`attachment`). In attachment mode the request is the scan's mention list (§4.6.3).

**Model.** New slot `completion_search` in `AGENT_MODELS` (`agents/utils/agent_models.py:300-460`),
tier_2. Phase 1 measures `_FLASH` against `_FLASH_MEDIUM` (what the simple-search searcher uses) for
accuracy and seconds, and picks one. `UsageLimits(output_tokens_limit=OUTPUT_TOKENS_CAP, request_limit=…)`.

**How the 3 rounds are enforced.** Every lookup tool takes a `search_id`. The deps count attempts per
search; a search already at **high** is closed and a 4th attempt is refused by code with a fixed
Arabic line. The prompt alone is not trusted for this. A request holds at most 10 searches (Q2).

**Tools** (all word match; none records a search fee; none can ask the user):

| Tool | Finds | Built from |
|---|---|---|
| `find_regulation(search_id, name)` | the law or regulation | `resolve_regulation_id`, then `manual_search_core(…, "regs", embedding_fn=None)`; `parent_law_id` for «اللائحة التنفيذية لنظام …» |
| `get_article(search_id, regulation, number)` | Mode A | `article_number_keys` + `regulation_versions.open_article` |
| `find_in_regulation(search_id, regulation, phrase)` | Mode B | new SQL function (§5); the law's `definitions` text first when the request is a definition |
| `find_circular(search_id, words, issuer="")` | a circular | `manual_search_core(…, "circulars", embedding_fn=None)` + issuing body |
| `find_service(search_id, name, provider="")` | a service | `_ilike_services` + the `services.fts` column (all 4,985 rows) |
| `find_case(search_id, case_number, court="", year="")` | a ruling by number | new: exact match on the library index facet, then `cases.case_number` / `judgment_number`; narrowed by court, then date |
| `find_principle(search_id, number=None, words="")` | a judicial principle | new: `official_principles` by `principle_no`, else chapter title + words |

Each tool returns short candidate lines with handles (`C1`, `C2`…) as simple search does. The model's
structured output only **selects handles** and labels each search; **code** opens the chosen handles
with `unfold` (plus a new small renderer for principles). **The model never types legal text.**

**Prompt rules that invert simple search's** (`agents/simple_search/prompts.py:349-362`,
`manual_search.py:1308-1335`): no user to ask; when a name does not match, try the official form, the
short form, the form without «نظام / لائحة», the parent law — then stop at round 3; keep numbers
exactly as given; never invent a law name that no candidate line shows.

**Versions.** `regulation_versions.resolve` already defaults to the in-force side. The helper never
asks which version; when two exist it returns the in-force one and says so in the result note (Q5).

**Output.** `CompletionResult.searches[]`, each with: the caller's words for it, `status`
(`found` / `candidates` / `not_in_library` / `not_found`), `confidence`, the selected handles, a
one-line note. The runner turns this into (a) the text the caller reads and (b) the pin records.

### 4.3 Confidence — decided by code from checkable facts

| Search | High | Medium | Low |
|---|---|---|---|
| Article in a law | unique exact title **and** the article number exists | best-guess title (coverage ≥ 0.60, one clear best) and the article exists | several possible laws: up to 3 titles, no text |
| Word in a law | exact title and the phrase sits in exactly 1 article, or in the definitions article for a definition request | 2–5 articles hold it (all returned), or found only after rewording | more than 5 hits or partial words |
| Ruling by number | exactly one ruling carries the number after narrowing | several share it and nothing narrows: up to 3 listed with court and date, no text | — |
| Principle | exact `principle_no` | one clear best by chapter + words | several |
| Circular | exact title | issuing body + coverage ≥ 0.60, one clear best | several |
| Service | exact name, unique (or made unique by the provider) | coverage, one clear best | several |

A law that no round can match is `not_in_library` (C17). Placeholder case numbers (`unk…`) never
match. The gate is always title coverage, never the BM25 score.

### 4.4 Called from the planners

New `agents/tool_repository/completion_search_tool.py` with `register_completion_search(agent, caller)`:

- `complete_search(request: str) -> str` — runs the helper with `run_tracked(stage="completion_search.run",
  slot="completion_search", …)` inside try/except, under a time limit (60 s to start; set from Phase 1
  numbers). Any failure returns a plain Arabic line, never `ModelRetry`. `ctx.usage` is **not**
  forwarded (the sub-run writes its own ledger row; forwarding would bill twice).
- `keep_results(result_ids: list[str]) -> str` — no model. The planner calls it to accept a medium
  result (C9).

What the planner reads back: one block per search — status line, the resolved title as a header (so it
can check the law), and the text cut at 4,000 characters with the existing marker. High results are
pinned at once; medium ones are shown with a medium-confidence note and pinned only through
`keep_results`.

**Search planner.** With the flag on, `register_fetch_article` is replaced by
`register_completion_search` (`planner/agent.py:279`). The prompt section at `planner/prompts.py:141-163`
is rewritten: one request can name several items; any of the five source types; "article number with no
law → `ask_user` first" stays; "still run the normal search" stays. `statute_articles` is added to the
list of block kinds at `:131`. The stale lines in `reg_compliance_search/prompts.py:243` and
`case_search/prompts.py:692` are aligned.

**Forwarding.** The block keeps its name `statute_articles` and its consumers. It is built from the
turn's pins. Pins are written to the card when they are made (not at the end of the phase), and the
turn's pin list is carried from the resume deps into the phase 2–3 deps — this closes defect 1.5.1.
Articles and principles travel in full; rulings, circulars and services travel in `unfold`'s standard
rendering.

**Writer planner.** `register_completion_search(agent, "writer_planner")` in
`agents/writer_planner/agent.py`; one prompt passage. The turn's pins reach the writer as one block in
the package, built in `agents/writer_planner/walkers.py` the same way the deep-search block is built.

**Masking.** With «وضع السرية» on, planners read encoded text, and nothing decodes tool arguments today.
Digit runs of 8 or more are masked, so a 10-digit case number in a request would be a fake. The tool
decodes the request with the turn's codec (`backend/app/services/masking_service.py:250`) before any
lookup. Article, law and decree numbers are short and untouched.

**Rulings — no gate (C24).** `find_case` opens the ruling it found without calling the library's
unlock: `judgment_access_resolver` is not used, the opener is handed a granted access object, nothing
is charged and no plan check runs. The full text goes to the agents. The card shows a ruling's title,
lead and link only (Q1), so this does not become a way to copy rulings in bulk.

### 4.5 The card — a new workspace item kind

New enum value on `workspace_item_kind` — **`pinned_texts`**, shown to the user as «النصوص المثبّتة» (C25).

- **One per conversation**, enforced by a unique partial index; created on the first pin, updated in
  place after that.
- Two writers can hit it at once (a planner's pin and a background attachment run), so the merge is one
  SQL function that locks the row (§5).
- `metadata.entries[]`: one entry per distinct text — source type, ids, title, confidence, and
  `cited_in[]` (the planner that asked, or the attachment and the position of the citing sentence).
- `content_md` is rendered from the entries: each text **once**, with a «ورد في» line per place it was
  cited (C21). Articles and principles show their full text; rulings, circulars and services show title,
  a short lead and the library link (Q1).
- Read-only for the user and for `artifact_editor`; **not** counted in the 15-item cap
  (`agents/orchestrator.py:124`, `shared/db/migrations/031_artifact_cap.sql:33,41`); its summary is set
  by code, so the LLM summariser does not run on it.
- A real workspace event is emitted on create and update so the screen refreshes (closes 1.5.2).
- If the router attaches the card to a planner, the planner sees its titles, not its full text.
- The 203 old `statute_package` cards stay as they are (Q4).

Files that learn the new kind are listed in §6. Old clients drop an unknown kind without crashing: the
web list filters it out and the mobile list drops it (`mobile/…/workspace-model.ts:180-193`).

### 4.6 The attachment flow

#### 4.6.1 The scan — model-free

New `agents/tool_repository/citation_scan.py`: `scan_citations(content_md, doc_type, titles) -> ScanResult`.
Pure code: no database, no model, same input → same output. `SCAN_RULE_VERSION` constant.

**Built in Phase 0, with one addition:** `titles` is the library's list of regulation titles, loaded
by the caller and handed in as data (`TitleIndex`). A law is recognised when the words in the document
equal a library title exactly, so «نظام المعاملات المدنية الصادر بالمرسوم…» stops at the title, and
titles that do not start with «نظام / لائحة» are found too. This is what lets code alone decide half
of all citations (§7.1). Each mention also carries **how it is tied to its law** (`basis`): same
phrase · back-reference · same sentence · the document's only law · nearest (a guess) · belongs to
something else («من العقد») · no law.

It finds:

- **Article mentions** — «المادة / المادّة / الماده», with or without «رقم» and brackets; the number as
  Latin digits, ٠-٩ digits, or words (reusing `article_number_keys`); plural and range forms
  («المادتين 77 و78», «المواد من 10 إلى 15», ranges expanded up to 10 articles); «الفقرة (أ) من المادة
  77» counts as article 77.
- **Law names** — «نظام …», «اللائحة التنفيذية لنظام …», «لائحة …», read up to a stop word, a mark or 8
  words; and **back-references** («النظام», «هذا النظام», «اللائحة») marked as such.
- **The document's own headings** — a mention that starts a line (after list or heading marks) is the
  document's own clause. It is recorded as a heading and never sent for resolution.

For each mention: character start and end in `content_md`, the paragraph number (from the segmenter's
own `paragraphize`, `agents/memory/attachment_segmenter/paragraphs.py:162-191`), the article numbers,
the law named in the same phrase (if any), and the nearest law named before it.

Case numbers, circulars and principles inside attachments are out of the first version (a case number
in an attachment is usually the user's own case).

#### 4.6.2 A new helper in the frame

`CITATION_PINS = HelperSpec(…)` in `agents/helpers/registry.py`:

| Field | Value |
|---|---|
| name / scope | `citation_pins` / item |
| depends_on | ("ocr", "summary") — the document type comes from the summary (C16) |
| critical_for | case_expander, reg_expander, aggregator, writer — the same as the segmenter, because the pins arrive inside the segments |
| ceiling | 300 s, the segmenter's |
| applies_to | has text **and** `doc_type != "contract"` (no type yet = not a contract) |
| enabled | flag + allowlist (§4.10) |
| stored / load | `stored=True`; `load` returns the scan row when it exists and its `content_chars` still equals the text length, else `None` |
| run | §4.6.3; raises on failure so the frame records it and retries |
| fallback | sections delivered without pinned text |

It starts beside the segmenter when the message arrives; neither waits for the other. Old attachments
get it the first time a critical consumer reads them (no bulk backfill, Q4). As with segment maps, a new
`SCAN_RULE_VERSION` does not redo stored results; changed text does.

#### 4.6.3 The run

1. Read the item. Scan. **No resolvable mention → write the scan row with zero counts and stop; no
   model call.** (415 of 661 attachments have no «المادة» at all.)
2. Build the helper's input: each mention's id, numbers, law hints, and its sentence (at most 300
   characters). The sentences are private text: encode them with a per-user codec and persist new fakes
   **before** the model call, as the segmenter does
   (`agents/memory/attachment_segmenter/runner.py:191-198, 594-595`).
3. **Code first** (changed after Phase 0): a citation whose law equals a library title, is in force,
   has the article, and whose link is checkable is decided by code with no model. Completion Search
   runs in attachment mode **only for what is left** — a law name that is not a library title, or
   «من النظام» with several laws named before it. It resolves each such name with `find_regulation`
   (≤3 rounds); fetching the article by number is code. In Phase 0, 128 of 217 citing files needed no
   model call at all.
4. Confidence, by code:
   - **high** — the law is resolved by exact title, the article number exists, **and** the link is
     checkable by the scan itself: the law is named in the same phrase, or the document names exactly
     one law, or it is a back-reference whose nearest earlier law name is unambiguous and the model
     agrees;
   - **medium** — the link rests on the model's reading alone (the law is named elsewhere and the
     document names several);
   - `not_in_library` (C17) · `whole_law` (C18, nothing pinned) · `unresolved`.
5. Store one row per citing mention in `attachment_citations` and the scan row (§5). Only **high** rows
   are pinned. Medium rows are stored but neither delivered nor shown (Q3).
6. Add the high pins to the card of the conversation the message belongs to (§4.5). Citations are
   stored per **file** (§5), and a file can be attached in more than one conversation: when a file that
   already has pins is used in another conversation, that conversation's card is topped up through the
   same merge, which never duplicates an entry.

The run opens its ledger scope the way the frame does (`agents/helpers/_runtime.py:180-202`), so its
model use counts toward the user's usage like every background agent. No search fee.

### 4.7 Delivery under the segment

**The link is the mention's character position**, not a stored segment id: a mention belongs to the
segment whose `char_start ≤ position < char_end`. This survives a re-map, needs no column on the
segment table (C20), and works whichever of the two helpers finishes first.

> Note for the owner: in the reflect I said "by paragraph number". The plan links by **character
> position** and also stores the paragraph number. Reason found while planning: paragraph numbering
> depends on the document type (500 or 1,200 characters per paragraph), and the map does not store the
> type — if the type is corrected later, paragraph numbers shift, character positions do not.

Changes:

- `wait_and_load_sections` waits for `citation_pins` together with the segmenter (one combined wait,
  not two in a row), then loads the item's pinned rows and batch-reads the official text from
  `articles_v2`. The text is read at delivery time, so it is always the library's current wording.
- `Section` gains its character range and a `pins` list (`attachment_delivery.py:50-63`,
  `sections_from_rows` `:272-292`). `_encode_sections` (`attachment_sections.py:29-51`) rebuilds
  sections field by field — it must carry `pins` through **without encoding** (official text is public;
  encoding would swap digits inside the law).
- `render_sections` (`:202-207`): after a kept section's body, one block per distinct article cited in
  that section — a tag line naming the article and its law, then the text. **In full under every
  citing section** (C22); once per section even if cited several times in it.
- A document with no segment map (short, or the segmenter failed): the articles are printed once after
  the document's text, in both the deep-search fallback (`case_documents.py:160-197, 382-392`) and
  `writer_bodies` (`:247-262`).
- Size (C23): each printed article is cut at **10,000** characters with a note (99% of articles are
  under about 5,000; the longest is 244,419). Pinned text does **not** count toward the 100k / 300k caps, like tags and
  summaries today. A safety net caps pinned text at 30,000 characters per document; past it every
  pinned article in that document keeps the same share.
- A section that is not delivered (boilerplate, repeat, or outside the recipient's values) brings no
  pinned text. 1,956 of 2,006 mentions sit in core sections.

Reach: the writer gets pins for every attachment type; deep search for the types in
`CASE_DOCUMENT_TYPES` only (contracts are in that list but are never scanned).

### 4.8 Cost, ledger, fees

- Ledger: `agent="completion_search.run"`, `agent_family` = the caller's family for planner calls
  (`llm_calls.agent_family` is free text). These rows never match the deep-search free-use counter,
  which is keyed on `deep_search.sector_picker` / `deep_search.search_fee.*`
  (`shared/db/migrations/197_deep_search_gate.sql:168-173`).
- `record_search_fee` is never called by the helper or its tools (C13). A test asserts it.
- Attachment flow: one model run per non-contract attachment that cites something — about 209 of the
  661 attachments so far.
- New latency on the planner: `fetch_article` is instant; the helper is a model run. Phase 1 reports
  seconds per request before any planner is switched.

### 4.9 Telemetry

`completion_search.run` stage span (via `run_tracked`): caller, searches, rounds used, counts per
status and confidence, duration. `helper.run` / `helper.wait` come from the frame for `citation_pins`.
Scan counts (mentions, headings, laws, back-references) on the helper span. Never any attachment text.

**Dependency:** Logfire has been dark since about 2026-11-15 (the backend's write token is invalid).
Live checks in Phase 7 need that fixed first, or they fall back to `llm_calls` and the new tables.

### 4.10 Flags

Same pattern as `agents/utils/segmenter_flags.py:18-37` (flag on, and the allowlist empty or holding
the user). Settings are cached, so a change needs a redeploy.

| Flag | Off means |
|---|---|
| `COMPLETION_SEARCH_ENABLED` / `_USERS` | the search planner keeps `fetch_article`; the writer planner has no lookup tool |
| `ATTACHMENT_CITATION_PINS_ENABLED` / `_USERS` | no scan, no rows |
| `ATTACHMENT_PIN_DELIVERY_ENABLED` / `_USERS` | rows exist but nothing is printed under segments |

Rollback of any part = its flag off. The card kind itself ships without a flag (nothing creates it
while the flags are off).

---

## 5. Database

Numbers: master took 227 on 2026-11-22 and the angle-map worktree holds 228, so this plan uses
**229, 230, 231** (written as 227–229 below until the files exist) — **re-check on the day**. Prod is
checked live before writing.

**227 (→ 229) — enum value.** `ALTER TYPE workspace_item_kind ADD VALUE IF NOT EXISTS 'pinned_texts';` in its
own file with no transaction block (a new enum value cannot be used in the transaction that adds it).
An enum value cannot be dropped later; unused, it is harmless.

**228 — the card.**
- Unique partial index: one live `pinned_texts` item per conversation.
- `upsert_pinned_texts_card(p_user_id, p_conversation_id, p_entries jsonb, p_content_md text)` —
  locks the row, merges entries by their key, writes; service role only.
- The 15-item cap function leaves the new kind out.

**229 — attachment citations.**

```
attachment_citation_scans          one row per scanned attachment
  item_id PK → user_files(file_id)   (ON DELETE CASCADE) — the same key the segment maps use
  user_id → users
  scan_rule_version, paragraph_rule_version, doc_type, content_chars
  mentions, headings, pinned, medium, not_in_library   (counts)
  model, created_at

attachment_citations               one row per citing mention
  item_id → attachment_citation_scans (ON DELETE CASCADE), seq          PK (item_id, seq)
  char_start, char_end, para_n
  sentence_start, sentence_end          positions only — no copy of the attachment's text
  law_as_written, article_number, basis
  regulation_id, article_id             null unless resolved
  status   pinned | medium | not_in_library | whole_law | unresolved
  index (item_id) where status = 'pinned'
```

Both tables: RLS on, a select-own policy, `REVOKE ALL … FROM anon, authenticated`, service-role
grants, `lock_timeout`, idempotent, a self-test block — the pattern of
`216_helper_runs.sql:91-161` and `215_attachment_segments.sql:141-172`. Deleting the file removes its
rows through the cascade.

**Key target — the file.** Since migration 223 (applied to prod 2026-11-22, checked live that day)
`attachment_segment_maps.item_id` and `helper_runs.item_id` point to `user_files(file_id)`: a file
outlives its conversation items and can be attached in several conversations. The new tables use the
same key and the same column name. Removing a conversation's pointer does not remove the file's
citations; deleting the file does.

**SQL function for Mode B** (in 229 or its own file): `find_in_regulation(p_regulation_id, p_phrase,
p_limit)` → article number, article id, a short excerpt around the hit; matches with
`luna_normalize_ar` on both sides (strips تشكيل, folds hamza). No new index: the per-law lookup is
indexed (`idx_articles_regulation`) and the largest law takes 55 ms.

---

## 6. File manifest

**New**

| File | What |
|---|---|
| `agents/completion_search/{__init__,agent,deps,models,prompts,lookups,confidence,runner}.py` | the helper |
| `agents/completion_search/tests/…` | lookups, rounds, confidence, no-fee, no-similarity |
| `agents/tool_repository/completion_search_tool.py` | `complete_search`, `keep_results` |
| `agents/tool_repository/citation_scan.py` + tests | the model-free scan |
| `agents/memory/citation_pins/{__init__,runner,store}.py` + tests | the attachment run and its tables |
| `agents/utils/citation_pin_flags.py` | the three flags |
| `shared/workspace/pinned_texts.py` | entry model, renderer, card upsert |
| `shared/db/migrations/227…229` | §5 |
| `scripts/citation_scan_dryrun.py` | Phase 0 counts over real attachments (numbers only) |

**Changed**

| File | Change |
|---|---|
| `agents/utils/agent_models.py` | slot `completion_search` |
| `shared/config.py` | six settings |
| `agents/deep_search_v4/planner/{agent,prompts,deps,runner,models}.py` | tool swap behind the flag, prompt section, turn pins, flush on resume |
| `agents/deep_search_v4/orchestrator.py` | build `statute_articles` from the turn's pins; carry pins across the resume |
| `agents/orchestrator.py` | cap exemption; real workspace event; resume deps |
| `agents/deep_search_v4/{reg_compliance_search,case_search}/prompts.py` | align the stale lines about the block |
| `agents/writer_planner/{agent,prompts,deps,walkers}.py` | tool, prompt passage, writer block |
| `agents/helpers/registry.py` (+ `tests/test_registry.py`) | `CITATION_PINS` spec |
| `agents/utils/attachment_sections.py` | combined wait, load pins, pass `pins` through the encoder |
| `agents/utils/attachment_delivery.py` | `Section.pins`, rendering, size rules |
| `agents/deep_search_v4/shared/case_documents.py` | pins in the no-map fallback |
| `agents/router/router.py:840`, `agents/utils/router_context_render.py` | the new kind, titles only |
| `agents/memory/summarize.py` | skip the LLM summary for the card |
| `agents/memory/item_analyzer/runner.py`, `agents/artifact_editor/agent.py:49` | know the kind; card not editable |
| `backend/app/services/workspace_service.py:76`, `shared/workspace/preview_lock.py:72` | read-only, lock rules |
| `frontend/lib/contracts/agents.ts:28` and `WorkspaceCard.tsx`, `WorkspaceList.tsx`, `WorkspacePane.tsx`, `WorkspaceItemViewer.tsx`, `MessageBubble.tsx` | label, icon, read-only viewer |
| `mobile/…/workspace-model.ts`, `WorkspaceCard.tsx`, `WorkspaceItemScreen.tsx`, `run-send.ts` (+ test) | the same on mobile |
| `.github/workflows/ci.yml:68-111`, `.gitignore:84-102` | add the new test folders (CI runs a whitelist; `tests/` is ignored by default) |

**Not touched:** `agents/simple_search/**` (C11), `agents/simple_search/manual_search.py` in
particular; the segmenter and its tables; `fetch_article.py` stays as a library and as the flag-off
path.

---

## 7. Phases

Each phase ends with tests green and nothing switched on for users.

| Phase | Work | Done when |
|---|---|---|
| 0 — Measure **(DONE 2026-11-22)** | `scan` prototype + dry-run script over the attachments, counts only: mentions, headings, how many links are checkable (the high / medium split of §4.6.3) | a one-page number report for the owner; fixtures written (synthetic text, never user text) — see §7.1 |
| 1 — Helper, group 1 **(DONE 2026-11-22, §7.2)** | package, slot, tools for regulation / article / word-in-law / circular / service, rounds, confidence, output | tests green; an evaluation over ~30 requests (popular and little-known laws, words-as-numbers, definitions) with accuracy and seconds for both flash settings |
| 2 — Helper, group 2 **(DONE 2026-11-22, §7.2)** | `find_case`, `find_principle`, the principle renderer, rulings opened without the unlock gate (C24) | tests green; duplicate case numbers and placeholders covered |
| 3 — Card **(BUILT 2026-11-23, §7.4)** | migrations 229–230, `pinned_texts.py`, backend, web, mobile contract | a card can be created and merged by a test; old clients ignore it |
| 4 — Planners **(BUILT 2026-11-23, §7.4)** | tool on both planners behind the flag, prompts, forwarding, resume fix, event, writer block | the full prompt of each planner re-read line by line; deep search and writer runs on the owner's account with the flag on |
| 5 — Attachment flow **(BUILT 2026-11-22, §7.3)** | scan final, migration 231, helper spec, run, card entries | rows written for a set of test attachments; contracts skipped; zero-mention files make no model call |
| 6 — Delivery **(BUILT 2026-11-22, §7.3)** | pins under segments, no-map fallback, size rules, masking pass-through | rendered output checked for a claim, a judgment, a short file and a masked conversation |
| 7 — Rollout | allowlist (owner) → review → everyone, one flag at a time | owner review of real conversations; then the aggregator question (C10) |

Deploy notes: any push to master kills searches in flight, so deploy in a quiet window; migrations
before the code that needs them; web before the first flag (so the card has its label).

### 7.7 The agent step for attachments (2026-11-23, owner decision C26)

The owner rejected the code-only attachment flow of §7.3: "the attachment should use a model" and
"the agent should be the responsible for fetching". Built the same day.

**How one attachment is handled now**

1. Code scans the text and finds every citation (C14, unchanged).
2. Code decides each citation it can **prove** — the law named in the same phrase with an exact
   library title, in force, the article exists. No model. (54% of all citations.)
3. Everything else that reading can settle goes to the **Completion Search agent**: the passage
   around the citation (400 characters, the citation marked), the law named in the phrase if any,
   the laws named before it, and the laws the document names. The agent decides which law the
   article belongs to and **fetches it itself** with its `get_article` tool. It is the same agent
   the planners use, with a message written for attachments.
4. Code then checks three facts before anything is delivered: the article number is the one the
   document wrote, the article exists in that law, the law is in force.
5. A file whose citations code proved calls no model at all.

**Sent to the agent:** a law named elsewhere in the document (the link is a guess) · «من النظام»
after several laws · a name that is not a library title (short, misspelt, running on) · a name the
library does not hold (three tries, then "not in our data", C17) · «2/77» numbers.
**Never sent:** a clause of a contract («من العقد») · a range too wide · a number written twice
that disagrees · a law not in force · a missing article of a law the document itself named.

**What the agent is told to refuse:** a clause of a contract, an agreement, bylaws or any document
that is not a law (answer «ليست مادة نظام») · a law the text does not point to clearly («النظام غير
محدد») · and it must compare the opening of the fetched article with what the passage says about
it, and drop the fetch when they have nothing in common.

**Limits and failure:** at most 40 searches per file (10 per request, 3 requests at a time, 60 s
per request tried twice, 200 s for the whole step). A request that fails keeps code's decisions —
no pin is ever lost. The text is masked with the user's codec before the model reads it (وضع
السرية), like the segmenter. The agent's model use lands on the user's ledger, like every
background agent; no search fee (C13).

**Files:** `agents/memory/citation_pins/agent_step.py` (new) · `runner.py` · `store.py`
(`reg_status`, the switch in `load_pins`) · `agents/completion_search/prompts.py`
(`ATTACHMENT_TEMPLATE`) · `agents/helpers/registry.py` (ceiling 300 s, own ledger scope) ·
`shared/config.py` + `agents/utils/citation_pin_flags.py` (`ATTACHMENT_CITATION_AGENT_ENABLED`) ·
migration 231 (3 counters on the scan row; still not applied) · `scripts/citation_pins_preview.py`
(`--agent`). Stored on every citation: `decided_by` = `code` or `model`, and for the agent a reason
(`agent` · `agent_confirmed_guess` · `agent_reworded_name` · `agent_named_another_instrument`).

**A fourth switch, on by default:** `ATTACHMENT_CITATION_AGENT_ENABLED`. Off = exactly the
code-only flow: the agent is not called and what it pinned before is not delivered. It is the
emergency setting if the agent's fetches prove wrong in production.

**Live test on the three real files of the test accounts** (5 decisions by the agent, all correct
after one change to its instructions):

| File | What code left open | What the agent did |
|---|---|---|
| Judgment (7,001 chars) | «في المادة التاسعة» ×2, tied by guess to نظام المرافعات الشرعية | refused both: «ليست مادة نظام — بند في عقد الإيجار ذاته» |
| Hearing record | «المادة التاسعة بعد المائة من نظام اثبات» | fetched article 109 of نظام الإثبات — delivered |
| Short judgment | «المادة (الثانية والعشرون) من النظام» after several laws | fetched article 22 of نظام الإيجار التمويلي — delivered |
| Short judgment | «قواعد عمل اللجان التمويلية … نصت الفقرة (الأولى) من المادة (الثانية)» | searched for those rules, not in our library — nothing delivered |

**The first run got the last row wrong:** it attached article 2 of نظام الإيجار التمويلي, the law
mentioned in passing in the same sentence — the same mistake code's guess makes. Two rules were
added to its instructions (the article follows the instrument the sentence is about, even when
that instrument is not in the list of laws; compare the fetched article's opening with the
passage). After that: correct in 4 runs of 4. This is the error to watch in production.

**Time:** the agent step took 20–41 seconds per file here (1–2 searches). It runs in the
background beside the segmenter, which takes longer.

**Workload, all non-contract attachments today (counts only, no model called):** 625 files, 225
cite an article, 2,324 citations. Code pins 1,253 (54%). **148 files (24% of all files) would call
the agent**, for 830 citations in 807 searches and 179 requests; the middle file sends 3 searches,
one file in ten sends 12 or more, 20 citations fall over the 40-per-file cap. Of the 830: 304 are a
guessed link, 287 a law name that is not a library title, 67 «من النظام» after several laws, 53
«2/77» numbers, 47 a missing article under a guessed law, 72 name forms.

**Not measured:** how many of the 830 the agent would deliver, and how often it would be wrong,
beyond the 5 decisions above. That needs the agent run on real customers' files, which was not
done without the owner's word.

**Tests:** 40 for the agent step, 4 more for the run. Full CI list, three runs: 4,821 passed and 0
failed on the third; the first two each had ONE failure, a different timing test each time
(`agents/helpers/tests/test_status.py::test_a_live_lease_elsewhere_is_polled_until_it_lands`, then
`agents/tests/test_conversation_memory_router.py::test_the_router_runs_beside_the_run…[dispatch]`),
neither in code this plan touches, and each passes 3 of 3 alone.

### 7.6 Ship readiness check (2026-11-23, owner: "are we ready to ship?")

**The build now sits on current master (`8aa2506`).** Master had moved 3 commits since the
build started (trial lengths, OCR text layer, angle map). Two files conflicted — the helper
registry and the settings file — both "two features added at the same spot"; both are kept. The
helpers now run in this order: ocr, summary, pre_router_memory, memory, segmenter, angle_map,
citation_pins. Still uncommitted, in the worktree only.

| Check on the merged build | Result |
|---|---|
| Python test list of the CI file (48 entries, master's angle-map tests included) | 4,777 passed, 0 failed |
| Web type check | clean |
| Mobile type check and unit tests | clean, 267 of 267 |
| Live database | nothing of this plan applied: no `pinned_texts` value, no citation tables, no card index |
| Migration numbers 229–231 | free on master (228, then 232 and 232b) |

**Cannot be proven before a deploy:** the card being written on the real database (needs
migration 229), and one whole search turn and one whole writing turn with the flags on.

**Who can use it:** nobody. The three flags default to off and no variable is set. Each flag has a
`_USERS` list of user ids; a flag that is ON with an EMPTY list is on for EVERYONE, so the list is
set in the same step as the flag. First user: the owner's test account (the one the angle map is
limited to).

**Order to ship:** commit → migrations 229, 230, 231 (229 alone) → deploy with the flags off →
the three flags with the `_USERS` list in one variable change (one restart) → one search turn and
one writing turn on the test account → widen.

### 7.5 Test on real attachments (2026-11-23, owner: "test it on real attachment")

Tool: `python scripts/citation_pins_preview.py --file <file_id> [--out DIR] [--show]`. It runs the
real scan, library facts, decisions, delivery under the stored segments and the card text for ONE
file, and stores nothing (no rows, no card, no model, no flag read). Run on three files of the
owner's own test accounts; outputs in
`C:\Programming\LUNA_AI_test_results\completion_search\real_attachment\`.

| File | Before the fixes below | After |
|---|---|---|
| Judgment, 7,001 chars, 9 segments | 9 citations, **0 pinned** | 7 citations: **5 pinned** (4 distinct articles), 2 medium; 2 quoted contract clauses left out |
| Short judgment, 1,556 chars, no segments | — | 3 citations: 1 pinned, 1 medium, 1 unresolved; the article follows the document's text once |
| Hearing record, 4,997 chars, no segments | 1 citation, "not in the library" | 1 citation, medium |

On the judgment, deep search would read 10,654 characters instead of 6,777 (+3,877 of official
text in 5 blocks); what counts toward the 100k / 300k caps is unchanged (6,135). Each article sits
right under the segment that cites it; article 57 is cited in two segments and printed under both
(C22). The card lists each article once with the judgment's own sentence beside it.

**What the real files showed, and what was changed (scan rule `cs-scan-v2`):**

1. **Courts write the number twice** — «المادة (31) الحادية والثلاثين من نظام المرافعات الشرعية». The
   words broke the link to the law, so every such citation fell to "medium". The words are now part
   of the mention. When the two writings DISAGREE («(37) السادسة والثلاثين») the mention is not
   trusted at all (`number_written_twice_disagrees`) — a cheap catch for a digit misread by a scan.
   No such disagreement exists in the current files.
2. **A judgment quotes the contract's clauses** — «(المادة الخامسة: التزامات المستأجر …)». A mention
   followed by a colon is somebody's own clause wherever it sits on the line, never a law citation.
3. **«المادة (57/2)»** in a law whose articles are plainly numbered is article 57, paragraph 2: it is
   now pinned when the article comes first and is the larger number. «2/57» stays a guess.
4. **«نظام اثبات»** (no «ال») was labelled "not in the library". A name that differs from exactly one
   title only by «ال» is now a medium guess (`name_differs_by_al`).
5. The short judgment showed why "the same sentence names one law" must stay medium: «قواعد عمل
   اللجان التمويلية … نصت الفقرة (الأولى) من المادة (الثانية)» sits in a sentence whose only
   recognised law is «نظام الإيجار التمويلي» — pinning it would have printed the wrong text.

**All non-contract attachments again, production path, rule v2** (620 files now, 224 citing,
2,297 citations): pinned **1,241 (54%)** · medium 378 · not in the library 287 · unresolved 218 ·
belongs to something else 173. Before these fixes: 1,161 pinned of 2,352.

### 7.4 Phases 3–4 — the card and the two planners (2026-11-23)

Built in the build copy. Nothing is deployed, no migration is applied, every flag is off.

**The card (Phase 3).** `shared/workspace/pinned_texts.py` + migrations 229 (enum value) and 230
(one live card per conversation — a unique index only). What the survey of the code showed, and
what was built on it:

- The new kind needs **no change** for the 15-item cap or for edit protection: the cap (SQL trigger
  and `_CAP_KINDS`) counts three other kinds, and the API allows edits for `note` / `agent_writing`
  only.
- The merge is done in code with a compare-and-set on `updated_at`, retried; two writers that both
  find no card are settled by the 230 index. No SQL function (the one in §5 is dropped).
- A trigger wipes `summary` whenever `content_md` changes, and a card with no summary is handed to
  the LLM summariser. So the card is born with its summary and the summary is written again, as a
  second statement, after every merge.
- Card events use the three shapes the chat already relays, so the card appears under the reply as
  the one green «افتح …» button: created → `workspace_item_created`, added to →
  `workspace_item_updated`, already complete → `referenced_existing_item`. They go on
  `deps._events` — the live `emit_sse` sink drops everything that is not a progress line, which is
  why the old card's event never arrived (§1.5.2).
- Web: the kind in `contracts/agents.ts`, label / icon / colour, a read-only viewer, and no
  auto-open of the pane (the report opens there). `tsc` passes. Mobile: the same maps and viewer;
  its workspace-model tests pass. The mobile reply shows no button for the card (its button lists
  agent outputs only) — the card is in the list.
- **Not built:** the library link on a ruling / circular / service entry (the site's link builder
  lives in the backend services; the entry carries the ids for it).

**The planners (Phase 4).** `agents/tool_repository/completion_search_tool.py`:
`complete_search(request)` and `keep_results(result_ids)`.

- Search planner: the tool replaces `fetch_article` and the prompt section is swapped **with** it
  (`decider_system_prompt(True)`); if the swap cannot be built the decider keeps the old tool and
  the old prompt. Flag off = the same prompt object and the same three tools as today.
- Writer planner: the two tools plus one passage after its tools table. The texts it pins reach the
  writer as ONE extra item inside `<sources>` (kind `pinned_texts`), because the writer's static
  rule is "cite only what appears in `<references>` or `<sources>`" — a new block would have been
  uncitable. Only an attachment is an exhibit, so the item is never numbered as one.
- `statute_articles` is built from the turn's pins; an article keeps the `## نص المادة …` heading
  the search agents' prompts name. The expander and aggregator prompts are **not** edited.
- **After a pause.** Every pinned block the planner reads carries a tag (`[P-1a2b3c4d]`). The tags
  stay in the run's message history, so the turn's pins are rebuilt from the history and the card
  on fresh deps — before the pause and after it. This closes §1.5.1 for both planners.
- Masking: the helper's model is given the request as it is; the real case number exists only
  inside the lookup (`MaskedLibrary`), what comes back about that ruling is encoded again, and the
  card stores the real text.
- One wording change that applies with the flag off too: the `planner_brief` field description
  said "articles fetched with fetch_article"; it now says "official texts fetched with your lookup
  tool" so it is true in both modes.

**What the live check with the real planner model changed** (three questions, read-only, no card):

1. The planner completed a law name on its own («نظام المرافعات» → «نظام المرافعات الشرعية») and the
   result was pinned as high. Now a law name counts as "written by the user" only when it stands in
   what the planner itself was given (the messages, the documents, the earlier work); a name the
   planner completed comes back **medium** and must be accepted with `keep_results`.
2. The planner rewrote «234» as «مائتان وأربعة وثلاثون» and the lookup could not read it. The
   lookup now reads counted number words, and the prompt says digits stay digits.
3. The planner added a topic and a research question to the request. The prompt now says: name the
   texts and nothing else; the helper is told to ignore a topic added to a request.

After these: the three questions behave as designed, and the helper's evaluation is 38 of 38
(8 answered by code in about 1.3 s, 30 by the model in 4.6 s on average, 14.1 s at worst).

Tests: the whole CI list run locally on 2026-11-23 — **3,949 passed, 0 failed**
(`C:\Programming\LUNA_AI_test_results\completion_search\ci_run2.txt`); the new suites are added to
`.github/workflows/ci.yml` and re-included in `.gitignore`.

### 7.3 Phases 5–6 — the attachment flow, built without a model (2026-11-22)

Built in the build copy with 69 tests; nothing runs until migration 231 is applied and
`ATTACHMENT_CITATION_PINS_ENABLED` is on; nothing is printed until
`ATTACHMENT_PIN_DELIVERY_ENABLED` is on.

- `agents/memory/citation_pins/{resolve,store,runner}.py`, the `citation_pins` helper in
  `agents/helpers/registry.py` (beside the segmenter, ceiling 60 s, no ledger scope),
  `shared/db/migrations/231_attachment_citations.sql`.
- Delivery: `attach_pins` / `render_loose_pins` in `agents/utils/attachment_delivery.py`, one
  combined wait in `agents/utils/attachment_sections.py`, the no-map case in the writer and in
  deep search. With no pins the delivered text is byte-identical to today (the 332 existing
  segmentation and helper-frame tests pass unchanged).

> **Reversed by the owner on 2026-11-23 (C26) — the agent step is back, see §7.7.** What follows
> is kept as the record of the code-only build.

**Change against §4.6.3 — the owner should confirm it: the attachment flow calls no model.**
Phase 0 showed that everything the plan allows to be *pinned* is decidable by code (exact library
title, law in force, article exists, link checkable). What a model could add — a law named
elsewhere in the document, a shortened name — is by the plan's own rule *medium*, and medium is
stored but never delivered (§8 question 3). So in this build:

- every citation is decided by code and gets one of: `pinned` · `medium` · `not_in_library` ·
  `other_target` · `unresolved` (+ a reason);
- a short name that opens exactly one library title («نظام التأمينات») is `medium` by code;
- no attachment text is sent to any model, nothing is charged, and the run takes seconds.

If the owner later wants medium guesses delivered or shown, the helper agent is added as a step on
the rows that are `unresolved` / `medium` today; nothing else changes.

The marker over a pinned article says where it comes from, so no agent prompt needs a new rule:
`〔من المكتبة النظامية، لا من المستند — المادة 77 من نظام العمل〕`. A document with no segments
gets its articles once after its text under
`〔نصوص نظامية من المكتبة أشار إليها هذا المستند — ليست جزءًا منه〕`.

### 7.2 Phases 1–2 results (2026-11-22)

Built in the build copy, 204 tests green (105 scan + 99 helper), nothing wired to any agent yet.
Evaluation against the live library, read-only, charged to no one
(`python scripts/completion_search_eval.py`; raw output in
`C:\Programming\LUNA_AI_test_results\completion_search\phase1\`):

| Setting | Right | Answered by code | Model answers: average / slowest |
|---|---|---|---|
| `_FLASH_MEDIUM` (**chosen**) | 31 of 31, then 7 of 7 on rulings and principles | 8 requests, about 2.3 s each from this PC | 7.3 s / 16.1 s |
| `_FLASH` | 30 of 31 (missed one phrase search after 3 attempts) | the same | 6.5 s / 14.2 s |

What changed against §4.2 while building, and why:

1. **Code answers the plain request with no model.** «نظام المعاملات المدنية المادة 56 والمادة 234»
   — one library title plus article numbers and nothing else — is read by the citation scan and
   answered straight from the library. The search planner's commonest request stays as fast as
   `fetch_article`; the model runs only when there is something to work out.
2. **Tools are one call per attempt**: `get_article(search_id, regulation, number)` resolves the law
   and fetches the article in one step (`regulation` is a name or an `L…` handle). This is what makes
   "3 rounds" countable by code.
3. **Mode B needs no SQL function.** The law's articles are read once and matched in code with
   tashkeel ignored; the `find_in_regulation` SQL function in §5 is dropped. The law's `definitions`
   column is not used: it is a derived field, not the official wording (C18).
4. **"As the request wrote it" is a checked fact.** A law name the model reworded, or picked from a
   list, can never be high — code compares the name with the request's own text.
5. **Short and long names**: a name that opens exactly one library title («نظام التأمينات») or is
   opened by one («نظام العمل السعودي») is a best guess (medium); a name that opens several
   («نظام المرافعات») lists them and picks none.
6. **A draft or cancelled text is never high**, and a law whose article texts are not in the library
   says so instead of "article not found".
7. Rulings: several that share a number are listed with court and date and no text, at **medium**
   (as §4.3 says); the court or the year narrows them. The ruling is opened through a granted access
   object built in the helper — a test proves the library's unlock is never called (C24).

Not done in these phases: nothing is registered on a planner, no migration exists yet, no flag.

### 7.1 Phase 0 results (2026-11-22)

Full report: `agents_reports/completion_search/phase0_scan_numbers.md`. Read-only run over all 704
attachments with text (98 contracts skipped), counts only.

- 217 of 606 non-contract attachments cite an article: 2,093 places, 2,309 citations; 123 own
  headings left out.
- **1,156 citations (50%) are pinned by code alone**; 469 (20%) are medium (the link to the law is a
  guess); 339 (15%) carry a law name that is not a library title (the helper resolves it, or it is
  not in our data); 171 (7%) belong to something else («من العقد»).
- 128 of the 217 citing files need **no model call**; 89 need the helper for 412 citations.
- **The segment link holds:** all 1,128 pinned citations in segmented files land inside exactly one
  segment by character position; none in a gap. 16 files with pins have no segments.
- Pinned text per file, printed under every citing segment: middle 1,352 characters, largest 27,411;
  no file passes the 30,000 net; the longest pinned article is 3,681 characters.
- The scan took 7.9 seconds for all files.

**Tests that must exist:** the helper never calls `record_search_fee`; no lookup reaches an embedding or
similarity call; a 4th round is refused; the model's output cannot carry legal text; a contract is
never scanned; a heading is never resolved; a medium row is never rendered; pinned text passes the
encoder unchanged; the caps are unchanged with pins present; one card per conversation under two
writers at once; a request with a masked case number is decoded before lookup; opening a ruling through
the helper never calls the library's unlock.

---

## 8. Open questions for the owner

Each has the default this plan assumes. None blocks Phase 0–2. Answered on 2026-11-22 and moved to §3:
the size of pinned text (C23), rulings (C24), the card's name (C25).

1. **What the card shows for rulings, circulars and services:** title, a short lead and the library
   link — not the full text (the agents still get the full text). With no unlock on rulings (C24), a
   full ruling on the card would let a user copy rulings without limit, so this default matters.
   *Default: yes.*
2. **Searches per request:** at most 10. *Default: 10.*
3. **Attachment confidence:** ~~only links the scan can check are pinned; a link that rests on the
   model's reading alone is stored as medium and not delivered.~~ **Answered 2026-11-23 (C26):** what
   the agent fetches is delivered once code confirms the law is in the library, in force, and has the
   article.
4. **Old data:** the 203 existing cards stay as notes; old attachments get their pins the first time
   they are next used, with no bulk run. *Default: as written.*
5. **Two versions of one law:** the helper returns the in-force one and says a second exists; a
   consultation draft is returned only when nothing in force matches. *Default: as written.*

---

## 9. Risks

| Risk | Handling |
|---|---|
| An article of the **wrong law** pinned under a segment | high needs a checkable link (§4.6.3); medium is never delivered; Phase 0 measures the split |
| A scanned file misreads a digit (the known ٢ / ٣ swap), so the wrong article is pinned | cannot be detected by code; the card shows the attachment's own sentence beside each article so the lawyer sees a mismatch |
| The document's own headings taken as citations in types other than contracts | the line-start rule; uploaded law texts and notes are in the Phase 0 report |
| Pinned text swells what agents read | 10,000 per article, 30,000 per document, once per section; measured in Phase 6 |
| Rulings open with no unlock (C24) | a ruling is returned only for an exact case number the caller already has; the card shows title and link, not the text (Q1) |
| The planner becomes slower (a model run where a lookup used to be instant) | Phase 1 reports seconds; time limit on the tool; flag off returns `fetch_article` |
| The helper invents or rewords a law name | handles only; code opens the text; candidates come only from lookups |
| Masking turns a case number in a request into a fake | decode before lookup; test |
| Two writers on one card | one locking SQL function; unique index |
| A file attached in several conversations | citations are stored per file (§5); each conversation's card is topped up through the same merge |
| An enum value cannot be removed | additive and unused when the flags are off |
| Logfire is dark | fix first, or verify from `llm_calls` and the new tables |
| Stale pins after an attachment's text changes | `load` compares `content_chars`; the run is redone |
| Shipped mobile builds do not know the new kind | they drop it without crashing; the card appears after the app update |
| `unfold`'s article lookup matches the raw number only (`unfold.py:1838-1864`) | the helper passes the normalised number; simple search itself is left alone |

---

## 10. Out of scope

- Mode C — finding a law through its sector (C6).
- The aggregator as a caller (C10).
- Case numbers, circulars and principles cited **inside attachments**.
- Citations inside contracts (C16).
- A bulk run over old attachments; converting old cards.
- Any change to simple search, to the segmenter, or to the library's index.
- Deleting `fetch_article.py`.
