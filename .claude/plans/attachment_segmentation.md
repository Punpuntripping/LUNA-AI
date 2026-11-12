# Attachment segmentation — plan v2

**Status:** BUILT 2026-11-11, uncommitted, all flags OFF — worktree `C:\Programming\LUNA_AI_segmentation`,
branch `feat/attachment-segmentation` from `origin/master` @ `29abb24`. Migrations 215 + 216 written,
NOT applied. Phase 0 done (`attachment_segmentation/phase0_results.md`). See **§11 Build log** for what
exists, what is left and the rollout order.

**Companion plan:** `.claude/plans/helper_frame.md` — the critical / not-critical helper frame the
segmenter runs in. Read it with this one; this file owns *what the segmenter makes and who gets what*,
that one owns *when helpers run and who waits for them*.

**Area:** the attachment flow — OCR (`agents/memory/ocr_extractor`), the attachment summary
(`agents/memory/artifact_summarizer`, run by `agents/memory/summarize.py` when `kind == "attachment"`),
and what the downstream agents read of an attachment. What the owner calls "the attachment analyzer"
is the summarizer's attachment flow — NOT `artifact_analyzer` (the follow-up verdict agent).

**Vocabulary:** the segmenter is **Layer 4 Memory** (system-side, never talks to the user), billed on
model **tier_2**. See CLAUDE.md "Layer vs Tier". "Helper", "consumer", "critical" — see helper_frame.md §2.

**Code base for line numbers:** `origin/master` @ `03fd65a` (2026-11-10). The main checkout is on
`feat/mobile-phase4b-iap` with unrelated uncommitted deep_search edits — build in a fresh worktree from
`origin/master`. Last migration on master: **212** → this work starts at **215** (verify prod first,
`project_migration_drift`).

**Experiment code:** `.claude/plans/attachment_segmentation/experiments/` (code only — see its README;
inputs/outputs contain customer documents and stay out of the repo).

---

## 0. In one paragraph

Long uploads reach the deep-search agents as their **first 12,000 characters**, and for the documents
users ask about most that is the least useful part (every long judgment has its grounds and ruling at
94–99% of the text). The plan: every attachment over **5,000 characters** is **segmented** once, by a
cheap model, into labelled sections rated metadata / core / supporting / boilerplate — **without
rewriting a word** — and stored, page by page and section by section, in new tables beside
`content_md`. Code then delivers by rating: the deep-search agents (both expanders, aggregator) get
the **core** sections, the writer gets **core + supporting**, in the original wording, under new caps
(100k per document, 300k per agent, proportional trimming). The segmenter is a **helper** in the new
helper frame: the router, both planners and the analyzer never wait for it; the expanders, the
aggregator and the writer wait up to 5 minutes, then fall back to the full text under the same caps. The
aggregator and writer get clear rules to **quote** the documents' own words when it matters.

---

## 1. The problem (verified 2026-11-09 on prod data + code)

- `agents/deep_search_v4/shared/case_documents.py`: `PER_DOC_FULL_TEXT_CAP = 12_000`,
  `TOTAL_FULL_TEXT_CAP = 30_000`; `build_case_document_blocks` cuts `content[:per_doc_cap]` (the start).
- Who sees what today: aggregator = every forwarded doc in full (capped); case / reg expanders = summary
  only unless the planner names the doc in `case_full_text_wis` / `reg_full_text_wis`
  (`planner/models.py:161`); the **writer** gets the whole `content_md`, uncapped
  (`agents/writer_planner/walkers.py:240`); the planner decider reads every attached item in full.
- 252 attachments since 2026-10-26, ~2.0M chars. 40 of the forwarded-type documents exceed 12k and
  get cut (judgment 14/34, contract 7, memo 5, claim 5, expert report 5, financial 2, letter 1,
  hearing minutes 1). Largest 80,984 (hearing minutes).
- **Where the value sits:** every judgment over 12k has its ruling / grounds markers at 94–99% of
  the text — past the cut. 14,157-char administrative judgments (picked 7 times by the planner):
  ruling at char 13,965.
- **Real user pain:** in one expert-report conversation the user asked what we meant by «تقرير
  الخبرة عُرض في البحث مقتطعاً…» — our own answer told them the report was cut, exactly where the
  ratios and stoppage dates they were disputing were.
- Planner review (61 deep-search turns with forwarded docs, 10-27 → 11-09): picks are mostly
  sensible; the problems are the cut itself, duplicate uploads spending the 30k twice, the 30k
  total dropping the user's own papers, and one wrong pick (a 62k contract named instead of the
  5.8k memo the user asked about).
- A deterministic cleaner saves only ~8% (images, table markup, repeated headers) — it moves the
  ruling inside 12k in 1 of 14 judgments. Not the fix.
- OCR joins pages with a blank line (`agents/memory/ocr_extractor/mistral_ocr.py:85`) — page breaks
  are lost, so nothing downstream knows where a page starts.
- No prompt asks the aggregator or the writer to quote the user's documents. The aggregator is told a
  long document "is cut at a marked point — never guess past it" (`aggregator/prompts.py:102`); the
  writer prompt only says to use the parties' names verbatim.

---

## 2. What we tested (2026-11-09 → 11-10) and what we learned

### 2.1 Option 1 vs option 3 (stronger model, 3 docs: judgment 49k, expert report 47k, contract file 41k)
- **Long per-type summary (option 1):** ~10k chars each, quotes verified verbatim, good at
  flagging contradictions and gaps — but written before any question, so it **drops details for
  good** (it never mentioned the rebar-relocation point the user later asked about).
- **Segmenting (option 3):** 100% coverage, 232/232 quote anchors exact; "core" = 21–33% of each doc
  (fits ~12k); found the judgment's value in two places (expert findings quoted by the court in the
  middle + grounds/ruling at the end), split the expert file into report + 7 attachments, and the
  contract file into 16 documents. → **Direction chosen: segmenting.**

### 2.2 Segmenter on the production model (DeepSeek V4 Flash 0731, Baseten, `artifact_summarizer` slot), 10 docs / 10 types
- **Works mechanically:** 10/10 valid maps, 0 gaps / overlaps (after v3: one run with 2 gaps, one
  with 2 overlapping paragraphs), $0.002–0.017 per doc (~$0.05 for all 10), 12–176 s.
