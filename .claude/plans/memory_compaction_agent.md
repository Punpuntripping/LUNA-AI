# Memory Compaction Agent — replacing the Wave 9 mocks with a real DeepSeek V4 summarizer

> **Status:** BUILT 2026-08-09 — steps 1, 3, 4, 5, 6, 8 done; 85 tests green. NOT deployed, data repair NOT run. See §9 "As built" for where the code deviates from this plan.
> **Scope:** `agents/memory/agent.py` — the last two mock LLM surfaces in the live pipeline.
> **Closes:** Wave 10 stub item #1 (`.claude/plans/wave_10_stub.md:12`).
> **Model:** deepseek-v4-flash (tier_2, deepseek-primary), per the `artifact_summarizer` / `item_analyzer` precedent.

---

## 1. The problem

Compaction is **real** — it advances the context cutoff and drops messages from every
downstream agent's view. The summary that replaces those messages is a **hardcoded
f-string**. So the pipeline genuinely deletes conversation history and substitutes a
sentence that carries none of it.

**Evidence — `agents/memory/agent.py:102-111`:**

```python
def _mock_compaction_summary(n_messages: int, conversation_id: str) -> str:
    return (
        f"ملخص للمحادثة السابقة: {n_messages} رسالة بين المستخدم والمساعد "
        f"في المحادثة {conversation_id}. "
        "تمت معالجة هذه الرسائل وتلخيصها للحفاظ على السياق مع تقليل استهلاك الرموز."
    )
```

Two message-count/UUID slots and no content. This is what the user sees in the
`ملخص المحادثة` viewer, raw UUID and all — and, more importantly, it is what four
downstream consumers see instead of the dropped turns.

### 1.1 Blast radius — who reads the mock

| Consumer | Path | What it receives |
|---|---|---|
| **Router** (every turn) | `agents/router/context.py:221-227` → `router.py:549-554` `inject_compaction_summary` | The mock verbatim, injected as "Conversation compaction summary (before the current window of messages)" |
| **Router message window** | `agents/router/context.py:240-291` `_load_filtered_messages` | Messages `> compacted_through_message_id` **only** — the compacted turns are gone |
| **Writer** | `backend/app/services/workspace_context.py:226-232` → `agents/writer/models.py` | `convo_context.content_md` = the mock |
| **Attachment summarizer** | `agents/memory/summarize.py:231-254` | The mock, clipped to 1500 chars, as "ملخّص سياق المحادثة السابق" |

### 1.2 Second bug — the mock is *overwriting real* item summaries

`agents/memory/agent.py:118-172` defines a module-local mock
`summarize_workspace_item` that writes `_mock_item_summary` (`agent.py:90-99`):
`ملخص للعنصر: {title} - {content_md[:200]}`.

`resummarize_dirty_items` (`agent.py:218`) calls **that local mock**, not the real
one in `agents/memory/summarize.py`. The orchestrator imports the mock module
directly (`agents/orchestrator.py:40`, `import agents.memory.agent as memory`) and
runs it in the pre-router hook on **every turn** (`orchestrator.py:1465`).

Consequences:

- **Overwrite.** Any item whose `content_md` drifts ≥25% (`DRIFT_THRESHOLD`,
  `agent.py:24`) gets its real artifact_summarizer summary replaced by a 200-char
  truncation on the next turn.
- **Backfill of junk.** `summarize.py:57` deliberately leaves `summary = NULL` for
  items under `MIN_CONTENT_LENGTH_CHARS = 300`. The mock treats NULL as dirty
  (`agent.py:209-211`) and fills it with `ملخص للعنصر: …` — so the gate that was
  supposed to save a call instead guarantees junk.
- Those summaries render into the router's workspace-item list
  (`context.py:228-236`), which is the surface the router uses to pick
  `attached_item_ids`.

This second bug is cheaper to fix than the compactor and should ship first.

### 1.3 Third bug — compaction #2 orphans compaction #1

`_load_workspace_item_summaries` keeps only the **most recent** `convo_context` by
`created_at` (`context.py:221-227`); `load_workspace_context` does the same
(`workspace_context.py:226-232`). Nothing folds the previous summary into the new
one, and `compact_conversation` only ever reads messages *after* the cutoff
(`agent.py:294-304`). So on the second compaction the first summary is silently
dropped and that span of the conversation is unrecoverable from context.

