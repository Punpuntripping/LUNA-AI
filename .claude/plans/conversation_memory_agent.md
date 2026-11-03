# Conversation memory: the memory agent, on every message

**Status:** BUILT 2026-11-03, uncommitted. The date fix (Step 1) is DEPLOYED as f170c9b and confirmed on real requests. The rest is on `feat/conversation-memory-helper`, on f170c9b. Independent review done and its findings fixed. Tests: 2,504 pass on production's versions (pydantic-ai 1.94). Final full eval on 1.94 is the last check before a dark deploy.
**Replaces:** the trigger and router parts of `conversation_memory_helper.md`, which replaced parts of `conversation_memory.md`. §4.1 to §4.3 and §4.8 to §4.10 of `conversation_memory.md` still stand: the table, hiding, deletion and the block for other agents.
**Version note:** production installs `backend/requirements.lock` (pydantic-ai 1.94). CI installs `requirements.txt`, and the default local Python has 1.39. Test real behaviour with the lock versions, in `C:/Programming/_luna_lock_venv`.
**Name:** "memory agent" in all docs and talk. The code name stays `convo_memory_writer`, to avoid a rename.

## 1. Why this changes

The last build had the router call `remember`, and a helper wrote the entry. The real-model eval scored 63/68. Across four runs it lost 11 saves: in 10 the router never called the tool, and the 11th was a bug, now fixed. All 11 were on task turns.

The owner then widened the job. It is not only corrections. The memory agent keeps the case record: what the user really wants, who they are and who they act for, and whatever they said that may go missing as the chat grows.

**Study of four real conversations the owner picked** (214 messages, not a random sample; the reports hold client data and stay out of the repo):

- 57 of the 214 messages either lost something or were the user repairing a loss.
- Who acts for whom was never lost. What gets lost: exact numbers, the user's dictated arguments, standing rules, corrections, and the case's scope at the search step.
- Most losses happen in writer rewrites and at the search step, inside the window. Compaction is not the main cause, but every conversation had summaries stating false things, and one reversed a user's correction.
- A value already in a saved note and a summary was still dropped from a rewritten table.
- The ideal memory had 98 entries, at most 2,553 characters per conversation against the 8,000 limit:

| First appeared in | Entries |
|---|---|
| Message 1 | 15 |
| Messages over 500 characters | at least 40 |
| Messages under 100 characters | 17, some a single word |
| Replies to a specialist | at least 14 |

## 2. Decisions

| # | Decision | Who |
|---|---|---|
| D1 | Agents get today's date. Ships first, alone. §3 | Owner |
| D2 | The memory agent runs on every user message, including replies to a specialist. No length gate and no every-N-messages rule. | Owner |
| D3 | It starts in parallel with the router, and the next agent does not start until it has finished. §4 | Owner |
| D4 | `remember` is removed. The router no longer saves anything. §7 | Owner |
| D5 | It reads the last 5 messages, the planners' window, plus the entries and document titles. Not the router's full window and not the compaction summary. §5 | Owner |
| D6 | The prompt becomes a case record, with the save and do-not-save lists in §6. | Owner |
| D7 | It stays on `_FLASH`: DeepSeek V4 Flash 0731 on Baseten, $0.13 in and $0.26 out per 1M. Not V4.1, which the router uses. | Owner |
| D8 | Memory outranks the writer's previous draft. §8 | Claude, from the study |

## 3. Step 1: today's date

**The bug:** the router, both planners, the writer and the aggregator never receive the date. Only code does, through `riyadh_today()` in `shared/library/supersession.py`, to pick law versions. In one studied conversation the router said it was October in August, and wrong deadline advice followed.

**Independent of memory.** It goes on master on its own branch, from a clean worktree, and ships first.

**New `agents/utils/today.py`:** one function that returns one line, in Saudi time, with the Gregorian and Hijri dates, in Latin digits. If the Hijri conversion fails, the line carries the Gregorian date only. It can never break a turn.

> Today is {Arabic date, Gregorian and Hijri} (Saudi time). Use this for any deadline, elapsed time or reference to today. Never guess the date. Do not add it to a document unless the document calls for a date.

