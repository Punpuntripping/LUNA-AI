# `simple_search` — The Lookup Family — Design Plan

A **fourth agent family** beside `deep_search` / `writing` / `memory`. Its premise is one
sentence: **put ONE legal object in front of the user, cheaply.** Not "research this
question across the corpus" — «افتح لي هذا النظام / هذه المادة / هذا الحكم».

> **Status: DESIGN — not built.** Decisions locked with the user 2026-08-15 across a
> multi-round reflection. Every number in §5 is MEASURED live against
> `dwgghvxogtwyaxmbgjod`; every file:line is verified against the tree at
> commit `3b80faa`.
>
> **Companion plan:** the manual-search fallback tool lives in its own document —
> `.claude/plans/simple_search_manual_search.md`. It is out of scope here.

---

## 0. Locked decisions

| # | Decision |
|---|---|
| **D1** | A **new `agent_family`**, `simple_search`. The **main router** decides between it and `deep_search`. Not a mode inside the planner. |
| **D2** | **Two agents**: `searcher` (owns the whole retrieval loop, can `ask_user`) and `synthesizer` (validates, answers, publishes). |
| **D3** | The synthesizer may **reject and loop back** to the searcher. **3 cycles, per turn, shared pool.** Loop-back spawns a **fresh** synthesizer that starts over. |
| **D4** | **Fan-out = 3 distinct documents** per turn, one synthesizer each, each writing its own WI and its own chat reply, with **minimal** summaries. |
| **D5** | The fan-out unit is the **document**, not the citation. N articles of ONE law → **ONE** synthesizer. |
| **D6** | **Non-integrative.** Comparison («قارن نظام العمل بنظام العمل التطوعي») is NOT this family — it routes to `deep_search`. |
| **D7** | **Six entry levels**, each with its own unfold function AND its own synthesizer prompt: chunks · full regulation · articles · judgments · circulars · services. |
| **D8** | **Unfold means the REAL content**, not deep_search's compressed payloads. Judgments unfold to the **full ruling**, not `cases.summary`. |
| **D9** | **Two locked thresholds per object**: real content above **25k tokens** → revert to summaries; summaries capped at **50k tokens**, position-truncated past it. Truncation is **budget-derived**, not fixed counts, with a **reserved appendix share** so ملاحق are never starved. |
| **D10** | The synthesizer's WI reuses **`kind='agent_search'`**. |
| **D11** | **Two new source types** — `article_full` (عرض المصدر = the full article body) and `regulation_summary` (عرض المصدر = the regulation's summary). The migration ships **before** any code emits the domain. |
| **D12** | **Gates protect the preview, not the agent.** The agent reads full content unconditionally. **Ruling analysis consumes the ungating; regulations are not metered.** |
| **D13** | `agents/tool_repository/fetch_article.py` is **reused as-is** for the article leg. |
| **D14** | Money cost is already captured by the `llm_calls` ledger. No separate cost design. |

---

## 1. Why this family exists

Today every question goes to `deep_search` or nowhere. «اش نظام المنافسات والمشتريات؟» —
a pure lookup with a known answer sitting in one row — burns the entire pipeline:
expander → sector_picker → parallel executors → LLM rerankers → aggregator →
postvalidator. Minutes of wall-clock and a meaningful slice of the user's points, to
retrieve a document we could have opened directly.

The corpus is already navigable by identity. `regulations_v2` has 3,951 regulations with
chunks; `cases` has ~30.5k rulings; `circulars` and `services` are complete wings. What is
missing is an agent that **opens** them instead of **searching** for them.

### 1.1 The routing boundary — TWO tests, both must pass

This lives in the **main router's prompt**. An earlier cut of this section had only Test 2,
which is not sufficient: «اش يقول نظام المعاملات المدنية عن علاقة الإيجار» passes Test 2
cleanly (the user plainly wants to *see* something) and is still deep_search.

#### Test 1 — is the target a WHOLE NAMED object, or a described part of one?

**The six entry levels (§4) ARE the addressable set.** Each is a granularity the corpus
stores as a row: a whole نظام (`regulations_v2`), one مادة by number (`articles_v2`), one
ruling, one circular, one service, one cited ref. If the user names one of those, it can be
opened. If they *describe* what they want found **inside** a document, there is no row to
open — it must be assembled by retrieval, which is deep_search.

| Question | Family | Why |
|---|---|---|
| «اش يقول نظام العمل» | `simple_search` | the whole نظام — a stored object |
| «اش يقول نظام المعاملات المدنية، اهم احكامه» | `simple_search` | still the whole نظام; "main provisions" is an overview of the document, served by the L2 unfold |
| «اش يقول نظام المعاملات المدنية **عن علاقة الإيجار**» | `deep_search` | **the dangerous pair** — same opening, same law, three trailing words apart |
| «اعطيني **تطبيقات** نظام العمل» | `deep_search` | applications need rulings too — several sources |

**The dangerous pair is the practical risk.** Rows 2 and 3 differ only by a trailing
qualifier, so a model will pattern-match «اش يقول نظام X» and route them identically unless
the prompt names the pair explicitly. It does.

Concretely why row 3 cannot be served here: نظام المعاملات المدنية is **716 articles /
178,535 chars**, so the L2 ladder lands on rung 2 and hands the synthesizer chunk summaries
of the entire civil code — a correct answer to row 2 and a useless one to row 3. And
«الشرط التعسفي» is a concept, not a string: only **3** of its 716 articles contain «تعسف»
literally while **22** mention «الإيجار», because the governing provisions live under
الإذعان / الغبن / good-faith rules. That is semantic retrieval by definition.

**Rule:** any qualifier narrowing INSIDE a document — «عن كذا»، «فيما يخص»، «الأحكام
المتعلقة بـ»، «المواد اللي تبين»، «تطبيقات» — moves the request to deep_search.

#### Test 2 — does the user want to SEE it, or to know what it DOES to them?

| Question | Family | Why |
|---|---|---|
| «اش هي المادة ٦٧ من نظام التنفيذ؟» | `simple_search` | they want the article's **text** |
| «اتنفذت عليّ المادة ٦٧ من نظام التنفيذ وصار لي…» | `deep_search` | they want to know **what happens to them** |
| «أنا خايفة من تطبيق المادة ٦٧ عليّ» | `deep_search` | **application**, not identity |

The last two both NAME an article and are both deep_search — **a cited article number is
never by itself a reason to route here.** The planner fetches articles itself when it needs
them (`fetch_article`, registered on the decider at `planner/agent.py:198`), so the
application path loses nothing.

#### The matrix

| | Named whole object | Described subset |
|---|---|---|
| **wants to see it** | **simple_search** | deep_search |
| **wants it applied** | deep_search | deep_search |

**One line:** if you can point at the single stored thing being asked for, and the user
wants to see it — simple_search. Otherwise deep_search.

#### The tie-break — when in doubt, deep_search

Added 2026-08-16. When the two tests genuinely leave the call undecided, **route
deep_search**, because the two errors are not symmetric:

* deep_search on a lookup costs time and points, and **still answers the question** — its
  planner can fetch a named article itself (`fetch_article`, registered on the decider).
* simple_search on a real research question opens ONE document and answers from it alone —
  missing the rulings, the related أنظمة, the context the answer needed. The user gets a
  confident, well-formatted, **incomplete** answer with nothing signalling the gap.

An incomplete answer that looks complete is the worse failure, so the family is
non-integrative by design (§0 D6) and the tie-break points away from it. The prompt also
carries an explicit anti-laziness guard — *apply the two tests first; fall back only when
they leave you undecided* — so the rule cannot collapse into "always deep_search", which
would delete the family's reason to exist.

Also never simple_search: comparison (§0 D6), and **an attached item never decides the
route** — a user who carried a library page in can still ask about something else (§2.3).

---

## 2. Architecture

```
                    ┌─ A: bare query ───────────▶ SEARCHER ─┐
router ──dispatch──▶├─ C: WI-ref attachment ────▶ SEARCHER ─┼──▶ SYNTHESIZER ──▶ chat ± WI
   (simple_search)  └─ B: library object ──────────────────┘        ▲       │
                                                                    └───────┘
                                                          reject → fresh searcher round
                                                          3 cycles, per-turn shared pool
```

### 2.1 `searcher` — Layer 2

Owns the entire retrieval loop. Responsibilities:

1. **Determine the data type** — `regs | judgments | services | circulars | article` — and
   carry it with every downstream request.
2. **Resolve the object.** Deterministic first: `fetch_article`'s title resolver
   (`resolve_regulation_id`) for regulations, `articles_v2` exact-text for مواد.
3. **Manual search** — see the companion plan. Note its scope is asymmetric:
   `fetch_article` is the repo's **only** identity resolver and it covers regulations and
   articles alone. There is nothing deterministic for **judgments, services, or circulars**,
   so for L4/L5/L6 manual search is not a fallback — it is the **primary and only** path.
4. **`ask_user`** when manual search also fails, or when resolution is ambiguous. Reuses
   the deferred-tool + `paused_runs` machinery (`agents/paused_runs.py`, migration 060;
   pattern at `planner/agent.py:140`).
5. **Abort out-of-scope turns back to the router.** When the question turns out not to be
   a lookup at all — it is application-not-identity (mis-routed past §1.1), integrative, or
   simply outside the family's scope — the searcher does not limp through a wrong answer.
   Its decision model carries an `aborted` flag and the orchestrator **re-routes via the
   main router**, exactly the planner's precedent
   (`PlannerDecision.aborted`, `planner/models.py:122-129`).
6. **Hand off identity, not content.** The searcher's product is the **resolved object** —
   ids + level + the preview evidence it matched on — never the unfolded body. The only
   unfold the searcher ever performs is **`unfold(preview)`** (§2.3.2): the snippet-bounded
   candidate lines it reads to pick a ref in case C. The full **`unfold(always)`** is the
   synthesizer's input path (§2.2) and never lived here.

**Invariant — no paraphrase.** Like the router, the searcher does not restate the user's
question. The synthesizer receives the raw message plus the unfolded object.

**Pause slot warning.** `find_open_pause` reads **the single open pause per conversation**.
The searcher and the deep_search planner share that one slot; a searcher pause must
therefore never be opened while a planner pause is live.

### 2.2 `synthesizer` — Layer 3

**Input: `unfold(always)`.** Every synthesizer invocation receives the §4 unfold of its
object under the §5 budget — run by the deterministic unfold layer (plain Python, §5.4) on
the resolved identity, in cases A, B, and C alike. This is why case B can skip the searcher
entirely and lose nothing: the full unfold never belonged to the searcher.

One synthesizer per object. Responsibilities:

1. **Validate** — is this actually the object the user meant? (Wrong نظام, wrong مادة
   number, right title but the executive regulation rather than the law.) On failure,
   reject → the searcher runs again → a **fresh** synthesizer sees the new result.
2. **Answer** in chat, in Arabic.
3. **Decide whether a WI is warranted.** Not every lookup deserves a card. When it is,
   publish `kind='agent_search'` with a **minimal** summary.
4. **Cite.** The object it unfolded becomes one or more references (§6).

**Per-level prompts.** Six variants (§4), keyed exactly like the aggregator's registry —
see §7.1.

### 2.3 The three input cases

**Case A — bare query.** «اش نظام المنافسات والمشتريات؟» Nothing attached. The searcher
resolves from text alone. This is the only case that reaches **manual search**. (`ask_user`
is not case-A-exclusive: case C can also pause when two refs both plausibly match what the
user said — see §2.3.1.)

**Case B — library object attached.** The user was reading a library page and brought it
into the chat, so its identity arrives pre-resolved. See §8.

> **CORRECTED 2026-08-16 — Case B does NOT skip the searcher.** An earlier cut of this
> section said identity arriving pre-resolved meant the synthesizer could be invoked
> directly. That was wrong, for the same reason the §1.1 boundary needs an addressability
> test: **attaching a page is CONTEXT, not a routing decision.** A user can carry in
> نظام العمل and then ask «اش المادة 5 من نظام التنفيذ؟» — the short-circuit answered about
> the wrong document. Worse, the carried item persists as a `kind='references'` workspace
> item, so the router may re-attach it on later turns and the hijack repeats.
>
> **Every turn starts at the router; inside this family every turn starts at the searcher.**
> The pre-resolved identity is still worth resolving at carry time (§8) — it just becomes a
> **candidate handle** the searcher may select, saving a resolution round-trip, rather than
> an answer imposed on the question. So Case B is not a third control path at all: it is
> Case A or Case C with one candidate pre-filled.

**Case C — WI-ref attachment.** «اش الحكم اللي في WI-1 وعن نزاع تاجرين؟ اعطيني تفاصيله»

Both units appear in one flow, which is why the distinction matters:

1. The **router** attaches `WI-1` — a *workspace item*. `MAX_ATTACHED_ITEMS = 7`
   (`agents/models.py:20`) applies here and is **not** changed by this plan.
2. Inside the searcher, the target is **one ref row of WI-1** —
   `workspace_item_references WHERE wi_id = <WI-1>`, the row whose `domain` and title match
   «نزاع تاجرين».
3. The searcher fetches that object's full content. For a ruling, that **consumes the
   ungating** (§7.3).

No corpus search happens in case C. It is a join and a fetch — the cheapest of the three.

#### 2.3.1 Case C must see what the USER sees

**A user names a source by what is printed on the card in front of them.** «اش الحكم اللي
عن نزاع تاجرين» is them reading their own screen. So the searcher's candidate list has to be
built from the panel's rendering — not from the raw row, and not from the existing
agent-facing manifest, because the two have diverged.

| | User sees (`ReferencePanel`) | Agent gets (`unfold_workspace_item` `SourceLine`) |
|---|---|---|
| **regulations** | `referenceLabel` + **`doc_type_raw` chip** (لائحة / تنظيم / دليل / مواصفة قياسية) + ≤500-char snippet | `{regulation name} — {chunk title}` |
| **cases** | title derived from the summary, falling back to **«حكم {court}»** + ≤500-char snippet | `[{case_number}] {FULL summary}` |
| **circulars** | `title` + تعميم chip + snippet | `تعميم: {title} — {entity}` |
| **compliance** | service name; **snippet explicitly blanked** (`references_service.py:298`) | service name |

Three divergences that will actually mis-resolve:

1. **`doc_type` is user-visible and agent-invisible.** The card prints لائحة / تنظيم / دليل
   where the domain label would say نظام (`ReferencePanel.tsx:382-385`). A user who says
   «اللائحة اللي في المراجع» is reading that chip — and the searcher cannot see it at all.
2. **The case card title is derived differently.** The panel derives it from
   `cases.summary` and falls back to «حكم {court}» when the summary is absent
   (`_build_case_shells` docstring, `references_service.py:522-527`). A user quoting
   «حكم المحكمة التجارية» is quoting that fallback — a string that appears nowhere in the
   agent's `[{case_number}] {summary}` line.
3. **The user's snippet is capped at 500 chars**; the agent gets the whole summary. The
   user can only refer to what fits in those 500.

**Measured during the build** (corrections to earlier estimates in this section):

- Case summaries are **larger** than first stated — over the 205 distinct cases actually
  cited by used refs: p50 = **2,375**, p90 = **3,512**, avg 2,419, max 5,785 chars. The
  over-fetch was worse than the plan claimed.
- **58.2%** of used regulation refs (1,794 of 3,081) print a chip other than «نظام» —
  لائحة تنفيذية 602 · دليل 222 · لائحة 209 · قواعد 117 · مبادئ وأحكام 111 · … So
  divergence #1 is the common case, not an edge case.
- The case card's snippet **already opens with «المحكمة: {court} ({level})»**, so the court
  is visible on *every* case card, not only through the summary-less title fallback.
- The «حكم {court}» fallback is rare: only **18 of 30,531** case rows have no usable text
  in `short_summary`/`summary`/`facts`/`ruling`. Still worth building — a user quoting it is
  quoting the only title their card ever had.
- **A fourth, latent divergence:** the panel joins a case ref by `ref_id`
  (`case:<case_ref>`, always populated) while the agent-side resolver joins by `item_id`
  (nullable). A NULL-`item_id` row would render a real card beside a
  «(مصدر غير متوفر)» stub. **Measured 0 NULLs across all 4,466 ref rows**, so it is latent,
  not current — no fallback was built rather than ship an untestable path.

#### 2.3.2 The rule: resolve on snippets, fetch on decision

Case C is **two phases** — the two unfolds, with two different owners — and conflating
them is the mistake to avoid:

| Phase | Unfold | Owner | Surface | Purpose |
|---|---|---|---|---|
| **Identify** | `unfold(preview)` | searcher | title + type chip + a **short snippet** | pick WHICH ref the user means |
| **Answer** | `unfold(always)` | synthesizer input (§2.2) | the full object (§4, §5 budget) | answer the question |

**The preview line is bounded ABOVE by what the card shows — never more.** That single
rule delivers both properties at once: the searcher can match anything the user might quote
(§2.3.1), and it cannot waste budget reading objects it is about to discard. Full content —
and the full summary — belong to `unfold(always)`, **after** the object is chosen.

> **This is a REDUCTION from today's behaviour.** `_resolve_cases`
> (`unfold_workspace_item.py:283-315`) currently emits `[{case_number}] {FULL summary}` into
> the tool result. At p50 = 2,035 chars and p90 = 3,290 chars per summary, a WI with 8 case
> refs spends ~16–26k chars just to let the model pick one. Capping at the card's snippet
> cuts that by roughly 4–6× and loses nothing the user could have referred to — they never
> saw past the cap either.

**Implementation.** Extend the `SourceLine` formatters
(`agents/tool_repository/unfold_workspace_item.py:237-407`) to carry the card's own fields —
`doc_type` for regulations, the panel's derived title including its «حكم {court}» fallback
for cases — and to truncate every body-derived string to the card's snippet cap.

The layering constraint forces the fix here rather than at the panel:
`fetch_item_references_payload` lives in `backend/` and `agents/` must never import from it.
Keep the two renderings tested against each other (§12) so they cannot drift apart again.

**Open — the snippet length.** The card's own cap is 500 chars (`build_snippet`,
`preprocessor.py:364`). Matching it exactly makes parity trivially provable and is the
default here. A tighter cap (~300) would cut identification cost further, at the risk of
dropping a phrase the user could see and quote. Worth one measurement before locking.