- **Quote anchors are unreliable on Flash** (38/45, 60/92 exact) → **boundaries by paragraph number**,
  never by quoted text.
- **The `core` / `supporting` rating is unstable** run to run with the identical prompt: judgment
  core 39% → 93% → 92%, expert report 53% → 33% → 71%, financial 2% → 65% → 2%. Paragraph-level
  agreement between runs: value rating 40–78% of the text, section label 65–96%.
- **What is stable:** decision-bearing sections — the court's grounds and ruling, expert findings
  and conclusion, a memo's closing requests — are core in every run. What flips is the parties'
  arguments, procedural history and attachments. Cause: the only criterion for core is one line
  («a lawyer … needs it word for word»), and the model has to guess future questions.
  **This matters more now:** the owner chose to deliver core *as rated* (D11) — Phase 0 must narrow it.
- **Financial type notes fixed financial** (core = totals + status only; routine installment tables
  supporting; repeated headers boilerplate) — concrete criteria work.
- **`metadata` value**: first occurrence of dates, issuer / court, numbers, sender / addressee,
  subject, index, opening, parties. Captures the right things; fairly stable. Mistakes: a repeated
  warning marked metadata, an appealed ruling's operative part inside metadata, a signature as metadata.
- **Hearing minutes on the judgment label list** (+ `attendance`): real structure now; one mislabel —
  the prosecutor's evidence list labelled `grounds`.
- **Section descriptions (`what`) are what the owner reads** — they matter more than the codes.
- **Label instability has a definitional cause** in judgments / hearing minutes (a defendant's
  statement fits `defense` or `evidence`, …). Fix: the speaker rule (P2).
- **Repeats:** one hearing record holds the whole investigation section twice (97–100% identical);
  the model only sometimes notices. → detect in code (P5).

### 2.3 Split-parallel (2 agents per long doc), 3 longest docs
- Cut at a page start near the middle (found from the repeated letterhead); each half gets the
  shared first page + the attachment summary + a 12-paragraph read-only overlap; code stitches.
- **0/6 halves needed a retry vs 4/6 single runs** (the judgment took 117 s / 151 s single vs 65 s
  split). Cost about the same. Seams clean. Blind spot: the second half cannot see repeats of the
  first half. Owner: "we are ok with this for now".

### 2.4 Relationship map — Opus vs V4 Flash vs V4.1
Tested; V4.1 ≈ Opus on judgment / contract at ~4–5¢/doc. **Out of this release (D20)** — details and
numbers kept in §10.2 and in the experiment scripts.

### 2.5 Prompts as tested
- Segmenter v3: `experiments/run_flash_segmenter.py` — `PROMPT`, `LABELS` (10 types;
  hearing_minutes = judgment + attendance), `TYPE_NOTES["financial"]`, schema `SegmentMap`.
- Split addendum: `experiments/run_split.py` — `CHUNK_NOTE`.
- Relationship map: `experiments/run_flash_rel.py` — `PROMPT`, `TYPE_RULES`, schema `RelMap`.

---

## 3. Decisions

**Owner — 2026-11-09/10 (experiments):**
| # | Decision |
|---|---|
| D1 | Pursue **segmenting** (original wording, cut by code) as the way long attachments reach the agents. |
| D2 | **One shared prompt** with per-type label lists (not a prompt per type). |
| D3 | A **`metadata`** value for identifying information (dates, official data, sender / addressee, subject, indexes, opening, parties). |
| D4 | **Hearing minutes use the judgment label list** (+ `attendance`). |
| D5 | **Financial gets its own type notes** (text in `run_flash_segmenter.py` `TYPE_NOTES`). |
| D6 | **Split long documents across 2 agents** (shared first page + overlap) — "ok for now". |
| D7 | Section descriptions (`what`) are the part the owner cares about most. |
| D8 | Relationship map explored (V4.1 tested as the candidate) — now deferred, D20. |

**Owner — 2026-11-11 (/reflect):**
| # | Decision |
|---|---|
| D9 | **Segment every attachment over 5,000 characters**, whatever its `doc_type`. Under 5k nothing changes. |
| D10 | **Storage = new tables beside `content_md`** (which stays as it is): one row per OCR page, one row per section with its label, value, description, paragraph range and its original text (§4.5). |
| D11 | **Deep search gets the `core` sections as the model rated them** — no code re-ranking (proposal P1 rejected). Applies to the case expander, the reg expander and the aggregator. |
| D12 | **The writer gets `core` + `supporting`** of every attachment the writer planner selected. **No cap on the writer** (owner "no" to a 300k writer cap) — it stays uncapped as today. **Until the metadata agent exists the writer also gets the `metadata` sections** (owner, §6 Q1 "yes"); deep search keeps core only. |
| D13 | **Planners pick items only; the content follows automatically.** No per-section or per-cut choice for any planner. The deep-search planner keeps naming documents for the expanders (`case_full_text_wis` / `reg_full_text_wis`); the writer planner keeps `selected_wis`. |
| D14 | **Caps: 100k per document, 300k per agent** (case expander, reg expander, aggregator). Over a cap → every core section is cut **proportionally from its last lines** (§4.6.3). Worked example: 400k of core against 300k → each section keeps 75% (a **25%** cut, not 33% — 100k is a third of the cap but a quarter of the total). |
| D15 | **Helper frame** (helper_frame.md): helpers declare which agents they are **critical** for. The segmenter is critical for the case expander, the reg expander, the aggregator and the writer; **not** critical for the router, the deep-search planner, the writer planner and the analyzer — those run while it works. OCR and the summary move into the frame too ("move them all"); so does the memory agent — **not critical for the router, critical for all else**. |
| D16 | **Critical wait = up to 5 minutes.** Past it, or if the segmenter failed, the consumer gets the **full text within the caps — 100k per document, 300k overall**, trimmed the same way as core (owner follow-up 2026-11-11; replaces the first answer, a 50k cut). Applies to deep search and to the writer. The segmenter keeps running and its result is stored for later turns; if a redeploy kills it, the next consumer that needs it re-runs it. |
| D17 | **Aggregator quoting** — woven into the answer, the aggregator judges when: (1) a **critical finding** in a document **must** be quoted; (2) otherwise quote where it helps the user understand our result and **how it affects their case**; (3) always **تأصيل** — tie what happened in the document to the legal rule. The owner's sample sentences are illustrations only, never templates. |
| D18 | **Writer quoting** — depends on the document type, the user's instruction and whether the material calls for it (e.g. a statement worth answering). One general rule, **do not overfit**. |
| D19 | **Metadata agent — planned, NOT built** (§10.1): runs after segmentation, takes every section rated `metadata` from all of the conversation's documents, critical only for the writer. |
| D20 | **Out of this release:** the relationship map (P10), the doc_type fixes (P11), section names in the planners. |
| D21 | **Improve the plan first** — no repo code until the plan is agreed; Phase 0 (experiments) comes before Phase 1. |