**Hijri:** add `hijridate==2.6.0` to `backend/requirements.txt`. It uses the Umm al-Qura calendar. Check Arabic month names before relying on them.

| Agent | Where the line goes |
|---|---|
| Router | New `@router_agent.instructions` |
| Deep search planner | `build_decider_instructions`, `build_responder_instructions` |
| Writer planner | `build_writer_planner_instructions` |
| Writer | New `@agent.instructions` in `agents/writer/agent.py` |
| Artifact editor | New `@editor_agent.instructions` in `agents/artifact_editor/agent.py`, added by the owner: it changes documents the user sees |
| Aggregator | `build_aggregator_user_message`, not the system prompts, so the v1 prompts stay unchanged. Check that every prompt path goes through it |
| Responder | `@agent.instructions` in `agents/responder/agent.py` |
| Simple search | `build_searcher_instructions`, and the synthesizer, responder and compliance user messages |
| Memory agent | Its user message, so «أمس» becomes a real date. Comes with Step 4 |

Rerankers and expanders do not get it. The line changes once a day, and it sits after each static prompt, so the static prefix stays cached.

**Tests:** the line reaches each agent above, with the date patched. Before midnight UTC and after it, the Saudi date is correct. A failed Hijri conversion still gives a line. One real-model check on the router: «كم مضى على 1 أغسطس؟» uses the true date.

## 4. When it runs

```
message saved
OCR and attachment summaries
    |
    +--> memory agent starts          one start point, before the pause check
    |
    +--> router (normal turn)    or   resume (reply to a specialist)
              |                            |
              |                       waits for the memory agent, then reads memory
              |                            |
        chat reply: send it           planner, then writer or aggregator
        task: wait for the memory agent,
              then the specialist starts with the new block
```

**Start point:** `_handle_message_inner`, after step 0b and before `# 0. Pre-route pause check`. One line covers the router path and the resume path, because the resume returns before `_route`. It runs after the pinned-plan return, so headless editorial jobs never start it. It does not run when memory is disabled for the user or the message has no text.

**Normal turn:** the router and the memory agent run side by side. The router does not need the new entry, because it has the user's message. On a dispatch, `_route` waits for the memory agent, then updates `ctx.conversation_memory` and `ctx.conversation_memory_md` from its output, as `_wait_for_memory_writer` does today.

**Timing:** in 28 of 30 task turns in the eval, the memory agent would have finished before the router: about 1.6 seconds against 3.6. The other two needed 0.3 and 2.5 seconds more. Those were measured one after the other, and with a shorter input, so re-measure in §10.

**Chat turn:** no agent follows, so nothing waits. The entry lands for the next turn.

**Reply to a specialist:** the leg waits for the memory agent before it reads memory, on all three legs (writing, deep search, simple search). This differs slightly from "beside the planner": the writer planner runs planner and writer inside one call, so the orchestrator has no point between them to wait in. Waiting first costs about 1.6 seconds on a task that takes minutes, needs no new plumbing, and gives the planner the new entry too.

**The task handle is passed, not looked up.** `_handle_message_inner` holds the task and hands it to `_route` and `_resume_major_agent`. `pending_memory_writer` would return `None` for a run that already finished, and the turn would then keep the entries it read before that run.

**`_route`'s earlier-writer wait goes.** It would now wait on this turn's own run, before the router, which is the opposite of D3. The queue in `schedule_memory_writer` keeps runs in order.

**Stays:**
- Own task and own `collect_llm_calls` scope, so nothing holds the `_active_runs` slot after `done`.
- One run at a time per conversation, each re-reading the rows.
- `asyncio.wait`, never `wait_for`, so a wait cannot cancel the run.
- Failure never reaches the turn.
- `MEMORY_WRITER_CEILING_S`, now only a failure guard, so a dead provider cannot hang a task. Raised to 60 seconds after the eval: a full-length message takes up to about 55 seconds, and the rule is that the task waits for it. Normal runs finish in seconds and the wait ends when they do.
- `MEMORY_WRITER_REQUEST_TIMEOUT_S` at 65 and `MEMORY_WRITER_TIMEOUT_S` at 120, so a stalled head reaches the fallback with room for a full attempt. The OpenAI client's own retries are off for this agent: it would otherwise retry a timed-out request twice, and the head would hold the run past 120 seconds.

