# One deep-search planner on V4.1 — build plan (2026-11-27)

## Why
Test account, conversation `fa4fd43c`: «ابحث» → router (no شامل) → ordinary planner
(`planner_decider`, V4 Flash 0731) asked a clarify question → the reply «ابغى بحث شامل»
resumed that paused planner (a pause resumes BEFORE the router) → «بحث عميق» ran.
Two planners decide the same thing: the ordinary one (asks, tools, Flash) and the شامل one
(`extended_planner`, V4.1, angles + budget, cannot ask). Whoever runs first owns the turn.

Owner: "we need the planner to be 1 agent and one agent only, using v4.1".

## Owner decisions (AskUserQuestion 2026-11-27)
- **O1 — Router hints, planner decides.** `DispatchAgent.extended` stays, as a hint.
  The planner sees the شامل option whenever the turn qualifies and decides.
- **O2 — No thread map (or شامل otherwise unavailable) and the user asks for شامل** →
  the planner says so (the reason in plain words) and offers «بحث عميق»; nothing runs
  without the user's OK.
- **O3 — The unlinked «الملفات» file** → investigated separately (background agent), not
  part of this build.

## Defaults stated to the owner
- V4.1 = `_WAFER_V41_DEFAULT` (the router's / old extended planner's policy).
- Behind `ONE_PLANNER_ENABLED` / `ONE_PLANNER_USERS` (test account first). Off → today,
  byte-identical (Flash decider + separate extended planner).
- The one planner keeps everything the decider has: `ask_user`, hand-back, tools,
  long-message review, request_quotes, already_covered.
- One run ends in: a question (`ask_user`) | one search (`PlannerDecision`) | a شامل plan
  (`ExtendedSearchPlan`, ≥ 2 searches → plan card). D6 (one-search plan → discard + replan)
  is gone. D28 (angles only, no file text) no longer holds: the planner reads the file text
  AND the angles.
- Responder (unified responder) untouched.

## Choices made in the build (flag to owner)
- C1. The plan card counts as the long-message review: a 500+ char message may end in a
  card without an `ask_user` first (the card is itself a confirmation; nothing runs).
- C2. The user asked for شامل by name and the planner decides ONE search is enough → it
  does not run «بحث عميق» silently: `ask_user` saying why one search covers it, offering
  «بحث عميق» (same spirit as O2).
- C3. The card's «بحث عادي» → `_dispatch(extended_off=True)`: the one planner runs
  WITHOUT the شامل option (else it could re-plan a card).
- C4. Workspace room caps the number of searches (validator), so a plan the workspace
  cannot hold is sent back rather than dropped after it is made.

## Build
1. `shared/config.py` — `ONE_PLANNER_ENABLED`, `ONE_PLANNER_USERS`;
   `agents/utils/one_planner_flags.py` — `one_planner_enabled(user_id)`.
2. `agent_models.py` — slot `deep_planner` = `_WAFER_V41_DEFAULT`.
3. `extended/models.py` — `ExtendedSearchPlan(ExtendedPlan)` + `request_quotes`.
4. `extended/view.py` — `render_extended_block(view, mode, wi_of)` (budget + documents +
   angles); `render_planner_input` reuses it, byte-identical.
5. `extended/offer.py` (new) — `ExtendedOffer`, `prepare_offer(...)` (eligibility, room,
   view, encoded block), `offer_instructions(offer)` (available / unavailable + hint),
   `extended_plan_problems(...)`.
6. `extended/prompts.py` — the one planner's شامل section (available + unavailable).
7. `planner/deps.py` — `one_planner`, `extended` (offer).
8. `planner/agent.py` — `create_planner_decider(one_planner=, extended=)`: V4.1 slot,
   `ExtendedSearchPlan` output when the offer is available, offer instructions,
   validators (limits/ids/room, ≥ 2 searches, request_quotes, review skip).
9. `planner/runner.py` — `kind="extended"` (plan + raw result), billed as the planner.
10. `extended/wiring.py` — `record_plan_card(...)` extracted from `plan_extended_turn`;
    «بحث عادي» → `extended_off=True`.
11. `orchestrator.py` — `_dispatch(extended_off=)`; `_run_deep_search` prepares the offer
    and turns `kind="extended"` into the card; `_record_deferred(extended=)` keeps the
    offer inputs across a pause; the deep-search resume rebuilds the offer and turns a
    plan into a card under a new run id.
12. Tests.

## Built (2026-11-27, branch `feat/one-planner` off master 454e6c8, after 625c270)
Also: the planner ledger rows are priced at the slot that ran (`_emit_planner_ledger(slot=)`,
`_feed_resume_decider(slot=)`): the one planner bills at V4.1, the responder at its own slot
(two rows instead of today's combined one, flag on only). The offer falls back to the
conversation's attached files when the router attached none (the router's own شامل section
looks at the same set).

Tests: `extended/tests/test_one_planner.py` (42), `test_wafer_provider.py` (+deep_planner),
call-site guard (+offer.py, ids only).

## Live check (offline, real V4.1 on Wafer, test-account files, read-only)
| Case | Turn | Result |
|---|---|---|
| A — fa4fd43c, no map (the bug) | «ابحث» → «ابغى بحث شامل» | question → «لم تُستخرج خيوط هذا الملف، فلا يتاح له بحث شامل الآن — أبدأ لك بحثًا عميقًا…؟» (11.8 s + 1.3 s) |
| B — same judgment, mapped (a63761f7) | «ابحث» → «ابغى بحث شامل» | question → شامل plan, 4 searches (9.8 s + 34.6 s) |
| C — mapped, no budget (dev) | «ابغى بحث شامل في هذا الحكم» | شامل plan, 4 searches, 51.6 s (3 requests) |
| C — mapped, Pro 18 pts (mode 20) | same | شامل plan, 3 searches inside the budget, 35 s |
| D — mapped | narrow «هل يجوز الاعتراض … بعد ثلاثين يوماً» | one search (reg_compliance_led), 18.9 s |
Planner call ≈ 24k in / 1–3k out ≈ $0.004 at Wafer list price (today's Flash planner avg
$0.0023); a plan ≈ 34–113k in / 6–10k out ≈ $0.01–0.02 (replaces the separate extended
planner, $0.005, plus the Flash decider).

## Open
- Owner 2026-11-27: "commit and push, with LUNA_AI_plan_edit". Flag for the test account:
  `ONE_PLANNER_ENABLED=true`, `ONE_PLANNER_USERS=<test account>`.
- DONE: `fix/extended-planner-card-edit` landed as 625c270 (the separate planner's
  prompt); its rule is ported into `prompts.one_planner_available_section` (+ "a version
  left with ONE search is a PlannerDecision").
- The unlinked «الملفات» file (separate investigation) — owner to choose the fix.

## Rollback
`ONE_PLANNER_ENABLED=false` → today's two planners, byte-identical.
