# Deep Search — Retrieval Reliability & Responder Reflect Loop

Status: PLANNED · 2026-08-22
Owner: deep_search_v4

Three parts, strictly ordered: **A** bounds the DB fan-out, **B** stops retrieval
failure from masquerading as an empty corpus, **C** adds the reflect/retry loop.
C must not ship before A and B — see §Sequencing.

---

## 0. Forensic basis

Conversation `483b00d8-2651-442d-97ca-a524cd7f8b2a` (2026-08-22 15:48 UTC),
trace `01a02a2864e0333e31cbe54753d339a0`. User asked whether a municipal
inspector could push into their home, and how to fight the resulting fine for
running a workshop in a residence. They got:

> عذراً، نتائج البحث القانوني المتاحة حالياً غير كافية… لم أعثر على نصوص نظامية

### What actually happened

| Stage | Evidence |
|---|---|
| Planner | `mode: "full"`, `support: false` — the **widest** mode, both executors |
| Fan-out | 10 × `search_topics` + 6 × `search_case_topics` = **16 concurrent** |
| Every RPC | `httpx.ReadTimeout` at **15.03–15.06s** (16 of 16) |
| Rerankers | never ran — zero reranker rows in `llm_calls` |
| URA | `ura_high: 0`, `ura_medium: 0` |
| Aggregator | `duration_ms: 0`, `model_used: "none"` — short-circuited, no LLM call |
| Phase spans | still reported `outcome: "ok"`, `rqr_count: 10` / `6` |

### The corpus was never the problem

Verified by direct query — all three legs of the question are covered:

| Sub-question | Corpus hit |
|---|---|
| Was the entry lawful? | `17573_reg_138` «حالات جواز تفتيش المساكن بإذن مسبب أو بالتلبس»، «إجراءات تفتيش المساكن: الحضور والتوقيت وتوثيق المحضر»؛ `17642_reg_020` «إجراءات إصدار أوامر تفتيش المساكن» |
| The offence itself | `17636_reg_047` «ممارسة النشاط دون ترخيص بلدي كمخالفة بلدية مستقلة» + «نطاق تعريف المنشأة والمحل» (the defence angle) |
| Fighting the fine | `17411_reg_502` «التظلم من عقوبات المخالفات البلدية أمام صاحب الصلاحية وديوان المظالم»؛ case law «التظلم أمام وزير الشؤون البلدية إجراء جوازي لا يسقط حق التظلم أمام ديوان المظالم»؛ a ruling annulling a municipal fine |

Counts: 1,817 بلدي / 1,809 تفتيش topics in `search_topics`; 1,042 بلدي /
1,055 أمانة in `case_topics`. The real `search_topics` RPC, run with the exact
4-sector filter from that turn, returns **59 rows in 862 ms** and puts
`17573_reg_138` at rank 1.

### The real cause: a throughput knee, not a blip

Grouping every search RPC in Logfire by fan-out width (single query alone ≈ 0.9s):

| Concurrent | Batch wall time | Perfectly serial (N × 0.9s) | Verdict |
|---|---|---|---|
| 4 | 1.25 – 2.5s | 3.6s | ~2–3× real parallelism |
| 5 | 1.09 – 1.48s | 4.5s | best observed |
| 6 | 2.2 – 3.8s | 5.4s | still winning |
| 7 | 3.1 – 4.5s | 6.3s | still winning |
| 8 | 5.4 – 12.7s | 7.2s | break-even → losing |
| 9 | 10.3 – 13.4s | 8.1s | **worse than serial** |
| 16 | >15s, all died | 14.4s | **collapse** |

Throughput peaks near 4–6 and degrades past 8: beyond the knee, more concurrency
makes the *total* slower. The 08-18 / 08-20 / 08-21 runs at 10.3–13.4s were
near-misses against a 15s ceiling. This has been trending into failure for days.