**Requirement:** the compactor MUST take the prior `convo_context.content_md` as an
input and emit a summary that *supersedes* it (covers both spans).

---

## 2. What the summary must actually contain

Per the product decision: the compaction summary is not a transcript recap. It
carries forward **two things** — plus one that falls out of them.

1. **نية المستخدم (user intent)** — the through-line. What the user is trying to
   accomplish across the compacted span: the matter, the parties, the role they're
   acting in, the constraints and preferences they stated, the framings they
   *rejected*. This is the part a message-by-message recap loses and the part the
   router most needs to route the next turn correctly.
2. **ما أُنتج من عناصر (workspace items produced)** — which WIs came out of that
   span, what question each answered, and where each fell short. Referenced by
   `WI-{wi_seq}` so the router can `unfold_workspace_item` on demand instead of
   guessing.
3. **خيوط مفتوحة (open threads)** — asks that were raised and not resolved, and
   decisions still pending on the user. Cheap to emit, and it's exactly what a
   dropped-message window otherwise destroys.

Note the WI rows themselves are **not** dropped by compaction — `context.py:228-236`
still lists every non-`convo_context` item. So section 2 is not an inventory; it is
the *narrative link* between what the user wanted and which item answered it. Ground
it by passing the WI list as input, and forbid inventing items not in that list.

---

## 3. Design

New package `agents/memory/convo_compactor/`, mirroring `agents/memory/artifact_summarizer/`
file-for-file. That package is the reference implementation for a Layer-4 memory
agent in this codebase and every convention below is lifted from it.

```
agents/memory/convo_compactor/
  __init__.py      exports run_convo_compaction, build_compactor_deps, models
  agent.py         create_convo_compactor() — Agent factory + UsageLimits + TextOutput salvager
  models.py        CompactionInput / CompactionLLMOutput / CompactionOutput
  prompts.py       SYSTEM_PROMPT_AR + build_user_message()
  deps.py          CompactionDeps (optional dump logger)
  runner.py        handle_compaction_turn() — one LLM call, track_stage, record_run
  tests/           TestModel + FunctionModel unit tests
```

### 3.1 Model slot

Register in `agents/utils/agent_models.py:189` `AGENT_MODELS`:

```python
# Tier_2 DeepSeek-primary — Layer-4 memory agent that compacts the oldest span
# of a conversation into a single carry-forward summary (user intent + produced
# WIs + open threads). One LLM call per compaction, fires at most once per turn
# and only above the 10k-token threshold. reasoning=medium: the job is inference
# over a long noisy span (what is the user actually after?), not extraction —
# but it does not warrant max-effort latency in a pre-router hook.
"convo_compactor":            _FLASH_MEDIUM,
```

`_FLASH_MEDIUM` (`agent_models.py:187`) is `ModelPolicy("tier_2", primary="deepseek",
reasoning="medium")` — deepseek-v4-flash head, and `_reasoning_settings`
(`agent_models.py:115-156`) bakes the correct per-cell reasoning control onto every
cell of the fallback chain (`reasoning_effort="high"` for DeepSeek-on-DashScope,
`thinking_budget=8000` for Qwen, `reasoning.effort="medium"` for OpenRouter).

> **Do NOT** copy `artifact_summarizer`'s agent-level
> `model_settings={"extra_body": {"enable_thinking": True}}` (`artifact_summarizer/agent.py:63-67`).
> `enable_thinking` is a **Qwen** control; on a DeepSeek head it is inert. The
> `reasoning=` policy field is the correct mechanism and it handles all four cells.

### 3.2 Contracts (`models.py`)

```python
@dataclass
class CompactionInput:
    messages: list[dict]          # [{role, content}] — the batch being compacted, oldest-first
    workspace_items: list[dict]   # [{wi_seq, kind, title, summary}] created in/before the span
    prior_summary_md: str = ""    # previous convo_context.content_md — "" on first compaction
    user_call_name: str | None = None   # optional; keeps the summary readable


class CompactionLLMOutput(BaseModel):
    """Single semantic field — the whole summary as Arabic markdown."""
    summary_md: str = Field(description="...")


class CompactionOutput(BaseModel):
    summary_md: str
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_reasoning: int = 0
    tokens_cached: int = 0
    model_used: str = ""
    failed: bool = True    # NOTE: defaults True — see §3.5 fail-closed
```

