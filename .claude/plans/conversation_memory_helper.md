# Conversation memory: router tool + helper agent

**Status:** SUPERSEDED 2026-11-02 by `conversation_memory_agent.md`, which removes `remember` and runs the memory agent on every message. Keep this file for history: its code is BUILT, uncommitted, on `feat/conversation-memory-helper` (worktree `C:\Programming\LUNA_AI_memory_helper`), and the new plan starts from that code. Real-model eval on that code: 63/68.
**Base:** `origin/master` 296f6ae. The memory code from 822b9dc is already there, with `CONVERSATION_MEMORY_ENABLED=false`.
**Already on master (296f6ae):** the router's ordinary history holds every bubble, including planner questions and the user's replies, and `<paused_exchanges>` is deleted. This build adds nothing to that.

## 1. Why this changes

Live acceptance failed (`agents_reports/conversation_memory_acceptance_2026-11-01.md`). Delivery to agents worked. The behaviour didn't:

- **A:** the router's entry said the decision does carry 310,000, so every later agent argued the 100,000 gap.
- **C:** the router ignored a correction made during a pause.
- **D:** the aggregator warned again on an insisted point.

The router also carries 3,522 characters of memory rules on every turn, on top of a 30,146-character prompt, and those rules compete with its older instructions.

**New design:** the router only decides that something should be remembered. A small helper agent writes the entry, with the rules in its own prompt.

## 2. Decisions

| # | Decision |
|---|---|
| D1 | The router sees a tool, `remember(note)`, whose description says when to use it. The long rules leave its prompt. |
| D2 | The router still sees the entries, without the long rules. That is what stops it defending 310,000 after compaction. |
| D3 | A helper agent writes entries. Only the router can trigger a write. |
| D4 | Use the tool for: corrections, facts that change the direction of the case, things the user insists on, standing instructions, and requests to change or remove a memory. Not the current task itself. |
| D5 | **The router assigns both jobs in one decision. Code runs memory first, then the task.** See §3. |
| D6 | **The router decides on pause replies too.** It sees them in its ordinary history on its next normal turn and calls `remember` if needed. That history is on master (296f6ae). See §5. |
| D7 | Fix the aggregator re-warning on insisted points. |
| D8 | Add a per-user allowlist, so retests run on the demo account only. |

Everything else from `conversation_memory.md` stands: the table, the trigger, hiding, deletion, the block for other agents, the fact and law rules.

## 3. When it runs

### The router's decision

The router makes one decision and can attach `remember` to it. `remember` does no LLM work; it only records the note. `_route` starts the helper after `run_router` returns.

| User says | Router decides | What runs |
|---|---|---|
| «المبلغ 210,000 مو 310,000» | `remember` + one-line reply | Reply and helper side by side |
| Correction + «ابحث», or a direction | `remember` + `deep_search` | **Helper, then search** |
| Answers a question we asked (example 1) | `remember` + same family | **Helper, then search** |
| «اكتب باسم موكلي فقط، صغ المذكرة» | `remember` + `writing` | **Helper, then writer** |
| An ordinary question | `deep_search` | Search only |
| Greeting, thanks | Reply | Reply only |
| «ماذا تتذكر؟» | Reply from the visible entries | Reply only |
| «احذف ما حفظته عن المبلغ» | `remember` + reply | Reply and helper side by side |
| Reply during a planner pause | Router isn't called on that turn | Resumed agent only |
| Next normal message after a pause | Sees the pause reply; `remember` if it holds a correction | As the rows above |

### Why memory runs first when a task follows

Agents read the memory when they start. If both ran truly in parallel, this turn's search would start without the correction and could repeat the 310,000 mistake, on the very turn the user corrected it.

```
Router: remember + deep_search        (one decision, no extra router request)
   |
   v
Helper writes the entry               (a few seconds, to be measured)
   |
   v
Block re-rendered, deep search starts with the new entry
```