Root config:
- `DEFAULT_SEARCH_CONCURRENCY = 10` — `agents/deep_search_v4/shared/__init__.py:20`
- reg takes its **own** `asyncio.Semaphore(10)` — `reg_compliance_search/loop.py:262`
- case takes **two more** — `case_search/loop.py:320`, `:694`
- they run in parallel from the orchestrator ⇒ **no global ceiling, peak ~20 in flight**
- `POSTGREST_TIMEOUT = httpx.Timeout(connect=5, read=15, write=15, pool=5)` — `shared/db/client.py:35`
- stale docstring still claims "default 3" — `reg_compliance_search/loop.py:604`

### The laundering site

`agents/deep_search_v4/reg_compliance_search/search.py:312-321`

```python
except Exception as e:
    logger.error("Topic search failed for '%s': %s", query[:80], e, exc_info=True)
    events.append({"type": "status", "text": "حدث خطأ أثناء البحث…"})
    return [], 0          # <-- byte-identical to "no results found"
```

`_search_topics_rpc` correctly re-raises (`search.py:353`), but this blanket
catch converts every transport death into an empty result set. Ten of these
produced ten empty `SearchResult`s → ten empty `RerankerQueryResult`s
(`rqr_count: 10`) → empty URA → aggregator short-circuit
(`aggregator/runner.py:155`) → the apology. Equivalent sites exist in
`case_search/search.py`.

---

## Part A — Bound the DB fan-out

### A1. Measure the knee first
Production samples are 1–2 runs per width. Write `scripts/bench_search_fanout.py`:
sweep N = 1…16 concurrent against the real `search_topics` / `search_case_topics`
RPCs with a fixed embedding set, ≥5 reps per width, report p50/p95 batch wall
time and per-call latency. Pick the cap from the measured knee, not from the
table above.

**Run against a non-peak window** — the benchmark itself saturates the instance.

### A2. One process-wide gate at the narrowest waist
New `agents/deep_search_v4/shared/db_gate.py`:

- `SEARCH_RPC_CONCURRENCY` — env-tunable, default from A1 (start conservative, ~5)
- Lazily-created semaphore keyed on the running event loop via
  `weakref.WeakKeyDictionary` — **do not** construct a module-level
  `asyncio.Semaphore` at import time
- Bounded acquire: `asyncio.wait_for(sem.acquire(), GATE_WAIT_S)`; on expiry
  raise `SearchGateTimeout` (a distinct type, consumed by Part B)
- Emit a `gate_wait_ms` span attribute so queueing is visible

Apply it **inside** `_search_topics_rpc` (`reg_compliance_search/search.py:327`)
and `search_case_topics_rpc` (`case_search/search.py:589`) — the single choke
point both executors and any future caller must pass through. Leave the existing
per-executor semaphores in place; they still cap pipeline-level work, they just
stop being the DB ceiling.

**Traps**
- *Replicas multiply the gate.* N backend replicas ⇒ the DB sees N × cap. Confirm
  the Railway replica count before choosing the number.
- *The gate serializes across concurrent users.* That is correct — the instance is
  shared — but it converts "everyone fails at once" into "everyone queues." The
  `gate_wait_ms` metric and Part B's honest failure path are what keep that
  legible instead of silent.
- *Thread pileup.* The sync Supabase client runs via `asyncio.to_thread`, so a
  stuck call holds a pool thread for the full read timeout
  (`shared/db/run.py` warns about this). A gate of ~5 bounds search to 5 threads.

### A3. Timeout headroom — only after A2
Raise `POSTGREST_TIMEOUT.read` 15 → 25s. Raising it *without* A2 is the worst
option available: the 16-wide run would still sit past the knee and merely
convert a fast failure into a slow one while holding threads longer. With the cap
in place nothing should approach the ceiling, so 25s becomes real headroom over
the legitimate 13.4s observed on 08-18.

### A4. Narrow retry — last, and guarded
Route the two RPCs through `run_db_retry` (`shared/db/run.py`) with
`attempts=2` plus jitter. `is_transient_db_error` already covers every
`httpx.TransportError`, `ReadTimeout` included.

**Guard against re-firing a saturated instance.** A whole batch timing out
together is the saturation signature, not a dropped socket, and retrying it
doubles load on an instance that has already collapsed. `is_transient_db_error`
cannot distinguish the two. Add a per-run transport-failure counter on deps: once
≥K calls in this run have died in transit, suppress further retries and fail
straight through to Part B.

