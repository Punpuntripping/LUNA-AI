# Unified Responder — one voice for search + writing turns

Status: PLANNED (2026-10-15) · Owner: agents pipeline · Related: `next_step_suggestions.md`, `simple_search_responder.md`, `writer_planner_templates_v2.md`

## 1. Why

Today three prompts write the chat bubble the user reads:

| Family | Who writes the bubble | Other jobs on the same call |
|---|---|---|
| deep_search | `planner_responder` (`deep_search_v4/planner/prompts.py:222`, slot `planner_responder` = `_FLASH`) | publish gate `build_artifact` / `referenced_wi`, `next_steps` |
| simple_search | `simple_search_responder` (`simple_search/responder.py`, slot `_FLASH`) | per-document `cards` verdicts (publish gate, D7 = failure publishes nothing), `next_steps` |
| writer | **`writer_planner` owns the turn** (`writer_planner/runner.py` `handle_writer_planner_turn`): it talks to the user on pauses (`ask_user`, `present_plan_for_approval` «النماذج المقترحة»), on `aborted`, and on error lines. But on a **completed** turn it just passes through the executor's text: `chat_summary=writer_output.chat_summary`, `key_findings=writer_output.key_findings` (`writer_planner/runner.py:765`), written by `agent_writer` (`writer/models.py:123`, tier_1) — which also receives the `welcome_instruction` (`runner.py:671`). | pauses / handoff / abort decisions (planner) |

Three voices means every tone / engagement / language change has to be made three times and drifts. The product goal is a **recommendation layer** on the reply that:

1. makes the chat more engaging — **legally**: the next useful step, not chit-chat;
2. extracts more information from the user — asks for the *material fact* that would change the legal conclusion;
3. speaks the user's language — register, length, terminology.

All three live in the **voice**, so the voice becomes one agent. The **decisions** (publish gates, card verdicts, failure direction) stay family-specific.

## 2. Decisions (locked)

| # | Decision |
|---|---|
| D1 | One agent `responder` (new package `agents/responder/`). Layer 2 (user-facing voice) · model slot `responder` = `_FLASH` (tier_2). |
| D2 | Prompt = **shared core** (static) + **mode block** (static per mode: `deep_search`, `simple_search`, `writing`) + **dynamic tail** (turn package + user profile). Byte-identical prefix per mode → 3 cache prefixes. |
| D3 | Output = shared base + mode-specific fields (discriminated union). Gates stay in the schema but each family's runner keeps its own failure policy (deep_search: publish-from-artifact fallback; simple_search: D7 publish nothing; writer: templated line). |
| D4 | **Context = the router's context, verbatim** — the same `RouterContext` object (`router/context.py:433`) passed through the orchestrator, **not reloaded**: same `message_history`, case memory/metadata, compaction summary, workspace summaries (title + summary), user preferences, call name. It inherits the router's *context*, never its *instructions* or tools. |
| D5 | On top of the router context it gets the **artifact package**: the body (bounded, see §5.3) **plus the producing agent's internal notes** — aggregator `gaps` / `confidence` / `planner_brief`; writer `notes_ar` / `confidence` / unfilled placeholders; simple_search per-doc digests (D1..Dn). |
| D6 | **Follow-up question** = a separate output field, appended **by code** on its own line *after* the closing line («والتفاصيل والمراجع الكاملة في بطاقة البحث المرفقة.»), after `key_findings` on writing turns. Never written inside `chat_summary_md`. |
| D7 | **Materiality test** decides question vs next move: ask only when the answer would change the legal conclusion. Then the question leads and chips shrink to ≤1 action or become answer chips. Otherwise no question; chips carry the turn. |
| D8 | Chips = **actions** (`narrow_search` / `draft` / `apply` / `open`) + new **answer** chips (`kind: "answer"`): a half-written reply pasted into the composer («نعم، ينص العقد على شرط جزائي بقيمة …»). |
| D9 | Address as a lawyer («عقد موكلك») **only** when `users.profession_group == "legal"` or the user said so in the conversation («موكلي», «أنا محامي»). Otherwise neutral second person («العقد»، «عقدك»). |
| D10 | The question is **captured**: `messages.metadata.follow_up_question` + a history note so the router knows the next user message is an answer. It is NOT an `agent_question` (that kind is a blocking pause and is filtered out of router history — `router/context.py:_load_filtered_messages`). |
| D11 | Rollout behind `RESPONDER_UNIFIED_FAMILIES` (comma list), per family, default empty → instant rollback per family. |