**Single output field, deliberately.** The three sections live in the prompt's
prescribed shape, not in three Pydantic fields. That makes the `TextOutput`
salvager loss-free — a flash model that finalises as plain text maps 1:1 onto
`summary_md` with no `ModelRetry` round, exactly as
`artifact_summarizer/agent.py:31-45` `_text_as_summary` does. A three-field output
would lose two sections on a text emission and force the retry. (Project precedent:
[[project_structured_output_salvage]].)

```python
output_type=[CompactionLLMOutput, TextOutput(_text_as_summary)]
```

with `_text_as_summary` raising `ModelRetry` below ~150 chars (a summary shorter
than that is not carrying a 10k-token span).

### 3.3 Prompt (`prompts.py`)

Follow the house style established in `artifact_summarizer/prompts.py`: **English
instructions, Arabic output**, explicit output-language guard, audience stated as
*other agents*, no user-facing tone.

Load-bearing content:

- **Audience.** The reader is the router / planners on later turns. Dense, neutral,
  no preamble, no closing, never address the user in second person.
- **Purpose.** "These messages are being REMOVED from the context window. Your
  summary is the only thing that survives. Anything you omit is lost."
  State this literally — it is the single instruction that most changes output
  quality on a compaction task.
- **The three sections** (§2 above), as a prescribed shape:

  ```
  **نية المستخدم:**
  [ما يسعى إليه المستخدم عبر هذه المرحلة — الموضوع، الأطراف، الصفة، القيود
   والتفضيلات التي صرّح بها، والصياغات التي رفضها]

  **ما أُنتج من عناصر:**
  - **WI-{n} — [العنوان]:** [السؤال الذي أجاب عنه، وما بقي ناقصاً]

  **خيوط مفتوحة:**
  - [طلب لم يُستوفَ / قرار معلّق على المستخدم]
  ```

- **Superseding rule.** "When `<prior_summary>` is present, your output REPLACES it.
  Fold its content in — do not reference it, do not assume the reader will see it."
- **Grounding rule.** Only cite `WI-{n}` values that appear in `<workspace_items>`.
  Never invent an item, a party, a date, or an amount.
- **Preserve verbatim:** names, dates, amounts, case/contract numbers, statute and
  article references. These are the tokens a legal follow-up turn actually needs;
  they must survive compaction unparaphrased.
- **Length target:** ~200–500 words. Soft, not enforced. Rationale: it must stay
  under the 1500-char clip that `summarize.py:49` applies when the attachment flow
  re-reads it, and a compaction summary that grows unboundedly defeats the purpose.

`build_user_message()` renders XML-ish tags matching the house pattern
(`prompts.py:86-99`): `<prior_summary>`, `<workspace_items>`, `<messages>`.

### 3.4 Runner (`runner.py`)

Structurally identical to `artifact_summarizer/runner.py:37-154`:

```python
with track_stage(
    "convo_compactor.run",
    conversation_id=...,
    agent_family="memory",
    message_count=len(input.messages),
    wi_count=len(input.workspace_items),
    has_prior_summary=bool(input.prior_summary_md),
) as span:
    ...
    result = await agent.run(user_message, usage_limits=COMPACTOR_LIMITS)
    span.record_run(result, slot="convo_compactor")   # ← emits the llm_calls ledger row
```

`record_run` is what lands the per-call cost row (`agents/utils/tracking.py:454`
`AgentSpan`), so compaction stops being invisible to
[[project_llm_calls_ledger]] — today `agent.py:395-397` explicitly notes there is no
ledger row because there is no call.

`COMPACTOR_LIMITS = UsageLimits(output_tokens_limit=20_000, request_limit=2)` —
same as `SUMMARIZER_LIMITS` (`artifact_summarizer/agent.py:25-28`); reasoning tokens
can spike on a long span.

**Input clipping.** Clip each message body (suggest 2000 chars) and cap the batch
before rendering. A 10k-token threshold with a 0.60 fraction means ~6k tokens of
messages in the worst normal case, which is fine — but a single pathological message
must not blow the request.

### 3.5 Fail-closed — the load-bearing rule

> **If the LLM call fails or returns an empty summary, `compact_conversation` MUST
> return `None` without inserting a `convo_context` item and without advancing
> `conversations.compacted_through_message_id`.**

