# رسائل الترحيب — Static Welcome Openers

**Status: BUILT 2026-08-14, not yet deployed.** 25 tests in
`backend/tests/test_welcome_message.py`.

The welcome is **the opening line of the turn's real answer**, not a separate bubble: whichever
agent produces the user-facing text for that turn opens with it, then answers. No new `messages`
row, no LLM cost beyond a handful of instruction tokens.

## Decisions (locked with the user)

| # | Decision | Choice |
|---|---|---|
| 1 | Surface | An **opening line inside the answer**, injected as a dynamic instruction on the responding agent |
| 2 | Who carries it | `router` (direct `ChatResponse`) **and** `planner_responder` (deep_search chat summary) — either can be the first responder |
| 3 | Trigger | The turn's first user-facing text; nothing on resume legs |
| 4 | Name | `preferred_name` via `shared/identity.py` (already the router's source), no-name variant otherwise |
| 5 | Profession | `legal` / `entrepreneur` get a targeted opener — **masculine honorific, as written**. See "Accepted cost" |
| 6 | Frequency of B | Only after a **quiet spell** (~7 days since the user's last activity) |

**Accepted cost on #5:** «المحامي» / «رائد الأعمال» are masculine and we store no gender, so women in
both groups see the masculine form; and `legal` is the card «قانوني» (hint: «محامٍ · طالب قانون · باحث
قانوني»), so a law student is greeted as «المحامي». Raised and accepted — do not "fix" it later
without asking.

**Superseded:** the first draft of this plan inserted a static assistant row above the user's first
message, with a three-bullet onboarding body. Dropped — the opener now flows straight into the answer
(«… بالنسبة لسؤالك»), which a separate bubble cannot do. The bullets (details / مساحة العمل / 3–5
دقائق) belong in the onboarding tour if they are wanted anywhere.

## The copy

Register is «أهلين» (the user's own wording). Each variant is one line, and ends handing the sentence
to the agent — the trailing «بالنسبة لسؤالك…» is the seam, deliberately open so the model completes it
in its own words («بالنسبة لسؤالك عن فصل الموظف أثناء التجربة…»).

### A — the user's first ever message. Beats B whenever both apply.

| `profession_group` | With name | Without name |
|---|---|---|
| `legal` | `أهلين بالمحامي {name}، شكرًا لتجربتك ريحان وإن شاء الله نكون عند حسن ظنك. بالنسبة لسؤالك…` | `شكرًا لتجربتك ريحان، وإن شاء الله نكون عند حسن ظنك. بالنسبة لسؤالك…` |
| `entrepreneur` | `أهلين برائد الأعمال {name}، شكرًا لتجربتك ريحان وإن شاء الله نكون عند حسن ظنك. بالنسبة لسؤالك…` | ditto |
| else / NULL / `declined` | `أهلين {name}، شكرًا لتجربتك ريحان وإن شاء الله نكون عند حسن ظنك. بالنسبة لسؤالك…` | ditto |

### B — first message of a later conversation, after a ~7-day gap.

| `profession_group` | With name | Without name |
|---|---|---|
| `legal` | `أهلين بالمحامي {name}، حياك الله من جديد. بالنسبة لسؤالك…` | `حياك الله من جديد. بالنسبة لسؤالك…` |
| `entrepreneur` | `أهلين برائد الأعمال {name}، حياك الله من جديد. بالنسبة لسؤالك…` | ditto |
| else | `أهلين {name}، حياك الله من جديد. بالنسبة لسؤالك…` | ditto |

## Trigger logic — resolved ONCE per turn

New `agents/utils/welcome.py`, called from `orchestrator.handle_message` before routing, so both
agents receive the same answer and the DB work happens once:

```
resolve_welcome(supabase, user_id, conversation_id) -> WelcomeState | None
render_welcome_instruction(state) -> str        # the whole prompt block
```

Fires only when `conversations.message_count == 0`, and never on a resume leg (`_resume_major_agent`
must not re-greet a user mid-clarification):

1. **A** — no `welcomed_at` key in `user_preferences.preferences`. Merge `{"welcomed_at": <iso>}`
   through the existing `merge_preferences` RPC — flat key, the JSONB merge is shallow (edu_* trap).
2. **B** — `welcomed_at` present AND the user's most recent *other* conversation was last updated
   more than 7 days ago. **Exclude the shared «محادثة تجريبية»** from that lookup: it is one global
   row every account sees, so its `updated_at` is not this user's activity and would suppress B for
   everyone.
3. Otherwise `None`.

Name via `shared/identity.py` (`clean_name` + first-name derivation), profession via
`users.profession_group`. Both are already loaded for the router — extend the same read rather than
adding a second query.

## Wiring

- `agents/router/router.py` — a new `@router_agent.instructions` callback rendering the block from
  `RouterDeps.welcome`. Sits beside `inject_user_call_name`, which stays: the welcome owns the
  opener, the name rule owns everything else.
- `agents/deep_search_v4/planner/deps.py` — `PlannerDeps` gains `welcome: str | None` (per-turn
  field, never persisted across a pause), hydrated in `build_planner_deps`.
- `agents/deep_search_v4/planner/prompts.py` — `build_responder_instructions` prepends the block.
  The responder's own rule «Start with the essence — not with preambles» directly contradicts an
  opening greeting, so the welcome block must say explicitly that this one line precedes the essence
  and the rest of the rule is unchanged. Without that, the two instructions fight and the model
  usually obeys the system prompt.
- **Non-determinism, accepted:** an instruction is not a prepend — the model may reword «شكرًا
  لتجربتك ريحان». The open «بالنسبة لسؤالك…» seam is what buys natural flow; a deterministic prefix
  would read mechanically against an answer that opens with a رule statement. `welcomed_at` is
  written when the instruction is *injected*, not when the text is verified, so a dropped greeting
  is not retried.
- **Do not emit the welcome as an early token.** `MessageList` shows `DeepSearchProgress` only while
  `isStreaming && !hasStreamContent`; a token emitted at turn start kills the progress card for the
  whole 3–5 minute run.
- **The writer's 500-char truncation.** `agents/writer/agent.py` silently truncates `chat_summary`
  at 500 chars, so a greeting prepended to a full-length summary would cost the END of the answer
  while the greeting survived. The cap is raised by `WELCOME_CHAR_ALLOWANCE` (160) on a welcomed
  turn. The deep_search responder has no such validator — only a `referenced_wi` resolver — so it
  needs no equivalent.

## Coverage — who else can answer first

`_dispatch` has three families plus the pause paths. A first message that is a drafting request
(«اكتب لي عقد عمل») is answered by `writer_planner.chat_summary` (`runner.py:344,722`), and an
ambiguous first message is answered by a planner's `ask_user` question. Neither carries the welcome
unless wired:

| Surface | File | Wired? |
|---|---|---|
| Router direct reply | `agents/router/router.py` | yes |
| deep_search chat summary | `deep_search_v4/planner/prompts.py` | yes |
| writing chat summary | `agents/writer_planner/` | yes |
| `ask_user` clarifying question | both planners' decider prompts | no — decided |
| memory family | `_run_memory` | no — system-side, not a greeting surface |

**Pause interaction (load-bearing).** A first turn that pauses on `ask_user` never reaches the
responder, so no welcome is written — and the resume leg is gated out (`message_count` is no longer
0 by then). Therefore `welcomed_at` must be merged **only when the turn completes**, never on the
paused path. Write it eagerly and that user silently loses their welcome forever; write it on
completion and they simply get it in their next conversation, which is correct.

## Router prompt (`agents/router/router.py`)

- `:196` and `:207` — delete "greetings" from the direct-answer list, replaced by one explicit rule:
  a greeting-only message gets a brief direct `ChatResponse` and is **never** dispatched. Without
  that line the standing bias to route can spend a full deep_search — and the user's points — on «مرحبا».
- `:473` (`inject_user_call_name`) — currently names a greeting as the example of where the name
  belongs. Retune, since the opener is now the welcome block's job.
- `agents/prompts/router/router__system.md:12,23` mirrors the deleted lines. Reference catalog only
  (not loaded at runtime) — sync it so it does not contradict the live prompt.

## Tests

`backend/tests/test_welcome_message.py` (or `agents/` equivalent) — A for a brand-new user; B only
past the 7-day gap; neither on a second conversation inside the window; A beats B; the six
profession/name variants; nothing on a resume leg; the demo conversation excluded from the
last-activity lookup; `welcomed_at` merged exactly once.
