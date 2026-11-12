# Helper frame — background helpers, critical / not critical

**Status:** PLANNING — written 2026-11-11 from the owner's /reflect answers. Nothing built.

**Why now:** the owner expects "a lot of helpers". The first new one is the attachment segmenter
(`.claude/plans/attachment_segmentation.md`); the metadata agent is planned after it. Rather than wiring
each one into the orchestrator by hand, every helper declares **which agents it is critical for**:
those agents wait for it, everyone else runs at the same time.

**Vocabulary:** helpers are **Layer 4 Memory** agents or plain code steps (system-side, never talk to
the user). Their model tier is per helper (see CLAUDE.md "Layer vs Tier").

**Code base for line numbers:** `origin/master` @ `03fd65a`. Build in a fresh worktree from
`origin/master` (the main checkout is on an unrelated branch with uncommitted edits).

---

## 0. In one paragraph

A **helper** prepares something an agent reads — an attachment's OCR text, its summary, its segments,
the conversation's memory entries. A **consumer** is an agent that reads it. Each helper declares what
it **depends on** and which consumers it is **critical for**. A critical consumer waits for the
helper's result up to a **ceiling**, then uses the helper's **fallback**. A consumer the helper is not
critical for never waits — it runs alongside. Results are stored, so later turns read them; a helper
that was killed (a redeploy) is re-run by the next consumer that needs it. The conversation memory
agent already works this way; the frame turns that one-off pattern into the rule for every helper.

---

## 1. Today on master (verified 2026-11-11)

| Step | How it runs | Who effectively waits |
|---|---|---|
| **OCR** | awaited in `_handle_message_inner` step 0a, whole-step budget 180 s (`run_ocr_extraction`) | everyone, router included |
| **Attachment summary** | awaited in step 0b, **one item after another** (`for … await summarize_workspace_item`) | everyone, router included |
| **Memory agent** (`convo_memory_writer`) | background task started right after 0b (`_start_memory_writer` → `schedule_memory_writer`); strong refs in `_BACKGROUND_TASKS`, queued per conversation (`pending_memory_writer`); consumers call `_wait_for_memory_writer` / `_settle_memory_writer` = `asyncio.wait` up to `MEMORY_WRITER_CEILING_S = 60` s, **never cancels**; a late result lands for the next turn | everything after the router: dispatch (`_route` ~3246), prior check / analyzer (~3201), resume legs (~1239, ~1461, ~1930). **Not the router.** |
| Pre-router memory hook | awaited in step 1: `memory.resummarize_dirty_items` + `memory.compact_conversation` (best-effort) | router + everything |

The memory agent is already a helper in all but name: a background task, a declared set of agents that
wait for it, a ceiling, no cancellation, a late result kept for the next turn. The frame generalises it.

---

## 2. Vocabulary

| Term | Meaning |
|---|---|
| **Helper** | Background job that prepares something about an item or the conversation. Never talks to the user. |
| **Scope** | `item` (one per workspace item: OCR, summary, segmenter) or `message` (one per user message: memory agent). |
| **Consumer** | An agent that reads helper results: router, deep-search planner, writer planner, analyzer (prior check), case expander, reg expander, aggregator, writer, and the other dispatched agents (responder, simple search, …). |
| **depends_on** | Helpers that must finish before this one starts (segmenter ← summary, for `doc_type`). |
| **critical_for** | Consumers that wait for this helper before they start. Every other consumer runs alongside it. |
| **Ceiling** | The longest a critical consumer waits, counted from when it starts waiting. |
| **Fallback** | What the consumer uses when the helper is not ready by the ceiling, or failed. |
| **Lease** | A stored "running until T" marker, so another turn / process knows a run is in flight and a dead run can be detected. |

---

## 3. Owner decisions (2026-11-11)

| # | Decision |
|---|---|
| H1 | Build the frame: each helper is **critical or not critical** for each agent; an agent a helper is not critical for runs at the same time. |
| H2 | **Segmenter**: critical for the case expander, the reg expander, the aggregator and the writer; **not** for the router, the deep-search planner, the writer planner, the analyzer. |
| H3 | **OCR and the summary move into the frame** ("move them all"). |
| H4 | **Memory agent**: **not critical for the router; critical for all else.** |
| H5 | A critical consumer waits up to **5 minutes** (stated for the segmenter); the segmenter's fallback is the **full text within the caps — 100k per document, 300k overall** (follow-up answer; replaced a 50k cut). |
| H6 | A late result is stored for later turns; a run killed by a restart is **re-run by the next consumer that needs it**. |
| H7 | **Metadata agent** (later): runs after the segmenter, critical **only for the writer**. |

---

## 4. The matrix

`C` = critical (waits) · `–` = not critical (runs alongside) · `(C)` = critical but already satisfied
because an earlier consumer in the same turn waited for it.