**Proposals by Claude — status after 2026-11-11:**
| # | Proposal | Status |
|---|---|---|
| P1 | Delivery decided by code from labels + key paragraphs | **Rejected** (D11). Key paragraphs are still produced and stored, not used for delivery. |
| P2 | **Speaker rule** in judgment / hearing labels (`claim` = plaintiff/prosecutor incl. the prosecutor's evidence list; `defense` = defendant/lawyer; `evidence` = from outside the parties; `proceedings` = what the court did procedurally; `grounds` = the court's own reasoning only; `ruling` = the operative decision) | Phase 0 |
| P3 | **Concrete core criteria per type** (as done for financial) for judgment / hearing, expert report, memo / claim, contract, letter, official record | Phase 0 — now the main lever on what deep search receives (D11) |
| P4 | Boundaries as explicit ranges (`from_p` / `to_p`) | Phase 0 |
| P5 | **Repeat detection in code** (near-identical passages → later copy = `repeat`, never delivered) | Proposed, Phase 2 — not yet confirmed by the owner (§6 Q3) |
| P6 | Validator + repair; fallback | Validator/repair proposed (Phase 2); fallback now set by the owner (D16) |
| P7 | Split above ~30k chars, single call below | Proposed, Phase 2 (D6 confirms splitting; the threshold is Claude's) |
| P8 | Keep page breaks in OCR output | Becomes the `attachment_pages` table (D10) |
| P9 | Finer paragraphs for judgment-like types (~400–600 chars, sentence-aware) | Phase 0 |
| P10 | Relationship map on V4.1 | Deferred (D20) — §10.2 |
| P11 | doc_type fixes (`documents_in_file`, precedents / blank templates → `reference`, …) | Deferred (D20) — §10.3 |

---

## 4. Target design

### 4.1 Where the segmenter sits (summary — the frame itself is in helper_frame.md)

```
upload ─► OCR ─► summary (doc_type) ─► segmenter ············ critical for: case expander, reg expander,
                    │                    (> 5,000 chars)                       aggregator, writer
                    └─► router ─► deep-search planner / writer planner / analyzer   (never wait for it)
                                        └─► [wait ≤ 5 min] expanders ─► aggregator
                                        └─► [wait ≤ 5 min] writer
```

- Depends on the summary because `doc_type` picks the label list (D2) and the summary feeds split mode.
- Runs once per attachment; the result is stored (§4.5) and read by every later turn.
- Old attachments (uploaded before launch) have no map: the first critical consumer that needs one
  starts the segmenter (helper_frame.md "missing → run it now"), so old long documents are segmented
  on first use. They have no page rows (their page breaks are already gone) — the paragraphizer runs
  on `content_md` alone and page numbers stay empty.

### 4.2 OCR pages (part of D10)
- `mistral_ocr.py` already receives `response.pages`; keep them: the runner writes one
  `attachment_pages` row per page (page number, the page's markdown, its char offset in `content_md`)
  in addition to today's joined `content_md`. `content_md` stays exactly as built today so the ~80
  Python readers, the OCR viewer, the retention sweep and export are untouched.
- `ocr_pages` / `ocr_chars` in metadata unchanged.

### 4.3 Paragraphizer (deterministic, `agents/memory/attachment_segmenter/paragraphs.py`)
- Input: the page rows when present, else `content_md`. Output: ordered paragraphs `P001…` with char
  offsets in `content_md` and page number.
- Rule as tested: non-empty line = paragraph; split > 1,200 chars at a space. P9 lowers the max for
  judgment-like types (Phase 0 decides the number). Ids must be reproducible from `content_md` alone —
  store `paragraph_rule_version` on the map.
- Paragraph boundaries are what every later cut snaps to (segments, trimming, fallback).

### 4.4 Segmenter agent (`agents/memory/attachment_segmenter/`)
- Package layout as the other memory agents: `agent.py`, `deps.py`, `models.py`, `prompts.py`,
  `runner.py`, `tests/`. New model slot **`attachment_segmenter`** on the `_FLASH` policy (Baseten
  DeepSeek V4 Flash 0731, same as `artifact_summarizer`) — its own slot so it can move independently.
  Output cap = the shared `OUTPUT_TOKENS_CAP` (never a per-agent cap).
- Prompt = v3 (§2.5) + P2 + P3 + P4 (+ P9's paragraphing) — Phase 0 produces the final text. The
  English instructions + per-type labels + per-type notes live in `prompts.py` (edit the .py, never a
  .md copy).
- Output (`SegmentMap`): `documents_in_file[]` (`from_p`, `to_p`, `name_ar`); `segments[]` (`from_p`,
  `to_p`, `label`, `value` ∈ metadata/core/supporting/boilerplate, `what` ≤ 15 Arabic words);
  `key_paragraphs[]` (`p`, `why`). `start_quote` dropped.
- Input: the numbered paragraphs, `doc_type`, the attachment's title + summary. **Masked** (وضع
  السرية) exactly like the summarizer (`summarize.py::_summarize_codec` / `_enc` — the codec is built
  per user because the helper outlives the turn); `what` and `name_ar` are decoded before storing.
  The stored section **text is sliced from the real `content_md`**, never from the model.
- Above the split threshold (P7, ~30k): two calls, cut at a page start near the middle (page rows make
  this exact), shared first page + summary + 12-paragraph read-only overlap, code stitches (merge
  same-label segments across the seam, re-mark a repeated first-page header boilerplate, merge
  duplicate embedded docs) — logic in `experiments/run_split.py`.
- Costs into `llm_calls` through the tracking hook (`run_tracked`), like the summarizer; Logfire span
  `attachment_segmenter` (item, doc_type, chars, split, model, tokens, ms, outcome).

### 4.5 Storage (D10) — migration 215 (number to confirm against prod)

```
attachment_pages
  item_id      uuid  FK workspace_items(item_id) ON DELETE CASCADE
  user_id      uuid  (owner — RLS + purge)
  page_no      int
  content_md   text  (the page as OCR returned it)
  char_start   int   (offset of the page inside workspace_items.content_md)
  created_at   timestamptz
  PK (item_id, page_no)

attachment_segment_maps          -- one row per segmented attachment
  item_id                 uuid PK FK workspace_items ON DELETE CASCADE
  user_id                 uuid
  paragraph_rule_version  text
  prompt_version          text
  model                   text
  split                   boolean
  documents_in_file       jsonb
  key_paragraphs          jsonb
  content_chars           int     (len(content_md) the map was made from — stale-map check)
  created_at              timestamptz

attachment_segments              -- one row per section, document order
  item_id      uuid  FK attachment_segment_maps(item_id) ON DELETE CASCADE
  seq          int
  from_p, to_p int
  page_from, page_to int  (null for pre-launch uploads)
  char_start, char_end int (offsets inside workspace_items.content_md)
  label        text
  value        text  CHECK (value IN ('metadata','core','supporting','boilerplate','repeat'))
  what         text  (Arabic description)
  content_md   text  (the original text of the section)
  PK (item_id, seq)
```

- The small header table (`attachment_segment_maps`) is a Claude addition to the owner's "pages +
  sections": it holds what belongs to the whole document (versions, the documents inside the file,
  key paragraphs) and makes "replace the whole map" one delete + insert. (§6 Q4.)
- RLS **enabled** on all three (Absolute Rule 6), owner-scoped policies written but **no grant to
  `anon` / `authenticated`** — same stance as migration 195: the backend reads them as service_role
  and the browser never touches them.
- Lifecycle: deleted with the `workspace_items` row (cascade) → account purge (`purge_user_data`) is
  covered if it deletes `workspace_items` rows — **verify in Phase 2**; the retention sweep purges the
  file only and never touches these tables; a soft-deleted item (`deleted_at`) is filtered by the
  readers through the `workspace_items` join.
- Stale map: if `content_md` length ≠ `content_chars` (re-OCR, manual edit), the map is ignored and the
  segmenter re-runs.
- Size: ~2.0M chars of attachments in two weeks → roughly the same again in pages + sections. Trivial.
- Helper status (running / done / failed, lease) is the frame's job — helper_frame.md §5.4.

### 4.6 Delivery to deep search (D11, D13, D14, D16) — `agents/deep_search_v4/shared/case_documents.py`

#### 4.6.1 Who gets which documents (unchanged rules)
- `forwardable_documents` unchanged (same 9 types + unset).
- Case expander: summaries of all forwarded docs + the delivered text of the docs the planner named in
  `case_full_text_wis`; reg expander the same with `reg_full_text_wis`; aggregator: delivered text of
  every forwarded doc, planner-named first. Planner decider: reads everything in full, as today.

#### 4.6.2 What "delivered text" is, per document
| Document | Delivered text |
|---|---|
| ≤ 5,000 chars | full `content_md` (as today) |
| > 5,000 chars, map ready | its `core` sections in document order, each preceded by a one-line tag with its `what` (e.g. `〔أسباب الحكم — تسبيب المحكمة لرفض الطلب〕`); between non-adjacent sections one marker naming what was left out: «[…] لم يُعرض هنا: {what}، {what}» (boilerplate / repeat runs left out silently) |
| > 5,000 chars, no map after the wait (failed / past 5 min) | **full text**, treated as one core section of the document for the caps (§4.6.3): cut at 100k, then trimmed with everything else to fit 300k; the existing «انتهى المعروض…» marker when cut |
| > 5,000 chars, map ready but **no core section** | same as no map (a map with nothing core is treated as a failed rating; logged) |

The section tags and markers are Claude proposals (§6 Q2): they let the aggregator say *which part* it
is quoting («القاضي في أسباب الحكم…») and stop it guessing at what was left out.

#### 4.6.3 Caps and proportional trimming (D14)
Per recipient (case expander, reg expander, aggregator), in this order:
A **fallback** full text (no map) counts as **one core section** of its document in every step below,
so it obeys the same 100k / 300k caps (owner, 2026-11-11).

1. **Per document (100k):** if a document's core sections total > 100,000, each core section keeps
   `100,000 / core_chars` of its length, cut from its end back to the last paragraph/line boundary.
2. **Fixed parts** = everything that is not core text: headings, summaries, full texts of ≤ 5k docs,
   markers.
3. **Per agent (300k):** `room = 300,000 − fixed`. If the core text of all documents > room, every core
   section keeps `room / core_total` of its length (same cut from the end). Example: fixed 0, core
   400k → every section keeps 75%.
4. Each trimmed section ends with «[…] اختُصر هذا المقطع لضيق المساحة».
5. If the fixed parts alone exceed 300k (many documents), today's rule applies: planner-named
   documents first, a document that does not fit drops to its summary + `_OVER_TOTAL_NOTE`.
- `stats` / telemetry gain `segmented`, `fallback`, `trim_factor`, `core_chars` per recipient
  (Logfire, as `case_docs.*` today).
- Constants: `PER_DOC_CAP = 100_000`, `TOTAL_CAP = 300_000`, `SEGMENT_MIN_CHARS = 5_000` (no separate
  fallback cap — the fallback uses the same two). With the delivery flag off, today's 12k / 30k path is untouched.

#### 4.6.4 Wiring
- `WorkspaceItemSnapshot` gains an optional `segments` field (the delivered sections, real text).
- `agents/deep_search_v4/orchestrator.py::run_retrieval`, right before `_build_case_document_lists`
  (~line 1338 on master): **wait for the segmenter** on the forwarded documents over 5k
  (`consumer="expanders+aggregator"`, ceiling 5 min — one wait covers all three, they start after it);
  load the stored sections; attach them to the snapshots.
- **Masking:** the snapshots there are the planner's encoded copies (`_encode_attached_for_planner`).
  The loaded section texts are real → encode each with the same turn codec (`encode_active`) before
  building the blocks. Sections are paragraph-aligned, so an entity is never split across a cut.
- Prompts to update (text drafts in §4.8):
  - aggregator `_SHARED_ROLE_AR` case_document bullet (`aggregator/prompts.py:102-106`) **and** its V2
    copy (`_SHARED_ROLE_AR_V2`) — v1 is live (owner keeps v1), keep both consistent;
  - case expander (`case_search/prompts.py:539` and `:694-696`) and reg expander
    (`reg_compliance_search/prompts.py:245`): "full text, possibly cut at a marked point" → "the
    document's core sections in its own words, with markers where parts were left out";
  - planner field descriptions `case_full_text_wis` / `reg_full_text_wis` (`planner/models.py:161`) and
    the planner prompt lines that describe what the expanders receive (`planner/prompts.py:120`,
    `:618`): "read in full" → "receive the document's core sections".

### 4.7 Delivery to the writer (D12, D13, D16) — `agents/writer_planner/walkers.py`
- `build_analyzed_items_direct`: for each selected `kind == "attachment"` over 5k → **wait for the
  segmenter** (`consumer="writer"`, ceiling 5 min) → `body_md` = `metadata` + `core` + `supporting`
  sections in document order, same tags and left-out markers as §4.6.2. **No cap** (D12). The
  `metadata` sections are the interim until the metadata agent exists (§10.1), then they come from it.
- No map after the wait → the **full text within the caps** (D16): each fallback document cut at 100k,
  and if the writer's fallback documents together exceed 300k they are trimmed proportionally from
  their ends (§4.6.3 rules). The segmented documents stay uncapped (D12). Today the writer gets every
  attachment uncapped; the owner chose the capped fallback.
- `≤ 5k` attachments and every non-attachment kind: unchanged (full `content_md`).
- The writer planner is not critical: it keeps working from summaries and can still `unfold` an item's
  full text while the segmenter runs.
- Masking: unchanged — the writer executor has its own encode hook on the package.
- **Why `metadata` rides along:** core + supporting alone would leave out the court, case number, dates
  and parties' capacities that a defense brief or an appeal puts in «بيانات القضية» — the writer has
  them today (full text). Owner: include them until the metadata agent exists (§6 Q1).

### 4.8 Quoting rules (D17, D18) — prompt drafts

**Aggregator** — replaces the case_document bullet in `_SHARED_ROLE_AR` (and the V2 copy). English
instructions, Arabic output, no sample sentences to copy:

> - **`case_document` blocks are the exception — they are the user's own case papers** (a ruling, a
>   statement of claim, a memorandum, hearing minutes, a contract, a letter…). Each opens with a
>   heading carrying its type and title, then its summary and the parts of its text selected for you,
>   in the document's own words; a marker names any part that was left out — never guess what it says.
>   They are the facts of the user's matter, and when the question touches one of them the answer
>   works from what it actually says.
>   - **A critical finding must be quoted.** When a document holds something that decides or changes
>     the answer — a ground the ruling rests on, a clause that settles the dispute, an admission, an
>     argument left unanswered, a decisive figure, date or deadline — quote its exact words in «…»,
>     say which document and which part it comes from («أسباب الحكم المرفق»), and then say what it
>     means for the user's case.
>   - **Beyond critical findings, quote to make the result land.** Where a short passage from the
>     user's own papers shows how our findings bear on their case better than a description would,
>     quote it and connect it to the finding. Use your judgment — quote what serves the user's
>     understanding, not to fill space.
>   - **Every quote is grounded (تأصيل).** Tie the quoted words to the legal rule from `<references>`
>     with its `[n]` — whether the rule supports them, contradicts them or leaves them open — and
>     state the consequence for the user.
>   - Quote exactly, never stitch passages together or adjust wording; keep each quote to the words
>     that matter. A figure or date that looks garbled may be an OCR misreading: quote it as written
>     and say it should be checked against the original.
>   - (kept) The legal rule comes from `<references>` only, cited `[n]`; a document is a source of
>     facts, never a legal authority, never a `[n]`. Refer to a document by what it is, never `WI-N`.
>     Without its text, do not state a document's facts more precisely than its summary does. If a
>     part that was left out likely holds what the answer needs, say so in the gaps section.