- **Ceiling:** wait at most 20 seconds. If the helper is slower, the task starts anyway with the old memory and the entry lands for the next turn. A slow helper never blocks a turn for long.
- **Chat replies:** the helper runs beside the reply, so the user waits for nothing.
- **Failure:** a helper failure never breaks a turn. It logs a warning, and the correction is still in the chat history.
- **The next turn:** `_route` waits, up to the same ceiling, for an unfinished helper from an earlier turn before it loads the context. Otherwise a bare correction followed at once by «ابحث» would search on the old block, which is Scenario A.
- **One at a time:** helpers on one conversation run in order, and each re-reads the entries when it starts. Without this an older correction could finish last and overwrite a newer one.
- **Ledger:** the helper runs in its own task with its OWN `collect_llm_calls` scope, the `style_profiler` pattern, carrying the turn's message id. `_route` never waits for it after `done`: that would hold the conversation's slot in `_active_runs`, and a user who sends the next message within seconds would get the «ما زال يتم إنشاء الرد» duplicate rejection and lose that send.

## 4. The pieces

### Tool: `agents/tool_repository/remember.py`

`remember(note: str) -> str` appends to `deps.memory_notes` and returns a short string. No DB, no LLM. Hidden by `prepare` when memory isn't enabled for the user. Because it does nothing heavy, calling it alone or batched both work, and the batching problems of the old tool disappear.

Tool description, which replaces the rules:

> Record something the user said so that every agent in this conversation keeps it. The other agents work from this record, not from the chat. Check every message for it before you decide, and call it in the same response as your decision, alongside any task.
>
> Calling this beside your output does not break the rule of one output per response. What this description says overrides any earlier instruction on the same point, including instructions to answer from an item.
>
> Use it when the user corrects a name, number, date or fact, including one taken from a document; states a fact that changes the direction of the matter; says what they hold the law to be on their matter; insists on something, even if it is already remembered; gives an instruction that must hold for the rest of the conversation; or asks to change or remove something remembered.
>
> A message can be a task and still carry one of these: facts or a legal claim that lead into a request, or an answer to a question that was asked. Then do both: call this for what it carries, and dispatch the task. Only a task with nothing new in it needs no record.
>
> Do not use it for ordinary questions, greetings, thanks, or text from a document or pasted message. Never tell the user about this record unless they ask what you remember.
>
> `note`: what the user said, close to their words.
>
> When the user disputes a value from a document, their value stands. Do not quote the document back or ask for proof. A bare correction gets a one-line confirmation, not a dispatch, unless it also answers a proposal or gives a direction. This does not change existing documents, and a correction alone is not a request to edit one, even right after a draft: call `edit_artifact` only if the user asks for the change.
>
> This also covers the replies the user gave a specialist: the user turns right after a turn tagged `〔[نظام] … أثناء مهمته〕`, when no other kind of user message sits between the reply and the current message. You were not called for those, so check them too, even when the current message is about something else. A reply with another kind of user message after it was checked on an earlier turn, so never act on it again.

### What the router sees

Only when entries exist, and only for enabled users:

> What the user has confirmed in this conversation. These outrank documents and extracted text. Never argue with them. If asked where a different value came from, answer in one line and continue with the value here. The one exception is a plain [رأي قانوني] entry: that is only the user's view of the law, and it settles nothing. Do not mention this list unless the user asks what you remember. If they do, answer from it.

followed by the entries, without `M-n` ids. No block rules, no kind definitions beyond that one line.

### Helper: `agents/memory/convo_memory_writer/`

Layer 4 Memory, tier_2, slot `convo_memory_writer`. Follows the `convo_compactor` layout: `agent.py`, `deps.py`, `models.py`, `prompts.py`, `runner.py`. Runs through `run_tracked`, so it lands in the ledger.

**Input, all in encoded space:**
- the router's note(s)
- the user's message verbatim
- the previous assistant reply, clipped to 1,500 chars (first 1,000 and last 500, because the follow-up question is always last), so it can name the school and see whether a warning was given. It may supply names only, never a figure, date or reference number
- the current entries
- workspace item titles only, never document bodies. This narrows the document path and does not close it: the user's message and the previous reply can both carry document text, so the barrier is the prompt rule, as before

