# Deep Search Live Progress Bar ("شريط التقدّم")

Status: BUILDING (design locked 2026-07-12 with user)

Live staged progress UI shown while `deep_search` runs (1–4 min), replacing the bare
`TypingIndicator`. Driven by **real pipeline events**, never a fake percentage.

## Locked decisions

| Decision | Choice |
|---|---|
| UI form | Step-tracker card + live evidence (detail line + counts under active step) |
| Backend depth | **Planner-level only** — wire the existing dead `emit_sse` hook + cheap phase-boundary events. Do NOT thread into reg/compliance/case executor loops (`loop.py`/`search.py` — ~50 status sites stay batched). |
| After completion | Collapse to **session-only** expandable chip (`▸ بحث معمّق · ٢٤ مصدرًا · ١:٥٠`). No DB persistence, no migration. |
| Scope | `deep_search` family only (`runningAgentFamily === 'deep_search'`). Other families keep TypingIndicator. |

## Stages (4, ordered)

| key | Arabic label | Fired when |
|---|---|---|
| `planning` | تخطيط البحث | deep_search dispatch begins (before phase 1 await) |
| `searching` | البحث في الأنظمة والأحكام | planner phase 1 decided → retrieval starts; refreshed per executor phase completion (reg/compliance/case) with counts |
| `aggregating` | التقييم والدمج | executors done → aggregator starts / `planner_retrieval_done` |
| `writing` | كتابة الإجابة | `planner_responded` → answer tokens imminent |

Terminal `done` progress event carries totals for the chip.

## Wire contract (single new SSE event)

```
event: agent_progress
data: {
  "stage": "planning" | "searching" | "aggregating" | "writing" | "done",
  "text": "<Arabic detail line>",            // optional
  "data": {                                   // optional, all keys optional
    "sources": 24,        // cumulative retrieved/kept results
    "queries": 6,         // sub-queries generated
    "sectors": ["..."],   // planner-picked sectors
    "phase": "reg" | "compliance" | "case",
    "confidence": 0.8,
    "elapsed_s": 110.4
  }
}
```

Existing `status` (free-text Arabic) stays as-is — still batch-flushed just before the
answer tokens; the frontend now consumes it to populate the chip's **expanded log**.

## Backend work (Python)

1. `agents/deep_search_v4/planner/deps.py` — `PlannerDeps.emit_sse` already exists (`:102`),
   `build_planner_deps(emit_sse=…)` already accepts it. **No change needed.**
2. `agents/orchestrator.py`
   - `_run_deep_search(...)` — accept `emit_sse: Callable[[dict], None] | None = None`,
     pass into `build_planner_deps` at `:1938` **and** the resume path (`:933`).
   - `_dispatch` deep_search branch (`:1655`) — replace the blocking `await _run_deep_search(...)`
     with an `asyncio.Queue` bridge: run it as a task, poll-drain the queue (`asyncio.wait_for(q.get(), 0.2)`),
     `yield` each progress event live, then `await` the task (re-raises on error) and drain the tail.
   - Translate planner lifecycle events (`planner_decided` / `planner_retrieval_done` /
     `planner_responded` / `planner_paused` / `planner_error`) → `agent_progress` events with
     the stage keys above. Emit `stage=planning` immediately at branch entry.
3. `agents/deep_search_v4/orchestrator.py` — add an `emit_sse` field to `FullLoopDeps`
   (populated from `PlannerDeps.emit_sse` in `run_retrieval` at `:1105`), and fire it at
   **4 places only**: end of `_run_reg_phase` (`:257`), `_run_compliance_phase` (`:443`),
   `_run_case_phase` (`:603`) with `{phase, sources}` counts, and before
   `handle_aggregator_turn` (`:860`) with `stage=aggregating`.
   Call `deps.emit_sse(ev)` **directly, guarded** — do NOT append these to `_events`.
4. `backend/app/services/message_service.py` — relay `agent_progress` in the
   `pipeline_producer` elif-chain (next to `status`, `:755`).

### Traps
- **Double-send**: `planner/logger.py emit()` appends to `_events` AND calls `emit_sse`.
  `_events` is batch-yielded at `orchestrator.py:1729`. Raw `planner_*` types are NOT in
  message_service's relay whitelist → harmless. But never push a translated `agent_progress`
  onto `_events`, or it double-sends.
- **Pause path**: on `planner_paused` / `agent_question`, the progress bar must be cleared
  (frontend) — the run is alive but the UI must stop showing "searching".
- **Cancel/disconnect**: the queue-drain loop must not swallow `asyncio.CancelledError`;
  cancel the deep_search task on generator close (`finally: ds_task.cancel()`).
- **Resume leg**: `_resume_deep_search` (`:933`) must pass `emit_sse` too, or a resumed run
  shows no progress.

## Frontend work (TypeScript)

1. `frontend/types/index.ts` (SSE block `:916-974`) — add `DeepSearchStage` union +
   `SSEAgentProgress` + `SSEStatus` interfaces.
2. `frontend/stores/chat-store.ts` — new slice:
   - `deepSearchProgress: { stage, text, sources, queries, startedAt, log: string[] } | null`
   - `deepSearchSummaries: Record<messageId, { sources, elapsedMs, log: string[] }>` (session-only, for the chip)
   - actions: `setDeepSearchProgress(ev)`, `appendDeepSearchLog(text)`, `sealDeepSearchSummary(messageId)`, clear in
     `finishStreaming` / `finishAgentRun` / `stopStreaming` / on `agent_question`.
3. `frontend/hooks/use-chat.ts` (switch `:371-622`) — add `case "agent_progress"` and
   `case "status"`. Seal the summary on `done`.
4. `frontend/components/chat/DeepSearchProgress.tsx` (new) — step tracker card.
   - Mount in the two TypingIndicator slots: `MessageList.tsx:411` and `:456`, gated on
     `runningAgentFamily === 'deep_search'`; fall back to `TypingIndicator` otherwise.
   - Reuse the canonical bar markup from `AttachmentUploadCard.tsx:150-162`
     (`h-1 rounded-full bg-muted` track + `bg-primary` fill). No shadcn progress/skeleton installed.
   - Luna v2 tokens only (`bg-card`, `border`, `text-muted-foreground`, `bg-primary`).
   - Elapsed timer (m:ss), `aria-live="polite"` announcing **stage transitions only**,
     `dir="rtl" lang="ar"`, RTL fill (right→left). Spinner (if any) stays clockwise — do NOT mirror.
   - Arabic strings hardcoded at top of file (AGENT_PHRASES convention in `TypingIndicator.tsx:16-30`).
5. `frontend/components/chat/DeepSearchSummaryChip.tsx` (new) — collapsed chip rendered above
   the assistant bubble when a summary exists for that message id; expands to the stage log.
   Reserve height / avoid layout shift on collapse.
   Cancel path already exists (composer Stop button, `ChatInput.tsx:643`).

## Verification
- Backend: unit test the queue bridge (progress events yielded BEFORE the answer tokens, not after)
  + a pause-path test (no orphan progress state). `pytest` for agents/.
- Frontend: `npx tsc --noEmit` + `npm run lint`.
- E2E: local dev, ask a deep-search question, confirm stages advance live and the chip seals.