**Writer** — new short section after «Citing the user's documents» in `agents/writer/prompts.py`:

> ## Quoting the user's documents
> The attachment `<source>` items carry the documents' own words. Whether to quote depends on the
> kind of document you are drafting, the user's instruction and the material itself: when the draft
> answers, relies on or challenges a specific passage — a ground of the ruling under appeal, the
> opponent's argument being rebutted, the clause being enforced — quote that passage exactly in «…»
> with its «(مرفق رقم n)» before dealing with it. Otherwise state the facts in your own words. Never
> quote what the source does not say, never alter the quoted words; a figure that looks garbled is
> quoted as written and flagged in `notes_ar`.

Both rules can ship **independently of segmenting** (they help on today's first-12k text too) — see §5.

### 4.9 Flags, telemetry, tests
- `ATTACHMENT_SEGMENTER_ENABLED` (default off) — the helper runs and stores maps (shadow mode: nobody
  reads them yet).
- `ATTACHMENT_SEGMENT_DELIVERY_ENABLED` (default off) — the consumers wait for and deliver sections,
  with the new caps. Off → today's 12k / 30k path byte-identical.
  (Two flags so maps can be collected and replayed before any agent sees them — §6 Q6.)
- Telemetry: helper spans (helper_frame.md), `case_docs.*` delivery stats, `llm_calls` cost.
- Tests: paragraphizer reproducibility; validator/repair; trimming maths (incl. the 400k example);
  delivery table §4.6.2 row by row; masking of loaded sections; writer body assembly; flags off =
  byte-identical blocks.

---

## 5. Phases

| Phase | Scope | Done when |
|---|---|---|
| **0 — prompt hardening** (experiments, no repo code) | P2 speaker rule, P3 core criteria for every type, P4 ranges, P9 finer judgment paragraphs, P5 repeats in experiment code; rerun the 10 docs **twice**; simulate delivery under §4.6.3 from the saved maps | Valid 10/10 both runs; decision-bearing sections (court grounds + ruling, expert findings + conclusion, closing requests, price / penalty / termination clauses, financial totals + status) rated **core in both runs** for every doc; core share per doc differs ≤ 15 points between runs; all 14 long judgments deliver ruling + grounds inside 100k |
| **1 — helper frame** | helper_frame.md phase F1: the frame + OCR and summary moved in, behaviour unchanged | Parity on recorded turns (same router input, same order), summaries run in parallel |
| **2 — segmenter helper + storage** | migration 215 (3 tables), OCR page rows, paragraphizer, agent, validator / repair, split mode, repeat detection, `ATTACHMENT_SEGMENTER_ENABLED` | Runs on every new attachment > 5k in shadow; maps valid; failure never blocks a turn; purge cascade verified |
| **3 — delivery** | `case_documents.py` + `run_retrieval` wait + walkers; caps; expander / planner prompt text; `ATTACHMENT_SEGMENT_DELIVERY_ENABLED` | Replay of the 61 recorded turns: what each question needed reaches the expanders / aggregator (measured, not assumed); writer packages carry core + supporting |
| **Q — quoting rules** | §4.8 aggregator + writer prompt changes | Can ship before Phase 3 (works on today's text); live check on a few judgment / memo turns |
| later | metadata agent (§10.1), relationship map (§10.2), doc_type fixes (§10.3) | — |

---

## 6. Open questions for the owner

**All answered 2026-11-11.** Q1 and Q5 explicitly; the rest by the owner's "ok, let's start building"
→ the recommended defaults: Q2 section tags + left-out markers **yes**; Q3 repeat detection in code
**yes**; Q4 header table `attachment_segment_maps` **yes**; Q6 two flags **yes**; Q7 split above ~30k
**yes**. Build started 2026-11-11 in worktree `C:\Programming\LUNA_AI_segmentation`, branch
`feat/attachment-segmentation` from `origin/master` @ `29abb24`.

1. ~~**Metadata gap.**~~ **Answered 2026-11-11: yes** — the writer gets the `metadata` sections until the
   metadata agent exists; deep search keeps core only (D12, §4.7).
2. **Section tags and left-out markers** (§4.6.2) — agents see which part each passage is and a
   one-line list of what was left out. Yes?
3. **Repeat detection (P5)** in code — a second copy of a passage is never delivered. Yes?
4. **Header table** `attachment_segment_maps` beside the owner's pages + sections tables. Yes?
5. ~~**Writer fallback.**~~ **Answered 2026-11-11:** the fallback is the full text **within the caps —
   100k per document, 300k overall** — for the writer and for deep search alike (D16, §4.6.3, §4.7).
6. **Two flags** (collect maps in shadow first, deliver later) — yes?
7. **Split threshold** ~30k (P7) — fine?

## 7. Risks

- **Core-as-rated instability** (39% → 93% of the same judgment). With D11 the rating *is* the
  delivery: a stingy run hides a section, a generous one just sends more. Mitigation: P3 criteria in
  Phase 0 with the "decision-bearing sections core in both runs" bar; the 100k / 300k caps make
  over-generous runs cheap.
- **Waiting.** A critical consumer can wait up to 5 minutes on a long document's first turn. Split mode
  (~65 s on the longest judgment) and starting the segmenter right after the summary keep the real
  wait well below that; the user sees a status line (helper_frame.md).
- **300k per agent** ≈ ~100k input tokens to each expander and the aggregator in the worst case — cost
  and latency. Today's biggest single upload is 81k chars; most turns are far below. Telemetry
  (`trim_factor`, `core_chars`) shows the real distribution after launch.
- **Fallback size:** a failed or late segmentation now sends up to 100k of a document (was 12k today),
  inside the 300k per agent — same cost ceiling as segmented delivery.
- **Redeploys** kill in-flight helpers (every push restarts the backend) → re-run on first need; costs a
  second segmentation, never a lost answer.
- **Wrong `doc_type`** → wrong label list (doc_type fixes deferred, D20).
- **OCR noise** is quoted verbatim (e.g. ٢ read as ٣) → both quoting rules say to flag suspicious
  figures.
- **Privacy:** pages and sections are customer text — same handling as `content_md`: RLS, no client
  grants, cascade on delete, masking before any LLM sees them.

## 8. Experiment artifacts (outside the repo, may be cleaned)

Scratchpad of session e320773d (full path in `experiments/README.md`):
- `attachments.json` — all 252 attachments since 10-26 (customer data).
- `attachments_ocr.html` — every attachment by doc_type with its OCR text and char count.
- `seg_exp/flash/`, `flash_meta/`, `flash_v3/`, `flash_v3_rep/` — segmenter runs 1–3 + baseline.
- `seg_exp/flash_split/split_vs_single.html` — split vs single.
- `seg_exp/opus_rel/`, `flash_rel/`, `v41_rel/` — relationship maps + `relationship_maps.html`.
- `planner_fulltext.json` — the 61 deep-search turns with the planner's picks, doc types and questions
  (input for the Phase 3 replay).

## 9. Next steps (pick up here)

1. Owner answers the rest of §6 (Q1 and Q5 answered 2026-11-11; Q2–Q4, Q6, Q7 have a recommended
   default) and helper_frame.md §7.
2. Phase 0: write P2 + P3 (all types) + P4 + P9 into `experiments/run_flash_segmenter.py`, P5 into the
   experiment code; regenerate inputs (`fetch_attachments.py`, `make_paras.py` — scratch folder, never
   the repo); rerun the 10 docs twice; measure against the Phase 0 bar; simulate §4.6.3 delivery.
3. Agree helper_frame.md (its own open questions).
4. Then cut build waves (frame → segmenter + storage → delivery; quoting rules any time) with file
   manifests.

---

## 10. Later (planned, not in this release)

### 10.1 Metadata agent (D19)
- **What:** a helper that runs **after segmentation** and takes every section rated `metadata` across
  **all** of the conversation's documents, to give the writer one clean set of identifying data (court
  and circuit, case / deed / contract numbers, dates Hijri + Gregorian, parties with capacities and
  representatives, subject, sender / addressee).
- **Frame:** depends on the segmenter (all documents of the conversation); **critical only for the
  writer**; not critical for anything else.
- **Open when it is planned:** input = the stored `metadata` sections (no new OCR read); output shape
  (structured fields vs the passages); relation to the writer planner's party validation (`parties`,
  `ask_user`). When it ships it replaces the interim: the writer stops receiving the raw `metadata`
  sections (§4.7) and reads the agent's output instead.