> **DECIDED 2026-08-16 — keep the `C1` registry; do NOT migrate to `(WI-N, n)`.**
>
> The question came up because `agent_communication_protocol.md` says it "doesn't invent a
> new short-token mapping table" and already defines `(WI-{seq}, n)` for "one reference
> across WIs". But that protocol governs the **inter-agent I/O surface** — what an LLM emits
> that another agent or the orchestrator consumes. `C1` never appears there. Verified:
> `SearcherDecision.selected` is read at exactly one site (`searcher.py:858`, the searcher's
> own output validator, which resolves handles → `ResolvedObject` before the decision leaves
> the agent); `SimpleSearchRunResult` carries no handles; and the orchestrator contains zero
> candidate-handle references. `C1` is **intra-family scratch state**, not a protocol token.
>
> **The division of responsibility this rests on:**
> * The user wants to discuss **WI-N itself** — what the report says or concluded → the
>   **router** opens it fully via `unfold_workspace_item` and answers directly.
> * The user wants a **source cited inside it** → dispatch `simple_search`. Which source it
>   is, and how the candidates are labelled while the searcher works that out, is the
>   **family's own business**.
>
> So the ref-level handle never has to cross the router↔specialist boundary at all: the
> router names the WI (`WI-N`, protocol-compliant), and the searcher resolves within it.
> Adding `(WI-N, n)` to the dispatch surface would push a decision the specialist is better
> placed to make up into the router — which is §13b-eval's failure mode, in a new costume.
>
> Note also that `(WI-N, n)` has **never been implemented anywhere** — the protocol's own
> status table still lists it ⏳ pending "when writer Layer 2 flow is built". Adopting it
> here would mean *defining* the standard, not conforming to one.
>
> *Naming hazard, no action:* "C1" in `orchestrator.py`'s comments means the **§12a C1
> contract**, not a candidate handle. Same string, two meanings, one file apart.

> **WI ≠ WI-ref.** A **WI** is a card (`workspace_items`, aliased `WI-n`), and it is what
> the router attaches. A **WI ref** is one cited source inside a card
> (`workspace_item_references`, keyed `(wi_id, n)`, rendered `[n]` in المراجع).
> **simple_search's unit is the ref.** It consumes refs in case C and produces refs
> everywhere. The two caps are unrelated.

---

## 3. Model, context, and limits

| Agent | Layer | Slot | Rationale |
|---|---|---|---|
| `searcher` | 2 | `_FLASH_MEDIUM` | Resolution + tool orchestration, not legal reasoning |
| `synthesizer` | 3 | `_FLASH_MEDIUM` | One object in hand; the reasoning is bounded |

`_FLASH_MEDIUM = ModelPolicy("tier_2", primary="deepseek", reasoning="medium")` — the
deepseek-v4-flash head at mid reasoning effort, through the usual `get_agent_model` →
`FallbackModel` chain. Deliberately **not** the aggregator's `_FLASH_MAX`: costing less
than deep_search is the entire premise.