This inverts the artifact_summarizer's best-effort contract, and the inversion is the
whole point. `artifact_summarizer` failing degrades an *enrichment* field — a
truncated fallback (`runner.py:96`, `content_md[:500]`) is strictly better than
nothing. Compaction failing and proceeding **destroys context**: the cutoff advances,
the messages leave the window, and a fallback string stands in for them. That is
precisely the present bug, and shipping a real model with a truncation fallback would
reproduce it in a subtler form.

Cost of failing closed: the conversation stays over the token threshold and retries
on the next turn. That is the correct trade — bounded by turn rate, self-healing.

**Retry-storm guard.** A conversation whose compaction fails persistently would
re-fire once per turn. Stamp `conversations.metadata.compaction_attempt.at` before
the call and skip if within a cooldown (suggest 600s), mirroring
`summarize.py:135-162` `_mark_attempt` / `ATTEMPT_RECENT_WINDOW_S`.

### 3.6 Masking (وضع السرية)

The compactor reads real message content and **stores** its output, so it is subject
to the store-real invariant documented at `summarize.py:60-77`:

- **Encode** every LLM-bound surface — message bodies, WI titles/summaries,
  `prior_summary_md` — via the turn codec.
- `persist_new_mappings(supabase, user_id, codec)` **before** the call.
- **Decode** `summary_md` before writing it to `workspace_items.content_md` /
  `summary`.

Context note: unlike `summarize.py`, the compactor runs **only** inline in a chat turn
(pre-router hook, `orchestrator.py:1466`), so `active_codec()` is always set — the
explicit-build branch of `_summarize_codec` is not needed. Reuse the helper anyway
rather than forking it; if `_run_memory` (`orchestrator.py:2488`) ever becomes a live
dispatch path, the detached case appears.

### 3.7 Cost scope

The pre-router hook must run inside a `collect_llm_calls` scope or the ledger row is
dropped. **Verify** that `orchestrator.py:1463-1468` sits inside `handle_message`'s
`collect_llm_calls` block (`orchestrator.py:65` imports it) — if it does not, wrap
the compactor call using the `in_scope()` guard pattern from
`summarize.py:338-355` `_maybe_scope`. Do not assume; check the actual nesting.

---

## 4. Wiring changes

### `agents/memory/agent.py`

| Action | Target |
|---|---|
| **DELETE** | `_mock_item_summary` (:90-99), `_mock_compaction_summary` (:102-111), the local mock `summarize_workspace_item` (:118-172) |
| **REWIRE** | `resummarize_dirty_items` (:218) → `from agents.memory.summarize import summarize_workspace_item` (the real one) |
| **REWIRE** | `compact_conversation` step 7 (:348) → `run_convo_compaction(...)`, fail-closed per §3.5 |
| **ADD** | Load `prior_summary_md` (latest `convo_context.content_md`) and the WI list before the call |
| **UPDATE** | Docstrings at :3-5, :253, :257-258 and the comment at :395-397 (all say "mock" / "no ledger row") |

Removing the local `summarize_workspace_item` is a **public-name change** — grep for
other importers of `agents.memory.agent.summarize_workspace_item` before deleting.
`agents/memory/__init__.py:13-31` does not re-export it (despite its docstring at
:5-6), and `orchestrator.py:42` already imports the real one from
`agents.memory.summarize` under the same name — so the two names currently coexist in
the orchestrator module namespace with different behaviour. Worth a second look
during implementation.

### `agents/utils/agent_models.py`

Add the `"convo_compactor"` slot (§3.1).

### `agents/memory/__init__.py`

Export `run_convo_compaction` + models alongside the artifact_summarizer exports, and
fix the docstring at :5-6, which claims `compact_conversation` /
`resummarize_dirty_items` are exported when they are not.

### No frontend change required

The viewer already exists (`WorkspacePane.tsx:187`, `WorkspaceList.tsx:15`). Separate
question worth raising: `compact_conversation` inserts with `is_visible: False`
(`agent.py:367`) because `convo_context` is internal, yet `WorkspaceList.tsx:26`
includes the kind and the user can open it. Either the item should be genuinely
hidden or `is_visible` should be `True` — the current state is neither. **Out of scope
for this plan; flagging it.** Once the summary is real it is arguably worth showing.

---

## 5. Data repair