| Helper | Scope | Depends on | Router | Deep planner | Writer planner | Analyzer | Case exp. | Reg exp. | Aggregator | Writer | Others¹ | Ceiling | Fallback |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **OCR** | item | — | C | (C) | (C) | (C) | (C) | (C) | (C) | (C) | (C) | 180 s² | attachment travels as filename only + status line «تعذّرت قراءة بعض المرفقات…» (today) |
| **Summary** | item | OCR | C | (C) | (C) | (C) | (C) | (C) | (C) | (C) | (C) | ?² | no summary: router falls back to `content_md`; case docs show a 1,500-char opening (today) |
| **Memory agent** | message | — | **–** | C | C | C | (C) | (C) | (C) | C³ | C | 60 s² | the memory as it was before this message (today) |
| **Segmenter** | item > 5k chars | OCR, summary | – | – | – | – | C | C | C | C | – | 300 s | full text within the caps (100k per document, 300k overall) |
| *Metadata agent (later)* | conversation | segmenter (all docs) | – | – | – | – | – | – | – | C | – | ? | — |

¹ responder, simple search, any other dispatched agent. ² owner question §7 Q1 — today's values shown.
³ the writer runs inside the writer-planner call, which already waited.

**Effect on the critical path of a first turn with a long attachment:**

```
OCR ─► summary ─┬─► router ─► [memory ≤60s] ─► deep planner ─► [segmenter ≤5min] ─► expanders ─► aggregator
                │                                                   ▲
                └─► segmenter (starts here, runs while the router and the planner work) ─┘
memory agent (starts with the message) ─────────────────────────┘ (router never waits for it)
```

---

## 5. Design

### 5.1 Module `agents/helpers/`
- `spec.py` — the helper declaration:
  ```python
  Consumer = Literal["router", "deep_planner", "writer_planner", "analyzer",
                     "case_expander", "reg_expander", "aggregator", "writer", "other"]

  @dataclass(frozen=True)
  class HelperSpec:
      name: str                              # "ocr" | "summary" | "memory" | "segmenter" | …
      scope: Literal["item", "message"]
      depends_on: tuple[str, ...]
      critical_for: frozenset[Consumer]
      ceiling_s: float
      applies_to: Callable[[ItemRow], bool]  # segmenter: kind == "attachment" and len(content_md) > 5_000
      run: Callable[..., Awaitable[Any]]     # does the work, stores the result
      load: Callable[..., Any]               # reads a stored result (other turn / other process)
  ```
- `registry.py` — **the matrix as code** (§4): one list of `HelperSpec`s, the single source of truth.
  A test asserts every consumer name used at a wait point exists and every helper's `depends_on` is
  registered (no cycles).
- `turn.py` — `TurnHelpers`, one per user message.
- `status.py` — stored status + lease (§5.3).