---

## Part B — Stop laundering failure as emptiness

Worth doing on its own merits: until "0 rows" and "the call never answered" are
distinguishable, any performance fix still degrades silently next time.

- **B1.** Change the failure return at `reg_compliance_search/search.py:312-321`
  (and the `case_search/search.py` equivalents) to carry the error rather than
  `[], 0`. Small result object or a `(rows, count, error)` tuple — either way the
  caller must be able to tell the two apart.
- **B2.** Thread it up: `SearchResult` → `RerankerQueryResult` →
  phase result → `AggregatorInput`.
- **B3.** Phase spans gain `failed_queries` / `total_queries`; `outcome` becomes
  `degraded` when `failed_queries > 0`. Today they say `ok` while every call dies.
- **B4.** Capture `max_score` per sub-query (see §B6) and derive
  `retrieval_status` mechanically (see §C1).
- **B5.** Arabic copy: on `failed`, say the search could not reach the database
  and offer a retry — **never** «لم أعثر على نصوص نظامية», which asserts a fact
  about the corpus that we did not establish. This is the user-visible harm: the
  turn told a lawyer the corpus was silent on a question it covers well.

### B6. Capture the similarity scores — the only mechanical relevance signal

`search_topics` is **k-NN with no threshold** — `search.py` states it outright
("no absolute gate"). It returns `p_per_type` rows per source type ranked by
score whether or not anything is relevant; every probe returned 59–60 rows.

Two consequences:

1. **`rows == []` from a healthy call is near-impossible.** The sector-filtered
   case already retries unfiltered (`search.py:190-202`). So an empty result today
   is almost always a *failure*, which is exactly why the current `return [], 0`
   is so damaging — it forges the one signal that should never occur naturally.
2. **Row count carries no relevance information. Score does.** Persist
   `max_score` (and the top-5 score vector) per sub-query alongside the row count,
   through the same path as B2, and onto the phase spans.

**Measured calibration** (2026-08-22, against live prod):

| Band | Meaning | Evidence |
|---|---|---|
| ≤ 0.378 | indistinguishable from noise | random unrelated topic pairs: p50 0.244, p95 0.331, max 0.378 (n=60) |
| 0.42 – 0.49 | topical drift | «غرامات مزاولة نشاط إعلام مرئي» against a municipal-licensing probe |
| ≥ 0.55 | genuinely on-topic | a covered subject sits in a dense cluster: 0.788 / 0.770 / 0.709 / 0.614 / 0.570 |

Proposed `ABSENCE_FLOOR ≈ 0.45`, comfortably clear of the 0.378 noise ceiling and
of the 0.55+ coverage band.

**Calibrate before trusting the number.** The measurements above are
corpus-topic → corpus-topic. Production queries are expander-generated sub-queries
embedded from natural language and may score systematically lower. So: **ship the
score logging first, observe the real distribution, then set the floor.** Do not
hard-code 0.45 on the strength of this table alone.

---

## Part C — The responder reflect loop

The original ask was: when results are insufficient, loop back and search the
other mode. Two findings reshape it.

1. **There was no other mode.** The planner had already picked `full`, which runs
   both executors; `support` is ignored in `full`
   (`planner/models.py:88-95`). Mode-switching is a no-op for the common failure.
2. **The scaffolding already exists and is dead.** `state.round_count`,
   `state.weak_axes` (read only when `round_count > 1`,
   `reg_compliance_search/loop.py:119`) and the per-sub-query `sufficient` flag
   are all present, but `RerankerNode` unconditionally returns
   `_end_placeholder(state)` and never routes back to `ExpanderNode`. Every
   production run shows `rounds_used: 1`.

### C1. Trigger — a mechanical three-state classifier

**Do not trigger on `sufficient` or on the aggregator's `gaps[]`.** Both are LLM
opinions: `sufficient` is a reranker verdict, `gaps[]` an aggregator verdict.
Steering a second retrieval wave on either means one flash model's judgment
decides whether to spend real money and latency — that is a second guess, not a
discriminator. (An earlier draft of this plan proposed exactly that. It was
wrong.)

There are **three distinct states** and each has a purely mechanical signature:

