# Attachment agent — the router's way into the user's files

**Status:** PLAN, 2026-11-27. Nothing built. Facts checked on master `3327abd` (worktree `C:\Programming\LUNA_AI_merger`), then re-checked against master `bdb812f` (router helper waits, shipped the same day) — router line numbers below are `bdb812f`'s.
**Working name:** attachment agent (`attachment_agent`). The Arabic name is the owner's call.
**Related:** `attachment_segmentation.md`, `relationship_map.md` (angle map), `large_document_index.md` (§4.5 is replaced by this plan), `artifact_analyzer.md` (the router-linked analyzer this copies parts of).

---

## 0. Owner decisions (2026-11-27)

| # | Decision |
|---|---|
| D1 | A **new agent** is the router's only way into the user's uploaded files. The current attachment pipeline (OCR → summary → segmenter → citation pins → angle mapper) stays the background step that makes the first version. |
| D2 | The router calls it as a **tool**; the result comes back to the router, which writes the reply or keeps routing. (Not the `CheckPriorReports` output pattern.) |
| D3 | It reads from an **outline** (the segment map + the angle map «الخيوط»), then **finds** a phrase, **reads sections** or **reads pages**. Small files are read whole. |
| D4 | The router keeps the summary / doc_type **cards** and its «which document do you mean?» rule. It **stops opening the user's files**, and still opens our own outputs (reports, memos, notes). |
| D5 | **Router only** for now. Writer planner, deep-search planner, artifact_analyzer, «بحث موسّع» and the merger keep reading as today. |
| D6 | The agent **owns the attachment** after the first pass: it reads it AND edits it. |
| D7 | It edits **only what the user specified**: promote sections (**promote only**, never demote), change threads / gaps / the client line / anything else in the angle map **without re-running the angle mapper**, and fix **small extraction mistakes** in the text (e.g. a misread name). |
| D8 | A **re-analysis** runs only when the user asks for one («ماعجبني التحليل اعيد تحليله» or similar). That is the attachment analyzer's job (the angle mapper re-runs). |
| D9 | The rule is the user's **intent**, not the field: if the user names what to change (including the client's side), the agent changes it; if the user asks to re-analyze, the analyzer re-runs. |
| D10 | Model: **Flash** (tier_2, `_FLASH`) for now. |
| D11 | Summaries: the router already sees the file's summary; the agent does not write summaries. A request for a full summary keeps going to the writing «summary» route. *(My reading of "summarizing is available, the router can see it".)* |

**Defaults accepted** (owner: "ok write the plan", in reply to the list of defaults, 2026-11-27):

| # | Decision |
|---|---|
| D12 | Text fixes are stored as an **overlay**; `content_md` is never rewritten (§3.3). Every edit is **recorded** and survives a re-segment. |
| D13 | Name: **attachment agent** / «وكيل المرفقات». |
| D14 | A text fix corrects **every occurrence** in the file; the reply says how many. |
| D15 | Text fixes and section promotions **follow the file** into every conversation it is attached to. |
| D16 | A re-analysis gets the user's critique **plus their earlier change requests** on that map, and is **billed** like a normal map (~2 points). |
| D17 | **No «الخيوط» UI change** in v1; undo by asking the agent. |
| D18 | The **router picks the files**; none picked = every attachment in the conversation. |
| D19 | A clear request is **applied directly** (no confirm step); an unclear one gets one short question. |
| D20 | **Free plan:** reading, text fixes and promotions yes; thread edits and re-analysis are not possible (free users have no angle map). |

Reopened by the same-day library-file decision, see §7: whether a **thread edit** stays with one conversation or follows the map everywhere it is carried.

---

## 1. How it works today (facts)

**Router.** Its section «The conversation's own documents» (`agents/router/router.py:285-295`) tells it to open the user's files itself with `unfold_workspace_item` (registered at `router.py:815`), which returns the file's **whole `content_md` with no cap, no pages and no sections** (`agents/tool_repository/unfold_workspace_item.py:873-898`). A 100-page contract is ~368k chars in one tool result. Budget: 15 requests, 9 tool calls (`router.py:230-243`). `list_workspace_items` labels an attachment `agent_search` (`kind_hint`, `router.py:890`) — a bug.