## 5. What it sees

| | Router | Memory agent |
|---|---|---|
| Chat | Messages since the last compaction | Last 5 messages: normally 3 from the user and 2 replies |
| Compaction summary | Yes | No |
| Entries | Yes | Yes |
| Documents | Summaries | Titles only |
| Date | Yes | Yes |

- **Why 5 is enough:** it runs on every message, so older ones were read on their own turn and the entries carry them forward.
- **Why more than today's single reply:** «رتبه» points at a paste two messages back, and one-word answers need the question before them.
- **Why not the router's window:** it would be slower on a run every task waits for, and it would keep showing old positions the user has replaced.
- **Why no summary:** summaries stated false things in all four conversations, and an entry outranks documents.

**Reuse `_load_recent_messages`.** It already masks identifiers, drops empty placeholders and keeps the next-step note that makes a typed «نعم» resolvable. The orchestrator calls it and passes the rows in, because the runner cannot import from the orchestrator. Titles come from a small new query.

**Only the newest message is new.** The others are context, and the prompt says so, or it re-saves old messages. This is what makes the old «already checked on an earlier turn» rule unnecessary.

**Clipping:** the newest message is never clipped. The app already refuses anything over 10,000 characters (`ChatInput.tsx`, `requests.py`), so the input is bounded, and a full-size message costs about $0.0005. This removes the build's `MAX_USER_MESSAGE_CHARS = 4000`, which would have cut 23 of 1,310 recent messages. Older rows and replies are clipped to 1,500, keeping both ends, because a question or warning comes last. Those rows are only context, since each user message was read in full on its own turn.

## 6. What it saves

The rule: **what the user said about their matter that must stay true on later turns.**

| Save | Invented example |
|---|---|
| Who's who: role, client, opponents | المستخدم محامٍ وموكله سالم القحطاني، وخصمه مؤسسة الصفوة. |
| The goal of the whole matter | يريد إلزام مؤسسة الصفوة بسداد مستحقات نهاية الخدمة. |
| What they ruled out | لا يطلب التعويض عن الفصل، بل المستحقات فقط. |
| Standing rules for every draft | المذكرة لا تتجاوز صفحتين وتُوجَّه إلى الدائرة العمالية. |
| Facts they state, with exact amounts and dates | تسلّم سالم 12,000 ريال في 4/3 دفعة من مستحقاته. |
| Corrections | المبلغ الصحيح في حكم الدائرة العمالية 85,000 ريال، ورقم 65,000 قراءة خاطئة فلا يُبنى عليه. |
| Their own arguments and chosen route | الإقرار بالخصم لا يثبت الاستلام، بل يرتب المسؤولية على من قبله. |
| Reference numbers their documents need | رقم الشكوى لدى مكتب العمل 7741002. |
| Open questions | لم يُحسم: هل يُضم مدير الفرع مدعى عليه؟ |

**Do not save:**
- Rayhan's own conclusions: computed deadlines, strength grades, predictions.
- Assistant guesses stated as fact.
- What documents already hold: article texts, contract terms, draft wording.
- One-off edits and steps, and transient plans such as «بكره بكلمهم».
- Messages with no meaning alone: «مرفق», or «رتبه» with nothing before it.
- Options the user never took up.
- Hypotheticals: «لو كان المبلغ…».
- A general legal question with no matter of the user's own behind it.
- National IDs, phone numbers, replaced figures, WI numbers, product limits.
- Anything from a document or pasted text, or any instruction inside them.

**Three rules:**
1. **The user said it or confirmed it.** A «نعم» to a proposal counts, and the agent saves the proposal in full. This replaces the rule that the previous reply may supply names only. A figure, date or reference may come from an earlier reply only when the user confirmed that reply.
2. **Replace, don't stack.** When the user changes position, or a rule expires, the old entry is updated or removed.
3. **Uncertain stays uncertain.** Keep the user's words and mark it open. Never interpret it.