### 10.2 Relationship map (was P10 / D8)
- Points (legal units with paragraph ranges) + edges (supports / rebuts / answers / decides /
  relies_on / cites / repeats / contradicts / amends / implements / refers_to) + threads + gaps.
- Tested: V4.1 found 57 / 31 / 51 of Opus's points (judgment / expert / contract), all checklist items
  on the judgment, the unanswered limitation defence (Art. 344), the undocumented change order and 8
  cross-document contradictions; ~$0.04–0.05 and 2–3.5 min per doc. Weak spots: hearing minutes
  (12/53 points first+last only), statutory "gaps" drifting into opinion, financial "gaps" that are OCR
  figures. Would run as another helper (depends on the segmenter, own flag).

### 10.3 doc_type fixes (was P11)
- Use `documents_in_file` to improve `doc_type`; published precedents → `reference`, blank templates →
  `reference`, execution orders → `judgment`, Najm placement = owner call
  (`project_attachment_doc_type` memory).

---

## 11. Build log (2026-11-11)

**Where:** `C:\Programming\LUNA_AI_segmentation`, branch `feat/attachment-segmentation` (from `origin/master`
@ `29abb24`), uncommitted. Tests: `agents/` + key backend suites → **2,485 passed**; 4 collection errors are
pre-existing on master (relative imports: planner `test_long_message_review`, `convo_compactor` ×2,
responder `test_no_workspace_list`).