**Router waits for the analysis (since `9d9b080` + `bdb812f`, 2026-11-27, ON for everyone).** Before routing, the router waits for the segmenter and citation pins (`ensure_ready`, now critical for `router`, `agents/helpers/registry.py:247, 470`) and for an angle map already running (`_wait_in_flight`, 300 s, `registry.py:377`) — for the files sent with the current message, with the staged waiting line «ريحان يستخرج… يلخص… يرتب… ينظم… يستخرج الخيوط». So on an upload turn the agent normally finds the segment map, pins and angle map ready. The router also has a per-user dynamic section for «بحث موسّع» (`inject_extended_search`, `router.py:793`) — the pattern this plan copies for its own section.

**Library files carry their whole map (owner, 2026-11-27).** A «الملفات» file attached to a new conversation carries its existing angle map whole (all its documents, original side); no new mapper call. Read side not built yet. Second rule: a library file with no map gets mapped if the plan is pro / pro_plus / max. So **one angle map can be read from several conversations**.

**Nothing answers a question about a file.** OCR, summary, segmenter, pins and the angle mapper all run once per file, in the background. No page reader, section reader or find-in-document exists for attachments.

**Segment map.** `write_segment_map` deletes the map (rows cascade) and re-inserts (`attachment_segmenter/store.py:123-145`). A map is stale only when `content_chars` ≠ the text length (`store.py:231-236`); a prompt-version bump does not re-segment. Re-segment happens when there is no map or the text length changed. Nothing reads `value` for staleness, so an in-place update is safe **until the next re-segment wipes it**.

**Angle map.** One map per file group: `attachment_angle_map_items.item_id` is UNIQUE (`228:146-158`). `write_angle_map` inserts the new map, then deletes the old one; **no history** (`angle_mapper/store.py:283-357`). Freshness compares only the member rows + `left_out` (`group.py:590-609`), so **editing angles, client, gaps, context, no_angle or unrelated_documents does not make a map stale**. Refs are stored as `D1.S7` (`angle_mapper/refs.py`); the UI silently drops any other form (`angle_maps_service.py:221-228`). «بحث موسّع» needs ≥ 2 angles (`extended/eligibility.py:119`).

**Re-running the mapper.** No forced path through the helper frame (a fresh map answers "ready"). `run_message_angle_map(supabase, message_id, item_ids)` (`angle_mapper/runner.py:478-551`) re-runs directly, keeps the flag + free-plan gates, takes no `helper_runs` lease, and bills to the **original upload message**. The prompt has no slot for the user's critique (`angle_mapper/prompts.py:424-446`).

**Report reading-corrections are not reusable as is.** `with_corrections` appends a note «تصحيح بعد قراءة النص الكامل…» under a report; it never replaces text, and it lives on one conversation's row (`agents/utils/reading_corrections.py`). Reusing its metadata key would make three report readers show attachment fixes with report wording.

**Masking.** Names are never masked; only runs of ≥ 8 digits and emails are (`shared/privacy/codec.py:7-8`). A name fix needs no decoding; a number / email fix is decoded with `masking_service.decode_text` and refused if it would store a fake (`codec.leaves_fake`).

**Pieces to reuse:** `citation_scan.fold` / `find_folded` (tashkeel, alef forms, ة/ه, ى/ي, Arabic-Indic digits, offsets back into the original — `agents/tool_repository/citation_scan.py:66-146`); `artifact_analyzer` package layout + `verify.normalize` (layout) + its retry-once output validator; `edit_artifact` as the example of an agent run inside a router tool (`agents/tool_repository/edit_artifact.py:184-286`); the `prepare=` hook to show a tool per user (`agents/tool_repository/handback_guard.py:18-24`); `load_pages`, `load_segment_maps`, `load_angle_map_for_items`, `labels.section_index` for valid refs.

---

## 2. The shape

```
user message
   │
 Router ── cards (summary, doc_type) ── «which document?» stays here
   │
   ├─ about / change something in the user's files ─▶ tool: attachment agent
   │                                                     │ reads: outline → find → sections / pages
   │                                                     │ edits: sections · threads/gaps/client · text fixes
   │                                                     │ re-analysis request → angle mapper (background)
   │   ◀── result: findings + checked quotes + edits done ┘
   │
   └─ writes the reply (ChatResponse) or keeps routing as today
```