Production rows carry mock text. Both are repairable — nothing was deleted, only
cut off.

### 5.1 Mock item summaries → NULL

```sql
-- Count first.
SELECT count(*) FROM workspace_items
WHERE summary LIKE 'ملخص للعنصر: %' AND deleted_at IS NULL;

-- Then clear; the real summarizer refills on the next dirty pass.
UPDATE workspace_items
SET summary = NULL, summary_source_length = NULL, summary_updated_at = NULL
WHERE summary LIKE 'ملخص للعنصر: %' AND deleted_at IS NULL;
```

Beware the interaction with `MIN_CONTENT_LENGTH_CHARS` (`summarize.py:57`): items
under 300 chars will stay NULL forever after this — which is the intended design, and
is why they must no longer be treated as dirty. Confirm `resummarize_dirty_items`
tolerates a permanent-NULL item without re-firing an LLM call every turn once it
delegates to the real summarizer; if it does not, add a "NULL is a valid terminal
state" check keyed on `summary_source_length` or a metadata marker.

### 5.2 Mock compaction summaries → regenerate

Compaction never deleted messages, so every compacted span is still in `messages` —
these are fully recoverable via a backfill script
(`scripts/backfill_compaction_summaries.py`):

1. Find `convo_context` rows where `content_md LIKE 'ملخص للمحادثة السابقة: %'`.
2. For each, load that conversation's messages up to `compacted_through_message_id`,
   plus its WI list.
3. Run `run_convo_compaction` and `UPDATE` both `content_md` and `summary`.
4. Log conversation_id + token cost per row; dry-run flag; batch cap.

Cheap (one flash call per affected conversation) and it retroactively restores
context to every live conversation that has been compacted.

---

## 6. Tests

Mirror `agents/memory/item_analyzer/tests/`.

| Test | Asserts |
|---|---|
| `test_compactor_output` | `TestModel` → runner returns `CompactionOutput`, `failed=False` |
| `test_text_salvage` | `FunctionModel` emitting plain text → `summary_md` populated, no retry |
| `test_short_output_retries` | Text under the floor → `ModelRetry` raised |
| `test_llm_failure_fails_closed` | Agent raises → `compact_conversation` returns `None`, **no** `convo_context` insert, cutoff **unchanged** ← the critical one |
| `test_empty_output_fails_closed` | Empty `summary_md` → same |
| `test_prior_summary_in_prompt` | `prior_summary_md` non-empty → appears in the rendered user message |
| `test_wi_list_in_prompt` | WI seqs render as `WI-{n}` |
| `test_below_threshold_no_call` | Under 10k tokens → no LLM call at all |
| `test_resummarize_uses_real_summarizer` | `resummarize_dirty_items` calls `summarize.summarize_workspace_item`, never a mock |
| `test_masking_roundtrip` | Codec-encoded input, decoded output before persist |

---

## 7. Build order

| # | Step | Why this order |
|---|---|---|
| 1 | Rewire `resummarize_dirty_items` → real summarizer; delete `_mock_item_summary` + local `summarize_workspace_item` | Independent of the new agent, stops the active overwrite, one-line-ish fix |
| 2 | Run repair 5.1 | Clears junk the moment step 1 stops producing more |
| 3 | Build `convo_compactor` package + register the slot | The new agent, in isolation |
| 4 | Tests (§6) | Especially the fail-closed pair — before it touches a live cutoff |
| 5 | Rewire `compact_conversation` + fail-closed + attempt cooldown + masking | Wiring, once the agent is proven |
| 6 | Verify cost scope (§3.7); confirm a `llm_calls` row lands with `slot='convo_compactor'` | Ledger correctness |
| 7 | Deploy; validate one real compaction via `/convo-monitor` | Real trace, real cost, real output |
| 8 | Backfill script 5.2, dry-run then live | Retroactive repair, last |

Steps 1–2 are shippable on their own and worth shipping first.

---

## 8. Open decisions

1. **Threshold.** `DEFAULT_COMPACT_MAX_TOKENS = 10_000` / `fraction = 0.60`
   (`agent.py:22-23`) were chosen for a mock that cost nothing. With a real call and
   a real summary, is 10k still right? A flash model's context is far larger; 10k is
   conservative and compacts more often than it needs to. Suggest revisiting to
   ~25–30k once the real summary is trusted — but change it in a **separate** step so
   the summary quality and the threshold change are independently observable.
