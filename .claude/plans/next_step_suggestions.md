# Next-step suggestions — clickable chips with a capability manifest

Status: PLANNED 2026-10-04 · Families: `deep_search` (planner_responder) + `simple_search` (responder)

## 1. Problem

Prod example (وقف ذري question, deep_search). The planner responder closed with:

> إذا تحب، أقدر أساعدك في البحث عن شروط وإجراءات تسجيل الوقف الذري لدى المحكمة المختصة، أو عن آراء فقهية مقارنة حول مسألة تحجيم الوقف بالثلث في حال الصحة.

- **«آراء فقهية مقارنة»** — impossible. There is no fiqh corpus. A «نعم» costs the user points for an empty search.
- **«لدى المحكمة المختصة»** — invented venue. The corpus holds «الدليل الشامل: توثيق وقف في السعودية» (MoJ) and «الدليل الشامل: تسجيل وقف» (GAA). The responder never saw them.
- **Two offers joined by «أو»** — breaks the prompt's "only one" rule.

Root causes:

1. **No capability knowledge.** `PLANNER_RESPONDER_SYSTEM_PROMPT` (`agents/deep_search_v4/planner/prompts.py:215`) says nothing about the corpora, the families, or what is out of scope. The only copy of that knowledge is scattered through the router prompt (`agents/router/router.py:227`, name search; family routing).
2. **No conversational context.** The responder sees only `<query>` plus the aggregator digest. The simple_search builder calls this out explicitly as divergence **D6** (`agents/simple_search/prompts.py`, `build_responder_user_message` docstring). As a result it cannot tell that a lawyer preparing a claim wants a drafting step, not another search.
3. **Suggestions are free text appended to the bubble.**
   - deep_search: `agents/orchestrator.py:3404-3406`.
   - simple_search: `agents/simple_search/runner.py:1816`.
   - Nothing is machine-readable, nothing is clickable, and a typed «نعم» leaves the router to reverse-engineer the offer.

## 2. Decisions (user, 2026-10-04)

| # | Decision |
|---|---|
| D1 | Scope = **deep_search + simple_search**. Writer is unchanged for now. |
| D2 | **Chips only.** No suggestion sentence in the bubble. |
| D3 | **0–3 chips**, each a different kind. Shown **only under the latest assistant message**. |
| D4 | Click = **paste into the composer** (editable). Never auto-send. |
| D5 | The deep_search ladder **drops "open a reference"**. Refs are already visible and clickable in the card, so offering them repeats them. simple_search keeps `<unselected_candidates>`: those objects were NOT opened or carded, so the user cannot see them. |
| D6 | Inject the decider's context blocks into the planner responder. |

Consequence of D4: there is **no routing hint**. The user may edit the text, so a pre-chosen family can point the wrong way. A clicked chip is an ordinary user message, and the router decides as usual.

## 3. Design

### 3.1 Capability manifest — one copy

New module `agents/utils/rayhan_capabilities.py`:

- Exports `RAYHAN_CAPABILITIES_MD`, a static English block with Arabic examples, ~250 tokens.
- It is a **static string**, placed in the system prompt (prefix-cache safe — see [prompt-caching] memory). Never put it in the dynamic instructions.

**CAN** (each line names the family that delivers it):

- Search regulations, implementing rules (لوائح) and appendices → deep_search.
- Search circulars → deep_search.
- Search anonymized judgments by facts, principle, or case/judgment number → deep_search.
- Search e-government service guides (procedure, channel, requirements, e.g. توثيق وقف) → deep_search.
- Open one named document in full → simple_search.
- Apply a found rule to the user's facts → deep_search follow-up.
- Compare rulings → deep_search.
- Draft a legal document (لائحة دعوى، مذكرة، عقد، خطاب) grounded in found sources → writing.

**CANNOT** (never offered):

- Fiqh opinions or madhhab comparisons.
- Search by party / person / company name (judgments are anonymized).
- Live case status, or any action inside ناجز / Absher / a government portal.
- Filing, submitting, or contacting an authority on the user's behalf.
- Foreign law.
- Web, news, or anything after the corpus snapshot.
- Fee or price quotes for government services beyond what a guide states.