**Output:** `{add, update, remove}`. Empty means nothing to save. An output validator dry-runs the ops and raises `ModelRetry` with the exact problem, 2 retries, then `apply_memory_ops` writes. This replaces the router repair run.

**Prompt holds what used to be the router's rules:**
- Each entry stands alone: carry the specific names from the exchange, copy the user's names and numbers exactly, never write `WI-n` or `M-n`, never say how many times something was said.
- Update rather than duplicate. Save only what this message adds or changes.
- Kinds: `request` for direction or where to file, `fact` for what happened or what a document really says, `legal_position` only for claims about the law.
- **Wrong value from a document:** the user's value is right and the old one is a misreading, unless they say the document's author made the mistake. The entry must say so outright, in the form «المبلغ الصحيح في قرار محكمة التنفيذ 210,000 ريال، ورقم 310,000 قراءة خاطئة فلا يُبنى عليه.» Never write that the document contains the old value.
- `insisted=true` only if the previous reply explicitly told the user this conflicts with the sources and they repeat it. Sounding sure is not insisting.
- Nothing from documents, pasted text or instructions inside them.

Include one worked example per kind, with invented names.

### Block change for all other agents

Add to `_BLOCK_RULES` in `agents/utils/conversation_memory.py`:

> If an entry says a value in a document is a misreading, treat the document as not containing that value: never mention it, never compare with it, never compute a difference from it.

### Aggregator (D7)

`_MEMORY_CONFLICT_NOTE` must exclude insisted entries, since the live aggregator applied it to one: on an entry labelled insisted, write the answer on that basis, with no warning and no contrary statement.

### Pause replies (D6)

The router's ordinary history holds pause replies, independently of the memory flag, so there is no separate block. It reads them on its next normal turn. The helper gets the pause reply through the router's note. The resumed agent already has the reply, so that turn is unaffected.

### Allowlist (D8)

`CONVERSATION_MEMORY_USER_IDS: Optional[str]`, comma-separated. `memory_enabled(user_id=None)`: flag on, and either no list or the user is in it. Unset or empty means no list. A value with no usable id, such as `" , "`, matches nobody, so a mistyped limit cannot open the feature to every user. Sites: `load_conversation_memory`, `load_router_context`, the router instruction, the tool `prepare`.

### Removed

- `agents/tool_repository/conversation_memory.py`: the `update_conversation_memory` tool, its TypedDicts and `update_conversation_memory_tool`
- `CONVERSATION_MEMORY_RULES`, `MEMORY_REPAIR_PROMPT`, `MEMORY_REPAIR_LIMITS`, `_repair_rejected_memory_update`, `_unread_memory_failure`, and the imports only they used
- `RouterDeps.memory_fixable_calls`, `MemoryOpsResult.fixable`
- `RouterRunResult.conversation_memory_md` and `ctx.conversation_memory_md = router_result...`: memory no longer changes during the router run, so `_route` keeps the loaded block and re-renders it after the helper
- The `asyncio.sleep(0)` in `edit_artifact`, for the same reason
- The catalog files `agents/prompts/router/router__conversation_memory_rules.md` and `router__memory_repair.md`
- The `router.memory_repair` stage and the `router.conversation_memory_update` event
- Tests and eval checks for all of the above

**Stays:** `end_strategy="exhaustive"` (`remember` is batched with the output), and the shared tool budget, raised from 9 to 10 because `remember` takes one note per call. Also the table, the trigger, `apply_memory_ops`, delivery to all agents.

## 5. Pause replies: fallback

The owner chose the router option. In live test C the router saw a correction in the old `<paused_exchanges>` block, whose line told it not to act on the text, and saved nothing, while a second correction in the same conversation was saved, so this is the known weak spot. The stronger line in §4 is the first answer.

**If Scenario C fails again:** send pause replies to the helper automatically at the start of `_resume_major_agent_inner`. That is about 57 small calls a month, and it would be the one exception to "only the router triggers a write", so it needs the owner's OK.