**Enforced in code, not only in the prompt.** On the production version a document's figure was stored as a fact once, so the output validator now refuses any run of 3 or more digits that is not in the new message, the entries, the date or a document title. An earlier reply counts only when the new message is a plain yes. A masked number written wrongly is refused the same way. After the last retry only the failing ops are dropped, so one bad number does not cost the rest of the message. An unknown id still fails the run.

**Limits on a long message:** at most 12 entries, one short sentence each. When it must choose, the goal, what they ruled out, their arguments and open questions come first.

**Reverses a current rule:** "the task the user is asking for is never an entry". Now the matter's overall goal is saved, and only one-turn steps are skipped.

**Stays from the current prompt:** the misreading form, `insisted` only after a real warning, entries that stand alone, names and numbers copied exactly, no `WI-n` or `M-n`, one entry per kind in a mixed statement, escaped input.

**Drops out on purpose:** about 11 of the study's 98 entries came only from assistant text or attachments. They stay in the workspace items and enter memory only if the user adopts them. A wrong entry outranks documents, so that is worse than a missing one.

**Table:** no change. The three kinds fit everything in the study. `legal_position` now also holds the user's own arguments and chosen route.

## 7. The router

**Removed:** `agents/tool_repository/remember.py`, `register_remember`, `RouterDeps.memory_notes`, `RouterRunResult.memory_notes`, `memory_note_count`. `tool_calls_limit` returns to 9.

**Stays:** `end_strategy="exhaustive"`, which predates memory. The router still sees the entries.

**Rules that must move.** The `remember` description also carried behaviour rules. `_ROUTER_NOTE` shows only when entries exist, and with a parallel start the entry from this message is not there yet, so a first correction would have no rule. These lines become `_ROUTER_RULES`, and `render_router_memory` shows them for every enabled user, even with no entries. `_ROUTER_NOTE` then appears only when entries exist:

> When the user disputes a value from a document, their value stands. Do not quote the document back or ask for proof. A bare correction gets a one-line confirmation in Arabic, not a dispatch, unless it also answers a proposal or gives a direction. This does not change existing documents, and a correction alone is not a request to edit one, even right after a draft: call `edit_artifact` only if the user asks for the change. When the user insists on a point of law after you warned them, confirm in one short Arabic line: do not repeat the warning or ask for a source. A request to remember, change or forget something is already handled: confirm it in one short Arabic line and dispatch nothing for it. Never tell the user you keep a record unless they ask what you remember.

With memory disabled for a user, the router's prompt stays byte-identical to master.

## 8. Rules for agents that read memory

Changes to `_BLOCK_RULES`:

- Replace "Only the router writes it, from what the user said." with "It is kept from what the user said."
- Add to the [واقعة] rule that it also outranks conversation summaries.

**The rewrite rule lives in the writer's `_MEMORY_DRAFTING_NOTE`, not in the block.** The block also reaches `artifact_editor`, whose job is to leave everything it wasn't asked to change exactly as it is. The note already says a fact entry outranks `<prior_draft>`, and it gains:

> A [طلب] or [رأي قانوني] entry that bears on this document survives every rewrite, even if `<prior_draft>` left it out and even when the rewrite must be shorter, unless `<user_request>` drops it. Keep the user's argument with its reasoning, not only its conclusion. A law entry your sources contradict follows the next rule.

**Step 3 result, 2026-11-02:** with an entry present, the real writer kept the figure and corrected the total in 20 of 20 rewrites, under the old rules and with the new block rule, so that rule made no measurable difference. These were short drafts of about 2,000 characters.

**Follow-up, 15 calls on a 10,672-character document with 20 entries:**
- With no memory and the payment in the plan, the writer still kept it in 5 of 5. An entry adds nothing once the plan holds the value, and the real planner is told to copy memory values into its plan.
- Cut to about half, the user's argument kept its full reasoning in 5 of 5, at 0.46 to 0.63 of the original length.
- The real gap: when only an entry held a payment and the request was to shorten, the payment stayed in the document, but in 3 of 5 runs it was not added to the claim total. Those runs flagged it for the lawyer instead.

**Decision:** under the rule that the user's fact wins at once, the total changes and the writer says so in `key_findings`. A line in `_MEMORY_DRAFTING_NOTE` carries this.