Consumers in this plan: planner responder system prompt and simple_search responder system prompt. Router adoption is a **follow-up**: it already encodes parts of this inline, and changing its cached prefix is a separate decision.

**Trap:** the manifest is a *claim surface*. When a corpus or family is added or removed, this file is the one to update. Add a line to `project_router_rayhan_docs` memory pointing here.

### 3.2 Structured output — `NextStep`

Shared model in `agents/models.py`:

```python
NextStepKind = Literal["narrow_search", "draft", "apply", "open"]  # "open" = simple_search only

class NextStep(BaseModel):
    kind: NextStepKind
    label: str   # chip text, Arabic, ≤ 40 chars, no trailing punctuation — «صياغة لائحة دعوى»
    prompt: str  # the full message pasted into the composer, Arabic, first person, ≤ 200 chars —
                 # «اكتب لي لائحة دعوى لإثبات الوقف الذري بناءً على نتائج البحث السابق»
```

Changes:

- `PlannerResponse.suggestion_md` → `next_steps: list[NextStep] = []`.
- simple_search `ResponderOutput.suggestion_md` → same.

Output validator (pydantic-ai `output_validator` → `ModelRetry`, once):

- ≤ 3 items, no duplicate `kind`.
- `label` ≤ 40 chars, `prompt` ≤ 200 chars.
- deep_search: `kind == "open"` is rejected.

Salvage on a second failure: truncate to 3, drop invalid items, and **never fail the turn over chips** (see [output-salvage] memory).

`prompt` is written as the **user** speaking («ابحث لي عن…», «اكتب لي…»), not as Rayhan offering. It is what lands in the composer.

### 3.3 Ladder — deep_search responder

Replaces `## suggestion_md rules` (`prompts.py:239-242`). Walk the ladder and emit 0–3, at most one per rung:

1. **narrow_search**: a reported gap or an unanswered aspect of the question that falls **inside** the manifest. Example: «إجراءات توثيق الوقف لدى وزارة العدل», which is in the compliance corpus. A gap **outside** the manifest is stated honestly in `chat_summary_md` and never offered.
2. **draft**: when `<recent_messages>`, `<case_brief>`, or the question signal the user is building a document or a case («موكلي»، «أبغى أرفع»، a prior writer card). Otherwise skip.
3. **apply**: applying the finding to the user's facts. Always available. It must name the facts it would apply to, taken from context: «طبّق الحكم على وقف والدي لكامل أملاكه», never a generic «طبّق على وضعك».
4. **Empty list** when the answer is complete and nothing above would add value, or `build_artifact=false` with a covering prior card. **Empty is allowed.** Three weak chips are worse than one good one.

Hard rules:

- Never offer what `chat_summary_md` already answered.
- Never name a specific article, service, or ruling that is not in the digest.
- Never offer anything on the CANNOT list.

### 3.4 Ladder — simple_search responder

Keep the existing ladder (`agents/simple_search/prompts.py:596-610`), mapped onto kinds:

| Rung | Kind |
|---|---|
| unselected candidate | `open` |
| rest of a truncated doc | `open` |
| look for a related object | `narrow_search` |
| apply to situation | `apply` |
| (new) drafting signal in context | `draft` |

The pause leg is unchanged: `suppress_suggestion=True` forces `next_steps=[]`. Keep the code-side guarantee at `runner.py:1816`, now as `next_steps = []`.

### 3.5 Context injection — planner responder (D6)

`build_responder_instructions` (`prompts.py:555`) gains the same blocks the decider renders, reusing the existing renderers:

- `_render_case_brief`, `_render_compaction_summary`, `_render_recent_messages`, `_render_prior_searches`.
- **Not** `_render_attached_items`: full attachment bodies are too heavy. The decider's `planner_brief` already carries the attachment facts and is rendered today.

Ordering: context blocks → digest → mode framing. Wrap them in a header that says the blocks are for framing the summary and choosing next steps, not for re-answering.

Masking: `deps.recent_messages` / `case_brief` are **already codec-encoded** on PlannerDeps (`deps.py`). Do not re-encode.

Cost: `_RECENT_MESSAGES_N` messages, ~1–3k tokens on tier_1. Record `planner.responder_context_chars` on the EVENT_RESPONDED span so it can be watched.

