# simple_search — the responder, and who owns the card

**Premise.** The lookup family has no agent that owns the turn. Layer 3
synthesizers write the workspace card body *and* the chat bubble *and* decide
whether a card exists at all — three jobs, one agent, N copies of it running
blind to each other. This plan splits them the way `deep_search` already does:
the **synthesizer** renders one document, and a new **responder** writes the
message the user reads *and* owns the publish gate.

Companion: `.claude/plans/simple_search_family.md` (the family),
`agents/deep_search_v4/planner/` (the `planner_responder` precedent — traced in
full below), `.claude/plans/writer_planner.md` (the planner/executor split).

---

## §1 — The defect, measured against the code

1. **One text does double duty.** `_finalise` (`agents/simple_search/runner.py:1305-1350`)
   uses the same `synthesis_md` as the chat bubble **and** as the card's
   `content_md`. When a card is published the user reads the whole document
   twice.
2. **Nobody owns the turn.** Up to 3 synthesizers run concurrently
   (`_run_round`, `runner.py:1243`), each blind to the others, and
   `orchestrator.py:2736` `"\n\n".join(...)`s them into one bubble. No framing,
   no ordering, no «فتحت لك الاثنين — الأول النظام والثاني لائحته التنفيذية».
3. **No next step exists as a field.** `SynthesizerOutput` has no
   `suggestion_md` analogue, and `_SHARED_ROLE` correctly forbids the register
   ("open with the answer, never narrate"). It is a document renderer being
   asked to also be the voice.
4. **The card decision is made by the wrong agent.** `wi_warranted` is emitted
   per-document by an agent that cannot see the other documents, the
   conversation's shape, or what the turn as a whole produced.

**Not a defect:** the synthesizer is *not* blind to the conversation. It already
receives `recent_messages` via `build_synthesizer_user_message`
(`prompts.py:459`) → `render_recent_messages` (`prompts.py:402`). What it lacks
is mandate and output shape, not context.

---

## §2 — The precedent, as it actually works

| Family | Artifact body | Chat voice | Who decides the artifact |
|---|---|---|---|
| `deep_search` | aggregator | `planner_responder` | **the responder** — `build_artifact` + `referenced_wi` (`planner/models.py:246-278`), applied at `orchestrator.py:3063` |
| `writing` | `writing_executor` (its own `chat_summary` capped at **500 chars, "do not re-draft"** — `writer/prompts.py:152`) | `writer_planner` | `PlannerDecision` (the planner) |
| `simple_search` | synthesizer | **synthesizer** | **synthesizer** |

The deep_search gate, verified end to end:

- **The publish is gated on the responder's return**, not merely messaged by it:
  `should_publish = agg_output is not None and response is not None and
  response.build_artifact` (`orchestrator.py:3063-3076`). On `False` the
  `workspace_items` row is never written; `output_item_id` stays `None` and a
  `referenced_existing_item` SSE event is emitted instead
  (`orchestrator.py:3117-3131`).
- **Bad aliases retry, they don't degrade.** `_resolve_referenced_wi`
  (`planner/agent.py:232-260`) is an `@agent.output_validator` that raises
  `ModelRetry` with an Arabic correction message when `referenced_wi` misses
  `deps.wi_alias_map`. Silent `None` was explicitly rejected.
- **The responder is skipped when there is nothing to respond about.** If phase 2
  *raises*, `_minimal_response()` (`planner/runner.py:158-172`) returns a
  code-written Arabic line with `build_artifact=False` and no LLM runs. If phase 2
  merely finds nothing, the responder *does* run — writing the honest empty-result
  message is its job.
- **Responder failure falls back to the artifact.** `_response_from_artifact`
  (`planner/runner.py:140-155`) takes the synthesis head (500 chars) as the chat
  summary and publishes anyway.

Two deliberate divergences for our family are recorded as **D6** and **D7**.

---

## §3 — Decisions

- **D1 — The responder lives inside the family, not at the router.** The router
  is Layer 1 and terminal after dispatch; returning to it costs a second router
  call on every lookup turn and pushes retrieval output into the most expensive
  shared prompt in the system. Decisive argument: the §13l deliver-then-ask path
  calls `_finalise` mid-turn and then raises `_SkipRunRecord`
  (`runner.py:1098`, `orchestrator.py:2624`) — the router is not in that path,
  so a router-owned closer would be missing on exactly the turn that needs one.