### 2.1 Router side
- **New tool** (working name `ask_attachment_agent`), shown only when the flag is on for the user (`prepare=` hook). Arguments: the files the router picked (`WI-n` aliases; empty = every attachment in the conversation). **No question argument**: the agent gets the user's message word for word from `RouterDeps.user_message`, so the router cannot reword it (the no-rewording rule, enforced by construction).
- **One call = the user's whole message.** It counts as one of the router's 9 tool calls.
- **`unfold_workspace_item` on an attachment**, router only, flag on: returns one system line pointing to the agent instead of the file. Reports, memos and notes unfold as today. Planner / writer-planner unfold is unchanged (D5).
- **Prompt:** the section at `router.py:285-295` is replaced, flag on, by a short section: identify the file from the cards; a vague pointer → ask which one (unchanged, `router.py:292`); otherwise call the agent; answer only from what it returned (place + quotes); never give a location it did not return; say plainly what was changed; a request to change or re-analyze a file goes to the agent too. A request that needs a **search** about what a file says still routes to deep search as today (the planner reads the files itself).
- **Flag off = today's section text, unchanged.** The router is one module-level agent, so the section moves out of the static prompt into a per-run section chosen by the flag, the way `inject_extended_search` (`router.py:793`) works. Flag off, the words are today's; only their place in the prompt moves (one prompt-cache miss at deploy). Pinned by a snapshot test.
- Fix the `agent_search` label on attachments in `list_workspace_items`.