**Phase 0 (experiments):** prompt **v4.4** + paragraph rule **v2-sentence-500j-1200** (judgment / hearing
500 chars sentence-aware, others 1,200). Bar: decision-bearing sections core in both runs **10/10**;
all 17 long judgments deliver ruling + grounds inside 100k; core-share within 15 points **10/10 in
round 2, 7/10 in round 3** (memo / hearing / letter flips — run-to-run variance; no decision-bearing
passage ever affected). Spend ≈ $0.72. Option for the owner: **run the segmenter twice and deliver the
union of both runs' core** (steadier, ~2× cost ≈ $0.01/doc, no extra wait in parallel) — NOT built.

**Built:**
| Part | Files |
|---|---|
| Flags | `shared/config.py`: `HELPER_FRAME_ENABLED`, `ATTACHMENT_SEGMENTER_ENABLED`/`_USERS`, `ATTACHMENT_SEGMENT_DELIVERY_ENABLED`/`_USERS` (all off) |
| Helper frame F1 | `agents/helpers/` (spec, registry = the matrix, turn, status/lease, runtime); `agents/orchestrator.py` behind the flag (off = old path in `else:` branches); migration **216 `helper_runs`** |
| Segmenter | `agents/memory/attachment_segmenter/` (paragraphs v2, prompt seg-v4.4, agent slot `attachment_segmenter` on `_FLASH`, runner with masking / split / repair / repeats, store + `load_segment_maps`); `agents/utils/segmenter_flags.py`; registered as helper `segmenter` (critical for case/reg expanders, aggregator, writer; 300 s; lease 300 s; max 2 attempts; version = prompt/paragraph versions) |
| Storage | migration **215** (`attachment_pages`, `attachment_segment_maps`, `attachment_segments`; RLS on, client roles revoked, cascades, self-checks); OCR page rows (`ocr_extractor`, behind the segmenter flag) |
| Delivery | `agents/utils/attachment_delivery.py` (values per recipient, tags + left-out markers, 100k/300k proportional trim, writer bodies), `agents/utils/attachment_sections.py` (wait + load + encode), `case_documents.build_case_document_blocks_segmented`, `run_retrieval` wait (stage `planning`, «نقرأ مستنداتك كاملة…»), `walkers._attachment_bodies` |
| Prompts | aggregator quoting rules (D17) — **live without a flag** (wording also fits today's cut text); writer quoting rule (D18) + tags note; expander prompts + planner rules (segmented variant chosen by the delivery flag) |

**Left / decisions:**
1. Owner: two-run union for core stability — yes / no.
2. Apply migrations **215 + 216** to prod (dry-run first; via `SUPABASE_DB_URL`) — owner OK needed.
3. Commit + which new test suites become durable (they are git-ignored by the `tests/` rule; whitelist in
   `.gitignore` + CI list or keep local): `agents/helpers/tests`, `agents/memory/attachment_segmenter/tests`,
   `agents/memory/ocr_extractor/tests/test_page_rows.py`, `agents/deep_search_v4/tests/test_case_documents_segmented.py`,
   `test_segmented_delivery_wiring.py`, `agents/tests/test_helper_frame_orchestrator.py`.
4. Regenerate the prompt catalog (`scripts/extract_prompts_md.py`) — reference only, optional.
5. Not built: OCR per item (frame runs OCR once per message), split mode untested on v4.4 live, repeat
   granularity is per segment (a mixed segment may deliver some text twice).

**Rollout order (proposed):** apply 215 + 216 → deploy with all flags off (aggregator / writer quoting
rules go live) → `HELPER_FRAME_ENABLED` on (parity watch) → `ATTACHMENT_SEGMENTER_ENABLED` on (shadow:
maps stored, nobody reads them; review maps + cost in Logfire / `llm_calls`) → replay the 61 recorded
turns → `ATTACHMENT_SEGMENT_DELIVERY_ENABLED` for one test user (`_USERS`), then everyone. Rollback at
any step = unset the flag.

---

## 12. v5 — one principle, the user's words, the preview tool (2026-11-12)

**Owner decisions (/reflect 2026-11-12):**
| # | Decision |
|---|---|
| D22 | The segmenter reads **the user's own messages** (never the assistant's): the message the file came with in full (safety cap 10k), then earlier user messages newest first up to **4,000 chars**. They can only **raise** a passage to `core`. Masked like the rest of the input. |
| D23 | Re-segmenting when the user later points at something — **out of scope for now**. |
| D24 | **Core / supporting by principle, not per label:** `core` = everything worth investigating and searching on for the case; `supporting` = only appendix-like data that holds nothing to investigate, **never on a guess**; **when in doubt `core`**. Tables that carry the decision or the figures in issue (expert final amounts / shares, financial totals + status, fee / penalty schedules, entries a record certifies) stay `core`; their routine rows are `supporting`. |
| D25 | Metadata stays **out of deep search** (writer only). |