- **D2 — The responder owns the card decision, per answer.** Same agent, same
  call, same reasoning as `build_artifact`: whether the turn leaves a durable
  artifact and how the turn is described to the user are one decision, and only
  the responder sees every answer at once.
- **D3 — It decides with the body in hand.** The responder runs after `unfold`
  and after synthesis, so truncation, summary-only payloads and thin content are
  *visible* rather than guessed at. Code keeps only the two vetoes it already
  owns: a ledger-refused ruling (`_is_refused_judgment` → `runner.py:527`, which
  never reaches a synthesizer at all) and an empty body.
- **D4 — The responder never retypes legal text.** A flash model paraphrasing a
  provision is precisely what `_SHARED_ROLE` forbids. Uncarded bodies are
  carried into the bubble **by code**, verbatim, under the responder's lead-in.
- **D5 — Money lines stay in code.** `unlock_acknowledgement`
  (`runner.py:356`) keeps appending after the responder runs. An LLM never
  writes a billing sentence.
- **D6 — Divergence: our responder sees `recent_messages`.**
  `build_responder_instructions` (`planner/prompts.py:568-614`) shows
  `planner_responder` only the welcome, the mode framing, the planner brief and a
  1600-char synthesis digest — every conversational surface is phase-1 only.
  That works there because the decider carries the thread. Here the searcher
  carries the thread but never writes to the user, so the conversational context
  must reach the agent that does. This is the whole point of the change.
- **D7 — Divergence: responder failure publishes NOTHING.** deep_search's
  fallback publishes on failure because the artifact *is* the product and the
  bubble is a summary of it. Here the bubble carries the text, so the safe
  direction inverts: on failure the user still gets every body in full, and the
  turn simply leaves no card. A missing card is recoverable by asking again; a
  wrongly-published card is permanent clutter in a 15-item-capped workspace.

---

## §4 — The searcher is untouched

An earlier draft moved the card decision here. Rejected under D2/D3: the searcher
runs before `unfold`, so it would be ruling on documents it has not seen. It
keeps its current job — identity, never content — and its `SearcherDecision`
schema does not change.

What the searcher *does* contribute is `deps.candidates` / `deps.candidate_lines`:
the objects it considered and chose not to open. Those are passed to the
responder as suggestion material (§6).

---

## §5 — The synthesizer shrinks

`SynthesizerOutput` (`agents/simple_search/synthesizer.py`) **loses**
`wi_warranted` and `wi_title`. It keeps `synthesis_md`, `used_refs`,
`rejected`, `rejection_reason`.

- `_SYNTH_RETRY_MSG` must be updated in the same edit. The module docstring
  already warns that a retry message naming the wrong field set misdirects the
  retry — the trap fires identically in reverse when fields are *dropped*.
- The prompt is otherwise unchanged: the synthesizer always writes the full
  document body and always cites `[n]`, because it no longer knows whether a
  card will exist.
- **`_strip_citation_markers` (`runner.py:143`) therefore survives** — and this
  is the acknowledged cost of D2. When the responder declines a card, that
  body's `[n]` markers point at a panel that was never published, and code
  strips them on the way into the bubble. It stops being a patch over a
  contradiction (an agent that cited, then declared itself uncarded) and becomes
  the designed hand-off step between two agents that each did their job
  correctly.

---

## §6 — The responder