### 2.2 The agent (Layer 3 — answers only to the router, never to the user)
Package `agents/attachment_agent/`, laid out like `artifact_analyzer` (deps, models, prompts, context, tools, verify, runner). Slot `attachment_agent` = `_FLASH`. Own `UsageLimits` (not the router's `ctx.usage`). Ledger: runs inside the router turn's `collect_llm_calls`, like `edit_artifact` — no extra plumbing. Router-side timeout ~90 s (the `completion_search` tool pattern); on timeout or error the router gets a plain line and answers that it could not read the file now.

**What code gives it (built by code, masked with the turn codec):**
- the user's message word for word + the last 6 conversation rows (same window as artifact_analyzer);
- per file: title, doc_type, summary, size, page count, whether a segment map / angle map exists;
- the **outline**: each section `D1.S7 · label · value · what · owner · pages` (boilerplate / repeat collapsed to one line); the conversation's **angle map**: client line, threads `A1…` (title, for_side, strength, weakness, sections), gaps;
- small files (≤ ~12k chars, final number at build) inline whole.

**When there is no map.** Since the router now waits for the analysis of the files sent with the message (§1), a missing map is the exception: files ≤ 5k chars (no segment map of their own; a small-file bundle may have one), a wait that hit its ceiling, old uploads, free users (no angle map), library files whose file never had a map. Then the agent reads the raw text with `find` + `read_pages`; it never starts the segmenter or the mapper itself.

**Read tools:**
| Tool | Does | Notes |
|---|---|---|
| `find(phrase, files?)` | every hit: file, section ref, page, a short passage around it | `find_folded`, so «٢٥/٣/١٤٤٧» finds «25/3/1447», tashkeel and alef forms ignored; capped hits |
| `read_sections(refs)` | the sections' full text with pages | per-call char cap |
| `read_pages(file, from, to)` | the pages' text | no page rows (older / non-OCR files) → fixed-size parts of the text, "part n of m" |
All read tools return the text **with the file's text corrections applied** (§3.3).

**Edit tools** — each takes `evidence`: the user's words asking for it. **Code checks the evidence appears in the current user message**; no match → refused. That makes D7/D9 ("only what the user asked") a rule code enforces, not just a prompt line.
| Tool | Change | Guard |
|---|---|---|
| `promote_sections(refs)` | value → `core` | promote only; refs must exist |
| `edit_thread(angle, fields)` | title, argument, strength, weakness, legal_question, for_side, for_party, sections (add / remove) | added refs must be valid `D·S` refs (canonicalised `D1.S7`) |
| `add_thread` / `remove_thread` | a new thread / drop one | remove only when the user says so; warn when «بحث موسّع» would drop below 2 threads |
| `add_gap` / `edit_gap` / `remove_gap` | the gaps list | refs canonicalised |
| `edit_client(side, party, basis)` | the client line | D9: a specified change, not a re-analysis |
| `fix_text(file, wrong, right)` | an extraction mistake | §3.3 checks |
| `request_reanalysis(critique)` | starts the angle mapper re-run | only on an explicit request (D8) |

Edit tools **stage** changes in deps; code applies them all **after** the run's output passes validation. A failed run changes nothing.

**What it returns to the router:**
- `outcome`: `found` · `not_found` (in what it read — never "not in the file" unless the file was read whole) · `which_file` · `edited` · `reanalysis_started` · `needs_clarification`;
- `points`: each a short statement + quotes;
- `edits`: one Arabic line per applied change («رُقّي القسم … إلى جوهري»، «صُحّح الاسم «محمد» إلى «محمود» في 4 مواضع»);
- `ask_user`: one short question when what to change is unclear.

**Checking its work (reflect):** every quote must appear word for word in the (corrected) text of a file it was given — `verify.normalize` for layout + `fold` for letters and digits. Failing quotes → one retry naming them, then dropped. **Code sets each quote's location** (file title, section what + owner, page) from where the quote sits; the model's own location claim is not used. Rendered to the router as a compact block per quote.

### 2.3 Re-analysis (D8)
`request_reanalysis` starts a background task: snapshot the current map into the edit log (so it can be restored) → `run_message_angle_map(...)` with a new **`guidance`** argument threaded into the mapper prompt = the user's critique word for word + the user's earlier change requests on this map (their words), so a re-analysis does not lose e.g. a client-side fix. A **carried** map (a library file's map read from another conversation) is re-run for its own group — all its files, as the owner's "upload all the map" rule keeps it whole. The old map stays in place until the new one is written (insert-then-delete, as today). Needs, at build: a `helper_runs` lease (the direct entry takes none, so it could race a frame run), and billing to the **current** turn's message (today it bills the upload message). Free plan: no map exists → the agent says threads are on paid plans; it does not build one. Takes ~1–4 min; the router replies right away («سيُعاد تحليل الخيوط وتظهر خلال دقائق»). v1 sends no SSE event; «الخيوط» shows the new map when opened.

---

## 3. Storage — one migration (234 at the time of writing; re-check against master and the worktrees before building)

### 3.1 Section promotions — `attachment_segment_overrides`
`file_id · char_start · char_end · value ('core') · evidence · message_id · conversation_id · user_id · created_at · reverted_at`.
Keyed by **character range on the file**, like the citation tables (231), so it survives a re-segment. Applied inside `load_segment_maps`: any section overlapping an override range loads as `core` (promote only, so the safe side). Every reader of `value` (deep-search delivery, writer, angle mapper, extended view «جوهري», «الملفات» UI) sees it with no other change.

### 3.2 Angle-map edits — in place + `attachment_angle_map_edits` (log)
Edits are applied **in place** on `attachment_angles` rows and the map's jsonb columns (safe: freshness ignores them). Because a library file now carries its map whole into new conversations (§1), an in-place edit is seen **everywhere that map is read** — see §7 for the one open choice. Each edit writes a log row: `id · map_id (no FK, survives a replace) · conversation_id · item_ids · kind · before · after · evidence · message_id · user_id · created_at`. A re-analysis writes one `snapshot` row first. Undo = the agent re-applies `before` when the user asks. Optional at build: `edited_at` on the map row (the UI shows `created_at` today; no UI change in v1).

### 3.3 Text corrections — `attachment_text_corrections` (overlay)
`id · file_id · wrong · right · occurrences · evidence · message_id · conversation_id · user_id · created_at · reverted_at`. Keyed by **file**, so a fix follows the file into every conversation. **`content_md` is never rewritten** (its length drives segment-map and angle-map freshness: rewriting it would re-segment, renumber the sections, break the threads, and make «بحث موسّع» refuse the file).

Checks before a fix is stored: `wrong` is one line (no newline); it occurs in the file's text; its count in the whole text equals the sum of its counts across the sections (otherwise it crosses a section / page join and is refused with a clear reason); after decoding (numbers / emails) no fake is left. Default: every occurrence in that file is fixed; the reply states the count.

**Where the overlay is applied** — one function `apply_text_corrections(file, text)` plus a sections variant:
- *Whole-text readers (one shared opt-in loader):* the router's no-summary fallback (`router/context.py`), `unfold_workspace_item` (planner and writer planner still read whole files), `_load_attached_items` (`orchestrator.py:309-379` → planner `<attached_items>`, case documents, simple search, extended fallback), writer walkers, workspace get/list routes, the «الملفات» file viewer (`files_service.py:622-662`, incl. Copy), template ingester.
- *Own hooks:* `attachment_sections.py:117-129` (deep-search + writer sections, before encoding); `files_service._sections`; `angle_maps_service` section text; extended `marked_text` (after marking — it compares paragraph offsets); the angle-mapper prompt paragraphs (so a re-analysis reads the fixed text); the segmenter's **LLM message only** (its stored rows are cut from the real text); the summary input with `_source_length` kept on the real length (else the summary is marked dirty forever).
- *Not applied:* citation-pin scans and page offsets (they work on positions in the original text).

**Derived wording** that repeats the wrong string (the file's summary in `user_files` + `workspace_items`, section `what` / `owner` / `brought_by`, thread titles / arguments, the item title) is fixed **once, in place**, at the time of the fix — none of these drive freshness. A later re-segment re-derives `what` / `owner` from the corrected LLM message.

---

## 4. Flags and rollout
- `ATTACHMENT_AGENT_ENABLED` + `ATTACHMENT_AGENT_USERS` (🚨 an empty `_USERS` with the flag on = everyone). Off = today's router behaviour and section text (§2.1).
- The overlay and override **readers** are gated by the same flag (build review, 2026-11-27): rows can only be created through the agent, so for a user without the flag the readers make **zero extra queries**. Rollback = flag off → text fixes and section promotions stop applying (the rows are kept and come back if the flag is turned on again).
- Order: test account (enterprise@rayhanai.com) → owner judges → one real user → everyone. No master push while searches are in flight.

## 5. Cost and time (estimates — measure in P1)
- Per question: Flash, outline + 1–4 reads ≈ $0.003–0.01, 10–40 s added to the router turn (artifact_analyzer on Flash measured $0.005 / 47 s with heavier context).
- Edits: no extra model call (code applies them).
- Re-analysis: one angle map ≈ $0.02 ≈ 2 points, background.
- Saving: a 100-page file no longer goes into the router whole.
- 🚨 Turn time: on an upload turn the router already waits up to ~5 min for the analysis (prod check 286 s), and that wait counts toward the 900 s pipeline limit. The agent's 10–40 s comes on top, before any search; P1 measures the worst case.

## 6. Phases
- **P0 — gold set.** ~25 questions on the test account's files (anonymized), each with the known answer passage: «وين قال…», «اش مكتوب في البند…», a thread question, a name typo. Baseline: today's router unfold (accuracy, tokens, time).
- **P1 — read-only agent + router tool + prompt swap** (flag). Target: gold set ≥ today's accuracy, every returned quote verbatim, big files answered without the whole text.
- **P2 — section promotions + angle-map edits + edit log** (migration).
- **P3 — text-correction overlay + every read hook + derived-wording fix.** The widest surface: one test per hook.
- **P4 — re-analysis** (guidance argument, lease, billing to the current turn).
- Each phase: tests whitelisted in CI, owner OK before any prod migration or deploy.

## 7. Open question

The earlier eight questions are now D13–D20. One is reopened by the same-day decision that a library file **carries its angle map whole** into new conversations:

**A thread edit on a carried map — where does it apply?**
- **(a) Everywhere the map is read** (my recommendation): one map, edited in place, every conversation that carries it sees the change. Simple, matches "the agent owns the attachment" and "upload all the map". Risk: a lawyer who uses the same file for the other party in a second conversation changes the first conversation's client line too.
- **(b) Copy on edit:** the first edit made from a conversation that only carries the map makes that conversation its own copy; later edits there touch the copy. Safer for the case above, but adds a second map per file group and a "which copy wins" rule for «الخيوط» and «بحث موسّع».

Until answered, P2 is built with (a); (b) can be added later without changing the storage of D12/D15.

## 8. Not in this plan
- Other callers of the agent (D5): writer planner, deep-search planner, artifact_analyzer, «بحث موسّع», the merger.
- The rest of `large_document_index.md` (windowed segmenter, map-reduce summary, section picker).
- Merging the five renderers of one file (old cut, core sections, extended marks, angle-map paragraphs, writer bodies).
- Side findings from the 11-27 survey, to be handled separately: the merger never gets conversation memory (`extended_merger/adapter.py:138-149`); the writer does not rebuild «المرفقات» for a statement of claim (`writer/exhibits.py:29`); the planner's `already_covered` can fire on attached files the artifact_analyzer cannot see; extended `SEARCH_INSTRUCTION` describes markers as «〔S…〕» but they are «〔D1·S7 · …〕».