**Built (worktree, uncommitted):** prompt **seg-v5.0** (labels keep their meaning, value arrows removed, per-type notes restate the principle); `runner._load_user_words` + "What the user said" block in whole and split messages; `SegmenterResult.user_words_chars`; preview mode (`run_attachment_segmenter(…, preview=True, min_chars=…)` — no store, no masking fakes saved, no ledger row); **test tool `scripts/segmentation_preview.py`** (`--conversation`, `--items`, `--repeat 2`, `--from-json`; per-conversation delivery; output to the scratchpad, never the repo). 248 segmenter / helper / delivery tests pass.

**Preview run v5 (22 attachments, 14 types, 3 split, one 5-file message; each twice; $0.22, 9 min):**
- 22/22 segmented (one first attempt failed and retried); split mode worked on all four docs over 30k (63k judgment, 81k hearing record, 74k expert report, 30k reference).
- Core share of case documents: median **82%** (the 16.5k judgment 17% in v4.4 → **86%**). Bank statement 2% by design (routine rows supporting, repeated header block boilerplate).
- Two-run agreement: avg **91.4%**, median **97.4%**, 14/22 ≥ 95%; core share within 15 points **20/22** (v4.4 Phase 0: 7/10). Low: a claim 63% (its fact narrative and embedded attachment flip), a chat export 65%, an unlabelled self-duplicating file 75% (cut differently between runs, so a different copy is marked repeat), the 74k expert report 77% (core 94% vs 71% in split mode). The tool now keeps every run's map to trace these.
- 5 files sent in one message: today the aggregator gets 27k and two files as summary only (30k total); v5 delivers 44.6k, all five.