| State | Signature (all facts, no LLM) | Retry? |
|---|---|---|
| `failed` | an RPC raised — no HTTP response at all | **No.** The DB is broken; a retry re-fires into it, and behind the A2 gate it queues behind the calls already stuck. |
| `absent` | every RPC returned 200, but `max_score < ABSENCE_FLOOR` across **all** sub-queries | **No.** Nothing relevant exists; a second pass cannot invent it. Say so plainly. |
| `lost` | `max_score ≥ ABSENCE_FLOOR` on at least one sub-query, yet **zero** results reached the URA | **Yes.** This is the only retry-worthy state. |
| `ok` | results reached the aggregator | n/a |
| `partial` | some sub-queries `failed`, others returned | Treat as `failed` for retry purposes; report honestly. |

The reframe that makes this work: **stop asking "was it sufficient?" and start
asking "did high-scoring candidates exist that failed to reach the aggregator?"**
That is a measurable mismatch between two pipeline stages — the k-NN scores on one
side, the URA population on the other — not a model's opinion about its own
output.

`lost` is precisely the reranker-drop failure mode already documented in
`project_reranker_keep_only_calibration` and `project_reranker_scope_leakage`. The
trigger detects it without consulting the reranker's own verdict.

Additional gates on the `lost` path:
- `retry_count == 0` for this turn
- elapsed wall-clock inside budget (§C5)

### C2. The thin-cases escalation — PRIMARY BEHAVIOUR (owner-specified)

This is the reflect loop for v1. It is **not** a sequential retry; it is a
parallel escalation that the aggregator joins on.

**Trigger — all mechanical:**
- `decision.mode == "case_led"` (not `full`, not `reg_compliance_led`)
- distinct case candidates across **all** sub-queries `< CASE_FLOOR` (default 5)
- counted **post-grouping, pre-reranker** — i.e. the candidate set the reranker
  would be shown
- **and** no case RPC reported `failed` (§C1) — see Trap 2

**Action:**
1. The cases that *were* found continue to the aggregator unchanged — never discarded.
2. The `reg_compliance` executor fires **in parallel**.
3. The aggregator awaits both, then synthesises over the union.
4. The answer **must** disclose that insufficient judgments were found in the
   database — even when the cases it did find are used.

**Implementation shortcut.** `apply.py:61` already defines
`{"base": "cases", "support": "reg_compliance"}`, and the support executor runs
iff `decision.support` is True with `ROLE_PROFILES["support"]["result_budget"] = 30`.
So this is **late-binding `support=True`** — reuse that path rather than inventing
an escalation mechanism.

**Threshold sanity.** `case_search/search.py:40-43` states the grouping floor is
calibrated so output "reliably yields ≥ 25 distinct cases — more than the 15 the
reranker is shown." `< 5` therefore sits far below design floor: a deep-failure
signal, not a marginal one. Keep `CASE_FLOOR` env-tunable.

#### Trap 1 — the trigger point — RESOLVED
Hook the count **between `FusionNode` and `SectionedRerankerNode`**.

The case graph is `SectionedExpanderNode → SectionedSearchNode → FusionNode →
SectionedRerankerNode`. `group_topic_rows` groups per query *per channel*, so
before fusion the same case appears several times across sub-queries and
channels — counting there overcounts and under-triggers. **Fusion is the earliest
point at which the distinct-case count is correct**, and waiting for it is free
(sub-millisecond in the trace: 15:49:00.856 → 15:49:00.8567).

Timing, from the §0 trace: case expander ~15s + search ~15s + fusion ~0s, then the
reranker. A reg pass ≈ expander 13s + search 10s + rerank 10s ≈ 33s.

- fire after the case reranker → 33s bolted on, fully serial
- fire at the fusion boundary → reg overlaps the case reranker → net ≈ 23s