### 3.6 Transport and persistence

**Orchestrator**

- `SpecialistResult` gains `next_steps: list[dict] = []`.
- deep_search (`orchestrator.py:~3402`): stop concatenating `suggestion_md`; fill `next_steps` from `response.next_steps`.
- simple_search (`orchestrator.py:~1591` and the fresh-dispatch twin at `~2813/2950`): same, from `SimpleSearchRunResult.next_steps`.
- **Both resume paths too.** The resume legs are where things got dropped before (see [responder-resume], [ss-forced-loop]).

**SSE**

- New event `next_steps` → `{"items": [{kind, label, prompt}]}`.
- Emitted once, **after** the last `token` and before `done`.

**Masking (وضع السرية)**

- `label` / `prompt` were generated from ENCODED input and can carry fake identifiers.
- `message_service` must `decode_text(codec, …, emit=True)` each field before the SSE and before persistence, so the row is stored real.
- This is the same pattern as `agent_question` (`message_service.py:1040-1053`). Add a test with a masked name inside `prompt`.

**Persistence**

- `message_service` captures the event like `captured_artifact_ids` (`message_service.py:896`).
- On `done` it writes `update_data["metadata"] = {**existing, "next_steps": items}`.
- Use key **`next_steps`**, NOT `suggestions`. `metadata.suggestions` already means "read-only clarifying-question chips" (`frontend/types/index.ts:202`, `MessageBubble.tsx:525`).
- No migration: `messages.metadata` exists. **Verify live** that the column is jsonb and that the placeholder row's metadata is `{}` rather than NULL before merging (see [migration-drift] memory).

**History — the typed «نعم» case**

Chips leave the bubble, so the conversation history must still show what was offered. Otherwise «نعم» after an offer is meaningless to the router.

- In `agents/utils/history.py` add `build_next_steps_note(next_steps)`, which renders one line:
  `〔[نظام] اقتُرح على المستخدم: صياغة لائحة دعوى · تطبيق على وقف والدك〕`
  The labels are joined with « · ».
- Append the note to assistant content in both:
  - `messages_to_history` (router);
  - `_load_recent_messages` (planners and writer), which must add `metadata` to its `.select(...)`.
- The note goes through the same codec choke point as the content.
- Extend the existing provenance legend: the note means "offered as clickable suggestions; if the user's reply accepts one, treat its label as the request".

### 3.7 Frontend

- `types/index.ts`:
  - `MessageMetadata.next_steps?: NextStep[]`;
  - `NextStep {kind, label, prompt}`;
  - SSE type `SSENextSteps`.
- Streaming hook: on `next_steps`, attach the items to the streaming assistant message's metadata (optimistic, so the chips appear without a refetch).
- New `components/chat/NextStepChips.tsx`:
  - rendered by `MessageBubble` only when `isLatestAssistant && isCompleted && !isAgentQuestion`;
  - `MessageList` passes `isLatestAssistant`.
  - RTL `flex-wrap gap-1.5`, rounded-full outline buttons, a small icon per kind:
    - narrow_search → Search
    - draft → PenLine
    - apply → Scale
    - open → BookOpen
  - Keyboard focusable; `aria-label` = prompt.
- Click → paste:
  - Add a chat-store action `setComposerText(text)`. The existing `pendingComposerDraft` is consumed **once on mount** (`ChatInput.tsx:244`), so it is not enough for an already-mounted composer.
  - `ChatInput` subscribes, sets the textarea value, focuses it, puts the caret at the end, and auto-resizes.
  - If the composer already holds user text, **replace it**. Shift-click could append; not in v1.
- Hide all chips the moment a new user message is sent; the latest-only rule covers this naturally.
- Mobile: chips wrap; verify at 375 px.

### 3.8 Telemetry

- Logfire attributes on responded:
  - `next_steps.count`;
  - `next_steps.kinds`;
  - `next_steps.salvaged`.
- Frontend analytics (`analytics_events`, see [analytics] memory): `next_step_clicked {kind, family}`, plus `next_step_sent {kind, edited: bool}`, where `edited` compares the sent text with the pasted text. Click → send → edit rates per kind tell whether the ladder is any good.