2. **`tiktoken`.** Still not in `backend/requirements.txt` (`agent.py:29-36`), so
   every threshold check uses `len//4`. Arabic tokenizes worse than that heuristic
   assumes, so real token counts are likely **under**-estimated and compaction fires
   later than intended. Worth adding now that the threshold gates a paid call.
3. **`_walk_to_safe_boundary`** (`agent.py:76-83`) is still a no-op passthrough. It
   is correct today because `messages.content` is plain text — but the moment tool
   parts enter the table this splits `ToolCallPart`/`ToolReturnPart` pairs. Not
   blocking; note it stays open.
4. **`is_visible`** on `convo_context` (§4) — hide it properly, or show it properly.
   **Resolved as cosmetic:** `_load_workspace_item_summaries` (`context.py:200-236`)
   never filters on `is_visible`, so the flag does not affect what the router sees.
   It is purely a UI question.

---

## 9. As built (2026-08-09)

Where the implementation deviates from the plan above. **The code is right and the
plan was wrong** in each case — corrected here rather than in place, so the reasoning
survives.

| # | Plan said | Built instead | Why |
|---|---|---|---|
| 1 | Attempt marker on `conversations.metadata` (§3.5) | `messages.metadata.compaction_attempt` on the **oldest post-cutoff message** | **`conversations` has no `metadata` column.** The message anchor self-invalidates correctly: on failure the cutoff doesn't move, so next turn recomputes the same anchor and finds the marker; on success the cutoff advances past it and the next window gets a fresh slot. |
| 2 | "Rewire `resummarize_dirty_items` → real summarizer" as a one-liner (§7 step 1) | Drift path calls with **`force=True`**, plus a re-implemented `summary_attempt` cooldown | The real summarizer is idempotent — `summarize.py:450` returns early when `summary` is set. A naive rewire makes **drift detection dead code**: every drifted item no-ops forever. `force` bypasses the summarizer's own double-bill guard, so the caller must supply one. |
| 3 | Permanent-NULL guard via "`summary_source_length` or a metadata marker" (§5.1) | Content-length pre-filter on `MIN_CONTENT_LENGTH_CHARS`, imported from `summarize.py` | Neither suggested signal exists for short items: `summarize.py` returns at `:503` **before** `_mark_attempt` runs, and `summary_source_length` stays NULL alongside `summary`. Content length is the only signal actually present — and it's what the daily sweep already uses (`summary_sweeper.py:93`). |
| 4 | — | `RESUMMARIZE_CAP_PER_TURN = 3` | The hook runs **sequentially, before the router**. After the §5.1 repair NULLs a backlog, an uncapped first turn stalls behind N blocking model calls. Counts attempts, not successes. |
| 5 | `_load_compaction_context` returns `("", [])` on failure | Returns **`None`** → fail closed | An empty `prior_summary_md` is indistinguishable from "first compaction", which silently re-triggers the §1.3 orphaning bug. A load failure must not compact. |
| 6 | — | Read phase (steps 1–3 of `compact_conversation`) wrapped | The docstring promises `None` on any failure, but three DB reads raised straight through it. Both live callers wrap the function, so it was contained — the contract is what the next caller reads. |
| 7 | Target 200–500 words, clip stays 1500 (§3.3) | `ATTACHMENT_CONTEXT_COMPACTION_CLIP_CHARS` raised 1500 → **3200** | 200–500 Arabic words is ~1200–3000 chars. The old clip cut the top half of the range mid-sentence, and since `خيوط مفتوحة` is the **last** prescribed section, open threads were exactly what got dropped. |
| 8 | — | `CompactionInput.conversation_id` (telemetry only) | The runner probed `getattr(input, "conversation_id", None)` against a field that didn't exist, so **every** compaction span was stamped `conversation_id=None` — unjoinable in Logfire, which is precisely what §7 step 7's `/convo-monitor` validation needs. |
| 9 | — | `_model_label_from_result` reads `all_messages()` | The copied-from-`artifact_summarizer` version probes `.model` / `._model` on `AgentRunResult`; pydantic_ai 1.39 exposes **neither**, so it always returned the slot label and never reported which fallback cell served the request. `agents/utils/tracking.py:385` had it right all along. |

### Carried forward — not fixed here

