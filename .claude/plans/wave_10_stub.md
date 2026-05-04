# Wave 10 — Stub (Carried-Over Items from Wave 9)

> **Status:** stub / inventory only. Not a design document. Records what Wave 9 explicitly defers.
> **Source:** `wave_9_agent_runs.md` Non-Goals, in-task "Wave 10+" markers, and Task 13 split fallback.

---

## Carried-over items

| # | Item | Where Wave 9 defers it | Why deferred |
|---|------|------------------------|--------------|
| 1 | Real LLM behind the memory agent (`summarize_workspace_item`, `resummarize_dirty_items`, `compact_conversation`) | Task 6: "Both are mock implementations for Wave 9 (return deterministic Arabic text); Wave 10+ swaps in real LLM calls. Same shape both ways." | Wave 9 ships the contract + plumbing; the LLM swap is a separate concern. |
| 2 | Real LLM swap for any other agent still mocked | Non-Goals: "No real LLM swap (Wave 10+)." | Wave 9 is plumbing + memory only. |
| 3 | Retry / replay UI for failed runs | Non-Goals: "No retry/replay UI (data lands in `agent_runs`; UX comes later)." | `agent_runs` row is the data; surfacing it is a frontend concern. |
| 4 | Semantic dirtiness for per-item summaries | Non-Goals: "No semantic dirtiness for summaries — length drift is enough for v1." | 25% length-drift is the v1 trigger. |
| 5 | Background queue for summarization | Non-Goals: "No background queue for summarization — runs lazily in pre-router hook." | Wave 9 pays first-turn latency on dirty items; queue eliminates it. |
| 6 | `ask_user` channel for the writer | Task 13.7: "Writer keeps `ask_user=None` for v1." | Wave 9 wires the channel only for the deep_search v4 planner. |
| 7 | Task 13 itself, if split off as 9B | Execution Order Phase 7: "Can ship in same wave or split off as 9B if Pydantic AI `message_history` serialization needs more bake time." | Carried only if Wave 9 doesn't ship pause/resume in-band. |
| 8 | Expired-pending-run garbage collection | Task 13.4: rows are marked `status='timeout'` on detection in the pre-route check, but no scheduled cleanup of stale `awaiting_user` rows is specified. | Out of scope for Wave 9; needed before the table grows. |
| 9 | Soft cap behaviors (auto-archive / merge / user-prompted delete) | Task 3 enforces a hard reject with Arabic error; the alternate behaviors discussed in design were not selected. | v1 is hard reject only. |
| 10 | **Tier-2 main memory agent** (user-facing, dispatched by router) | Wave 9 ships only Tier-4 memory operations (`summarize_workspace_item`, `resummarize_dirty_items`, `compact_conversation`) — system-side, condition-triggered, no user comms. The Tier-2 main memory agent that the router can dispatch (e.g. "remember that the client prefers formal tone", "what do you remember about this case?", "forget the bit about X") is not in scope for Wave 9. | Wave 9 only wires the system-side memory family; the user-facing dispatch path is reserved for Wave 10. Re-uses the same agent_runs telemetry and the same DispatchAgent envelope as deep_search/writing — no new infrastructure, just a new agent_family value. |

---

## Not in this list

- Frontend "view full compacted history" affordance — possible Wave 10 item, but never explicitly named in Wave 9 plan. Listed here only as an external observation, not a Wave 9 carry-over.
- Cost dashboards on top of `agent_runs` — same: implied by the schema, not deferred by name in Wave 9.

These are noted in the master plan's stale "Future Waves" table, not in `wave_9_agent_runs.md`. Update the master_plan separately if you want them tracked here.