## 4. File manifest

| File | Change |
|---|---|
| `agents/utils/rayhan_capabilities.py` | NEW — the manifest |
| `agents/models.py` | `NextStep`, `NextStepKind`; `SpecialistResult.next_steps` |
| `agents/deep_search_v4/planner/models.py` | `PlannerResponse.next_steps` replaces `suggestion_md` |
| `agents/deep_search_v4/planner/prompts.py` | system prompt: manifest + ladder; `build_responder_instructions`: context blocks |
| `agents/deep_search_v4/planner/agent.py` | output validator for next_steps |
| `agents/deep_search_v4/planner/runner.py` | fallback responses (`:163`, `:179`) → `next_steps=[]`; telemetry |
| `agents/deep_search_v4/cli.py` | print next_steps (`:311`, `:435`) |
| `agents/simple_search/responder.py` | `ResponderOutput.next_steps`; validator |
| `agents/simple_search/prompts.py` | manifest + kind-mapped ladder; schema example |
| `agents/simple_search/runner.py` | carry next_steps on `SimpleSearchRunResult`; pause suppression |
| `agents/orchestrator.py` | stop concatenating; emit `next_steps` SSE on deep + simple, fresh + resume legs; `_load_recent_messages` selects metadata + note |
| `agents/utils/history.py` | `build_next_steps_note`; append in `messages_to_history` |
| `backend/app/services/message_service.py` | relay + decode + capture + persist into metadata; router history load selects metadata |
| `frontend/types/index.ts` | types |
| `frontend/stores/chat-store.ts` | `setComposerText` |
| `frontend/components/chat/ChatInput.tsx` | subscribe + paste + focus |
| `frontend/components/chat/NextStepChips.tsx` | NEW |
| `frontend/components/chat/MessageBubble.tsx`, `MessageList.tsx` | render latest-only |
| SSE hook (`useChat`/stream handler) | handle `next_steps` |
| tests | see §5 |

## 5. Tests

- **Planner responder prompt.**
  - Manifest present in the system prompt.
  - Context blocks render when deps carry them and are omitted when empty.
  - The system prompt stays byte-identical across turns (cache).
- **Validator.**
  - 4 items → retry.
  - Duplicate kind → retry.
  - `open` on deep_search → retry.
  - Salvage truncates and never raises.
- **Orchestrator.**
  - deep + simple, fresh + resume: the `next_steps` SSE is emitted after the tokens, and `chat_summary` no longer contains suggestion text.
  - simple_search pause → no `next_steps` event.
- **message_service.**
  - Masked fake inside `prompt` is decoded in the SSE **and** in the persisted metadata.
  - Existing metadata keys are preserved on merge.
- **history.**
  - The note is appended for rows with `metadata.next_steps`, absent otherwise, and codec-encoded.
- **Frontend.**
  - `npx tsc --noEmit`.
  - Playwright:
    - ask → chips appear only under the latest reply;
    - click → composer holds the prompt, focused, nothing sent;
    - send a new message → old chips gone;
    - reload → chips restored from metadata on the latest reply.
- **Live eval.** Re-run the وقف ذري question.
  - Pass: no fiqh offer; any procedure chip names وزارة العدل / ناجز or carries no venue at all; ≤ 3 chips.

## 6. Rollout

1. Backend and frontend ship together. The frontend ignores an unknown SSE event, but the backend stops appending suggestion text. Deploying the backend alone means **no suggestions at all** until the frontend lands, which is acceptable but should be brief. Deploy the frontend first; it is harmless without the event.
2. No migration (verify the `metadata` jsonb live first).
3. After deploy, watch for 3 days:
   - `next_steps.count` distribution and salvage rate;
   - `next_step_clicked` / `next_step_sent` / `edited` per kind.

## 7. Open / follow-ups

- Router adopts `RAYHAN_CAPABILITIES_MD`. Decide whether to move the inline rules (name search, etc.) into it.
- Writer family chips (revise / add exhibit / copy to Najiz). Different kinds, separate plan.
- The deep_search responder could see the aggregator's *dropped-but-relevant* references for a better narrow_search rung. Not needed for v1 (D5).