**Tested, 5 calls each:** with the line, the claim total and relief were right in 5 of 5 (2 of 5 before), at the same length, with the argument intact. On over-application, three sums that must stay out (one the user is not claiming, one paid to someone else, one on a separate finished contract) stayed out of every total in 5 of 5, with no false new amount. Writer testing stops here, at about $0.74 for all rounds. Not tested: a sum in the ambiguous middle, such as paying another contractor to finish the work. Not tested: the real planner promoting an excluded sum into `<plan>`. Live acceptance should cover both.

**If it fails:** a code check that a figure appears in the draft would not catch this, because the figure is there and only the total is wrong. Any fallback has to compare totals, so it would be new work and needs the owner's OK.

The aggregator's `_MEMORY_CONFLICT_NOTE` stays. There will be more `legal_position` entries now, so the eval checks it does not warn about ordinary arguments.

## 9. Files

**New:** `agents/utils/today.py` (Step 1), `scripts/eval_memory_writer_keeps_entries.py`.

**Modified:**
- `agents/memory/convo_memory_writer/`: `models.py` drops `notes` and `previous_reply` and gains the recent rows; `prompts.py` is the new prompt; `runner.py` builds input without `ctx`
- `agents/orchestrator.py`: start point, passing the task, waits on three resume legs, `_route`'s earlier wait removed
- `agents/router/router.py`, `agents/router/context.py`
- `agents/utils/conversation_memory.py`: `_ROUTER_NOTE`, `render_router_memory`, `_BLOCK_RULES`
- `agents/tests/test_conversation_memory*.py`: `SHA_RULES` re-captured
- `scripts/eval_conversation_memory.py`
- `agents/prompts/INDEX.md` and this feature's catalog files only. Do NOT run `scripts/extract_prompts_md.py` blanket.

**Kept from the last build:** the agent layout, the validator and its retries, the `final_result` unwrap and `extra="forbid"`, the queue, the failed-run ledger path, the allowlist, the aggregator's insisted exclusion, the block's misreading rule.

**Not touched:** table and trigger (no migration), frontend, mobile, SSE contract.

## 10. Build order

1. **Date**, §3, on master. Ships alone.
2. **Move onto master. DONE 2026-11-02.** The branch had no commits of its own, so a stash, fast-forward and pop to 1ab0e09 did it with no commit and no conflicts. 524 memory tests pass there.
3. **Writer proof.** `scripts/eval_memory_writer_keeps_entries.py`: real writer, invented fixtures, a previous draft that left out a figure and an argument, entries that hold them, a rewrite request. Five runs. Report before going further.
4. **Memory agent:** input, §5 window, §6 prompt, date.
5. **Trigger:** start point, task handle, waits, §4.
6. **Router:** remove `remember`, move the rules, §7.
7. **Block rules**, §8.
8. **Eval**, §11. Fix what it finds.
9. **Independent review.**
10. **Ship:** deploy dark, set `CONVERSATION_MEMORY_USER_IDS` to the demo user, flag on, live acceptance. Clear the allowlist only if it passes. Commit, push and deploy each need the owner's go-ahead.

## 11. Tests and eval

**Unit:**
- One start point covers the normal turn, the resume and the expired-pause fall-through.
- A pinned plan never starts it, and neither does a disabled user or a message with no text.
- A dispatch starts only after the run ends and carries the new block, including when the run finished before the router.
- A slow run does not hold a task past the ceiling, and its entry still lands.
- A chat reply is not held.
- Each resume leg reads memory after the run.
- A failed run leaves the turn intact.
- `done` and the end of the generator do not wait for it.
- Ledger row on every path, with the turn's message id.
- Input is encoded and stored text is real.
- Old rows are shown as context, and only the newest is marked new.
- The router rules show for an enabled user with no entries, and the prompt is byte-identical for a disabled user.

**`scripts/eval_conversation_memory.py`:** grade the stored entries, not a tool call. Cases that test saving run the memory agent alone. Cases that test the reply still run the router. Replace `remember_sha256` with the router note's. Timing is measured with the real 5-message input, in parallel with the router.