This also matches the spec wording exactly ("less than 5 cases *for the
reranker*" = a pre-rerank count). The reranker only ever drops from that count,
so waiting for it could not change the decision anyway.

**Rejected: speculative firing from t=0.** Running reg in parallel on every
`case_led` turn and discarding it when unneeded would hide the latency entirely,
but pays a full reg executor on every such turn to save time on a rare path.
Revisit only if S0 telemetry shows thin-case turns are common — distinct-case
count per turn is exactly what S0 logs.

**Architecture — the executor must not spawn its peer.** Having `FusionNode`
launch the reg executor from inside the case graph puts a Layer 3 task agent in
charge of starting a sibling, and makes the phase spans misreport what ran
beneath them. Instead: `FusionNode` sets an `asyncio.Event` on `CaseSearchDeps`
carrying the count; the orchestrator races it against the case phase task
(`asyncio.wait(..., return_when=FIRST_COMPLETED)`) and owns the escalation
decision. The graph **signals**; the orchestrator **escalates**. The case graph
continues to its reranker untouched.

**Safety net.** If the signal never fires but the final phase result still shows
`< CASE_FLOOR`, escalate then — degrading to the sequential path rather than
silently skipping the escalation.

#### Trap 2 — a low count can mean failure, not absence
If some `search_case_topics` calls died in transit (§0 laundering site), the
distinct-case count is artificially low. Escalating is then harmless, but
**disclosing "insufficient judgments in the database" would be false** — the exact
E8 harm this plan exists to fix, re-introduced through a new door. So:

- gate the trigger on `retrieval_status != failed` for the case phase
- when any case RPC failed, escalate **but** disclose a retrieval problem, not a
  corpus absence

This is why C2 depends on S4, not just on S0.

#### Trap 3 — this feature REQUIRES the S2 gate
Escalation puts reg's ~10 sub-queries in flight while the case phase is still
finishing — the exact ~16-wide profile that produced the outage in §0. Shipped
without the fan-out cap, this feature *manufactures* the original failure, and it
does so preferentially on the queries that most need help. **Hard dependency: S2
must be live first.**

### C3. The disclosure must be mechanical, not LLM discretion
Set `insufficient_cases: true` on the aggregator input when C2 fires, render the
Arabic notice from code (or mandate a prompt section and assert it), and
**post-validate that it survived**.

Precedent exists: `aggregator/postvalidator.py:514` already enforces "if any
sub_query is insufficient, `gaps[]` must be non-empty." Same shape here. Do not
leave the disclosure to the aggregator LLM's judgement — a model that has just
written a confident answer off strong regulatory sources is exactly the model
that will quietly drop a caveat about weak case law.

### C4. Later increments — not in this plan
Deferred until C2 is live and measured:
- retry on the `lost` state (§C1) — high scores, zero reached the URA
- dropping the sector filter on a second pass (precedent at `search.py:190-202`)
- reviving round-2 / `weak_axes` re-expansion
- the symmetric case: `reg_compliance_led` with a thin reg harvest escalating to
  cases — **not requested; do not build speculatively**

### C5. Guards
- **Max one retry per turn.** Non-negotiable.
- **Never retry on `retrieval_status == failed`** — a broken DB re-fails, and with
  the A2 gate in place it also queues behind the very calls that are stuck.
- **Hard wall-clock budget.** The failing turn already took 62s; a second full
  pass risks the ~177s gateway cancel documented in
  `project_planner_responder_resume_query`. Bound pass 2 explicitly.
- **Emit SSE progress** («جاري توسيع نطاق البحث…») so the extra latency reads as
  work, not as a hang. Ties into `project_deep_search_progress_bar`.
- **Fan-out stays capped on pass 2.** Pass 2 must go through the same A2 gate —
  a retry that re-fires 16 wide reproduces the original failure exactly.

---

## Sequencing

**A → B → C, in that order.**

Building C first is actively harmful: with today's code the loop's trigger
condition (`references == 0`) is exactly what a DB outage produces, so the very
first thing the reflect loop would do is re-fire a second 16-wide fan-out at an
instance that has already collapsed — doubling the load and taking twice as long
to produce the same apology.

B is the gate for C: without the transport-error signal (B1–B3) *and* the
similarity scores (B6), C cannot separate the three states in §C1 — it can only
see "zero results," which is the one thing all three have in common.

Note the score logging in B6 is also the calibration input for `ABSENCE_FLOOR`,
so it must run in production for a while before C's threshold can be set.

---

## Execution workflow

Seven stages. Each names its gate — do not start the next until the gate passes.

```
S0 instrument ──┬── S1b observe scores (calendar time) ── S4 classify ── S5 copy ── S6 reflect loop
                │                                                                        ▲
                └── S1a bench knee ── S2 bound fan-out ── S3 harden ────────────────────┘
```

| # | Stage | Does | Gate to proceed |
|---|---|---|---|
| **S0** | Instrument | B1–B3 failure flag + B6 score capture, surfaced on phase spans. **No behavior change** — still returns empty, just no longer silently. | Prod spans show `max_score` and `failed_queries`. |
| **S1a** | Bench the knee | `scripts/bench_search_fanout.py`, N=1…16, ≥5 reps, **off-peak** (the bench itself saturates). | A `SEARCH_RPC_CONCURRENCY` chosen from measured p50/p95, not from §0's table. |
| **S1b** | Observe scores | Watch real expander sub-query `max_score` from S0. Needs ≥2 weeks of traffic; Logfire clamps queries to 14 days, so persist per-turn rows if the window must be longer. | An `ABSENCE_FLOOR` chosen from the observed distribution. |
| **S2** | Bound fan-out | `db_gate.py`, applied inside `_search_topics_rpc` + `search_case_topics_rpc`. Bounded acquire, `gate_wait_ms`. | No run exceeds the cap; p95 batch wall time improves; a 16-wide load test produces zero timeouts. |
| **S3** | Harden | read 15→25s; `run_db_retry(attempts=2)` + jitter + saturation guard. | Injected *single* socket failure recovers. Injected *mass* failure does **not** retry-storm. |
| **S4** | Classify | Derive `retrieval_status ∈ {ok, partial, failed, absent, lost}` per §C1. | Replaying turn `483b00d8` classifies **`failed`**, not `absent`. |
| **S5** | Honest copy | Arabic per state; never assert corpus absence on `failed`. | Each of the five states renders its own copy; no state can emit «لم أعثر على نصوص» unless it is `absent`. |
| **S6** | Thin-cases escalation (§C2) | `case_led` + `< CASE_FLOOR` distinct cases ⇒ late-bind `support=True`, fire reg in parallel, aggregator joins, mandatory disclosure. | Escalation fires on a thin `case_led` turn and not otherwise; `failed` never produces an absence claim; peak in-flight stays under the S2 cap; the disclosure survives post-validation. |

**Why this order.** S0 is risk-free and unblocks everything — ship it first and
alone. S1a/S2/S3 (the outage fix) and S1b/S4/S5 (the honesty fix) then run as two
independent tracks; only S6 needs both. S6 is last because its trigger condition
is indistinguishable from an outage until S4 exists, and because a second wave is
only safe once S2 caps it.

**Rollback.** S2 and S3 are single env-tunable constants
(`SEARCH_RPC_CONCURRENCY`, `POSTGREST_TIMEOUT.read`) — revert without a deploy.
S6 ships behind a flag, default off.

## Build status — 2026-08-22

**Shipped (uncommitted):** S0 instrumentation, S2 gate, S3 timeout half, S1a script.
561 `deep_search_v4` tests pass.

- `shared/db_gate.py` — process-wide gate, `SEARCH_RPC_CONCURRENCY=5` (one below the
  measured knee, leaving a slot for non-search PostgREST traffic on the same pool),
  `GATE_WAIT_S=30`. Smoke-tested: 16 callers → peak 5 in flight.
- `POSTGREST_TIMEOUT.read/write` 15 → 25s; `_client_options()` now references the constant.
- reg + case: typed failure (`SearchOutcome` / `CaseSearchOutcome`), score capture,
  gate wiring, `max_score` on the executor-local `RerankerQueryResult`.
- `orchestrator.py`: reg phase span stamps `failed_queries` / `total_queries` /
  `max_score` / `gate_wait_ms` and flips to `outcome="degraded"`; case phase log
  record gains the same fields plus `distinct_cases`.
- `scripts/bench_search_fanout.py` — S1a sweep, validated end-to-end at width 1.

**Corrections to §0 forensics**
- The `outcome: "ok"` observed during the incident was the **reg** phase only.
  `_run_case_phase` is not wrapped in `track_stage` at all — the case phase was
  **silent**, not misreporting. It is still a bare `_logfire.info` record; promoting
  it to a real span is a follow-up.
- The case path is **not** symmetric with reg. Reg had one blanket catch; case has
  five narrower ones. The incident site (`case_search/search.py:644-646`) is fixed;
  two on the legacy `prompt_1`/`prompt_2` path are documented and left in place
  (`orchestrator.py:103` pins `prompt_3`, so they are unreachable in production).
- `case_search` has a score threshold that reg does not: `deps.score_threshold`,
  default **0.005** (`case_search/models.py:411`), applied to topic rows before
  grouping. That is a null-vector guard, not a relevance gate — far below the 0.378
  noise ceiling — so it will essentially never explain a low candidate count.
  `topic_rows` / `topic_rows_kept` are now captured so this is answerable from
  telemetry rather than from reasoning about the default.

**Known gaps in what shipped**
1. **`max_score` / `retrieval_error` stop at the executor-local `RerankerQueryResult`.**
   `ura/reg_adapter.py:207` and the case adapter build the *shared* RQR field-by-field,
   and `shared/models.py` has no such fields — so the signal reaches the spans and
   local logs but **not the aggregator's input**. This is S4's first task: without it
   the classifier in §C1 cannot see the scores at decision time.
2. **New tests will not ship.** `.gitignore:14` is a bare `tests/`. Both agents' new
   regression tests pass locally but are ignored. Precedent exists for re-including
   durable suites (`!shared/privacy/tests/`, `!backend/tests/test_masking_wiring.py`);
   these are regression cover for a production incident and arguably qualify.
   Repo-policy decision, not taken.
3. **Arabic `summary_note` still says «لا توجد نتائج» on a dead RPC.** That string is
   aggregator input, so changing it is a model-input change — B5's job, not
   instrumentation. Typed `error` / `retrieval_error` are in place for whoever takes it.
4. **S3's retry is not built** — only the timeout half. The saturation guard still
   needs designing.

**Unrelated pre-existing breakage** (confirmed not caused by this work):
`agents/tests/test_cost_ledger.py` fails to import — `tier_of_subagent` is absent from
`agents/utils/agent_models.py`. Plus 10 failures / 35 errors across `router`, `writer`,
`item_analyzer`, `utils`. Also stale: `agents/utils/tests/test_tracking_enforcement.py:117`
allowlists `case_search/reranker.py` as "tracked by deep_search.phase.case span" — that
span does not exist.

## Found during build — not yet addressed

**The async Supabase clients are unhardened (120s timeout).**
`get_async_supabase_client()`, `get_async_supabase_anon_client()` and
`get_user_client()` (`shared/db/client.py:209`, `:226`, `:238`) pass no
`ClientOptions` and are never routed through `_harden_sessions()`. They run on
supabase-py's default **120s** postgrest timeout — so neither the old 15s nor the
new 25s applies to anything on those paths. "The PostgREST timeout is 25s" is
true only for the sync client.

Not in scope for S0–S3 (deep_search retrieval is entirely on the sync path), but
it is a 120s hang waiting to happen elsewhere in the backend. Triage separately:
enumerate the async-client callers, decide whether they want the same 25s or
something workload-specific, and harden them the way the sync client is.

## Open decisions

0. **`ABSENCE_FLOOR` value** — do not hard-code from the §B6 table. Ship the score
   logging, observe real expander sub-query scores in production, then set it.
1. **Exact cap number** — pending the A1 benchmark. Start at 5 if we ship before
   measuring.
2. **Scale up vs. cap down** — the Supabase compute tier has not been checked. If
   the instance is small, the knee is low and raising the tier is a legitimate
   fourth lever to price against engineering time. Worth checking before A2 lands.
3. **Replica count** — determines whether the process-wide gate is the real
   ceiling or needs dividing by N.
4. **Is pass 2 billed?** A second retrieval wave costs real money against the
   points plans (`project_subscription_plans`). Decide whether a reflect pass
   consumes points, and whether the user is told it happened.