### 5.2 `TurnHelpers` — one per user message
- Created in `_handle_message_inner` where steps 0a/0b run today; the pinned-plan (editorial) path
  creates none (throwaway conversation, no attachments — today's steps are no-ops there too).
- `start(items, message)`: schedules every applicable helper as an `asyncio` task as soon as its
  `depends_on` are done (done-callbacks chain the DAG). Strong references in a module-level set, the
  way `_BACKGROUND_TASKS` keeps the memory agent alive after the SSE stream ends. One task per
  (helper, item) — a second request for the same pair joins the running task.
- `await ready(consumer, item_ids=None) -> dict[item_id, Readiness]`: for every helper critical for
  `consumer` and applicable to those items → `asyncio.wait(tasks, timeout=remaining ceiling)` —
  **never `wait_for`**, which would cancel the helper (same rule as `_settle_memory_writer`). Returns
  `ready` / `fallback(reason)` per item; never raises.
- Handed down the call chain the way `memory_task` is today: `_route`, `_dispatch`, the resume legs,
  the deep-search deps (`PlannerDeps` → `FullLoopDeps`), the writer-planner deps → walkers. The
  `memory_task` parameter becomes `helpers`.
- While a consumer waits more than a few seconds, it emits one SSE status event (wording §7 Q4, e.g.
  «نقرأ مستنداتك كاملة…»).

### 5.3 Across turns and processes (H6)
A consumer looking for a helper result checks, in order:
1. a task in this process (this turn, or an earlier turn still running) → wait on it;
2. a stored result → load it;
3. a live lease held by another process / turn → poll the stored status until the ceiling;
4. nothing, an expired lease (killed by a redeploy), or a retryable failure → **start it now** under a
   fresh lease (conditional UPDATE, so two consumers can't both start it) and wait.

Status storage — §7 Q2. Proposal: a generic table for new helpers
`helper_runs(item_id, helper, status, attempt, lease_until, started_at, finished_at, error, version)`,
RLS on, no client grants (migration 195 stance), cascade on `workspace_items` delete. OCR and the
summary keep their existing `metadata` markers (`ocr_status`, `summary_attempt`) because other code
reads them (the attachment viewer banner, the retention sweep's two windows, the OCR quota settle).

The memory agent keeps its own per-conversation queue and its rows (`pending_memory_writer`); it is
message-scoped and has no item row. If a redeploy kills it, today the message's entry is lost — §7 Q5.

### 5.4 Masking (وضع السرية)
Helpers outlive the turn's ContextVar codec. Every LLM helper builds its codec per user the way the
summarizer does on detached paths (`summarize.py::_summarize_codec`), persists new fakes before the
LLM call, and decodes model output before storing (store-real). Consumers encode what they load with
the turn codec, as today.

### 5.5 Concurrency, failure, telemetry
- Per-helper semaphore (provider rate limits — the split segmenter makes 2 Baseten calls per doc).
- A helper's exception is caught inside its task → status `failed`, logged; consumers only ever see
  `fallback`.
- Logfire: `helper.run` (helper, scope, item_id, attempt, ms, outcome) and `helper.wait` (consumer,
  helper, items, waited_ms, outcome ready / fallback / failed / not_applicable). Cost stays in
  `llm_calls` through each helper's tracking hook.

### 5.6 Where consumers wait (hook points on master)
| Consumer | Hook | Waits for |
|---|---|---|
| router | `_handle_message_inner`, where steps 0a/0b are awaited today, before the pause check | OCR, summary |
| dispatched agents (deep planner, writer planner, responder, simple search, …) | `_route` → today's `_wait_for_memory_writer` (~3246) becomes `helpers.ready(…)` | memory |
| analyzer (prior check) | `_route` ~3201 | memory |
| resume legs | today's `_settle_memory_writer` calls (~1239, ~1461, ~1930) | memory |
| case expander, reg expander, aggregator | `deep_search_v4/orchestrator.py::run_retrieval`, before `_build_case_document_lists` (~1338) | segmenter |
| writer | `writer_planner/walkers.py::build_analyzed_items_direct` | segmenter (later: metadata agent) |

### 5.7 Moving OCR and the summary in (H3) — what changes, what must not
- **Must not change:** the router still waits for both (same router input); both still run **before
  the pause check**, so a file sent as the answer to a pause reaches the resumed agent
  (`_load_reply_attachment_ids`); the failure status line; the OCR quota settle; `ocr_status` /
  `summary_attempt` markers.
- **Changes:** OCR runs per item (today one batch call), so item 1's summary can start while item 2 is
  still in OCR; summaries run **in parallel** instead of one after another — faster turns with several
  attachments; the segmenter starts the moment its item's summary lands.

---

## 6. Phases

| Phase | Scope | Done when |
|---|---|---|
| **F1 — frame + existing helpers** | `agents/helpers/`, registry, `TurnHelpers`; OCR, summary and the memory agent moved in; `HELPER_FRAME_ENABLED` (off → today's code path byte-identical) | Parity on recorded turns: same router input, same waits, memory still lands before dispatch; summaries run in parallel |
| **F2 — first new helper** | segmenter registered + status / lease storage (segmentation plan Phase 2) | Helper runs in shadow; leases recover after a redeploy |
| **F3 — consumers wait for it** | expanders / aggregator / writer wait (segmentation plan Phase 3) | Replay as in the segmentation plan |
| later | metadata agent, relationship map, any new helper = one `HelperSpec` + its wait point | — |

---

## 7. Open questions for the owner

**Settled 2026-11-11 by "ok, let's start building" → defaults:** Q1 **per-helper ceilings** (segmenter
300 s, OCR 180 s, memory agent 60 s — today's values); Q2 generic **`helper_runs`** table for new
item-scoped helpers (OCR / summary keep their metadata markers); Q3 the pre-router memory hook moves in
as a helper **critical for the router** (same behaviour as today); Q4 status line «نقرأ مستنداتك
كاملة…» after ~5 s of waiting; Q5 a memory run killed by a redeploy keeps **today's behaviour** (that
message's entry is lost; the next message's run reads the rows again). Q3–Q5 had no recommendation
in the first draft — Claude's picks, open to change.

1. **Ceilings — one 5-minute ceiling for every helper, or per helper?** Today OCR has 180 s and the
   memory agent 60 s. A 5-minute memory ceiling would let one slow memory run hold every dispatch for 5
   minutes. Recommendation: per helper — segmenter 5 min, OCR and memory keep theirs.
2. **Status storage** — a generic `helper_runs` table for new helpers (§5.3), or per-helper markers in
   `workspace_items.metadata` like `ocr_status`? Recommendation: the table.
3. **Pre-router memory hook** (`resummarize_dirty_items` + `compact_conversation`) — move it into the
   frame too? Today the router waits for it; is it critical for the router?
4. **Status line** while a consumer waits — the wording, and after how many seconds it shows.
5. **Memory agent killed by a redeploy** — today that message's entry is simply lost. Re-run it on the
   next need like the other helpers, or keep today's behaviour?

## 8. Risks

- **Orphan tasks** after the SSE stream ends → strong references + done-callbacks (the memory agent's
  pattern); every task ends in a stored status.
- **Double runs** across processes or turns → lease + conditional UPDATE.
- **Longer worst case**: a critical wait can add up to its ceiling; the status line tells the user.
- **Parity when moving OCR / summary** — the resume ordering (OCR + summary before the pause check) is
  the trap; F1's parity check covers it.
- **Redeploys** kill every in-flight helper (each push to master restarts the backend) → leases expire,
  the next consumer re-runs.
- **Masking**: helpers outlive the turn codec (§5.4).