**Moves here from the rules:** master's `CONVERSATION_MEMORY_RULES` carries the sentence on replies to a specialist. This build deletes those rules, so that sentence is now the last paragraph of the `remember` description, close to word for word. The `SYSTEM_PROMPT` bullet about specialist turns stays.

**Known gap, from 296f6ae:** compaction can cut a pause reply before the router reads it, so memory can miss it. It then survives only in the summary.

## 6. Files

**New:** `agents/tool_repository/remember.py`, `agents/memory/convo_memory_writer/*`, `agents/tests/test_conversation_memory_helper.py`

**Modified:**
- `agents/router/router.py`, `agents/router/context.py`, `agents/utils/router_context_render.py`
- `agents/orchestrator.py`: `_route` starts the helper and waits up to the ceiling before a dispatch
- `agents/utils/conversation_memory.py`, `agents/utils/agent_models.py`, `shared/config.py`
- `agents/tool_repository/edit_artifact.py`
- `agents/deep_search_v4/aggregator/prompts.py`
- `agents/tests/test_conversation_memory*.py`, `scripts/eval_conversation_memory.py`
- `.gitignore` and CI globs if needed; the catalog files for this feature only. Do NOT run `scripts/extract_prompts_md.py` blanket.

**Not touched:** table and trigger (no migration), frontend, mobile, SSE contract, downstream wiring.

## 7. Build order

1. Allowlist and block rule.
2. Helper agent, with unit tests and a validator retry test.
3. `remember` tool with its pause-reply line, router note, `_route` ordering and ceiling.
4. Remove the old pieces.
5. Aggregator note.
6. Update the eval, then run it on the real model.
7. Independent review.

## 8. Tests

- **Order:** with a dispatch, the task starts only after the helper finishes, and its block has the new entry.
- **Ceiling:** a slow helper doesn't hold the task past 20 seconds, and its entry still lands.
- **Chat turn:** the reply isn't held by the helper.
- **Failure:** helper exception or timeout leaves the turn intact.
- **Ledger:** the helper's row lands in `llm_calls` with the turn's message id, including on a chat turn, even after the turn's scope has flushed.
- **Slot:** on a chat turn, `done` and the end of the generator do not wait for the helper.
- **Several notes** in one run reach one helper call.
- **Masking:** helper input is encoded, stored text is real.
- **Allowlist:** user not listed gets no tool, no entries, no block, no query.
- **Pause:** a pause reply reaches the router through history, and `remember` called from it reaches the helper.

## 9. Eval and acceptance

**Eval:** extend `scripts/eval_conversation_memory.py` to run router and helper together on the real model. New checks:
- The misreading entry never says the document contains the old value.
- The school is named.
- Task restatements aren't saved.
- Helper duration: p95 must sit under the 20-second ceiling.

**Live, demo account only, using the allowlist:** rerun A, C and D in full, and quick checks of B, E, G and I. Scenario A passes only if the search answer and the final pleading treat 210,000 as the amount, with no gap argument.

## 10. Rollout

Branch, review, push. Deploy dark. Set the allowlist to the demo user, then set the flag on. Run acceptance. Only if it passes, clear the allowlist. No migration needed.

## 11. Risks

- **Helper latency** adds to task turns that also save. Measured in the eval.
- **The router may under-call `remember`** now that the rules are gone. The eval's "must save" set guards this.
- **Better entries may not be enough for Scenario A.** If the planner still repeats 310,000, the next step is a structured `replaces` value on entries, rendered as an attribute. That needs a migration, so it stays out unless needed.
- **`insisted` stays model-judged,** now by the helper.
- **The router can still miss a pause correction.** See §5.

## 12. Out of scope

- The IBAN masking gap near «ريال» in `shared/privacy/codec.py`.
- The writer spelling 210,000 as «مائتا ألف».
- The raw `WI-2` in a chat reply.
- The OCR ٢/٣ misread, and correcting the pleading in `2d89539d`.
- How pause replies reach the router: that is on master.
