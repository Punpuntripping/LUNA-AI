// SSE event payloads for `POST /conversations/{id}/messages` — moved verbatim
// from frontend/types/index.ts (the web re-exports them from `@/types`), plus
// the event-name map at the bottom of this file (mobile Phase 2).

import type { AgentFamily, WorkspaceCreator, WorkspaceItemKind } from "./agents";
import type { NextStep, TemplateUsed } from "./messages";

// ==========================================
// SSE EVENTS
// ==========================================

export interface SSEMessageStart {
  user_message_id: string;
  assistant_message_id: string;
  conversation_id: string;
}

export interface SSEToken {
  text: string;
}

/**
 * Emitted INSTEAD of message_start when a send is rejected because a pipeline
 * is already running for this conversation (per-conversation in-flight dedup —
 * backend message_service `_active_runs`). No new pipeline is started and no
 * duplicate user message is saved. The client drops its optimistic duplicate
 * and lets the existing in-flight assistant message surface on completion.
 */
export interface SSEDuplicate {
  assistant_message_id: string;
  conversation_id: string;
  detail: string;
}

/**
 * Emitted INSTEAD of message_start when the per-user quota gate rejects the
 * send (shared/quota gate fires before OCR + router). The user message is
 * already persisted; no assistant placeholder is created and the stream ends
 * immediately. ``meter`` is which counter tripped ("plan" = account not
 * activated yet), ``period`` is the window ("session" = rolling 5h),
 * ``resets_at`` is ISO-8601 UTC (empty for plan-inactive). ``message_ar`` is
 * a pre-rendered Arabic string the banner can show verbatim. Point limits:
 * 1 USD = 100 points.
 */
export interface SSEQuotaExceeded {
  meter: "ocr" | "ord" | "web" | "plan";
  period: "session" | "daily" | "weekly" | "monthly" | "none";
  used: number;
  limit: number;
  resets_at: string;
  message_ar: string;
  /**
   * The plan the block was enforced against — EFFECTIVE, so an expired paid
   * subscription that fell back reports `"free"`. `null` = no plan assigned
   * (account not activated), which buying a plan does not fix. Optional so a
   * pre-deploy backend that omits the field degrades to the banner rather than
   * mis-triggering.
   *
   * Drives the AUTO-OPENING modal only, which stays free-only — WHAT to offer
   * comes from `upgrade_options`. A full-screen pitch at someone who already
   * paid reads differently than one at a free user.
   */
  plan_id?: string | null;
  /**
   * The plans that would actually unblock this send: purchasable, priced above
   * the user's plan, AND with a strictly higher limit on the window that
   * blocked them — ordered by price, cheapest first.
   *
   * Computed server-side (`shared/quota._upgrade_options`) because the enforced
   * limits live in the `plans` table and `lib/pricing.ts` carries them only as
   * Arabic prose; a client-side ladder would need a second copy of every number
   * to drift against.
   *
   * Empty = there is nothing to sell: `max` is already at the top of the ladder,
   * and an unactivated account (`plan_id: null`) is not fixed by a purchase.
   * Optional so a pre-deploy backend that omits the field degrades to
   * banner-only (a missed upsell, never a pitch that cannot help).
   */
  upgrade_options?: string[];
  /**
   * parallel_conversations plan §2: why the gate refused. `"parallel_reserve"`
   * = the user is NOT out of points — the balance just cannot cover another
   * run alongside the ones already in flight, so the client shows `detail`
   * without the "you've hit your limit" upgrade framing. Absent on older
   * backends (= `"limit"`).
   */
  reason?: "limit" | "parallel_reserve";
  /** Arabic explanation for `reason: "parallel_reserve"` (falls back to `message_ar`). */
  detail?: string;
}

/**
 * SSE `parallel_limit` — the per-plan cap on conversations answering at the
 * same time refused this send (parallel_conversations plan §2). Like
 * `quota_exceeded`, the server persisted nothing.
 */
export interface SSEParallelLimit {
  /** Arabic banner text. */
  detail: string;
  /** The plan's `max_parallel_runs`. */
  limit: number;
  /** Runs of this user already in flight server-side. */
  running: number;
  running_conversation_ids: string[];
  /** `"max"` when upgrading to «القصوى» would raise the cap; else `null`. */
  upgrade_plan: "max" | null;
}