New agent, new slot `simple_search_responder` in `agents/utils/agent_models.py`,
**tier_2** (this family's premise is that it costs less). New module
`agents/simple_search/responder.py`; prompt lives in
`agents/simple_search/prompts.py` beside the others (`prompts-md-ref-only`: the
`.py` is the source, never `agents/prompts/*.md`).

### Inputs

Assembled by `_finalise` — all of it already in scope there.

| Input | Source | Why |
|---|---|---|
| the raw question | `question` | never paraphrased (§2.1) |
| `recent_messages` | already threaded through `_answer_loop` | **D6** — the conversational frame |
| per-answer digest | `_Answer` | stable label, level, object title, unfold flags (`truncated`, `payload=="summary"`), and a bounded body excerpt |
| dispatched-vs-answered | `dispatched` counter vs `len(answers)` | §7 honesty rule |
| **unselected candidates** | `deps.candidates` / `deps.candidate_lines` | the suggestion's grounding |
| unlock / refusal state | `unlock_records`, `_JUDGMENT_DENIED_NOTE` | never suggest re-opening a refused ruling |
| `welcome_instruction` | moved off the synthesizer | §9 |

The **body excerpt** is bounded, following `_SYNTHESIS_DIGEST_CHARS = 1600`
(`planner/prompts.py:277`) — the responder judges whether a body deserves a card
and frames what it is; it never needs the whole document, and shipping three
full documents into a flash context would undo the family's cost premise.

`deps.candidates` is the load-bearing input for suggestions. The searcher **saw**
the لائحة التنفيذية sitting beside the نظام and chose not to open it — so «تحب
أفتح لك اللائحة؟» is grounded in what the turn actually considered, rather than
invented by a model staring at its own output.

### Output

```python
class CardVerdict(BaseModel):
    doc: str            # the stable label the runner assigned (D1, D2, D3)
    card: bool
    title: str = ""     # Arabic, ≤80 chars, no verbs; empty when card=False

class ResponderOutput(BaseModel):
    chat_summary_md: str            # Arabic; the bubble's lead-in / framing
    suggestion_md: str = ""         # ONE next step, offering tone, or empty
    cards: list[CardVerdict] = []   # one entry per dispatched document
```

**`doc` labels follow the `WI-{n}` alias discipline.** The runner assigns
`D1..Dn` in dispatch order and renders them in the digest; the responder
references them and never emits a `document_key` or a UUID.

**An `@agent.output_validator` raises `ModelRetry` on an unknown label**, copying
`_resolve_referenced_wi` (`planner/agent.py:232-260`) — including the Arabic
correction message, because the responder self-corrects better in the language
it is writing. A missing verdict for a dispatched document defaults to
`card=False` and logs; a verdict for a label that does not exist is a retry, not
a silent drop.

`title` falls back, in order: the responder's string → `objects[0].title` →
`label_ar()`. `_finalise:1341` already implements the last two.

### Prompt rules

Adapted from `PLANNER_RESPONDER_SYSTEM_PROMPT` (`planner/prompts.py:199-241`):

- Conversational Arabic prose. No `##` headings, **no `[n]` markers** — those
  belong to the card.
- **Never restate the document.** When you grant a card, point at it; when you
  decline one, the body follows verbatim below you — introduce it, do not
  summarise it.
- Name what was opened and in what relation («النظام وأمامه لائحته التنفيذية») —
  this is the fan-out coherence the family has never had.
- Be honest when fewer documents came back than were asked for (§7).
- **Card rules** — the default is `card=true`. Decline when the body is a
  one-line answer, a pointer, a not-found, or so truncated that the card would
  mislead. A card for two sentences is clutter; the workspace caps at 15 items.
  When you decline, do **not** write «التفاصيل في البطاقة» — there is no card.
- One suggestion only, offering tone («إذا تحب…»، «أقدر…»), never one the answer
  already covered. Empty is a valid and frequent output.
- Latin digits only (`latin-numerals`).

---

## §7 — Where it hangs, ordering, and failure

**`_finalise` (`runner.py:1273`) is the single choke point** — both the terminal
path (`:1212`) and the pause path (`:1098`) call it, and `resume_simple_search`
re-enters through the same `_answer_loop`. Putting the responder anywhere else
guarantees drift between dispatch and resume.

**The publish is gated on the responder's return**, exactly as
`should_publish` gates `publish_search_result` at `orchestrator.py:3063`. New
order inside `_finalise`:

1. drop empty bodies; apply the refused-judgment veto (already short-circuited
   upstream at `runner.py:527`)
2. build the digest, assign `D1..Dn` in dispatch order
3. **call the responder**
4. publish only the answers it granted a card
5. assemble the bubble (§9)

### Concurrency

Waiting is already structural: `_run_round` returns only after
`asyncio.gather(...)` settles every synthesizer, and `_answer_loop` hands
`list(answers.values())` — the complete set across all cycles — to `_finalise`.
**No new synchronisation is required.** Three things do need handling:

1. **Order.** `answers` is a dict keyed by `document_key`, insertion-ordered by
   round — so a document answered in cycle 2 (after a rejection) sorts *after*
   cycle-1 answers regardless of the order the user asked in. The `D1..Dn`
   labels are assigned from dispatch order, and the responder frames in that
   order.
2. **Dropped synthesizers.** `_run_round` catches per-task exceptions, logs, and
   `continue`s (`runner.py:1268-1275`). The responder receives `dispatched`
   alongside `answered` and must say so when they differ — otherwise it
   announces "opened both" for a turn that opened one. This gap exists today;
   the responder is where it gets closed.
3. **Zero answers.** `_finalise` already falls back to `_DEGRADED_AR` /
   `_ALREADY_ANSWERED_AR`. Those are code-written lines and stay code-written —
   **the responder is not invoked when there is nothing to respond about**,
   matching `_minimal_response` (`planner/runner.py:158`).

### Failure (D7)

If the responder raises, times out, or exhausts `output_retries`, `_finalise`
degrades deterministically:

- bubble = today's behavior — every body joined in dispatch order, `[n]` markers
  stripped, nothing lost;
- **no cards published**, `created_item_ids` empty;
- warn-level log naming the turn, and the unlock acknowledgement still appends.

This is the inverse of `_response_from_artifact` and deliberately so — see D7.

---

## §8 — Pause and resume

- **Pause leg** (`degraded_fallback=False`, `runner.py:1098`): the responder runs
  **with suggestions suppressed**. The searcher's `ask_user` question is the
  turn's next step; a second suggestion above it reads as two competing
  questions. `chat_summary_md` and the card verdicts still run — the
  pre-question delivery is exactly where framing helps most, and those answers
  were already paid for.
- **Resume leg**: `skip_keys` carries the documents the paused leg already
  delivered *and already carded* (`runner.py:1058`). Those must reach the
  responder as already delivered, never as new — re-announcing them shows the
  user the same opening twice, and re-granting a card would publish a duplicate.
- `_ALREADY_ANSWERED_AR` path (`:1222`): no responder call, unchanged.

---

## §9 — Assembly: what the model writes vs what code writes

`_finalise` builds the bubble in this fixed order:

```
[responder.chat_summary_md]
[verbatim synthesis_md of every UNCARDED answer, dispatch order, markers stripped]  ← code
[responder.suggestion_md]                                    ← omitted on the pause leg
[unlock_acknowledgement(...)]                                ← code, unchanged (D5)
```

- Carded answers contribute **nothing** to the bubble — their body lives on the
  card. This is the fix for §1.1.
- Uncarded bodies are moved by code, never regenerated (D4), and never dropped:
  a `card=False` verdict must not be able to lose an answer. This closes the
  analogue of the implicit deep_search hole where `build_artifact=False` without
  a `referenced_wi` produces a silent no-publish and no SSE event.
- **The welcome line moves to the responder.** It is the opening line of the
  turn and the responder now owns the opening; this deletes the
  `welcome_instruction if not answers else None` hack (`runner.py:1252`) and its
  twin in `_run_round` (`:1250`). `mark_welcomed` semantics in
  `orchestrator.py:2775` are untouched — a question-only turn still leaves the
  welcome unspent.

---

## §10 — File manifest

| File | Change |
|---|---|
| `agents/simple_search/responder.py` | **new** — agent factory, `ResponderOutput` / `CardVerdict`, `RESPONDER_LIMITS`, salvager, `ModelRetry` output validator |
| `agents/simple_search/prompts.py` | **new** `SIMPLE_SEARCH_RESPONDER_PROMPT` + `build_responder_user_message` (digest renderer, `D1..Dn` labels, bounded excerpts) |
| `agents/simple_search/synthesizer.py` | drop `wi_warranted` / `wi_title`; update `_SYNTH_RETRY_MSG` |
| `agents/simple_search/runner.py` | responder call + gated publish + assembly in `_finalise`; `D1..Dn` labelling; welcome moves off `_run_round`; pass `dispatched` / candidates / unlock state |
| `agents/utils/agent_models.py` | register `simple_search_responder` (tier_2) |
| `agents/simple_search/searcher.py` | **unchanged** (§4) |
| `agents/simple_search/tests/` | responder unit tests + `_finalise` assembly tests (all-carded / none-carded / mixed / pause / resume-skip / zero-answers / responder-raises / unknown-label retry) — **and the harness must patch `responder.get_agent_model`**, see trap §11.9 |
| `agents/simple_search/eval/` | `adv_unlock_01.py:156`, `adv_unlock_02b.py:105` construct `SynthesizerOutput` with the dropped card kwargs; `rerun_b_regression.py:10` names them in prose. Pydantic 2 defaults to `extra='ignore'`, so these do **not** raise — they silently drop the kwargs and assert against a card path that no longer exists |

Cost: **+1 flash call per lookup turn** — roughly 10-15% on a family that
already runs a searcher plus up to three synthesizers.

---

## §11 — Traps

1. **Dropping `SynthesizerOutput` fields without updating `_SYNTH_RETRY_MSG`.**
   The salvager's retry message enumerates the schema; a stale list teaches the
   model to emit fields that no longer exist.
2. **Letting the responder cite.** Any `[n]` it emits points at a panel it never
   saw. The prompt forbids it; a test asserts it.
3. **Calling the responder when `answers` is empty.** `_DEGRADED_AR` is a code
   line for a reason — a model asked to narrate an empty result invents absence
   («لا يوجد نظام بهذا الاسم»), the exact failure `refusal_message`
   (`runner.py:534`) was written to prevent. deep_search reached the same
   conclusion independently (`_minimal_response`).
4. **A `card=False` verdict silently swallowing the body.** The verdict controls
   *where* the text goes, never *whether* it ships.
5. **Publishing before the responder returns.** The whole gate collapses; the
   ordering in §7 is load-bearing.
6. **Forgetting the resume leg.** Any assembly logic added outside `_finalise`
   silently skips resume, and the two paths diverge invisibly.
7. **Suggesting a refused ruling.** `unlock_records` must reach the responder, or
   it will offer to open the thing the ledger just declined to open.
8. **Shipping full bodies into the responder context.** Bounded excerpts only —
   three whole أنظمة in a flash context erases the family's cost premise.
9. **A test harness that patches only two of the three agents.** `test_runner.py`
   stubs the searcher and the synthesizer; adding a third agent to the turn
   without adding it to the harness sends every runner test to a live model —
   measured at 40s → 299s and real Arabic in the assertions. The suite must be
   runnable offline, and a green suite that cost money is not green.

---

## §12 — Success criteria

- A carded lookup shows a **short conversational bubble + a card**, not the
  document twice.
- A 3-document fan-out produces **one** framed message naming the relation
  between the documents, in the order the user asked.
- A one-line lookup («اش رقم المادة؟») produces no card, no `[n]` anywhere in
  the bubble, and reads as one voice.
- A turn that dispatched 3 and answered 2 **says so**.
- A pause turn delivers framed answers then the question — with no competing
  suggestion between them.
- A resume turn never re-announces or re-cards a document the paused leg
  delivered.
- A responder that raises still delivers every body in full and publishes
  nothing (D7).
- A responder that names a nonexistent `doc` label retries in Arabic rather than
  dropping the answer.

---

## §13 — Found during the build (fixed, and pinned by tests)

Three defects the plan above did not anticipate. All three are fixed in the
module and have a named regression test in `tests/test_runner.py`.

1. **The honesty line fired on every rejection loop-back.** `dispatched` counted
   fan-out *slots* (`dispatched += len(todo)` per cycle, with `todo`
   re-including the previous cycle's rejections), so a turn that asked for one
   document, rejected the wrong object and answered correctly on the retry
   rendered `dispatched="2" answered="1"` and opened by apologising for a
   document the user had just received. §12's criterion inverted into "a turn
   that answered everything apologises" — on the exact path the D3 pool exists
   for. Now: a `lost` counter tracks only synthesizers that never came back
   (`len(todo) - len(round_answers)`), and `dispatched` is `len(answers) + lost`.
   A rejection is a document the loop **replaced**; only a crash is one the user
   lost.
2. **D7 silently spent the welcome.** §9 made the responder the greeting's only
   writer — inside the `try` that D7 swallows. The bodies still ship, so the
   orchestrator sees a delivered answer and calls `mark_welcomed`: a first-time
   user is greeted never, once, permanently. `compose_opening` returns the
   literal Arabic line, so the D7 fallback now opens with it in code.
3. **The excerpt truncation marker could never fire.** `_finalise` pre-clips to
   `RESPONDER_EXCERPT_CHARS`; the builder then re-clipped at the identical cap
   and appended `[…]` only `if len(excerpt) > CAP`, which after the pre-clip is
   unreachable. A body cut at 1600 chars reached the model ending mid-sentence
   with nothing marking the cut, inviting the responder to frame a partial
   document as a whole one. The marker now keys off `body_chars`, which survives
   the clip.

**Operational note:** `.gitignore:14` blanket-ignores `tests/`, and this suite
carries no `!` re-include (unlike `backend/tests/test_*.py`). So none of the
test work appears in `git diff` and the suite is local-only rather than
CI-enforced. Deliberate-looking, but this change is exactly the kind that
benefits from CI.
