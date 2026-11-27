"""«بحث شامل» — extended search (plan: .claude/plans/extended_search.md; build spec:
.claude/plans/extended_search_build.md).

When a deep-search turn carries attachments with a stored angle map and the user is
eligible, an angle-aware planner plans one or more searches inside a budget mode picked
by code; a multi-search plan pauses the turn on a plan card; the chosen option runs its
searches in parallel through the existing retrieval path; a merger turns their reports
into one answer.

Modules (owners in the build spec):
  models     — shared data shapes (this build's contract; keep field names stable)
  budget     — budget modes, mode pick, budget block, draft estimate
  eligibility— who gets the extended planner on a turn
  view       — the planner's view of the angle map + each document's marked whole text
  prompts    — the planner's system prompt
  planner    — the angle-aware planner agent (+ validator, masking, ledger)
  card       — plan → plan card payload + its text fallback
  brief      — each search's planner_brief (§4.9 b)
  runner     — N searches in parallel
  merger     — reports → one answer (§4.6, §4.9 c)
  publish    — workspace items (reports + merged with children) + chat summary
"""