export interface SSEDone {
  message_id: string;
  usage: {
    prompt_tokens: number;
    completion_tokens: number;
  };
  /**
   * Window B Tasks 5–7: workspace_items produced by the agent run that
   * authored this assistant message. Echoed on the live `done` event so the
   * chip + clickable [n] citations light up immediately, without waiting for
   * the next messages-list refetch. ``null`` when the turn produced no
   * artifact (mock RAG, Q&A, paused agent_question, etc).
   */
  artifact_ids?: string[] | null;
  /**
   * Window B Tasks 5–7: workspace_items the planner referenced instead of
   * publishing a new card (Phase E build_artifact=False branch). Drives the
   * "راجع البطاقة السابقة" chip.
   */
  referenced_item_ids?: string[] | null;
}

// ==========================================
// SSE EVENTS (Agent)
// ==========================================

export interface SSEAgentRunStarted {
  agent_family: AgentFamily;
  subtype?: string | null;
}

/**
 * deep_search_progress_bar plan — the four ordered stages of a deep_search
 * run, plus the terminal ``done`` marker that carries the run totals for the
 * collapsed summary chip.
 *
 * ``done`` is NOT a step: the tracker renders four steps
 * (planning → searching → aggregating → writing) and treats ``done`` as
 * "all four complete".
 */
export type DeepSearchStage =
  | "planning"
  | "searching"
  | "evaluating"
  | "aggregating"
  | "writing"
  | "done";

/**
 * Optional evidence payload on an ``agent_progress`` event. EVERY key is
 * optional — a phase-boundary event may carry counts while a lifecycle event
 * carries none, so consumers must treat a missing key as "unchanged", never
 * as zero.
 */
export interface SSEAgentProgressData {
  /** Cumulative retrieved/kept results so far. */
  sources?: number;
  /** Sub-queries the planner generated. */
  queries?: number;
  /** Planner-picked sectors. */
  sectors?: string[];
  /** Which executor phase just finished. */
  phase?: "reg_compliance" | "case";
  confidence?: number;
  elapsed_s?: number;
}

/**
 * ``event: agent_progress`` — the live staged-progress event emitted by the
 * deep_search branch of the orchestrator (planner-level only; the reg /
 * compliance / case executor loops stay batched). Drives
 * ``DeepSearchProgress``; the terminal ``stage: "done"`` event seals
 * ``chat-store.deepSearchSummaries[assistant_message_id]``.
 */
export interface SSEAgentProgress {
  stage: DeepSearchStage;
  /** Arabic detail line for the active stage. */
  text?: string | null;
  data?: SSEAgentProgressData | null;
}

/**
 * ``event: status`` — free-text Arabic progress line. The backend has emitted
 * these from ~50 call sites all along (batch-flushed just before the answer
 * tokens); the frontend now consumes them to populate the expanded log of the
 * deep_search summary chip. Ignored when no deep_search run is in flight.
 */
export interface SSEStatus {
  text: string;
}

export interface SSEAgentRunFinished {
  agent_family: AgentFamily;
}

/** Emitted when an agent pauses to ask the user a question (ask_user tool). */
export interface SSEAgentQuestion {
  type: 'agent_question';
  run_id: string;
  question: string;
  suggestions?: string[];
}

/** Emitted when a paused agent_run resumes after the user replied. */
export interface SSEAgentResumed {
  type: 'agent_resumed';
  run_id: string;
  agent_family: AgentFamily;
}

export interface SSEWorkspaceItemCreated {
  item_id: string;
  kind: WorkspaceItemKind;
  title: string;
  created_by: WorkspaceCreator;
  subtype?: string;
}

export interface SSEWorkspaceItemUpdated {
  item_id: string;
}

export interface SSEWorkspaceItemLocked {
  item_id: string;
  locked_until: string;
}

export interface SSEWorkspaceItemUnlocked {
  item_id: string;
}

/**
 * Phase E (full_redesign §3.4a / §6.3 / §9 O5):
 *
 * Emitted by the orchestrator when the planner's responder concludes that a
 * prior workspace_item already covers the current question and therefore
 * sets ``build_artifact=False`` + ``referenced_item_id=<id>``. No new card
 * is published; the frontend surfaces a chip on the in-flight assistant
 * bubble that the user can click to jump to the existing card.
 */