**Proposal — bundling small files sent together (not built, owner to decide):** of 98 messages with 2+ files, **23** are all small files (each ≤ 5k) that only pass 5k together (19 of one doc type, 16 with photos — a document photographed page by page); 31 carry 2+ long files. Proposal: (a) files over 5k keep one segmenter run each, all in parallel (raise the helper's concurrency from 3 to 6 so a 5-file message starts at once); (b) small files from ONE message whose total passes 5k form a **bundle**: joined in upload order with a file-boundary paragraph, segmented in one call (the segmenter's `documents_in_file` tells pages-of-one-document from separate documents), the map cut back at the file boundaries and stored per item with a shared `bundle_id`; delivery stays per item; (c) in a mixed message, long files alone, small ones bundled among themselves; (d) later: show a bundle that is one document as ONE case document block.

**Owner decision D26 (2026-11-12) — units per message, all in parallel:**
1. Every attachment **over 5k** gets its OWN segmenter run (one call, or two when split above ~30k).
2. The **remaining attachments ≤ 5k** of that message: if 2+ and together **> 5k** → ONE **bundle** run (split on the combined size above ~30k); otherwise they travel whole.
3. All runs of a message start **in parallel**.
Example: 40k, 20k, 4k, 2k, 4k → three runs: [40k] (2 calls), [20k], [4k + 2k + 4k].
Bundle mechanics: files in upload order, each paragraphized by its own doc_type, numbered globally, un-numbered file-boundary lines; label list = most common doc_type (mixed → generic); repeats detected across the whole bundle; map cut back per file and stored per item with `bundle_id` / `bundle_size` (migration 215 edited, not applied). Delivery: a small file with sections delivers its sections (deep search: core; writer: metadata + core + supporting); without sections it travels whole as before. Helper concurrency 3 → 8.

**Bundles built + previewed (2026-11-12):** `agents/memory/attachment_segmenter/bundles.py` (`plan_units`, `bundle_for_item`, `segmenter_applies_to_item`), `run_attachment_bundle` (one call per bundle, un-numbered file-boundary lines, cut back per file, repeats across files, per-member maps with `bundle_id` / `bundle_size`, in-process dedupe so members trigger ONE run); helper `applies_to` = `segmenter_applies_to_item`; delivery sections-first for small bundled files; preview tool runs `plan_units` units in parallel. Preview on 5 messages (3 mixed, 2 small-only; 22 files, 11 units ×2, $0.05, 114 s): every message split into units exactly per D26, no repairs needed. **Fixed after the preview:** a bundle used the label list of its most common type for every file (a registry extract bundled with two contracts got contract labels) → the bundle prompt now gives the label list + notes of EVERY type present and each file uses its own. **Open (owner):** (1) official records — the data a record states about its subject (owner, managers, status, vehicle, policy) is sometimes rated `metadata`, which keeps it out of deep search; proposal: for records, `metadata` = only the issuer's header (name, logo, reference number, date), the subject data = `core`; (2) "a blank or standard form" vs a FILLED form — a car-rental contract form flips core ↔ supporting; proposal: a form with filled values is case content (`core`), only an empty template is `supporting`.

**v5.1 (2026-11-12, owner "1-ok 2-ok"):** records — `metadata` is only the record's own header (issuer, logo, type, number, date); what it states about its subject (owner, managers, status, vehicle, policy, accident parties, amounts) is `core`. Forms — an EMPTY template is `supporting`; a form whose fields carry the case's values is `core`. Re-run: 22 single files agreement avg 92.9% (v5.0 91.4), 15/22 ≥ 95%, core within 15 pt 20/22; bundles avg **93.9%** (v5.0 80.7), min 60.0 (v5.0 16.2); the registry extract now 100% agreement, the filled rental form 95.7%. Still unstable: one insurance record with OCR garbage + a redacted block (core 20% vs 59%).

**Dropped (owner, 2026-11-12):** a «ريحان يستخرج / يطلع على / ينظف / يقرأ المستندات» typing-indicator was built and removed the same day — the segmenter mostly runs within the router's time, so there is no wait worth showing "as of now". Nothing of it remains in the branch.