## 3. Example (acceptance scenario)

User: «تأخر المطور ثلاثة أشهر عن تسليم الفلة حسب التاريخ المنصوص عليه في العقد»
→ deep_search returns delay remedies; aggregator gaps include the contract terms.

Bubble:

> …(essence: the buyer's remedies for delay)… والتفاصيل والمراجع الكاملة في بطاقة البحث المرفقة.
>
> يختلف التعويض جوهرياً إن كان العقد يتضمن شرطاً جزائياً عن التأخير — هل ينص عقد موكلك على ذلك أو على مهلة سماح؟

(«عقد موكلك» only because profession = legal; otherwise «هل ينص العقد…».)

Chips: `[نعم، يوجد شرط جزائي…]` `[لا يوجد شرط جزائي]` `[صياغة إنذار للمطور]`

Other material candidates the responder ranks (asks the **one** most decisive, max two if tightly linked): off-plan sale (وافي → different regime), formal notice already sent. Never asks what the search could find, or what the context already answers.

## 4. Agent design

### 4.1 Package

```
agents/responder/
  __init__.py
  agent.py      create_responder(mode, model_override) — one Agent per mode, cached
  prompts.py    CORE_PROMPT, MODE_BLOCKS[mode], build_turn_message(pkg)
  models.py     ResponderInput (dataclass), ResponderOutput union
  runner.py     run_responder(pkg) -> ResponderOutput | None (None = failed; caller applies its policy)
  context.py    render_router_context(ctx) — shared renderers (see 4.3)
  tests/
```

Slot added to `agents/utils/agent_models.py`: `"responder": _FLASH`. `planner_responder` / `simple_search_responder` slots are removed only in §9 cleanup.

Structured output uses the shared salvager (`agents/utils/structured_output.py`) with a retry message naming **this** schema's fields.

### 4.2 Prompt layers

**Shared core (static):** role (Rayhan's voice), Arabic rules (Latin digits only, no `##`, no `[n]` markers, no fabrication, honest confidence), address rule D9, `next_steps` contract (ladder from `planner/prompts.py` §next_steps, generalized), follow-up question policy (§6), "never retype legal text", `RAYHAN_CAPABILITIES_MD` (module-level concatenation, as today).

**Mode blocks (static):**
- `deep_search` — current planner_responder rules: chat summary framing per mode (`_MODE_FRAMING` moves to dynamic tail, unchanged), `build_artifact` / `referenced_wi` gate rules, closing line only when `build_artifact=true`.
- `simple_search` — current simple_search_responder rules: lead-in not answer (bodies pasted by code), `cards` verdicts per D-label, `open` chips allowed.
- `writing` — new: introduce the draft in 1–2 sentences, point at the sections that need the user (from `notes_ar` / placeholders), closing line pointing to the card.

**Dynamic tail (user message, order fixed):** welcome block (when due — `utils/welcome.py`, unchanged behaviour) → mode framing → artifact package → user profile (§7) → profession flag.

### 4.3 Context = router context

- Refactor the router's `inject_*` renderers (`router/router.py:514-735`: case context, call name, preferences, workspace summaries, compaction summary) into pure functions in `agents/responder/context.py` (or `agents/utils/router_context_render.py`) that **both** the router and the responder call → one rendering, no drift.
- `message_history` = the same `RouterContext.message_history` list, passed to `agent.run(..., message_history=...)`.
- The orchestrator keeps the `RouterContext` it built in `_route` and hands it to the family runner via `MajorAgentInput` / deps (new optional field `router_context`). Pinned / router-less dispatches (headless) → `None` → responder falls back to today's narrower context (`render_responder_context`).
- وضع السرية: the object is already encoded with this turn's codec — **never re-encode, never reload**. The responder's output goes through the same decode path as today's `chat_summary` (verify in orchestrator token emission + `message_service` persistence).

### 4.4 Output schema

```python
class ResponderBase(BaseModel):
    chat_summary_md: str
    next_steps: list[NextStep]            # actions + answer chips, see §6.3
    follow_up_question: str | None = None # D6; ≤ 300 chars, one question (two only if tightly linked)
    question_basis: str | None = None     # internal: which gap/fact it targets — logged, never shown

class DeepSearchResponse(ResponderBase):
    mode: Literal["deep_search"]
    build_artifact: bool = True
    referenced_wi: str | None = None

class SimpleSearchResponse(ResponderBase):
    mode: Literal["simple_search"]
    cards: list[CardVerdict]              # moved from simple_search/responder.py

class WritingResponse(ResponderBase):
    mode: Literal["writing"]
```

One Agent per mode with its own `output_type` (the union is for typing/tests; each mode agent only sees its own schema — smaller tool schema, stable prefix).

## 5. Wiring per family

### 5.1 deep_search
- `deep_search_v4/planner/runner.py:568` — replace `create_planner_responder(...)` with `run_responder(mode="deep_search", ...)` when the family is flagged on.
- Keep `_response_from_artifact` (`runner.py:155`) as the failure fallback (artifact IS the product). Map `DeepSearchResponse` → existing `PlannerResponse` fields so `orchestrator.py:3469-3493` is untouched.

### 5.2 simple_search
- `simple_search/runner.py` `_finalise` — replace the responder call; keep D7 (failure → ship bodies in full, publish no card).
- `CardVerdict` + validation move into `agents/responder/models.py`; `simple_search/responder.py` becomes a thin adapter until §9.

### 5.3 writer (via writer_planner — the turn owner)
- Call site = **`writer_planner/runner.py`**, completed path (~line 750-770), after `handle_writer_turn` returns and the draft is published — NOT inside `agents/writer/`. The planner owns the writing turn; the responder becomes its closing stage, exactly as phase 3 is for the deep_search planner.
- Responder inputs from the planner: `decision.intent_ar`, `plan_md`, `chosen_template`, `parties`, `edit_mode`; from the executor: draft body, `notes_ar`, `confidence`, `key_findings`.
- `welcome_instruction` moves from the executor (`writer/deps.py`, `writer/agent.py`, `runner.py:671`) to the responder — the executor no longer writes anything user-facing.
- `WriterLLMOutput.chat_summary` → dropped from the executor prompt/schema (executor keeps `key_findings`, `notes_ar`, `confidence`).
- Planner-authored texts that stay as they are (not the responder's job): pause questions (`ask_user`, `present_plan_for_approval`), `aborted` rationale on fresh dispatch, error lines (`runner.py:379/500/548/727`).
- Failure → templated line «أعددت المسودة في البطاقة المرفقة، وراجع النقاط أدناه.» + executor's `key_findings`.
- Cost: +1 flash call per drafting turn. Writer turns gain `next_steps` + follow-up question (they have neither today).
- Responder never runs on paused turns («النماذج المقترحة», planner pauses, simple_search question-as-text).

### 5.4 Artifact body bounds
| Mode | Body passed |
|---|---|
| deep_search | full `synthesis_md` up to `RESPONDER_BODY_CHARS` (start 12k), else today's trimmed digest; always `gaps`, `confidence`, `key_findings`, `planner_brief`, per-source ref counts |
| simple_search | per-doc digests (`ResponderDocDigest`, 1600 chars) — unchanged |
| writing | full draft up to `RESPONDER_BODY_CHARS`, else outline + first/last sections; always `notes_ar`, `confidence`, placeholders left unfilled |

## 5.5 Responsibility map — before → after

| Job | Before | After |
|---|---|---|
| Final bubble text — deep_search | `planner_responder` (inside deep_search planner, phase 3) | `responder` (mode `deep_search`), still called by the deep_search planner runner |
| Final bubble text — simple_search | `simple_search_responder` | `responder` (mode `simple_search`), called by `simple_search/runner.py` `_finalise` |
| Final bubble text — writing | `agent_writer` executor (`chat_summary`), passed through by `writer_planner` | `responder` (mode `writing`), called by `writer_planner` runner |
| Welcome opening line | planner_responder / simple_search responder / writer executor (3 places) | `responder` only |
| `next_steps` chips | planner_responder + simple_search_responder (writer: none) | `responder`, all three modes |
| Follow-up question + answer chips | — | `responder` |
| Publish gate deep_search (`build_artifact`/`referenced_wi`) | planner_responder | `responder` emits it; planner runner applies it (unchanged policy) |
| Card verdicts simple_search | simple_search_responder | `responder` emits them; `_finalise` applies D7 (unchanged) |
| Pause questions / plan approval / abort / handoff | deep_search planner decider, writer_planner decider | **unchanged** |
| Synthesizer bodies, aggregator report, draft body | synthesizers / aggregator / executor | **unchanged** |
| Style profile | — | `style_profiler` (Layer 4, async) |
| Routing an answer to a follow-up question | — | router (new rule) |

The rule that falls out: **the planners decide, the responder speaks.** Nothing below Layer 2 writes user-facing text any more.

## 6. Follow-up question

### 6.1 Policy (in shared core)
1. **Materiality test**: ask only if the answer would change the legal conclusion, the applicable regime, or the next procedural step.
2. Source of the question = the producing agent's gaps / notes. The responder words the question; it does not invent the gap.
3. Never ask what `message_history`, case memory, attachments or workspace summaries already answer.
4. Never ask what a search could find — only facts that live in the user's file (contract terms, dates, notices, amounts, the user's role).
5. One question; two only when tightly linked («شرط جزائي أو مهلة سماح»). Explain *why* in the same sentence («يختلف التعويض جوهرياً إن…»).
6. Address rule D9.
7. No question on: paused turns, `build_artifact=false` because a prior card covers it, empty-results turns (state the gap instead), or when the previous assistant turn's `follow_up_question` went unanswered and the user moved on (don't nag — max one unanswered in a row).

### 6.2 Placement (code, not prompt)
Bubble = `chat_summary_md` → (writing: key_findings bullets) → `"\n\n" + follow_up_question`. Emitted as the last `token` before `next_steps`/`done` in `orchestrator.py` (deep/simple path ~1415/1618/3040; writer path ~3040-3058). The closing line stays the last line of `chat_summary_md`; the question is always its own paragraph after it.

### 6.3 Chips
- `NextStepKind` gains `"answer"` (`agents/models.py`, `frontend/lib/contracts/messages.ts` + `next-steps.ts`, mobile via `@contracts`).
- Rule: with a question → up to 2 `answer` chips + ≤1 action; without → today's ladder (≤3, one per kind). Total ≤3.
- `answer` is **exempt from the one-per-kind dedup** in `parseNextSteps` and `next_step_errors`; icon in `NextStepChips.tsx`.
- `answer` prompt = the user's half-written reply, first person, may end with «…» for the user to complete.
- Contract-first rollout: web + mobile parser ship **before** the backend emits `answer` (old clients drop unknown kinds — safe, but dedup must not hide the 2nd answer chip).

### 6.4 Capture
- Persist `metadata.follow_up_question` (+ `question_basis`) on the assistant message at `done`, same path as `captured_next_steps` in `message_service.py:1050-1314` (new SSE event `follow_up` or extend the `next_steps` event payload — prefer a separate event; frontend ignores it).
- `utils/history.py`: `build_follow_up_note` → `〔[نظام] سُئل المستخدم: …〕`, appended in `append_next_steps_note` alongside the chips note (both router `messages_to_history` and orchestrator `_load_recent_messages`).
- Router prompt: when the user's message answers the previous `follow_up_question`, route to the **same family** with `target_wi` = that turn's WI (refine), mirroring the planner→router handback pattern. Add router tests for «نعم يوجد شرط جزائي ١٠٪» after the villa turn.
- Phase-later (not v1): write answered facts into case memory via the memory agent.

## 7. User profile (language mirroring)

- New Layer 4 agent `style_profiler` (tier_2), runs **async after a turn** every `STYLE_PROFILE_EVERY_N` user messages (start 8) or when none exists.
- Output (small, ≤600 chars rendered): register (فصحى رسمية / بيضاء / سعودية دارجة), preferred answer length, terms the user uses (e.g. «الشرط الجزائي» vs «غرامة التأخير»), whether they write as counsel or as a party. **No facts, names, numbers or case details** (PDPL + masking: profile must be identifier-free; validator rejects digits/names).
- Storage: new table `user_style_profiles` (user_id PK → users.user_id, profile jsonb, source_msg_count int, updated_at) — **RLS on**, owner-only select; service role writes. Migration via @sql-migration, verify live.
- Rendered at the end of the dynamic tail (after the artifact) to keep the prefix cached. Router may adopt it later (out of scope v1).

## 8. Measurement

`analytics_events` (139): `next_step_clicked` (exists? verify) with `kind` incl. `answer`; new `follow_up_answered` (router classified the next user message as an answer); per-turn log fields `responder.mode`, `asked_question`, `question_basis`, `chips_n`. Compare 2 weeks before/after per family: chip CTR, answer rate, turns/conversation, follow-up within 24h.

## 9. Phases

| Phase | Scope | Agent |
|---|---|---|
| P0 | Contracts: `answer` kind, dedup exemption, icon, `metadata.follow_up_question` type — web + mobile, ship first | @nextjs-frontend, @expo-mobile, @integration-lead |
| P1 | `agents/responder/` package, prompts, schema, shared router-context renderers, unit tests (TestModel/FunctionModel, prefix byte-identity per mode, placement, masking passthrough) | builder |
| P2 | Wire deep_search → simple_search → writer behind `RESPONDER_UNIFIED_FAMILIES`; pass `RouterContext` through orchestrator | builder |
| P3 | Follow-up question: field, code placement, SSE + metadata capture, history note, router "answer" routing | builder + @sse-streaming |
| P4 | `style_profiler` + `user_style_profiles` migration | @sql-migration + builder |
| P5 | Analytics events + Logfire fields | builder |
| P6 | Live validation: villa scenario (legal profile vs individual profile), simple lookup, writing turn; @logfire-monitor-agent per turn; then enable families one by one | @smoke-tester |
| P7 | Cleanup after 1 week stable with all families on — full inventory in §9.1 | builder |

### 9.1 Dead code & prompts to delete (P7)

Done when `grep -rn "planner_responder\|simple_search_responder\|PLANNER_RESPONDER\|build_responder_\|render_responder_context"` returns only `agents/responder/` and historical reports. Delete the `RESPONDER_UNIFIED_FAMILIES` flag and its old-path branches in the same PR.

**deep_search planner**
- `planner/agent.py` — `create_planner_responder`
- `planner/prompts.py` — `PLANNER_RESPONDER_SYSTEM_PROMPT`, `build_responder_instructions`, `build_responder_user_message`, `render_responder_context`, `_RESPONDER_CONTEXT_HEADER`. `_MODE_FRAMING` moves to `agents/responder/prompts.py`
- `planner/models.py` — `PlannerResponse` (replaced by `DeepSearchResponse`; the runner maps it straight onto `SpecialistResult`)
- `planner/deps.py` — welcome fields, `_agg_output`/`_decision` passing for the responder (keep only what the responder package reads)
- `planner/runner.py` — phase-3 responder block (~556-625); keep `_response_from_artifact` as the fallback
- `planner/__init__.py` exports; tests `test_responder_next_steps.py`, responder parts of `test_planner_agents.py`, `conftest.py`, `test_run_retrieval.py` → ported to `agents/responder/tests/`

**simple_search**
- `simple_search/responder.py` — whole file (`CardVerdict` + checks move to `agents/responder/models.py`)
- `simple_search/prompts.py` — responder system prompt + `build_responder_user_message` + welcome rendering
- `simple_search/models.py` — `ResponderDocDigest` moves to the responder package
- `simple_search/runner.py` — responder call + welcome plumbing in `_finalise`
- `simple_search/__init__.py` exports; tests `test_responder.py`, `_fmodels.py`, `test_runner.py` responder fakes; eval scripts `eval/adv_unlock_01.py`, `adv_unlock_02b.py`, `rerun_b_regression.py` (repoint or delete)

**writer / writer_planner**
- `writer/models.py` — `WriterLLMOutput.chat_summary`, `WriterOutput.chat_summary`
- `writer/prompts.py` — `chat_summary` in the output example (~186) and its rules (~95, ~199)
- `writer/deps.py`, `writer/agent.py` — `welcome_instruction`
- `writer_planner/runner.py:671` (welcome to executor), `:765` (pass-through) → replaced by the responder call

**shared**
- `agents/utils/agent_models.py` — slots `planner_responder`, `simple_search_responder`
- `agents/models.py` — `next_step_errors` moves to/extends into the responder package (answer-kind rule); `welcome_instruction` field on `MajorAgentInput` if nothing else reads it
- `agents/utils/welcome.py` — consumers collapse to router + responder
- `agents/router/router.py` — references to the old responders in comments/docs
- `agents/orchestrator.py` — comments ~3201-3225, ~3469 describing the planner's phase 3; writer `key_findings` emission stays
- `agents/tests/test_cost_ledger.py` — agent names `planner_responder` → `responder`; check the `llm_calls` agent label + any Logfire dashboards/alerts/queries filtering on the old names
- `backend/tests/test_welcome_message.py` — repoint at the responder
- `agents/prompts/INDEX.md` + `agents/prompts/search/`, `writers/` catalog entries (reference-only catalog — update to point at `agents/responder/prompts.py`)

**Not deleted:** deep_search planner decider, writer_planner decider, synthesizers, aggregator, `agent_writer` executor (minus `chat_summary`), `_response_from_artifact`, the D7 policy in `_finalise`.

## 10. Traps

- `agent_question` ≠ follow-up question (§D10) — reusing the kind hides it from router history.
- Don't reload router context — masking fakes would diverge between router and responder.
- Prefix discipline: welcome, profile, profession, mode framing all go in the dynamic tail; nothing per-turn in the system prompt.
- `next_steps` key, never `suggestions` (= agent_question chips).
- Shared `OUTPUT_TOKENS_CAP` only — no per-agent cap for the responder.
- simple_search D7 inverts the failure direction — the shared runner returns `None`, the family decides.
- `chat_summary_md` must not contain the question or any offer («إذا تحب…») — chips + the question field replace it.
- Headless/pinned dispatch has no `RouterContext` — must degrade, not crash.

## 11. Open questions

1. Move `key_findings` from the writer to the responder? (v1: keep in writer.)
2. `STYLE_PROFILE_EVERY_N` and whether the router also consumes the profile.
3. Should an answered follow-up auto-trigger a refine run, or just route like any message? (v1: route; router decides.)