export interface SSEReferencedExistingItem {
  type: "referenced_existing_item";
  item_id: string;
}

/**
 * next_step_suggestions plan §3.6: ``event: next_steps`` — 0–3 clickable
 * follow-ups for the answer just streamed (actions one-per-kind, plus up to
 * two ``answer`` chips — unified_responder plan §6.3). Emitted once, after the last
 * ``token`` and before ``done``, and only when non-empty. The same items are
 * persisted on the assistant row as ``metadata.next_steps``.
 */
export interface SSENextSteps {
  items: NextStep[];
}

/**
 * ``event: templates_used`` — the templates a writing turn's draft was built
 * on, emitted before the chat text tokens. Empty list = drafted without a
 * template. Persisted on the assistant row as ``metadata.templates_used``.
 */
export interface SSETemplatesUsed {
  items: TemplateUsed[];
}

// ==========================================
// SSE EVENTS — not consumed by the web (mobile Phase 2)
// ==========================================

/**
 * ``event: error`` — the run (or the send) failed. ``detail`` is Arabic and is
 * shown verbatim. ``code`` is optional: ``"QUOTA_UNAVAILABLE"`` (quota
 * refusal) or ``"PIPELINE_TIMEOUT"`` (message_service.py) — never branch on it
 * as an exhaustive set.
 */
export interface SSEError {
  detail: string;
  code?: string;
}

/** ``event: heartbeat`` — keep-alive every ~15 s, empty payload. Ignore it. */
export type SSEHeartbeat = Record<string, never>;

/**
 * ``event: agent_selected`` — the router picked a family (message_service
 * forwards ``{agent_family}``). The web ignores it.
 */
export interface SSEAgentSelected {
  agent_family: AgentFamily;
}

/**
 * Every event name ``message_service.stream_message`` writes to the wire, mapped
 * to its ``data:`` payload. Verified against the ``_sse_event(...)`` call sites
 * (2026-10-15). ``ask_user`` has a forwarding branch there but no producer in
 * ``agents/`` emits it, so it is deliberately not listed.
 */
export interface SSEEventMap {
  message_start: SSEMessageStart;
  token: SSEToken;
  duplicate: SSEDuplicate;
  quota_exceeded: SSEQuotaExceeded;
  parallel_limit: SSEParallelLimit;
  done: SSEDone;
  error: SSEError;
  heartbeat: SSEHeartbeat;
  status: SSEStatus;
  agent_selected: SSEAgentSelected;
  agent_run_started: SSEAgentRunStarted;
  agent_progress: SSEAgentProgress;
  agent_run_finished: SSEAgentRunFinished;
  agent_question: SSEAgentQuestion;
  agent_resumed: SSEAgentResumed;
  workspace_item_created: SSEWorkspaceItemCreated;
  workspace_item_updated: SSEWorkspaceItemUpdated;
  workspace_item_locked: SSEWorkspaceItemLocked;
  workspace_item_unlocked: SSEWorkspaceItemUnlocked;
  referenced_existing_item: SSEReferencedExistingItem;
  next_steps: SSENextSteps;
  templates_used: SSETemplatesUsed;
}

export type SSEEventName = keyof SSEEventMap;

/** Runtime list of {@link SSEEventName} (22 names) — for name guards. */
export const SSE_EVENT_NAMES = [
  "message_start",
  "token",
  "duplicate",
  "quota_exceeded",
  "parallel_limit",
  "done",
  "error",
  "heartbeat",
  "status",
  "agent_selected",
  "agent_run_started",
  "agent_progress",
  "agent_run_finished",
  "agent_question",
  "agent_resumed",
  "workspace_item_created",
  "workspace_item_updated",
  "workspace_item_locked",
  "workspace_item_unlocked",
  "referenced_existing_item",
  "next_steps",
  "templates_used",
] as const satisfies readonly SSEEventName[];

/** Compile-time check: the runtime list covers every key of the map. */
type _MissingSSEName = Exclude<SSEEventName, (typeof SSE_EVENT_NAMES)[number]>;
const _allSSENamesListed: _MissingSSEName extends never ? true : never = true;
void _allSSENamesListed;

export function isSSEEventName(name: string): name is SSEEventName {
  return (SSE_EVENT_NAMES as readonly string[]).includes(name);
}