- **`artifact_summarizer` has bugs 8-adjacent and 9 verbatim.** `_model_label_from_result` (`artifact_summarizer/runner.py:157-176`) is the same dead lookup, and its `record_run` at `:100` is unguarded while `_NoopHandle` (`tracking.py:543-546`) implements neither `record_run` nor `set_outcome` — so it raises `AttributeError` under `LUNA_TRACK_DISABLE=1`. Both are pre-existing in a deployed agent and out of scope for this plan.
- **Compaction's message set diverges from the router's window.** `router/context.py:293-303` drops `agent_question` / `agent_answer`; `compact_conversation`'s select does not. So the 10k gate is measured over more messages than the router holds, and clarification Q&A the router never saw can land in the summary. Arguably desirable (those turns carry intent) — but it is an unstated divergence between the thing measured and the thing replaced. Left alone deliberately: changing it shifts *when* compaction fires, which §8.1 wants observable on its own.
- **Test-double trap, pinned in `test_bare_testmodel_takes_the_text_branch_and_fails_closed`.**
  (moved below §10 — see the end of this file)

---

## 10. Consumer extension — both planners (2026-08-09)

§1.1 listed four consumers of the compaction summary. It was wrong by omission:
the **deep_search_v4 planner** and the **writer_planner** were not among them and
could not see the summary at all. Worse, their conversation window
(`_load_recent_messages`, `orchestrator.py:252`) is a fixed `_RECENT_MESSAGES_N = 5`
tail that is **not** cutoff-filtered — so after a compaction they saw 5 recent turns,
no summary, and no signal that anything had been compacted away. On the prod
conversation that triggered this work (30 messages, 19 behind the cutoff) that is 19
invisible turns with nothing standing in.

Both now render it into their decider system prompt as a `<conversation_summary>`
block placed between `<case_brief>` and `<recent_messages>`, with an explicit note
that those turns are NOT in the recent window.

**Carrier:** `MajorAgentInput.compaction_summary_md` — one field, both planners.

**Zero extra DB reads on a fresh turn.** `load_router_context` already loads and
encodes the value for `run_router`; `_route` hands the same object to `_dispatch`,
which parks it on `MajorAgentInput`. Only the two **resume** legs (which never call
the router) load it, via a `kind='convo_context'` + `LIMIT 1` helper —
`_load_compaction_summary`, `orchestrator.py:417`. The deep_search resume leg loads
once and reuses it across phase 1 and phases 2–3.

**Masking invariant — encoded ONCE, at the orchestrator boundary.** Neither renderer
calls the codec. This is a deliberate departure from writer_planner's
`_render_attached_items` / `_render_prior_artifacts`, which encode at render time
because they read `WorkspaceItemSnapshot` objects shared with non-LLM consumers that
must stay real. The compaction string exists only to be prompted, so a second encode
would be double-encoding. Documented at both deps fields and both renderers so nobody
adds one later.

### Open — not built

- **The phase-3 responder does not get it.** `PlannerDeps` is shared, so
  `build_responder_instructions` *could* render it, but that instruction is
  deliberately a trimmed artifact digest; the comprehension surface (`case_brief`,
  `recent_messages`, `prior_searches`, `attached_items`) is decider-only by design.
  Two-line change if wanted. Argument for: the responder composes the final answer,
  and if the user's through-line lives in the compacted span it currently answers the
  literal recent question without it.
- **Should `_load_recent_messages` respect the cutoff?** It currently doesn't, so the
  planners' 5-message tail can overlap the span the summary describes. Filtering it
  (as `router/context.py:286-287` does) would partition cleanly, but costs recency on
  a window that is only 5 messages wide. Left unfiltered deliberately.
- **The summary is written for the router's job.** The three prescribed sections
  (§2) carry intent, produced WIs, and open threads. A planner about to run a legal
  search also wants *which search angles were already tried and came back empty* —
  not currently captured. Revisit against real output rather than guessing.

---

### Test-double trap `TestModel(custom_output_text=…)` cannot drive a `[Model, TextOutput(…)]` union (pydantic_ai `models/test.py:178-183` asserts `output_mode != 'tool'`), and bare `TestModel()` emits a short default that trips the 150-char `ModelRetry` floor — so it exercises **fail-closed, not success**. Use `custom_output_args=` for the happy path and `FunctionModel` for the salvage path.