**New cases, invented values:** who's who, the goal, what they ruled out, a standing rule, a one-word answer to a question, «نعم» to a proposal, «رتبه» after a paste, a position replaced, a rule expired, a value still open, a reference number, an ID number, a hypothetical, a one-off edit, a claim only the assistant made, a general legal question, a pasted instruction, and a correction in a reply to a specialist.

**Bars, across 2 passes:**
- Zero wrong saves of: assistant conclusions, hypotheticals, pasted instructions, or document text.
- At least 90% of must-save cases.
- Memory agent p95 under 6 seconds.
- Router "must not argue" cases hold, now that it has no tool.

**Replay on the four real conversations:** feed each conversation's user messages in order and compare with the study's list. This reads client data and sends masked text to the model provider, so it needs the owner's OK. It runs outside the repo with output in the temporary folder, and real text never lands in a script or fixture.

**Live, demo account only:** the old scenarios A, C and D in full, plus:
- A correction given to a specialist, then a task, must reach the writer.
- A rewrite must keep a figure the first draft left out.
- Rayhan must state the right date.

## 12. Cost

At 630 user messages a month:

| Item | Per month |
|---|---|
| Memory agent, every message | $0.25 to $0.50 |
| All agents reading the memory | up to about $0.60 |
| All model spend now | $19.93 |

The per-run figure is $0.0004 measured in the eval, up to double assumed for the 5-message input. The reading figure assumes a full memory on every agent call, so it is an upper bound.

## 13. Risks and accepted gaps

- **The memory agent is the only writer.** Nothing checks its judgement. The §11 bars guard this.
- **Facts outrank documents.** Saving more often means a casual statement can outrank a document. The hypothetical and assistant-claim cases guard this.
- **A rewrite can still lose an entry.** §8 and Step 3.
- **Existing conversations start empty.** Older messages are not read, so a long conversation only builds memory from launch onward.
- **The router can say "noted" before the entry exists,** or the save can fail. The correction is still in the chat.
- **"What do you remember" lags by one message,** because the router reads entries from before this run.
- **Single worker assumption** for the queue, as before.
- **"Forget this" can be confirmed falsely.** The router says it is done before the run ends, so if the run fails the entry stays.
- **A failed or restart-lost run is not reread.** That message's entries are gone, because only the newest message counts as new.
- **A reworded insisted position loses the flag** unless the model sets it again, so agents may warn again.
- **At the 8,000-character limit** the model is told to merge or remove. If it does not, new entries are dropped with a warning, and a removal it chooses is unasked.
- **Two numbers one space apart are masked as one run,** so they are stored joined. Fixing it means changing a locked codec rule.
- **`edit_artifact` does not wait** for the memory agent, so it can read entries from before this message.
- **A stalled provider costs every task the 60-second wait** until it recovers. The fallback's entry lands next turn.
- **New on 1.94:** a thinking-only reply cut off at the token limit fails with no retry. Watch for it in acceptance.
- **Deleting a conversation can race a run:** the live check narrows it, and a small window remains.
- **Failed runs reach the ledger with `model` NULL and `outcome` "ok".** Known, not fixed here.

## 14. Out of scope, found on the way

- `save_memo` stores only the current message, so «رتبه» gets saved instead of the letter it points at.
- Compaction summaries state false things, and one leaked raw model reasoning.
- `SynthesizerOutput` in simple search accepts a wrapped answer as empty, the hole fixed in `MemoryOps`.
- `_text_as_chat` lets a literal `<tool_call>` reply through. Seen once in about 270 eval turns, and in none of 1,277 real replies over 90 days.
- `conversations.message_count` undercounts.
- Workspace summaries in the router's context carry raw titles outside any tag, and its current-turn `question` is not neutralised.
- CI installs `requirements.txt`, not the lock production uses, so a passing CI says little about production's library versions.
- `_REFS_FAMILY_KINDS` in `agents/writer_planner/walkers.py` and `_REFS_KINDS` in `agents/writer/prompts.py` list `agent_writer`, but drafts are stored as `agent_writing`, so a previous draft's references never reach the writer.
- A masked case number may be stored undecoded in workspace item titles. Likely, not verified.
- `shared/privacy/codec.py`: digit groups joined across a space or dash, and the IBAN gap near «ريال».
- Errors already in delivered documents from the study. The owner decides whether to tell those users.