Output ceilings: searcher **16k** (the output is a handful of ids; the headroom is for
reasoning tokens, which count against `output_tokens` on DashScope), synthesizer **24k**
(against the aggregator's 100k — it answers about ONE document).

### 3.1 What each agent's context holds

| | searcher | synthesizer |
|---|---|---|
| system prompt | `SEARCHER_SYSTEM_PROMPT` | one of six, keyed by level |
| **conversation window** | **yes** — instructions block | **yes** — head of the user message |
| candidates / rejections | handles `C1…` + this turn's rejection notes | — |
| the object | — (identity only, never content) | the unfold, up to the §5 budget |
| tools | resolvers · `manual_search` · `ask_user` | none — one call |

### 3.2 The conversation window — both agents, one renderer

**Added 2026-08-16.** An earlier cut passed neither agent any history; `message_history`
appeared only on the pause/resume path. That was an omission, not a decision, and it had a
concrete failure: the router **does not paraphrase** (§2.1), so a follow-up reaches this
family raw. «واللي بعدها؟» after an article answer has its referent one turn up and nowhere
else — the searcher could only `ask_user` for something the user had just said. deep_search
never had this problem twice over: the planner gets `recent_messages` **and** owns
`query_restatement`; simple_search has neither.

Both agents now receive the window, rendered by ONE shared
`prompts.render_recent_messages` so they cannot drift.

Loaded by **`orchestrator._load_recent_messages`, the same loader the planners use**
(`_RECENT_MESSAGES_N = 5`, already built for `MajorAgentInput` in `_dispatch` and simply
handed across). Reusing it is correctness, not convenience: it attaches the provenance and
user-attachment tags, drops empty placeholder rows before taking the window, and **passes
every row through the turn's masking codec**. Rendering from any other source would put
unmasked PII in front of these two agents.

Escaped and fenced as `<recent_messages>` DATA with an explicit read-never-obey line — the
same discipline as the router's `<workspace_items>`, and for the same reason: history is
user-authored text landing in a prompt.

Register both slots in `agents/utils/agent_models.py` (the aggregator's neighbourhood,
~`:189`). The deep_search aggregator runs `_FLASH_MAX`; this family deliberately does not —
the whole point is that it costs less.

Usage limits mirror `ROUTER_LIMITS` in shape (`router/router.py:180`): bounded
`output_tokens_limit`, small `request_limit`, small `tool_calls_limit`.

---

## 4. The six entry levels

**Unfold here means the real content.** deep_search compresses because it ranks across many
candidates; simple_search holds exactly one object and can afford the real thing. What we
take from deep_search is the *pattern* — a per-level function turning a DB row into
agent-facing text with measured caps — not its payloads.

`chunks_v2.corpus` is the body/appendix discriminator. Three values, live-verified:

| corpus | chunks | regs | words |
|---|---|---|---|
| `without_articles` | 28,978 | 2,140 | 12.29M |
| `with_articles` | 14,024 | 1,811 | 5.79M |
| `appendix` | 5,388 | 1,184 | 4.38M |

`with_articles` vs `without_articles` also tells you whether a regulation has rows in
`articles_v2` at all — a level-3 precondition.

### L1 · Regulation chunks
`chunks_v2.content` — the **real body**, plus `title` and `context` for framing.

> **The single biggest gap.** `CHUNK_SELECT` in
> `reg_compliance_search/unfold_reranker.py:79-82` **deliberately omits `content`** —
> deep_search's reranker never sees a chunk body. A fetch-one-object agent must add
> `content` to the select or re-fetch enrich-style (`ura/enrich.py:63-64` already selects
> it). Do not assume the existing helper returns a body.

Reusable: `unfold_chunk_simple` / `unfold_chunk_precise` / `format_chunk` — all exported and
already imported cross-module.

### L2 · Full regulation
A composite, in this order:

```
regulations_v2.llm_summary        (the abstract — typically ~1.3k chars)
regulations_v2.intro              (highly variable: 190 chars … 29,203 chars)
BODY       — corpus <> 'appendix' — all content OR all summaries   (§5 ladder)
APPENDIXES — corpus  = 'appendix' — all content OR all summaries   (§5 ladder)
```

> **Ordering trap.** The canonical document order is
> **`corpus DESC, position, chunk_ref`** — `library_service._ordered_chunk_query`
> (`backend/app/services/library_service.py:2548-2570`). Ordering by `position` alone
> **interleaves ملاحق into the body** (rationale at `:2540-2543`). Because `agents/` must
> never import from `backend/`, this must be **copied**, not imported — and getting it
> wrong is silent: you simply get an appendix in the middle of the نظام.

This level has **no existing agent-side path**. `RegulationSourceView`
(`source_viewer.py:146`) is legacy and `build_source_view` no longer produces it.

### L3 · Articles
`articles_v2.content`, keyed by exact text `(regulation_id, article_number)`.

`agents/tool_repository/fetch_article.py` is reused wholesale — its deps Protocol has
exactly **one** member (`.supabase`), so it drops into the searcher unchanged. Reuse the
full pure layer: `_normalize_title`, `_rank_candidates`, `resolve_regulation_id`,
`fetch_article_result`, and the calibrated constants (`_MIN_MATCH_SCORE = 0.40`,
`_AMBIGUITY_MARGIN = 0.1`).

**One behavioural change.** In deep_search the fetched article is *text only, never a
citation* (`fetch_article_tool.md` §7). Here it becomes a **first-class reference** —
`article_full` (§6). That is the intended difference, not an oversight.

**Fallback (revives a superseded path).** When `articles_v2` has no row for the number,
walk `chunks_v2.owns` (the jsonb MADDA map) to find the chunk that carries the مادة.
`fetch_article_tool.md` §2 records that `articles_v2` *superseded* the `owns` approach —
we are deliberately reinstating it as a **second layer**, not a replacement.

> **Two limits found in the build — the fallback is narrower than this plan implied.**
> 1. **`owns.MADDA` is an INTEGER array; `articles_v2.article_number` is TEXT.** So the
>    fallback is *structurally unable* to serve compound numbers («1-1», «81 مكرر») — which
>    are exactly the ones most likely to be missing from `articles_v2` in the first place.
>    The implementation returns a clean miss rather than a wrong chunk. (Same root cause as
>    the `articleNo` defect in §13 — compound article numbers are a recurring weak spot.)
> 2. **It changes granularity.** The owning chunk carries a *run* of مواد — asking for
>    المادة 14 returns a chunk holding 15–18 as well. The unfold labels this in Arabic so
>    the synthesizer cannot present a multi-article chunk as a single article.

**باب.** There is no `bab` key anywhere in `chunks_v2`. A باب resolves off
**`chunks_v2.title`** — 3,851 chunk titles contain «الباب» and 4,343 contain «الفصل», so
title matching is viable. باب is **not** a new source type; it resolves to a run of chunks
which are cited individually.

### L4 · Judgments
**Full `cases.content`** — the ruling itself.

> This is a deliberate widening. deep_search **never** puts raw ruling text in front of an
> LLM (D2 in `case_topics_loop.md` made `cases.summary` a hard replacement), and the source
> popup shows the ملخص because the full text is the PDPL-sensitive payload the
> `/judgments` wing is noindexed over (`source_viewer.py:178-181`). D12 authorises it —
> the gate protects the preview surface, not the agent — and D12's other half means this
> path **consumes the ruling's unlock** (§7.3). Recorded here so it is a decision, not a
> discovery.

Header metadata from the same row: `court`, `court_level`, `city`, `case_number`,
`judgment_number`, `date_hijri`, `appeal_*`.

**Mandatory:** run `strip_pipeline_sections`
(`deep_search_v4/shared/case_summary.py`) on anything derived from `cases.summary` —
16,505 of 30,531 rows carry a resolver-telemetry appendix and 252 carry a Python traceback.
Use `shared/court_levels.py` for the Arabic court-level label; four independent
hand-rolled ternaries previously mislabelled all 125 supreme rulings as ابتدائي.

### L5 · Circulars
Full `circulars.content` + issuing entity + `circulars.source` link. The user-facing popup
already serves this **uncapped** (`source_viewer.py:529-553`), so the agent side matches it.

> **DEVIATION shipped, flagged for sign-off.** D9 gives "two thresholds per object", but the
> four single-body levels (articles · judgments · circulars) have **no summary twin** — there
> is no rung 2 to fall to. Real rows nonetheless blow the 25k-token ceiling:
> **21 of 51,792 articles** (max 244,419 chars ≈ 88.9k tok), **57 of 30,531 rulings**
> (max 188,472), **4 of 1,843 circulars** (max 168,782). The build therefore applies
> `REAL_CONTENT_MAX_CHARS` as a **position-truncation net on every single-body level**
> (`rung=0`, ladder N/A) — against the letter of "uncapped" above. L1 chunks never need it
> (corpus max 32,052). Flip it if you would rather one 88.9k-token article blow the object
> budget than be truncated.

### L6 · Services
The real content: `intro_title`, `intro_description`, `steps`, `requirements`,
`required_documents`, `service_context`, `service_url`.

> **Constant collision.** `MAX_SERVICE_CONTEXT_CHARS` is defined **twice with different
> values** — `600` in `reg_compliance_search/unfold_reranker.py:73` and `2_000` in
> `ura/services_unfold.py:30`. Importing both into one module is a live hazard. Alias at
> the import site or define a third, local constant.

**Output constraint — REVERSED 2026-08-23 (user).** This section used to carry the
2026-08-03 decision that the answer stays a *well-framed pointer* and never restates a
procedure's steps, because doing so made us the apparent authority on a process we do not
own. That was written when a خدمة was only ever the issuing entity's payload. `/compliance`
changed the premise: **`service_guides` is our own authored guide**, published whole and
ungated, and `unfold_service` now hands it to the synthesizer alongside the card
(`.claude/plans/compliance_service_guides.md` §0). The L6 prompt tells the model to walk it.

What did NOT reverse is `ServiceSourceView` (`source_viewer.py:203-218`): the reference
**popup** is still title-and-link only. That is a different surface with a different reason
— it renders the entity's own steps under our chrome — and it stays bodyless.

**The screenshots reach the model as WORDS.** `guide_md` is written around its screenshots:
324 of 337 guides carry lines that are only a `{guide_ref}_{n}` token. `render_service_guide`
replaces each with `service_guide_images.description` — the Arabic sentence written for this
purpose — at the hole's own position, and DELETES any hole with no image row. No URL, no
storage path, no bytes: a model cannot open an image, and one that thinks it can will invent
what it saw. Budget: `MAX_SERVICE_GUIDE_CHARS` (60,000) for the guide, the remainder of the
68,750 ceiling for the card, so the two can never overrun the level together.

---

## 5. The size ladder

**Two locked thresholds, per object** (not per turn — each of the up-to-3 fanned-out
synthesizers gets its own):

```
REAL_CONTENT_MAX_TOKENS = 25_000   # above this → serve summaries instead
SUMMARIES_MAX_TOKENS    = 50_000   # above this → position-truncate the summaries
ARABIC_CHARS_PER_TOKEN  = 2.75     # one named constant; never scatter //4 heuristics
```

### 5.1 The three rungs

| Rung | Condition | Payload |
|---|---|---|
| **1** | real content ≤ **25k tokens** | Full content — body + appendixes, verbatim |
| **2** | content > 25k, summaries ≤ **50k tokens** | Summaries — `chunks_v2.summary`, body + appendixes |
| **3** | summaries > **50k tokens** | Position-truncated summaries — fill from the first chunk forward until the slice is spent, drop the rest |

Both tests measure **body + appendixes together**; the appendix reservation (§5.3) governs
how the rung-3 slice is *divided*, not whether a rung fires.

`chunks_v2.summary` coverage is **48,388 of 48,390** chunks. There are no gaps to handle.

### 5.2 Where each rung bites — measured, 3,951 regulations

At 25k tokens ≈ 68,750 chars and 50k tokens ≈ 137,500 chars (2.75 chars/token):

| Rung | Regs | Share |
|---|---|---|
| 1 — full content | 3,530 | **89.3%** |
| 2 — summaries | 405 | 10.3% |
| 3 — truncation | **16** | **0.40%** |

Rung-2 median: a regulation that trips the 25k switch carries ~27,310 chars (~10k tokens)
of summaries — comfortably inside the 50k cap. Max summaries anywhere: 489,678 chars.
Body distribution for reference: p50 = 12,185 chars · p90 = 58,959 chars.

**89% of regulations never leave rung 1, and rung 3 is sixteen documents in the entire
corpus.** The ceilings exist for the tail, not the common case. Versus the earlier
single-50k design, the 25k switch moves ~218 mid-size regulations from full content to
summaries — the deliberate trade: a leaner common case for the synthesizer, at zero loss
for the 89% and the same tiny rung-3 tail.

**The appendix count, per rung** (1,184 regs have appendixes at all):

| | Regs |
|---|---|
| Rung 1 with appendixes — full ملاحق text served verbatim | 938 |
| Rung 2 with appendixes — ملاحق summarized alongside the body | 238 |
| Rung 3 with appendixes | 8 of the 16 |
| **Flipped 1→2 BY their appendixes** (body alone ≤ 25k, body+ملاحق > 25k) | **92** |
| Appendix content **alone** exceeds the 25k switch | 72 |

The 92-row line is the proof that both ladder tests must measure body **+ appendixes**
together: score the body alone and those 92 regulations serve full text past the ceiling
the switch exists to enforce.

### 5.3 The appendix reservation

Truncation is **budget-derived** — fill until the slice is spent — **not** fixed counts.
But greedy filling starves one side or the other, and the corpus proves it in both
directions:

- 1,184 regs (30%) have appendixes. p50 = 4,317 chars · p90 = 46,352 chars ·
  **max = 1,161,719 chars.**
- كود البناء السعودي العام (`16a94b17…`): 332 body chunks, 140,746 words, 246,135 chars of
  chunk summaries, **zero** appendix chunks. Body-first greed leaves ملاحق nothing on a
  regulation that has appendixes.
- The 1.16M-char appendix regulation is the mirror image: appendix-first greed starves the
  **body**.

**Design: reserved floors with spillover.**

```
BODY_SHARE = 0.75   APPENDIX_SHARE = 0.25   of the SUMMARIES_MAX_TOKENS (50k) slice at rung 3
```

- Each side truncates **by position** within its own slice.
- **Whatever a side does not use flows to the other** — load-bearing, since 70% of
  regulations have no appendixes at all and must not waste the reservation.
- Start at 75/25 and move it on measurement. Follow the house convention: a named constant
  with a comment stating how it was calibrated (the model is
  `MAX_AGGREGATOR_CONTENT_CHARS` at `case_search/unfold_ura.py:41-44`).

**The 25% reservation is verified against the rung-3 population.** Of the 16 rung-3
regulations, 8 have appendixes, and only **1** has appendix summaries exceeding the
reserved 12.5k-token slice (~34,375 chars; corpus-wide max appendix summaries =
85,626 chars, p90 among appendix-bearing regs = 3,611). So the reservation fully covers
the ملاحق of 15 of the 16 rung-3 documents, and spillover handles most of the last —
75/25 needs no adjustment on current data.

### 5.4 Measuring the budget

The ladder decision is **deterministic** — plain Python, no LLM. The token conversion lives
in the one `ARABIC_CHARS_PER_TOKEN` constant (§5 header).

> **CORRECTED during the build — this was partly impossible as written.** It said decide
> from `word_count` **and** `length(content)` before fetching. **`length(content)` is not
> reachable through PostgREST** — computing it means selecting the column, which *is*
> materialising the body. Only `word_count` exists pre-fetch.
>
> Shipped resolution: a measured `ARABIC_CHARS_PER_WORD = 6.28` (Σchars/Σwords over all
> 48,390 chunks; p50 6.27 · p90 7.09 · p99 8.64) drives a **memory guard** at
> `CONTENT_FETCH_GUARD_FACTOR = 2.0` × the ceiling, and the **real rung comes from an exact
> re-measure after fetch**. A pure `word_count` verdict misclassifies **21 of 3,951**
> regulations (11 false-big, 10 false-small) — without the split, 11 regulations that
> genuinely fit would be wrongly denied rung 1. The guard still caps materialisation at
> ~326k chars against a 1.94M worst case.

### 5.5 OPEN — is the ceiling on the payload or on the rendered document?

§5 says "50k tokens per object" without saying **what is measured**. The build budgeted the
**chunk payload** (what §5.1 "summaries" denotes) and let the L2 frame — headings, title,
`intro`, `llm_summary` — ride on top. Measured overrun on the rendered document:

| Regulation | Rendered | Budget | Over |
|---|---|---|---|
| كود البناء السعودي | 56,067 tok | 50,000 | **+12%** |
| `65e6caee…` | 58,042 tok | 50,000 | **+16%** |

**RESOLVED 2026-08-15 — the ceiling is on the RENDERED DOCUMENT.** What costs money and
consumes context is what enters the prompt, so the budget must mean the thing that enters
the prompt; a ceiling the frame can exceed by 16% is not a ceiling. The ladder therefore
reserves headroom: measure `llm_summary` + `intro` + every heading FIRST, subtract, and give
the chunk payload only what remains. `REAL_CONTENT_MAX_TOKENS` (25k) is likewise a
rendered-document test.

Consequence to accept: a regulation with a 38k-char `intro` has less room for body, which
is correct — the intro is part of what the user asked to see. This is a one-constant change
if the call turns out wrong.

Related, and already handled: **`intro` had no budget** in §4 L2. It reaches **38,638 chars**
live (§4's "29,203" was stale) ≈ 14k unbudgeted tokens, so it is capped separately at
`MAX_REG_INTRO_CHARS = 8_000` (intro p50 1,122 · p99 13,756; 97 regs / 2.5% clip).
`MAX_REG_ABSTRACT_CHARS = 3_000` clips nothing today (`llm_summary` max 2,415).

---

## 6. Sources and references

### 6.1 The two new types

| Type | Backs | عرض المصدر shows |
|---|---|---|
| `article_full` | `articles_v2` row | the **full article body** |
| `regulation_summary` | `regulations_v2` row | the regulation's **summary** |

The other four levels map onto existing domains: chunks → `regulations`, judgments →
`cases`, circulars → `circulars`, services → `compliance`.

### 6.1a The wire contract — PIN. Every layer must match this byte for byte.

Backend Literals, the SQL CHECK, the TS unions and the frontend `Record`s are written by
different hands. They agree only if they agree here first.

| Concept | Article | Whole regulation |
|---|---|---|
| `domain` (DB CHECK + both Literals + TS `ReferenceDomain`) | **`articles`** | **`regulation_docs`** |
| `ref_id` | **`article:<articles_v2.id>`** | **`regdoc:<regulations_v2.id>`** |
| `source_type` | **`article_full`** | **`regulation_summary`** |
| `DOMAIN_META.label` | «مادة» | «نظام» |
| `DEFINITE_DOMAIN_TYPE` | «المادة» | «النظام» |
| Backing table | `articles_v2` | `regulations_v2` |
| `item_id` column holds | `articles_v2.id` | `regulations_v2.id` |

`regulation_docs`, not `regulations` — that name is taken and means **a chunk**.

**SourceView members.** Both mirror `ChunkSourceView` (`source_viewer.py:87-111`), and the
body field **must be named `content`**: `extractSourceContent`
(`ReferencePanel.tsx:1015-1021`) does `"content" in view ? view.content : ""`, so any other
field name silently renders a blank dialog with no copy button.

```python
class ArticleFullSourceView(BaseModel):
    source_type: Literal["article_full"] = "article_full"
    title: str = ""                    # «المادة 81 من نظام العمل»
    article_num: str | None = None     # articles_v2.article_number (text)
    content: str = ""                  # FULL articles_v2.content
    regulation_title: str = ""
    regulation_source_url: str = ""    # parent regulations_v2.landing_url

class RegulationSummarySourceView(BaseModel):
    source_type: Literal["regulation_summary"] = "regulation_summary"
    title: str = ""                    # clean_title or title
    content: str = ""                  # llm_summary, falling back to summary
    regulation_source_url: str = ""    # landing_url
```

The legacy `ArticleSourceView` / `SectionSourceView` / `RegulationSourceView` stay untouched
(§6.1) — do not delete, rename, or re-point them.

> **Naming collision — this fails at import time if ignored.** `article` and `regulation`
> are **already taken** as `source_type` values: the legacy `ArticleSourceView`
> (`source_viewer.py:120`) and `RegulationSourceView` (`:153`), the `Reference.source_type`
> Literal (`aggregator/models.py:36-45`), and the frontend union
> (`frontend/types/index.ts:702`). Reusing those names gives the Pydantic discriminated
> union at `source_viewer.py:237-248` **duplicate discriminator values**.
>
> **Locked: use `article_full` / `regulation_summary`.** The legacy members stay in place
> for reload compatibility of pre-v3.0 persisted `source_view` payloads.
>
> Worse on the frontend: that legacy arm is a permissive `[k: string]: unknown` bag
> (`types/index.ts:702-705`), so a new `article` variant would **typecheck against the
> wrong arm** and render through the fall-through at `ReferencePanel.tsx:1100-1105` as
> bare markdown — with no error, *looking like it works*.

### 6.2 The `reg:` prefix trap

`domain='regulations'` hard-assumes the id is a **`chunks_v2.id`**
(`references_service._reg_chunk_id_from_row:443-456`, `_build_reg_shells:472-510`).

If a full-regulation ref carries `reg:<regulations_v2.id>` or an article ref carries
`reg:<articles_v2.id>`: the uuid check passes, the row inserts cleanly, the read path finds
nothing, prunes the shell at `references_service.py:504-509`, and renders a stub with no
عرض المصدر. **Zero errors anywhere in the chain.**

**Therefore: distinct `domain` values AND distinct `ref_id` prefixes.** Proposed
`article:<articles_v2.id>` and `regdoc:<regulations_v2.id>`.
`_parse_simple_ref_id(prefix, ref_id)` (`source_viewer.py:281-288`) is generic and works
for both.

### 6.3 Migration order is not negotiable

`workspace_item_references.domain` is a hard CHECK, currently
`('regulations','compliance','cases','circulars')` (migration 102).

The scar is documented in the repo: `persist_item_references` writes all refs of a WI in
**one batch insert** and the publisher **swallows the exception by design**, so one
out-of-CHECK row previously took the whole batch down — four `agent_search` items shipped
with `metadata.ref_count=9` and **zero** reference rows, rendering with no المراجع section
at all.

That specific failure is now mitigated: `references_service.py:1029-1050` retries row by
row, so only the offending refs are lost. But they are lost **silently** (ERROR log only,
return value ignored by the publisher at `:214`).

**The migration widening the CHECK ships before any code emits the new domain.** There is
already a regression test for exactly this shape at
`backend/tests/test_references_service.py:399-440`.

### 6.4 The `[n]` invariant

Reference numbers are assigned **in code, before the LLM runs** — the central
anti-hallucination mechanism (`aggregator/models.py:29-33`). The model only *selects*.
simple_search must not regress this: the synthesizer receives pre-numbered references and
chooses among them.

Two prompt rules to carry **verbatim** from `aggregator/prompts.py`:

- **Western digits only inside `[n]`** (`:47`, `:92`) — Arabic-Indic digits break the
  clickable link.
- **`[n]` is reserved for references** (`:64`, `:95`) — article numbers go bare in prose
  («المادة 81», never «[81]»).

---

## 7. Reuse, and what not to touch

### 7.1 Prompt registry

Copy the **pattern**, not the content: a module-level `dict[str, str]` plus a getter that
**raises `KeyError` listing the available keys** (`aggregator/prompts.py:691-718`). No
silent default.

Structure each of the six as the same f-string sandwich the aggregator uses:

```
_SHARED_ROLE  +  <per-level body>  +  _CITATION_RULES
```

Write our own `_SHARED_ROLE` / `_CITATION_RULES` pair — the aggregator's footer hard-codes
`used_refs`/`gaps`/`confidence` and sub-query "sufficiency" semantics that a lookup has no
concept of. Keep the two digit/citation rules from §6.4 verbatim.

**Do not inherit `check_structure`'s prompt_key coupling** (`postvalidator.py:140`); the
`prompt_mode_*` keys are absent from its recognised list, so it silently passes ~95% of
production traffic (`:546-548`).

Per project convention, prompts are edited **in the `.py`**; `agents/prompts/*.md` is a
generated catalog — regenerate via `scripts/extract_prompts_md.py`.

Escape every interpolated user-controlled value with the `_esc` discipline
(`aggregator/prompts.py:24-32`, mirrored at `router/router.py:436`). That is the
XML-forgery injection defence, not cosmetics.

### 7.2 Drop-in reusable

| Component | Where |
|---|---|
| `make_json_salvager` | `agents/utils/structured_output.py:123-159` — generic, 3-line opt-in. **`retry_msg` must name OUR fields**; copying the aggregator's misdirects the retry |
| `persist_item_references` | `references_service.py:868` — takes a plain `list[Reference]`; pass `ura_results=None` and it degrades cleanly |
| `Reference` | `aggregator/models.py:27-135` — **do not fork.** `ReferencePanel.tsx` and the whole read path are keyed to this exact shape |
| `render_aggregator_content` + `AggregatorItem` | `preprocessor.py:224` / `ura/schema.py:176` — `AggregatorItem` is flat and methodless, so construct one from a raw row and call the renderer. **No URA needed** |
| `extract_cited_numbers`, `strip_thinking_block` | `postvalidator.py:105`, `:98` |
| `fetch_article.py` pure layer | whole module |
| `strip_pipeline_sections`, `court_levels.py`, `services_unfold.py` | as noted in §4 |
| `create_workspace_item` + the `workspace_item_created` SSE shape | already generic over `kind` |
| `paused_runs.py` + the deferred `ask_user` pattern | `planner/agent.py:140` |

### 7.3 Gating and unlocks

- **The agent reads full content unconditionally.** Gates exist to protect the public
  preview surface, not to keep bytes from the model.
- **Judgments consume the ungating.** A ruling opened by simple_search spends the same
  single unlock the `/judgments` page uses, so a user who unlocked it there does not pay
  twice.
- **Regulations are not metered.**
- `reference_resolver.py:202-239` already resolves `reg:<chunk>` to either
  `('article', '{reg_id}#{n}')` or `('regulation', reg_id)`, and **both content types
  already exist in the ledger vocabulary** (`library_items_service.py:88-90`). The new
  types short-circuit straight to them — **no new metering type is needed.**

**Fix the charge-before-build ordering.** `workspace.py:381-473` charges at step 4
(`resolve_access`, `:433`) and builds at step 5 (`build_reference_source_view`, `:450`). Any
source that fails to build therefore **costs a real unlock and returns a 404**. Rulings are
the metered thing and the two new domains land directly in this path — so build first, then
charge.

### 7.4 Do NOT reuse

| Component | Why |
|---|---|
| `AggregatorInput` | demands `sub_queries: list[RerankerQueryResult]`; `from_ura` derives domain from `ura.produced_by` |
| `preprocess_references`, `build_aggregator_user_message` | both walk the URA |
| `correction.py` | its gap block reads `sq.sufficient`; with no sub-queries `gap_honesty_ok` is trivially true. Lift `_citation_correction_block` (`:94-109`, ~15 lines) alone if citation self-correction is wanted |
| `validate_llm_output` | 4 of 7 checks need `AggregatorInput`. Write a ~20-line validator: `extract_cited_numbers` + a dangling check |
| `publish_search_result` | hard-wires `agent_family="deep_search"` (`publisher.py:184`) and `subtype="legal_synthesis"` (`:77`), and `SearchPublishInput` carries five deep_search-only fields. Write a **~60-line sibling publisher** reusing `create_workspace_item` + `persist_item_references` directly |
| `artifact_builder` | degenerates to `content = synthesis_md` (`DEFAULT_DISCLAIMER_AR` is now `""`). Keep only its `ref_count` / `cited_count` metadata keys, which the frontend reads |
| The DCR chain, `cli.py`, `replay.py`, `log_parser.py` | ~1,900 lines of A/B research apparatus |

---

## 8. Case B — the library carrier

Today the authed CTA «تحدّث مع ريحان عن هذه الصفحة» links to a **bare `/chat`**
(`AskRayhanWidget.tsx:426`), carrying nothing. The object identity is lost at the door.

**Nothing needs inventing — the blog chip already proves the whole path.**
`blog_service.create_blog_item` (`:698-781`) does: public object → server-side snapshot →
`workspace_items` row with `summary` pre-filled → `attachment_ids` → `message_attachments`
→ visible as a summary on every later turn and unfoldable on demand.

And `ask_service.fetch_grounding(supabase, page_type, page_id)` (`:488-512`) **already takes
the exact `(pageType, pageId)` pair the widget is holding**, including the
`reg_slug/article_slug` shape for articles.

**Build:** a `create_library_item` twin of `create_blog_item`, bodied by `fetch_grounding`,
deduped on `metadata.source_page_type` + `source_page_id`.

**Content shape (D-user):** «طلب مستخدم رئيسي» in spirit — the reg's **summary**, the
judgment's **summary**, `service_context` for services. **Never the whole regulation.**

> ⚠️ **OPEN — the shipped body is NOT summaries, and this needs the user's call.**
> Measured live: `fetch_grounding` grounds a **regulation on its first four body chunks**
> (6,000 chars of نظام العمل) and a **judgment on a composed narrative**
> (الملخص/الوقائع/الطلبات/الأسباب/المنطوق, 2,076 chars) — neither is
> `regulations_v2.llm_summary` nor `cases.summary`. `MAX_CONTEXT_CHARS = 6000` means the
> hard constraint above ("never the whole regulation") holds by construction, so nothing is
> unsafe. But if «summary» was meant literally, reusing `fetch_grounding` is the wrong
> source and Case B needs a different body builder.
>
> Related: **`fetch_grounding` has no `service` branch at all** (only
> regulation/article/judgment/blog), so «`service_context` for services» is currently
> unreachable — it falls into the unsupported-type refusal. Its own docstring
> (`ask_service.py:494`) claims five grounded types including `service`; that docstring is
> stale, a pre-existing one-line defect.
>
> Also corrected: "**nothing in production creates it**" (of `kind='references'`) is **false**
> — two legacy rows exist (2026-05-19, 2026-06-18, title «مراجع», empty `content_md`). They
> carry no `metadata.source_page_*`, so they cannot collide with the dedup key. The
> **uncapped** claim IS confirmed empirically: a 16th `note` was rejected with
> `workspace_items_cap_exceeded` while a `references` row inserted in the same breath.

**Kind: `references`.** The enum member exists (`026_workspace_items.sql:29-31`), is handled
by `workspace_context.py:233-238`, and **nothing in production creates it.** Critically it
is **uncapped** — the 15-item cap counts only `agent_search | agent_writing | note`
(`031_artifact_cap.sql:41`) — so library objects do not crowd the workspace.

**Anon return path** already exists: `post-login-intent.ts` + `AuthGuard.tsx:152-157`
(`chat_with_blog`) does create-convo → import → navigate. Add a fourth intent variant.

**New-chat carry** clones `pendingBlogTokens` (`chat-store.ts:208-209`, `:432-433`,
`:581-599`) plus the two drain effects (`ChatInput.tsx:444-450`, `:379-385`) and the
`onError` clears (`app/chat/page.tsx:62-67`). Mind the documented ordering constraint at
`ChatInput.tsx:440-443`.

**Coverage today: regulation · article · judgment · blog.** `fetch_grounding` returns `""`
for `circular`, `form`, `calculator`, `topic`, and there is **no `/services` route** and no
`service` member of `LibraryPageType`. Circulars and services reach simple_search via cases
A and C only. Building those grounders is **deferred**, not forgotten.

**Zero backend plumbing on the send payload.** `SendMessageRequest`
(`backend/app/models/requests.py:166-174`) has exactly one non-text field, `attachment_ids`,
and `_insert_attachment_links` filters only on ownership — it already accepts any owned
`workspace_items.item_id`. `_estimate_ocr_pages` already skips non-`attachment` kinds, so a
library object costs **zero OCR quota** by construction.

---

## 9. Traps

| # | Trap | Where |
|---|---|---|
| 1 | **Migration before code.** A new `domain` before the CHECK widens → refs dropped silently row-by-row | `references_service.py:1029-1050` |
| 2 | **Name collision** — `article`/`regulation` already discriminate the SourceView union; duplicates fail at **import time** | `source_viewer.py:237-248` |
| 3 | **Frontend legacy arm** absorbs a new `article` variant with **no compile error** and renders bare markdown | `types/index.ts:702-705`, `ReferencePanel.tsx:1100-1105` |
| 4 | **`reg:` assumes a chunk id** — a reg/article uuid inserts cleanly and renders a dead stub | `references_service.py:443-456`, `:504-509` |
| 5 | **Unknown domain drops the row entirely** on read — the `[n]` card never renders and the inline marker goes dead | `references_service.py:240-242` |
| 6 | **Charged-then-404** — `resolve_access` runs before the view builds | `workspace.py:433-457`; also `:433-435`, `:780-798` |
| 7 | **Appendix interleave** — ordering by `position` alone puts ملاحق inside the body | `library_service.py:2540-2570` |
| 8 | **No chunk body** in the reranker select — `content` deliberately absent | `unfold_reranker.py:79-82` |
| 9 | **`MAX_SERVICE_CONTEXT_CHARS` defined twice** — 600 and 2,000 | `unfold_reranker.py:73`, `services_unfold.py:30` |
| 10 | **One open pause per conversation** — searcher and planner share the slot | `agents/paused_runs.py` |
| 11 | **Service-role client required** for enrichment reads; the anon key hits RLS and returns empty `in_()` silently | `ura/enrich.py:13-15` |
| 12 | Sync supabase client **must** be wrapped in `asyncio.to_thread` | house pattern |

Two pre-existing one-line bugs sit in files this work touches — fix them in passing:
`blog_service.py:668` silently drops `circulars` on blog import, and
`retrieval_artifacts_service.py:113` omits `circular:` from `_REF_PREFIXES`.

---

## 10. Files

### New
| File | Contents |
|---|---|
| `agents/simple_search/__init__.py` | |
| `agents/simple_search/searcher.py` | agent, deps, `ask_user`, resolution orchestration |
| `agents/simple_search/synthesizer.py` | agent factory keyed by level |
| `agents/simple_search/prompts.py` | `SYNTHESIZER_PROMPTS` dict + `KeyError` getter, six variants |
| `agents/simple_search/models.py` | pure pydantic — decision, output, level enum |
| `agents/simple_search/unfold.py` | the six `unfold(always)` functions + the §5 ladder (`unfold(preview)` lives in `unfold_workspace_item.py`'s `SourceLine` formatters — §2.3.2) |
| `agents/simple_search/runner.py` | the 3-cycle loop, fan-out, per-turn pool |
| `agents/simple_search/publisher.py` | ~60-line sibling publisher |
| `shared/db/migrations/1XX_simple_search_ref_domains.sql` | widen the CHECK + column comment |
| `backend/app/services/library_item_service.py` | `create_library_item` |

### Modified
| File | Change |
|---|---|
| `agents/models.py` | add `"simple_search"` to the `agent_family` Literal (`:66`) |
| `agents/router/router.py` | prompt section — the §1.1 identity/application boundary |
| `agents/orchestrator.py` | dispatch branch |
| `agents/utils/agent_models.py` | two slots |
| `agents/deep_search_v4/source_viewer.py` | two new SourceView members + `_fetch_article_by_id` / `_fetch_regulation_by_id` + `__all__` + `_self_test` |
| `agents/deep_search_v4/aggregator/models.py` | `Reference.source_type` + `domain` Literals |
| `backend/app/services/references_service.py` | `by_domain`, two `_build_*_shells`, two `_fetch_*_by_id`, two id-parsers, `build_reference_source_view`, `persist_item_references` chain, `_stub_reference` |
| `backend/app/services/reference_resolver.py` | short-circuit the two new prefixes |
| `backend/app/api/workspace.py` | reorder build-before-charge |
| `agents/tool_repository/unfold_workspace_item.py` | two `SourceLine` formatters + `by_domain` |
| `frontend/types/index.ts` | `ReferenceDomain`, `ReferenceSourceType`, `SourceView` |
| `frontend/components/workspace/ReferencePanel.tsx` | `DOMAIN_META`, `DEFINITE_DOMAIN_TYPE`, `SourceViewContent`, `referencePrimaryUrl`, `extractSourceContent` |
| `frontend/components/library/blocks/AskRayhanWidget.tsx` | the two dead `/chat` links |
| `frontend/stores/chat-store.ts` + `ChatInput.tsx` | library-ref chip + carry slot |

---

## 11. Build order

Ship in this sequence; deviating re-runs the migration-102 failure.

1. **Migration** — widen the domain CHECK.
2. **Unfold layer** (`unfold.py`) + the ladder. Unit-testable without an agent.
3. **Source types** — `source_viewer` → `references_service` read → write →
   `reference_resolver` → route ordering fix.
4. **Frontend types + ReferencePanel** (the exhaustive `Record`s fail loudly — good).
5. **The two agents** + prompts + publisher.
6. **Router prompt** + `agent_family` + orchestrator dispatch.
7. **Case B** — `create_library_item`, widget CTAs, chip + carry.
8. **Manual search** — per the companion plan.

---

## 12. Tests

- Ladder: rung selection at each boundary; the appendix reservation with (a) no appendixes,
  (b) a body-heavy reg, (c) ~~the 1.16M-char appendix reg~~ → **`65e6caee…`**, and
  **(d) the flip case** — body alone ≤ 25k but body+ملاحق > 25k must land on rung 2 (92 real
  regs have this shape; scoring the body alone is the measured failure mode).

  > **Fixture correction.** `0cf48da9…` (1,161,719 chars of appendix *content*) cannot test
  > the reservation: it holds only **89,497 chars of appendix summaries**, under the 137,500
  > ceiling, so it lands **rung 2 with zero truncation**. It is an excellent **(d)** flip
  > fixture. The only regulation in the corpus that actually exercises spillover is
  > **`65e6caee…`** (155,141 chars of summaries; body 91,876 whole, appendixes truncated
  > 45,440 of 63,265).
- Document order: assert `corpus DESC, position, chunk_ref` — a ملحق must never appear
  between two body chunks.
- Each of the six unfolds against a real row.
- Refs: one test per new domain mirroring
  `test_references_service.py:654-697`, which explicitly asserts the
  `"unknown domain"` warning does **not** fire.
- The batch-reject fallback (`test_references_service.py:399-440`) with a new domain.
- Fan-out: 3 documents → 3 WIs, 3 chat replies; N articles of one law → **1** synthesizer.
- Loop: rejection → fresh synthesizer; pool exhausts at 3 turn-wide, not per synthesizer.
- Router routing: the three §1.1 sentences land in the right family.
- Case C: WI-ref join resolves the right row; a ruling fetch consumes exactly one unlock.
- **Case C parity (§2.3.1–2.3.2)** — assert **both bounds**, per domain:
  - *at least the card* — the `doc_type` chip, the case title including its «حكم {court}»
    fallback, every string the panel renders;
  - *and no more than the card* — a case line must **not** contain summary text beyond the
    snippet cap. Regression-guards the over-fetch `_resolve_cases` does today.

  This pair is what stops the two renderings drifting apart again.

---

## 11a. The `agents/` → `backend/` rule, stated accurately

Earlier drafts of this plan said "**`agents/` must NEVER import from `backend/`**" and passed
that to the build agents. **That is over-broad and it cost real work** — it pushed the
`unfold(always)` and `unfold(preview)` authors into *copying* `_ordered_chunk_query` and the
case-shell logic that they could legitimately have imported, creating duplicated logic that
can now drift.

Measured: **12+ modules under `agents/` import from `backend/`**, including
`agents/agent_search/publisher.py` — which makes the exact three imports
(`decode_for_persist`, `create_workspace_item`, `persist_item_references`) that
`simple_search/publisher.py` needs. The real, narrower rule is the one written at its only
source, `ura/schema.py:84-86`: a **constant duplicated for parity** with a public-page
window is duplicated *deliberately* so the retrieval core carries no backend dependency.

Working rule for this family:

| Layer | May import `backend/`? |
|---|---|
| **Publishers / persistence** (`publisher.py`) | **Yes** — `agent_search/publisher.py` is the precedent, verbatim |
| **Tools that write app state** (`save_memo`, `edit_supabase_md`, `fetch_article`) | **Yes** — all three already do |
| **Retrieval + unfold core** (`unfold.py`, `manual_search.py`, rerankers, URA) | **No** — keep it dependency-free and duplicate the few constants, as `schema.py` does |

The copies already made in `unfold.py` are therefore correct for *that* layer and should
stay; the rule was only wrong where it was applied to the publisher.

---

## 12a. Wave-2/3 wire contracts — PIN. Parallel agents code against these.

Same discipline as §6.1a: interfaces written by different hands agree only if pinned first.

### C1 · Runner entry point (`agents/simple_search/runner.py`)

The orchestrator's dispatch branch codes against exactly this; the runner author implements
it. Mirrors `run_router`'s shape (`router/router.py:700`).

```python
async def run_simple_search(
    question: str,
    supabase: SupabaseClient,
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    *,
    attached_items: list[dict] | None = None,   # case B/C payload; [] for case A
    user_preferences: dict | None = None,
    user_call_name: str | None = None,
    welcome: "WelcomeState | None" = None,
    emit_sse: "Callable[[dict], Awaitable[None]] | None" = None,
) -> "SimpleSearchRunResult": ...

@dataclass
class SimpleSearchRunResult:
    chat_messages: list[str]           # one per synthesizer, in fan-out order
    created_item_ids: list[str]        # WIs published this turn (may be shorter)
    sse_events: list[dict]             # workspace_item_created, drained by _route
    paused: bool = False               # True when the searcher called ask_user
    question_text: str | None = None   # the ask_user question, when paused
    aborted: bool = False              # out of scope → orchestrator re-routes via router
```

### C2 · Manual-search tool (`agents/simple_search/manual_search.py`)

House tool-module pattern (`tool_repository/fetch_article.py`): a pure layer plus a
registration entry point. The searcher **calls `register_manual_search(searcher_agent)`**;
it does not implement the tool.

```python
def register_manual_search(agent) -> None: ...          # @agent.tool inside
async def manual_search_core(supabase, query: str,
                             data_type: str) -> list[dict]: ...   # pure, testable
```

`data_type` ∈ `regs | judgments | services | circulars | article`. Failure contract is a
**plain string return, never `ModelRetry`**. Ambiguity mirrors `fetch_article`'s
`AMBIGUOUS:` convention. Full design: `.claude/plans/simple_search_manual_search.md`.

### C3 · Case-B route

```
POST /api/v1/conversations/{conversation_id}/library-items
body  { "page_type": "regulation|article|judgment|blog", "page_id": "<slug>" }
200   { "item": { "item_id": "...", "title": "...", "kind": "references" } }
```

Twin of `POST /conversations/{id}/blog-items` (`backend/app/api/blog.py:631-657`). Body from
`ask_service.fetch_grounding(supabase, page_type, page_id)`. Dedup on
`metadata.source_page_type` + `metadata.source_page_id`. Frontend sends the returned
`item_id` in the existing `attachment_ids` array — **no send-payload change**.

### C4 · `article_no` is a STRING everywhere

`articles_v2.article_number` is TEXT and carries «1-1», «81 مكرر» (487 of 51,792 rows —
§13). The `number` typing is the bug. Pin: **`str | None`** in Python, **`string | null`**
in TypeScript, at every hop — the backend `unlocked` payload, `ReferenceUnlockInfo.article_no`
(`types/index.ts:1399`), `UnlockedNoticeInput.articleNo` (`gate-copy.ts:387`). `arNumber()`
takes a `number`, so the article path needs a string-safe formatter (Arabic-Indic digit
mapping without `Math.round`).

---

## 13b. Wave-2/3 build log (2026-08-15/16)

**Wave 2 — all five lanes landed.** `searcher`/`synthesizer`/`prompts`/`runner`/`publisher`
+ two `agent_models` slots · `manual_search.py` · the Case-B carrier backend
(`library_item_service.py`, `api/library_items.py`) and frontend (chip, CTA, carry slot,
4th post-login intent) · the three wave-1 follow-ups.

**Wave 3 — both agents died on a session limit having written nothing usable; completed
inline.** Wiring: `agents/models.py` (`simple_search` on the `agent_family` Literal),
`router.py` (the §1.1 identity-vs-application prompt section), `orchestrator.py` (the
dispatch branch). Case-B identity bridge: `build_simple_search_object` (below).

Verified by the coordinator: `agents/simple_search/tests/` **326 passed** ·
`backend/tests/test_library_item_service.py` **33 passed** · full `backend/tests`
**1661 passed / 2 failed** (the known `test_wave_8b_legacy_removal.py` pair) ·
`agents/router/tests/` 2 failures **proven pre-existing by stashing the diff** (they assert
the Arabic «سياق القضية الحالية» against an injector since translated to English).

### Decisions taken inline

* **Pause needs no `run_id` on C1.** The runner writes its own `paused_runs` row, so the
  branch just raises `_SkipRunRecord()` and the orchestrator's existing handler re-queries
  via `_find_awaiting_user` and emits `agent_question` with the real id.
* **`aborted` does NOT re-route via `_route`.** The planner's abort does, but it fires on
  the RESUME leg where the router has not just run (`_resume_major_agent:~1110`). In fresh
  dispatch the router is what sent us here, so recursing risks a loop; the turn answers
  honestly in place and the user's next message routes fresh.
* **Fan-out concatenates.** Up to 3 synthesizer replies join into the turn's single
  assistant message — the SSE contract is one message per turn, so separate `token` yields
  would append to the same bubble regardless.
* **`LEVEL_SOURCE_TYPE["service"]` corrected at source** from `"compliance"` (a *domain*)
  to `"gov_service"`; every service reference would otherwise have failed `Reference`
  validation at construction. The runner's local override is now empty.

### Case-B identity bridge (closes the §2.3 gap)

`create_library_item` persisted only a **slug**, but `runner.resolved_from_attachment`
needs row ids — so Case B silently degraded into a Case-A search, defeating the feature.
`library_item_service.build_simple_search_object` now resolves ids at carry time and writes
`metadata.simple_search_object`. Round-trip verified through the runner's own reader:

| page_type | level | minted ref_id |
|---|---|---|
| `regulation` | `regulation_doc` | `regdoc:<regulations_v2.id>` |
| `article` | `article` | `article:<articles_v2.id>` |
| `judgment` | `judgment` | **`case:<case_ref>`** |

The judgment row deliberately carries **`case_ref`, not `cases.id`** — `case:` refs have
always keyed on the ref (`ura/enrich._enrich_cases`), and writing the uuid would mint a
ref_id that resolves to nothing. Every failure path returns `None` on purpose: falling back
to the searcher is cheap and correct; opening a guessed document is neither. `blog` has no
simple_search level and always returns `None`.

### Still open after wave 3

1. **Migration 136 is UNAPPLIED** — verified live again. Until it is, every `articles` /
   `regulation_docs` ref this family writes is dropped row-by-row, silently (§9 trap 1).
   **This is the ship blocker.**
2. **`ResolvedRef.article_no` is still `Optional[int]`** (`reference_resolver.py:133`,
   `int()` at `:306`) — wave 2's `workspace.py` view-fallback covers the workspace reveal,
   but **`blog.py:317` still sends `null` for compound مواد**. See §13.
3. **`unlocked.title` is still the composite** for `article:` refs (§13).
4. **No tests written for the wiring itself** — router routing of the three §1.1 sentences,
   the dispatch branch, fan-out→messages, `aborted`, and `paused`. The pieces they exercise
   are individually tested; the seam is not.

---

## 13b-eval. THE convergent finding — the router answers instead of dispatching

Two independent evals (Case B and Case C) failed on the **same rule**, from opposite
directions. This is the single highest-value finding of the whole exercise.

**Case B's A/B is the cleanest evidence** — 32 real router runs, identical sentences, the
attachment the only variable:

| input | result |
|---|---|
| «اش يقول نظام العمل؟» — **no attachment** | `dispatch · simple_search` **3/3** |
| «اش يقول نظام العمل؟» — **carried page attached** | **`chat`, 0/3** — 1,014 chars answered off the 6k snapshot |
| «اش يقول هذا النظام؟» (deictic) | **0/4**, all chat |

**0 of 7** attached whole-object questions reached the family. Case C hit the mirror image:
all four «اش الحكم اللي في WI-N…» became `chat_response`, the router restating the 500-char
snippet as «التفاصيل». Comparison 0/3.

**Root cause:** `router.py:217` ("questions about the content of a prior report → answer
directly") plus the Necessity check at `:207`. A `kind='references'` carried page renders
into `<workspace_items>` indistinguishably from an agent-authored report, and Case C's own
§2.3.1 snippet work is *what makes answering directly look feasible* — we improved the
manifest and thereby taught the router it did not need the specialist.

**FIXED 2026-08-16 (prompt only).** The rule was over-scoped, not wrong:
* answering directly is now scoped to **what a prior report concluded**;
* opening a **source or document** («اعطيني تفاصيل هذا الحكم»، «اش يقول هذا النظام»،
  «ابغى نص المادة») is explicitly excluded → `simple_search`;
* the test is stated as *"are you asked what WE wrote, or what the LAW says?"*;
* **"a summary in front of you is not a reason to answer"** — summaries exist to identify
  and route, and restating a 500-char snippet as «التفاصيل» is named as the router's most
  common failure;
* a carried library page (`kind='references'`) is declared a **SOURCE, not a prior report**;
* Necessity now says "possible" means you actually know the answer, not that some text is
  in context.

**Not yet re-measured** — the A/B fixtures exist in `agents/simple_search/eval/`, so the
fix is verifiable by re-running them.

---

## 13c. Eval findings — resolution + routing (2026-08-16)

Full report: `agents_reports/simple_search_eval_resolution.md`. Labeled fixture run against
the live corpus on `xl0rch` (scratch convo `86a76749…`, hard-deleted, verified 0 rows).

### Routing PASSES — 42/43 (97.7%). No prompt change needed.

**The dangerous pair holds 12/12** across five different laws. An adversarial set added
after the textbook forms scored 6/6 also passes **11/11** — including qualifiers §1.1 never
enumerates («بخصوص»، «حول»، «في موضوع») and one narrowing variant with **no trigger word at
all**. Overview requests dressed in narrowing language («ملخص»، «محتويات»، «اهم نقاطه»)
correctly stayed `simple_search`. The single miss («أنا خايفة من تطبيق المادة 67 عليّ»)
became a `ChatResponse` asking which law — defensible, and not a boundary violation.

### Resolution FAILS — refusal precision 67% (manual) / 82% (deterministic)

**The three thresholds are NOT mis-calibrated** — the plan's headline row reproduces
exactly («نظام الفساد المالي والإداري» → `not_found/singleton_below_floor`, cov 0.50, score
14.79). **Do not tune `_MIN_TITLE_COVERAGE`**: moving it trades these false positives for
false negatives on the 22 fixtures that currently resolve correctly. The bugs are in the
**ladder composition**, not the gates.

| # | Bug | Evidence |
|---|---|---|
| **1** | **The ILIKE recall rung can win Gate 2 alone.** It hard-codes `score=0.0` ("carries no ranking signal") but `decide` gates on **coverage only** — and every ILIKE row contains the query substring, so coverage is high *by construction*. All **3 of 3** `score==0.0` wins in the set were on must-refuse fixtures. | The absent law resolves onto «الترتيبات التنظيمية…لمكافحة الفساد المالي والإداري» at **medium confidence** |
| **2** | **A rung-① false resolve blocks the rung holding the truth.** "Advance only if not resolved" stops the ladder one rung short. | **Verified in SQL:** «نظام الإقامة المميزة» has **0** rows in `search_index`; its لائحة has **1**. BM25 can only return the لائحة, scores coverage 1.00, resolves — and never reaches the ILIKE row carrying `pin=True` on the actual نظام. `det` and `manual` then return *different documents* for the same query, both confidently, with nothing arbitrating |
| **3** | **`_fetch_reg_candidates` normalizes before RANKING but not before FETCHING** — its own docstring says the raw string is the ILIKE pattern. A user-typed leading «ال» excludes the right row from the pool entirely, then the distinctive-token fallback picks something unrelated. | «النظام العمل» → **«النظام الصحي»** |
| **4** | **`_query_terms` doesn't split the «و» conjunction.** | one space in «نظام المنافسات **و** المشتريات» drops the flagship law to **rank 6**, behind five documents that merely cite it |
| **5** | **No digit/ordinal normalization on the article leg.** `shared/privacy/codec.normalize_digits` already exists, unused. | Arabic-Indic «٨١» and the ordinal «الحادية والثمانون» both miss |

Bugs 1 and 2 are the critical pair: together they turn "I could not find that law" into a
confident wrong document. Neither is a threshold problem, so neither is fixed by tuning.

---

## 13m. §13j fix wave — LANDED (2026-08-17)

All five §13j findings fixed. Two agents, disjoint lanes (family internals /
`orchestrator.py`), both coding against the §13l pin. Combined suites verified by the
coordinator: **458 passed** (`agents/simple_search/tests` 402 + `agents/tests` 56), one
pre-existing collection error (`test_cost_ledger`, `tier_of_subagent` absent at HEAD).

| §13j | Fix as landed |
|---|---|
| **1 money bug** | Pause branch runs `_finalise` first — answers delivered, cards published — THEN `paused=True`. Orchestrator streams them and **persists two assistant rows (answer first, question LAST)**, because `message_service` deletes the placeholder on `agent_question` and the frontend discards the bubble — *streaming is not delivery*. Question-last is load-bearing for `_pause_is_current`. |
| **2 resume** | Pause rows carry `message_history` + `deferred_payload` (incl. the `C1…Cn` registry — restored pre-run or a resumed case-C pick fails validation). `resume_simple_search` rehydrates via `DeferredToolResults` and re-enters the SAME `_answer_loop` as a fresh turn. **Never raises** — an unrehydratable row degrades to a fresh lookup over question+reply. |
| **3 swap** | Runner: occupied slot ⇒ no row, question inline, `paused=False`. Orchestrator: `question_text` authoritative; a foreign `_find_awaiting_user` row is **never emitted, never resolved** (emitting with empty run_id would route the reply into whoever holds the slot). |
| **4 spend surface** | `unlock_notes` captured **at the resolver seam**, not `_finalise` — a charged-then-rejected ruling (unlock-01's exact loss) still surfaces. Fan-out order, deduped, `charged` OR-ed. One Arabic line, correct number agreement, chat-only. **Semantics: GRANTS ONLY** — a refusal is not `charged=False` (that reads as "already unlocked"); refusals surface via the §13l.5 message. |
| **5 refusal** | Refused judgment groups short-circuit before any agent exists; the runner emits identity + the per-reason Arabic line deterministically. Audited: none of the refusal strings lie. Counts as an answer, not a rejection — no retrieval cycle burned. |

**Inter-agent notes worth keeping:**
* A resumed searcher that asks AGAIN delivers the second question inline (its own row still
  holds the slot during resume) — so the orchestrator's ask-again-on-resume branch is
  defensive dead code today. Harmless; becomes live only if chaining is ever wanted
  (resolve-before-call).
* The persistence home for fix #1 is the **orchestrator**, deliberately: `message_service`'s
  placeholder-deletion is correct for planner pauses, and teaching it this family would
  leak family knowledge into a service that has none. Revisit only if a third family needs
  deliver-then-ask.

**Still open, by choice (the §13j residue):**
* A *served* synthesizer still denies its siblings exist in a partial fan-out
  (unlock-03's other half) — §2.2 gives it one document and no notion of being one of N.
  Fixing it means threading fan-out context into `build_synthesizer_user_message`.
* C1 v2 has no `run_id` field (orchestrator reads it back from `paused_runs` — safe,
  inferential); `unlock_notes` has no consumer yet beyond the reply line.

---

## 13l. C1 v2 — PIN for the §13j fix wave (2026-08-17)

Two agents build against this in parallel with **disjoint files** (family internals vs
orchestrator). Byte-exact, like §12a.

```python
@dataclass
class SimpleSearchRunResult:
    chat_messages: list[str]
    created_item_ids: list[str]
    sse_events: list[dict]
    paused: bool = False
    question_text: str | None = None
    aborted: bool = False
    abort_reason: str = ""
    # NEW — every judgment access this turn, in fan-out order. Surfaces the
    # spend C1 could not express (§13j #4). charged=False ⇒ already unlocked.
    unlock_notes: list[dict] = field(default_factory=list)  # {"case_id": str, "charged": bool}
```

**Semantics, binding on both sides:**

1. **Deliver-then-ask (§13j #1).** `paused=True` MAY carry non-empty `chat_messages` —
   answers already produced (and charged for) are DELIVERED, never discarded. The
   orchestrator streams them BEFORE emitting `agent_question`.
2. **`question_text` is authoritative (§13j #3).** When set, the orchestrator emits IT in
   the `agent_question` SSE — never the row `_find_awaiting_user` returns, which may be a
   different (older, planner) pause.
3. **Occupied slot ⇒ no pause row (§13j #3).** If an open pause already exists for the
   conversation, the runner does NOT record a second one: the searcher's question goes out
   as the last `chat_messages` entry with `paused=False`, and the user's answer arrives as
   a fresh routed turn. A lost answer-channel beats a swapped question.
4. **Pause rows are resumable (§13j #2).** When written, they carry `message_history` +
   `deferred_payload` and `agent_family='simple_search'`. The family exposes
   `resume_simple_search(user_reply, pause_row, supabase, user_id, conversation_id,
   case_id, *, emit_sse=None) -> SimpleSearchRunResult` (agent 1 provides, agent 2 calls
   from `_resume_major_agent`).
5. **Refused groups never reach a synthesizer (§13j #5).** A judgment group whose access
   came back refused gets its Arabic quota/refusal line emitted deterministically by the
   runner — an LLM must never be handed an empty body to explain, because it explains it
   as «غير موجود».
6. **The spend is acknowledged (§13j #4).** When ≥1 `charged=True` this turn, the final
   reply carries one Arabic line naming how many rulings were opened from the user's
   balance.

---

## 13k. Adversarial retest, lane 2 — family + corpus (2026-08-16)

Report: `agents_reports/simple_search_adv_family.md` · data: `adv_family_results.json`
(flushed per probe). 19 probes: 14 PASS · 2 pass-not-diagnostic · 1 pass-one-layer-later ·
1 PARTIAL · 1 FAIL. Ledger verified clean by **set-diff on `unlock_id`** — 0 added, 0
missing (the lane switched off raw counts mid-run after watching lane 3's rows appear and
vanish beneath its reads; `ledger.delta` is unreliable when two lanes share a ledger).

**Headline — the §13g abort guard is correct in BOTH directions, live:** «اش الحكمين اللي
في WI-2؟» → `aborted=False`, 2 synthesizers, 2 replies, 2 cards. «قارن الحكمين اللي في
WI-2» fed straight to `run_simple_search` → `aborted=True`, Arabic `abort_reason`, **0
synthesizers, 0 cards, 0 ledger rows** — the expensive direction is the free one.
`corpus-01`: appeal-on-the-same-row gave ONE document, no phantom-plurality abort.
`hair-03`: same-document comparison did not abort and one synthesizer actually wrote the
comparison. `corpus-02`: the searcher generalized the باب strategy to the 14-article range
unprompted — resolved the parent نظام once instead of burning the tool budget, and labelled
its coverage.

**The FAIL — ordinal fallback on an absent `[n]` (B1b).** Panel prints `[4][5][7][11]`;
«افتح المصدر رقم 3» **confidently opened `[7]` — the third candidate in internal order** —
and answered as المصدر رقم 3. No hedge, no ask. The `[n]`-prefix fix covers the hit case
(B1/B2 pass); the miss case needed its own rule. **FIXED same day** — the searcher's
candidates block now says: if the requested number appears on no line, say so or
`ask_user`; NEVER fall back to counting positions.

**The PARTIAL — the searcher gets a card's refs but never the card's own identity
(casec-04).** The `agent_writing` premise was unfounded (17/17 candidates, 0 NULL
`item_id`) — but «اش الحكم اللي في المذكرة؟» hunted for a source *named* مذكرة among the
refs. «المذكرة» IS the attached card. **FIXED same day** — the runner now prepends each
attached card's own line («البطاقة المرفقة WI-N "title"…») to the candidates block, which
is also where the WI-N ↔ C-handle vocabulary gap (lane 3's blocker) gets bridged.

Also: `casec-05` dedup happens at `group_documents`, not the collector — the money property
holds one layer later than the fixture claims. `hair-01`'s two replies each re-read
«الحكمين» inside their own document (siblings are invisible — cosmetic, inherent to D5).
Two fixture premises were corpus-refuted and corrected: نظام العمل has **16 أبواب** (bab-03
now asks for العشرين), and «نظام العلم» is an **exact title hit** — the flag law — not a
typo (corpus-05 now uses «المعاملات المدينة»).

Same-day inline fixes after all lanes closed (nothing was measuring live): the two prompt
rules above, the casec-04 card-identity lines, and lane 3's #6 (`_deferred_question` now
parses JSON-string `ToolCallPart.args` — the user was shown `{"question": …}` twice).
**383 tests passing.**

---

## 13j. Adversarial retest, lane 3 — money + state (2026-08-16)

Report: `agents_reports/simple_search_adv_money_state.md`. Restoration verified by the
coordinator in SQL: **17/53 unlocks, newest 2026-08-14, only the pre-existing user pause
row remains, 136 conversations.** The lane's own money sentence: *of the 5 rulings the
ledger was charged for during measurement, the user would have received 1.*

### The findings, ranked — every one needs a decision or a fix

| # | Finding | Severity |
|---|---|---|
| **1** | **Charge-then-pause-discard (`unlock-02`, worse than predicted).** Three unlocks charged (17→20, the WI's exact three rulings) — then the turn returned `paused=True` with **zero replies and zero cards**, asking the user to re-attach the very report whose rulings it had just billed. Mechanism isolated at zero cost (`unlock-02b`): `run_simple_search`'s pause branch returns `_empty(…)`, **discarding answers already produced and already paid for**. A pause with answers in hand must either deliver what is answered alongside the question, or the charge must move to delivery. | **money bug** |
| **2** | **`simple_search` pauses cannot resume at all (`state-02` "passes" for the wrong reason).** The orchestrator's resume leg (`:764-781`) has no simple_search branch, and independently `_record_searcher_pause` never writes `deferred_payload` (confirmed live). The searcher's `ask_user` is **write-only machinery**: the user is asked a question whose answer goes nowhere. | **functional gap** |
| **3** | **The pause-slot collision is a silent question SWAP, not a clobber (`state-01`).** No second row, planner row intact — but the `_SkipRunRecord` handler re-queries `_find_awaiting_user` and never reads `ss_outcome.question_text`, so the user is shown the *planner's* months-old question and their reply resumes deep_search. The searcher's question is silently lost. | state bug |
| **4** | **`unlock-01` silence is structural.** Both rulings charged on a mis-resolution; and `SimpleSearchRunResult` has **no field** for charges — nothing could surface the spend even if the prompt wanted to. C1 needs a `charged` surface before any UX fix is possible. | design |
| **5** | **`unlock-03`: partial fan-out is not mush — it is self-contradictory.** The served ruling publishes a card while the refused synthesizers' replies **assert the other rulings and the attached report do not exist**. §2.2's per-document isolation speaking, not the quota. The refusal prompt must say «نفدت وحدات الفتح», never «غير موجود». | prompt bug |
| **6** | **`question_text` reaches the user as raw JSON** — `runner._deferred_question` returns `ToolCallPart.args` verbatim when it is a JSON string: `{"question": "…"}` observed twice live. The planner path uses `args_as_dict()` with a comment naming this exact hazard. Invisible to all 383 tests because `_fmodels.py` passes dicts. One-line fix. | cosmetic-critical |
| **7** | **The searcher cannot resolve a `WI-N` alias in the question.** «…اللي في WI-2» made it pause — its handles are `C1…Cn`; `WI-N` is the router's vocabulary. Recorded because it *blocked the money fixture*; the searcher prompt needs one line mapping "the attached item the user calls WI-N" onto its candidate list. | prompt gap |

**None of these are fixed yet** — lane 2 is still measuring live, and editing production
mid-measurement would contaminate its probes. Fix order once it lands: 6 and 7 are
one-liners; 1 and 2 are the ones with teeth (1 is the money bug, 2 makes `ask_user`
honest); 3–5 ride along.

---

## 13i. Adversarial retest, lane 1 — routing (2026-08-16)

Report: `agents_reports/simple_search_adv_routing.md` · data: `adv_routing_results.json`.
57 scored runs (53 correct), scratch deleted + verified, **nothing charged** (17/53).

**The §13g comparison gate: 0/9 → 9/9.** Same three sentences, same WIs. And the mechanism
is the right one: **all nine runs unfolded the cited item first** (up to 19,867 chars in
context) **and dispatched anyway** — the "you must not compose the comparison yourself"
half of the patch is doing the work, not a keyword reflex.

| Block | Result |
|---|---|
| A · comparison re-measure | **9/9** (was 0/9) |
| B · battery (hairline/باب/control/casec-02) | 29/33 |
| C · regression sentinels | **15/15** — dangerous pair 6/6, باب routing 6/6, `ctrl-*` clean |

**`hair-01`'s trap did not fire** — «اش الحكمين اللي في WI-2؟» routed simple_search 3/3
against the same 18-ruling WI whose «قارن الحكمين» goes deep 3/3. The one-word boundary
holds at the router.

**`hair-04`'s trap was REFUTED, and the fixture was wrong, not the router.** The bare form
never reaches the qualifier rule (no law named → the known «من أي نظام؟» ask). The
law-named probe went simple_search **3/3**: the qualifier rule does not over-fire on a
مادة. Fixture corrected in place.

**`hair-02` is the one real miss (2/3):** one run asked *which two rulings* instead of
routing — contradicting the scoped Ambiguity rule in its own words. Non-deterministic;
the comparative phrasing (not the plurality) is the trigger.

**Corpus correction to the lane's own finding:** it reported bab-02's لائحة and نظام as
absent from `regulations_v2`. **Refuted in SQL — both exist under MALFORMED titles**:
«نظام المنافسات **و** المشتريات الحكومية» (the detached-waw row) and «**الائحة** التنفيذية
لنظام المنافسات» (missing a ل — a *new* malformed-title class the «ال»-stripping normalizer
mishandles: «الائحة»→«ائحة» ≠ «لائحة»). Recorded on the fixture.

Also notable: `ctrl-02`'s setup revealed the account holds **zero** simple_search-published
WIs — the family has never run for this user in prod. The lane published one real card and
deleted it after; the router answered follow-ups from it correctly (3/3, no re-dispatch).

---

## 13h. Adversarial battery (2026-08-16) — hunting the seams, not the rules

The earlier fixtures test the rules as written; `agents/simple_search/eval/fixtures_adversarial.py`
hunts the queries that fall BETWEEN them. **24 fixtures across 7 surfaces** (hairline 4 ·
باب 3 · case_c 5 · corpus 5 · unlock 3 · state 2 · control 2), **8 already CONFIRMED against
code or corpus** before any eval ran. Two were fixed on the spot:

1. **The `[n]` was dropped from Case-C candidate previews** — `preview = lines.get(n)` used
   the SourceLine *text* and discarded `n`, so «افتح المصدر رقم 3» (the user reading the
   `[3]` printed on their own المراجع panel) had nothing to match. Fixed: previews are now
   `[n]`-prefixed, the searcher prompt says the `[n]` **is** the panel's number, and
   `test_case_c_preview_carries_the_users_citation_number` pins it.
2. **باب was missing from the router's addressable set** — Test 1's closed list had no
   باب/فصل, so «اعطيني الباب الثالث من نظام العمل» would deep_search a query the family was
   designed to serve (§4 L3; 3,851 «الباب» + 4,343 «الفصل» chunk titles). Fixed at three
   layers: the router set now names it; the searcher's How-to-resolve says a باب request
   resolves the **parent نظام** (no باب resolver exists — the L2 unfold carries the أبواب);
   and the whole-reg synthesizer variant scopes its answer to the named باب and must not
   renumber or guess a missing one.

The six other CONFIRMED-but-unfixed entries, by weight:

| id | The confirmed fact |
|---|---|
| `unlock-01` | A synthesizer-rejected ruling's unlock **was already charged at unfold time**; no refund path exists. The user pays for our mis-resolution. **Design decision, not a bug fix.** |
| `unlock-02` | A 3-judgment fan-out spends **3 unlocks in one message**, silently — on `/judgments` each is an explicit click. |
| `state-01` | `find_open_pause` holds **one slot per conversation**; a searcher pause on top of an open planner pause is reachable and untested (§9 trap 10). |
| `corpus-01` | `cases` stores appeal on the **same row** — «الحكم الابتدائي والاستئنافي» is one document wearing two names; the abort rule can fire on phantom plurality. |
| `corpus-02` | «المواد من 77 إلى 90» = 14 resolves vs `tool_calls_limit=10` — the range dies mid-budget or silently under-serves. |
| `hair-03` | Router (all comparison → deep) and searcher (same-document comparison is NOT integrative) **disagree by design** on «قارن المادة 77 بالمادة 78» — recorded trade-off, either path must still answer. |

The PREDICTED entries are the next eval's worklist — notably `hair-01`
(«**اش** الحكمين» — one word from «قارن الحكمين»; does the new abort guard over-fire?),
`hair-04` (the qualifier rule firing on «وش تقول المادة 77 **عن** التعويض» where a مادة is
the atom and nothing exists below it to search), and the two `ctrl-*` guards that make sure
the fixes didn't eat the router's legitimate direct answers.

---

## 13g. The comparison gap — the fan-out cannot serve an integrative question

Found by the user, 2026-08-16, from the re-run failures. **Two independent problems, not
one**, and the second is structural rather than a prompt bug.

### The trap

If «قارن بين حكم الابتدائية وحكم الاستئناف اللي في WI-2» *did* reach this family, both
rulings resolve perfectly — resolving them was never the problem. Then `group_documents`
gives **2 groups** (different `case_ref`s), so **2 synthesizers open one ruling each and
never see the other.** The user gets two unrelated summaries concatenated, no comparison,
two workspace cards — and **an unlock spent per ruling** to not answer the question.

Both routing tests pass on it: two addressable objects (Test 1 ✅) the user wants to see
(Test 2 ✅). Only the *integrative intent* disqualifies it, and that was a footnote.

### The fix — three layers, because the router alone is measurably leaky (0/9)

1. **Router prompt.** Comparison/weighing/ranking is now its own gate with the four real
   failing queries verbatim, and states outright that **both tests can pass and it is still
   not this family**. Adds the second half the eval exposed: *you must not compose the
   comparison yourself either* — the manifest gives ~500 chars per ruling, enough to
   identify and nowhere near enough to rank by strength of سند.
2. **Searcher — the structural guard.** The searcher is the **only** agent that ever sees
   "one question, N documents", and that signal was unused. `SearcherDecision.aborted` now
   names the integrative case explicitly, with the distinction that matters:
   «اش نظام العمل واش نظام التنفيذ» is **two independent lookups → fan out**;
   «قارن نظام العمل بنظام التنفيذ» is **one integrative question → abort**.
   The test is whether the documents are asked about *separately or against each other* —
   not how many there are.
3. **`aborted` now goes somewhere.** It previously answered in place, which for a comparison
   is the worst outcome — a dispatch spent for a degraded message. The orchestrator now
   hands off **directly to `deep_search`**, deliberately NOT via `_route`: the router just
   chose this family and, measured, would choose it again (0/9), so recursing risks both a
   loop and a second wrong choice. Straight to deep_search is deterministic and terminal —
   the router established it is a legal question, the searcher established it is not a
   lookup, one family remains. `abort_reason` is additive on §12a C1 so a turn that
   silently changed family stays traceable.

**Not fixable at the synthesizer:** by the time one runs it holds a single document and has
no knowledge of its siblings. The guard must sit upstream of the fan-out.

Tests: `test_an_integrative_comparison_aborts_instead_of_fanning_out` (asserts
`synthesizer_runs == 0`) and `test_two_independent_lookups_still_fan_out` — the second is
what stops the guard swallowing the case the user explicitly asked for. **382 passed.**

---

## 13f. RE-RUN results (2026-08-16) — measured after every fix

All three re-run lanes died on a **session limit**, but had already written their result
files. Numbers below are read from those files, not from agent prose. Account restored to
exact baseline afterwards (136 conversations · 179 WIs · 1,522 refs · 53 unlocks · 0
scratch); **nothing was charged** — judgment unlocks stayed at 17, newest 2026-08-14.

### Case C routing — the headline unknown, ANSWERED

`case_c_rerun_routing_results.json`, 3 repeats/case:

| expected → got | | |
|---|---|---|
| **simple_search → simple_search** | **11/12** | was 0/4, then 1/7 after patch 1 |
| deep_search → **chat_response** | **0/9** | comparison **still broken** |
| writing → writing | 3/3 | ✅ |

**The twin patch (`:327`/`:331`/`:330`) is what fixed Case C.** Patch 1 alone moved 0/4 → 1/7;
patching the verbatim copy moved it to 11/12.

**Still open — comparison of cited sources.** «قارن الحكمين اللي في WI-N» goes to
`chat_response` 9/9, never deep_search. Same family as §13b-eval: the router can see both
rulings' snippets in the manifest and concludes it can compare them itself. The
answer-directly rule was scoped for *opening* a source; **comparing two of them is a third
shape neither patch covers.**

### The tie-break did NOT over-fire — 21/24, zero leakage to deep_search

`rerun_routing_tiebreak_results.json`:

| class | expect | got |
|---|---|---|
| whole named law | simple_search | **6/6** |
| whole + «اهم احكامه» | simple_search | **6/6** |
| article + named law | simple_search | **6/6** |
| **article, bare (no law named)** | simple_search | **3/6** — the other 3 → `chat` |

**Not one case leaked to deep_search**, which is what over-firing would have looked like.
The three misses go to `chat` and are almost certainly the router asking «من أي نظام؟» —
defensible, and the same shape as the planner's own "article cited but law not named → ask"
rule (`fetch_article_tool.md` §5.1b).

### Resolution — the fix lane's numbers reproduce independently

`rerun_resolution_results.json`, 37 fixtures, both legs:

| class | n | det | manual |
|---|---|---|---|
| **tp_article** | 8 | **8/8 PASS** | **8/8 PASS** |
| tp_full_reg | 10 | 9 PASS | 9 PASS |
| fp_lookalike | 4 | 3 PASS | 3 PASS |
| fp_described | 4 | 2 PASS, 2 SOFT | 4 SOFT |
| fp_absent | 5 | 1 WRONG_DOC | 1 WRONG_DOC |

**Exactly one wrong-doc commit per leg**, and they are precisely the two the fix lane
predicted would survive: `det` on fp-01 «نظام الفساد المالي والإداري», `manual` on fp-05
«نظام الذكاء الاصطناعي السعودي».

### The runtime article path — my own fix, confirmed 9/9

`rerun_runtime_article_results.json` exercises `resolve_article` (the tool the searcher
actually calls), not `_fetch_article_content`. All nine PASS: «81» · **«٨١»** ·
**«الحادية والثمانون»** · «67» · «1» · «1-1» · **«25 مكرر»**, plus both negatives (absent
article 9999, absent law) correctly returning nothing.

### fp-05 vs reg-10 — structurally inseparable, CONFIRMED

`rerun_structural_pair_results.json`:

| | reg-10 — correct: **resolve** | fp-05 — correct: **refuse** |
|---|---|---|
| top_coverage | **0.75** | **0.75** |
| gate | coverage | coverage |
| confidence | medium | medium |
| top_score | 14.30 | **15.88** ← the WRONG one scores higher |
| n_candidates | 1 | 5 |

Identical on every gate feature the resolver uses, and the wrong answer scores higher.
The candidate count also **inverts** the earlier "a singleton is the most dangerous case"
heuristic — here the singleton is the correct one. **No coverage floor, score floor, or
singleton rule can separate this pair.** Any fix must use a different signal (e.g. checking
that the resolved title actually contains the query's head noun, or an existence probe),
not a threshold.

---

## 13e. Fix round (2026-08-16) — what closed, what is still open

`agents/simple_search/tests/` **379 passed** · `agents/tool_repository/tests/` 133 ·
`backend/tests/` **1696 passed / 2 pre-existing**. Every number below was re-verified by
the coordinator, not taken on report.

### Closed

| Item | Result |
|---|---|
| **§13d #2 — D12/§7.3 unlock** | **CHARGED, and structurally so.** `render_judgment` is the only function that writes `cases.content` into a prompt and now refuses without a `JudgmentAccess`; `_synthesize_group.judgment_access` is **keyword-only with NO default**, so a forgotten wiring is a `TypeError`, not a free ruling. Verified by `inspect.signature`. Live: 0 → refusal with no body; charge → +1 row, body served; **same ruling again → delta 0**; an already-unlocked ruling → free, its row untouched; a نظام → unchanged. Sharing is structural — `UNIQUE (user_id, content_type, content_id)` + `ON CONFLICT DO NOTHING`. The one ledger row written was hard-deleted; ledger byte-identical to pre-run. |
| **§13d #3 — F3 `regulation_id`** | Batched identity join, once per call. 12/12 on the test WI, **1,126/1,127 corpus-wide**; 12 keys → 3 groups → **1**. D5 restored. |
| **§13d #4 — F4 card titles** | Titles now come from the backing row, not the preview line. On the 18-ruling panel: title==preview **0/18**, «قضية:» prefix **0/18**, `##` markdown **0/18**. Chip retention **1,117** — exactly the eval's chip-bearing count, so parity survives the write. |
| **Case B titles (§13-CaseB #1)** | judgments **10,000/10,000 diverging → 0/10,000**; mid-word cuts 460 → **0**; articles 800/800 → **2/800**; live HTTP spot-check 15/15 judgments, 14/14 blogs. |
| **Case B dedup** | Normalized `metadata.source_page_key`. Live: **8 carries of 4 objects → 4 rows** (was 8). |
| **Case B identity shapes** | All bridgeable shapes resolve; the one genuinely ambiguous shape (bare article slug — «المادة-80» exists in ~1,769 أنظمة) withholds identity instead of guessing, and every downgrade now logs. |
| **Uncarded `[1]`** | Root cause was `runner.py`: the chat body was appended **before** the `wi_warranted` gate, while `_CITATION_RULES` requires `[n]` unconditionally — so a not-worth-a-card answer was *invited* to cite and shipped uncarded. Markers are now stripped when no card is published (prose kept; a dead citation is worse than none). Two tests pin both directions. |

### Two corrections FROM the agents, worth recording

1. **My brief was wrong about the judgment title.** I said use `judgment_display_title`; the page H1 is `judgment_subject` — `display_title` appends « — {court} {year}هـ» and is the `<title>`/hub-card form. Confirmed in the function's own docstring. Using the one I named is *what causes* the 100% divergence, and the المراجع panel had already settled it the same way.
2. **Eval finding F-7 (blog title) was a FALSE POSITIVE.** It compared against `postHeadline`, which feeds `<title>`/OG — not the heading. Live over all 100 posts: the current chain matches the rendered `<h1>` **100/100**; `postHeadline` matches **9/100**. "Fixing" it would have created the defect it appeared to remove.

### Still open

* **Case C routing — not re-measured since the second patch.** The first fix hit `:217`; the verifier found a **verbatim twin at `:327`** plus `:331` (which described the Case-C sentence shape exactly while omitting `simple_search` from its outcomes) and `:330` (item-disambiguation being generalised to *sources inside* items). All three are now patched, and the Ambiguity check is scoped so the router stops disambiguating on the specialist's behalf. **Re-run `agents/simple_search/eval/` to confirm.**
* **Blog carries inject dangling `[n]`** — 25/25 sampled bodies, 95/100 live posts. `_ground_blog` returns the post's answer text while `blog_posts.references` are not carried with it. Two candidate remedies (strip, or append the post's own numbered المراجع bounded by `MAX_CONTEXT_CHARS`); **a product decision, not taken.**
* **`eval/case_c_unlock.py:57` is stale** — calls `unfold()` with no resolver, so it now reports a refusal. Re-point it at `runner.judgment_access_resolver` and it becomes a standing live regression check.
### §13c retrieval ladder — all five fixed, no threshold moved

`_MIN_TITLE_COVERAGE`, `_MIN_MATCH_SCORE`, `_AMBIGUITY_MARGIN` are byte-identical.
37 fixtures, both legs, same scorer:

| | `det` before → after | `manual` before → after |
|---|---|---|
| Refusal precision | 81.8% → **81.8%** | 66.7% → **86.7%** |
| Resolve recall | 71.4% → **90.5%** | 72.7% → **86.4%** |
| Wrong-doc commits | 3 → **1** | 4 → **1** |

`tp_article` 75% → **100%** on both legs. **Recall dropped nowhere**, and the calibration
singleton («نظام العمل التطوعي السعودي») still resolves.

Three corrections the lane made to the eval's own diagnosis, each measured:
* **Bug ① 's stated mechanism was wrong.** The full-string ILIKE branch returned **0 rows**
  for all three false positives — they came from the distinctive-**token** retry. So the
  rung was split (`_RECALL_ONLY_RUNGS = {"ilike_token"}`) rather than ILIKE banned; a
  blanket ban scored identically on the eval but broke the services fallback.
* **Bug ②'s obvious fix costs a true positive.** Running the whole rung unconditionally
  broke the calibration pair's positive half, so only the token retry stays gated.
* **Bug ③'s early-return was a second instance of itself** — the raw pattern returned five
  لوائح *citing* the law, which looked like success, so the variant holding the law was
  never tried.

Blast radius measured before touching «و»: 13 of 3,951 titles carry a detached waw, and
collapsing them creates **zero** new duplicate-normalized groups (32 before, 32 after);
«زارة» outside «وزارة» has **0** victims in this corpus.

**Fixed by the coordinator afterwards — the tree's THIRD copy of the article key.**
`searcher.fetch_article_identity` had its own `.eq("article_number", …)`, and it is the
**runtime** path: the eval measures `_fetch_article_content`, so «٨١» read as fixed while
`resolve_article` — the tool the searcher actually calls — still missed it. It now shares
`article_number_keys`, with a test over «٨١» / «الحادية والثمانون» / «81» / «1-1» that also
pins the raw key being tried first.

### Two remaining wrong-doc commits are threshold-limited, not composition bugs

`det` on «نظام الفساد المالي والإداري» (difflib 0.5806, clears the 0.40 floor) and `manual`
on «نظام الذكاء الاصطناعي السعودي» (coverage 0.75, BM25 15.88, singleton). The second is
**structurally identical to the calibration positive** — coverage 0.75, BM25 rung, singleton
— with the opposite correct answer, so **no coverage floor can separate them.** For the
first, 0.5806 vs the positive's 0.8792 *would* separate at ~0.6–0.7, but that is a threshold
move needing its own calibration run. Reported, not taken.

**Also open:** bug ①'s fix turns three confident wrong answers into **candidate tables**,
not `not_found` — an LLM could still pick C1 wrongly. The stricter alternative was measured
and costs true positives on the «نظام المنافسة السعودي» shape. One line at
`manual_search.decide()`'s `recall_only` branch if the stricter behaviour is wanted.

**Latency, measured:** no trigram index on `regulations_v2`, so each ILIKE pattern is a
seq scan over 3,951 rows × 2 columns at ~370 ms. A leading «ال» or waw-initial token adds a
second (~760 ms); a full miss plus token retry is ~1.14 s worst case. Durable fixes (a
single `.or_()` round trip, or a normalized indexed column) are out of that lane.
* The `search then write` gate draws its proposal on an ordinary إنذار letter that `:279` explicitly exempts. Writing lane, not this one.

---

## 13d. Eval findings — Case C on real conversations (2026-08-16)

Report: `agents_reports/simple_search_eval_case_c.md`. Read-only over `xl0rch`'s real
corpus (135 WIs / 1,522 refs); scratch convo `3101cee8…` hard-deleted and verified; corpus
byte-identical afterwards.

**Headline: the searcher works; the things around it do not.**

**PASSES**
* **Selection 13/13** across all four domains, including 5/5 on an 18-ruling panel picked
  by the phrase a user reads off the card («مكب النفايات»، «مغاسل عبر تطبيق إلكتروني»).
  Three regulation picks were made **on the `doc_type` chip alone** — strings that did not
  exist in the manifest before §2.3.1. Chip exposure measured **736/1,117 (65.9%)**, above
  the 58.2% recorded earlier.
* **Parity — both bounds hold** over all 135 WIs / 1,345 used refs: chips 1,117/1,117,
  case titles 53/53, case bodies 53/53 inside the cap. Measured reduction on one WI:
  **36,459 → 9,422 chars (3.9×)**. The latent `ref_id`/`item_id` divergence did NOT fire.
* **Ambiguity — the two cases with teeth passed**: two rulings both titled «دعوى تعويض عن
  أضرار» → asked; the plan's own «نزاع تاجرين» → asked.

**FAILURES, ranked**

| # | Finding |
|---|---|
| **1** | **Case C is unreachable — routing 2/10.** All four «اش الحكم اللي في WI-N…» became `chat_response`, the router answering by restating the 500-char snippet as "the details". `router.py:217` ("answer directly from a prior report") + the Necessity check at `:207` beat the single Case-C bullet — **and §2.3.1's snippet is precisely what makes answering directly look possible.** A 5-case control (dangerous pair, «المادة 77», «ابغى نص الحكم نفسه مو الملخص») routes correctly, so the harness is sound. Comparison also 0/3. |
| **2** | **D12/§7.3 is UNIMPLEMENTED.** `resolve_access` is called from **zero** modules under `agents/` — verified. A ruling's full 3,343-char `cases.content` was served with **17 unlocks before, 17 after**. `unfold.py:851` asserts the charge happens; it does not. Today: the full ruling is free in chat, then the user pays an unlock to open its source card. The "same unlock" property is sound (17/17 ledger `content_id`s = `cases.id`), so adding the charge inherits it free. |
| **3** | **F3 — `_identity_from_ref_row` never fills `regulation_id`** (0/27 chunk objects), so `document_key` split 3 chunks of ONE لائحة into **3 groups → 3 synthesizers, 3 WIs, 3 replies**. D5 violated, by the join already run to build the preview line. |
| **4** | **F4 — published cards are titled with the raw manifest line**: `obj.title` carries the chip prefix and `## الملخص` markdown, `title == regulation_title`, and `doc_type` is lost on the write. |
| **5** | **F6 — the agent snippet exceeds the card** on 42/53 case lines: it carries «(ابتدائي)/(استئناف)» the card never prints, because `enrich._CASE_COLS` never selects `court_level`. 11 lines lose 668 chars of quotable tail. A §2.3.2 upper-bound violation. |
| **6** | Byte-identical service cards → guessed instead of asking (2/4 ambiguity). |
| **7** | **F7 — FIXED 2026-08-16.** `_run_round` accepted `recent_messages` and never forwarded it to `_synthesize_group`, so §3.2's window never reached the synthesizer. **This was a coordinator bug**: a blind `str.replace` targeted `for g in groups` while the real code reads `for i, group in enumerate(groups)`, so the edit silently no-opped — and the §3.2 tests passed because they asserted on `build_synthesizer_user_message` directly rather than through the loop. Fixed, plus `test_history_actually_reaches_the_synthesizer_through_the_loop`, which asserts through `run_simple_search`. |

---

## 13a. Wave-1 build log (2026-08-15)

Built: migration `136` (**written, NOT applied**) · both source types through
`source_viewer` / `aggregator.models` / `references_service` / `reference_resolver` ·
the build-before-charge reorder · `agents/simple_search/{models,unfold}.py` (the six
`unfold(always)` functions + ladder) · the `unfold(preview)` card-parity formatters ·
the frontend types + `ReferencePanel`.

Verified independently by the coordinator, not taken on report:
`backend/tests/test_references_service.py` + `test_reference_source.py` **99 passed** ·
`agents/simple_search/tests/` **92 passed** · `agents/tool_repository/tests/` **133 passed** ·
`source_viewer` self-test **OK (6 variants)** · `frontend` `tsc --noEmit` **exit 0** ·
full `backend/tests` **1610 passed, 2 failed** — both in `test_wave_8b_legacy_removal.py`,
pre-existing and unrelated (that file imports only `importlib`/`inspect`/`Path` and contains
zero references to any changed module).

**Contract change to be aware of.** Build-before-charge (§7.3) necessarily inverts one
precedence: for *unbuildable source **and** out of quota*, the response is now **404
«تعذّر عرض هذا المصدر»** where it was the **402** refusal body. `resolve_access` has no
dry-run form — the decision *is* the charge — so the two cannot both come first. No new
information leaks (`has_source` already gives the owner that bit on the list), and a test
pins that a built view is never serialized on a 402.

**Committing: several test files need `git add -f` or the suite ships as untracked.**
`.gitignore:14` is a blanket `tests/` rule (hiding all of `agents/simple_search/tests/` and
`agents/tool_repository/tests/`). `.gitignore:19` is `backend/tests/*` followed by an
**explicit `!` allowlist** (`:20-108`) — so `backend/tests/` is opt-in per file, not tracked
wholesale. An earlier draft of this section said "`backend/tests/` is tracked normally";
that is wrong, and `git check-ignore backend/tests` (the DIRECTORY) is what makes it look
true.

Verified per file:

| File | State |
|---|---|
| `backend/tests/test_reference_source.py` | tracked (`!` at `:28`) |
| `backend/tests/test_reference_library_links.py` | tracked (`!` at `:35`) |
| **`backend/tests/test_references_service.py`** | **IGNORED — needs `-f`** |
| **`backend/tests/test_library_item_service.py`** | **IGNORED — needs `-f`** |
| all of `agents/*/tests/` | **IGNORED — needs `-f`** |

Either `git add -f` them or add matching `!` lines. Same trap as commit `3b80faa`.

Two schema facts neither this plan nor its sources recorded: **`articles_v2` and
`regulations_v2` are VIEWS** (`pg_class.relkind='v'`), so there is no FK for a PostgREST
embed — the article → parent-نظام join must be a second batched fetch. And the article body
is capped at 2,000 chars **in the list shell only** (`_ARTICLE_SHELL_CONTENT_CHARS`; body
p50 325 · p90 1,334 · **max 244,419**), with the full body re-read at reveal time.

---

## 13. Deferred

- `fetch_grounding` grounders for `circular` / `form` / `calculator` / `topic`, and a
  `/services` route + `service` member of `LibraryPageType` — until then circulars and
  services have no library door (§8).
- باب as a first-class source type — resolves to a run of chunks for now (§4 L3).
- Cross-turn dedup of library items beyond the per-conversation `source_page_id` key.
- **`blog.py:317` is the last unpatched hop for C4.** It builds a byte-identical `unlocked`
  payload and still emits `resolved.article_no`, so the **anonymous blog reveal still sends
  `null` for a compound مادة** even though the workspace reveal no longer does. The clean fix
  is upstream and subsumes the workaround: widen `ResolvedRef.article_no`
  (`reference_resolver.py:133`) to `str | None`, setting it from the raw `article_number` on
  both branches of `_resolve_article_row`, and **keep the existing `int()` for `content_id`
  only** — that coercion is load-bearing for metering (a compound number has no published
  مادة page to key a ledger row on) and must not be removed.
- **`unlocked.title` is wrong for an `article:` ref** — `_resolve_article_row` sets no
  `title`, so the payload falls back to `ArticleFullSourceView.title`, which is the
  *composite* «المادة 6 من نظام العمل». The notice then reads «تم فتح المادة 6 من «المادة 6
  من نظام العمل»…». Pre-existing since wave 1; same "reads wrong after a spent unlock"
  family as the `article_no` defect.
- **`library_url` is `None` on the LIST for both new domains.**
  `library_items_service._public_page_urls_for_reference_rows` (~`:975-1007`) handles only
  `cases` / `circulars` / `regulations`, and falls through to no-URL by documented design.
  So the «فتح النظام في ريحان» button will not render on `articles` / `regulation_docs`
  cards. The **reveal** path is fine — it goes through `resolve_access`'s resolved tuple,
  verified returning `/regulations/{slug}`. One follow-up in that file.
- **`Reference.render_label()` returns a bare `regulation_title` for `article_full`** — it
  special-cases only the legacy `article`/`section`. Affects `artifact_builder`'s
  end-of-document list. One-line fix, deliberately left.
- **`articleNo` is typed `number`, but `articles_v2.article_number` is TEXT.**
  `gate-copy.ts:387` and `types/index.ts:1399` both declare `number | null`, and `arNumber`
  (`gate-copy.ts:23-25`) does `Math.round(value)`. Compound numbers («1-1», «81 مكرر») cannot
  survive that, so the backend must pass `null` and `unlockedNotice` silently falls to its
  generic branch — naming the whole نظام while the reader is looking at one مادة, **after
  spending an unlock.** Measured blast radius: **487 of 51,792 articles (0.94%), across 7
  regulations.** Small, but the failure is silent and lands on a metered action. The fix
  (widen to `number | string | null`) spans `ReferenceUnlockInfo.article_no`, `gate-copy.ts`
  and the backend `unlocked` payload — deliberately not done inside a single lane.
  Secondary nit in the same function: the no-name fallback says «أُضيف» where «المادة»
  needs «أُضيفت».
- **«افتح التحليل للنظام» / «افتح التحليل للحكم»** — the follow-up affordance that escalates
  a fetched object into deeper analysis (user's original sketch, 2026-08-15). The natural
  wiring is a simple_search WI whose follow-up dispatches `deep_search` with the object
  attached — but that is a routing behaviour to design deliberately, not a v1 feature.
