/**
 * Chat-depth run tracker — `.claude/plans/product_analytics.md` §3b, Phase 3.
 *
 * ONE module owns the answer to "is a run in flight right now, and how long
 * has the user been waiting for it". Every chat-depth `track()` call in the
 * app goes through this file, which buys three things the plan asks for:
 *
 * 1. **T13 — no timers.** Every `ms_*` prop is a difference of `Date.now()`
 *    stamps captured AT the events themselves. Background tabs throttle
 *    `setInterval`/`setTimeout` to ≥1s (often far worse) and that is precisely
 *    when these measurements are taken, so an elapsed counter would lie.
 * 2. **T9 — analytics must never break the chat.** `track()` is already
 *    fire-and-forget, but every call here is additionally wrapped: a throw
 *    from the analytics layer can never propagate into an SSE handler, a
 *    submit handler, or a render.
 * 3. **A single definition of `run_state`.** That field is what separates
 *    "left BEFORE the answer" from "left AFTER reading it" on the very same
 *    browser event, so it must not be re-derived differently in two places.
 *
 * State lives at module scope rather than in a store on purpose: mutating it
 * must never re-render the chat (a deep_search run emits thousands of
 * progress/token events — see T16).
 */

import { track } from "@/lib/analytics/client";
import type { RunState } from "@/lib/analytics/events";
import { useChatStore } from "@/stores/chat-store";
import type { WorkspaceItemKind } from "@/types";

// ---------------------------------------------------------------------------
// T9 — the only place that talks to the analytics client
// ---------------------------------------------------------------------------

type TrackFn = typeof track;

/**
 * `track()` is documented as never throwing. This wrapper makes that a
 * property of OUR call sites rather than a promise we rely on: if the
 * analytics client is unavailable, misconfigured, or throws for any reason,
 * chat behaviour is unchanged and the event is simply lost.
 */
const safeTrack: TrackFn = (...args) => {
  try {
    track(...args);
  } catch {
    // Never surface an analytics failure to the chat. T9.
  }
};

// ---------------------------------------------------------------------------
// Run state
// ---------------------------------------------------------------------------

interface ActiveRun {
  /**
   * Identity of this run, so a LATE terminal event from a superseded run can
   * never rewrite the state of the run that replaced it. A new send aborts the
   * previous stream, and that abort surfaces a tick later — by which time the
   * new run already owns the tracker.
   */
  token: number;
  conversationId: string | null;
  /** Assistant message id — unknown until `message_start` lands. */
  messageId: string | null;
  /** t₀: the moment the USER submitted. Never moved by a reconnect (T14). */
  sentAt: number;
  /** T16: `run_first_token` is emitted once, not once per token. */
  firstTokenSeen: boolean;
  /**
   * Latest deep_search stage from `agent_progress`. Recorded, never emitted —
   * its whole value is telling us which stage the user gave up at when
   * `tab_hidden` fires (T16).
   */
  stage: string | null;
  /**
   * Agent family, from `agent_run_started` — the moment the router's choice
   * first becomes known to the client. `null` until then, and legitimately
   * `null` forever for a send that was quota-blocked or died before it was
   * ever classified. Stamped on every run event because a general_qa run and
   * a five-minute deep_search run have completely different abandonment
   * profiles, and an aggregate that blends them is not actionable.
   */
  family: string | null;
  state: RunState;
  doneAt: number | null;
}

let activeRun: ActiveRun | null = null;
let runTokenSeq = 0;

/** Hard cap on every per-message map so a long session can't grow unbounded. */
const MAX_TRACKED_MESSAGES = 60;
const MAX_TRACKED_ITEMS = 60;

/** `done` timestamps by assistant message id — the base for `ms_since_done`. */
const doneAtByMessage = new Map<string, number>();
/** Messages that already fired `answer_seen`; it is once per message, ever. */
const answerSeenMessages = new Set<string>();
/** `workspace_item_created` timestamps — the base for `ms_since_created`. */
const wiCreatedAt = new Map<string, number>();
/** Conversations already announced, so a re-render can't double-count. */
const conversationOpenedAt = new Map<string, number>();

function remember<V>(map: Map<string, V>, key: string, value: V, cap: number): void {
  map.set(key, value);
  if (map.size > cap) {
    const oldest = map.keys().next();
    if (!oldest.done) map.delete(oldest.value);
  }
}

function isVisible(): boolean {
  if (typeof document === "undefined") return false;
  return document.visibilityState === "visible";
}

// ---------------------------------------------------------------------------
// Public reads — used by useRunVisibility to stamp every visibility event
// ---------------------------------------------------------------------------

/**
 * The field the whole feature turns on.
 *
 * `in_flight` wins over everything: a run whose SSE stream dropped is still
 * running server-side (use-chat detaches to a background poll rather than
 * re-POSTing), and the user is still waiting for it. A finished run stays
 * `completed` until the next send supersedes it, so leaving AFTER reading the
 * answer is distinguishable from leaving while waiting for it. `idle` means
 * this tab has no run worth talking about — nothing sent yet, or the last one
 * failed / was aborted.
 */
export function getRunState(): RunState {
  if (activeRun?.state === "in_flight") return "in_flight";
  try {
    const { isStreaming, isAgentRunning } = useChatStore.getState();
    if (isStreaming || isAgentRunning) return "in_flight";
  } catch {
    // Store unavailable — fall through to the tracker's own view.
  }
  return activeRun?.state ?? "idle";
}

/** ms since the user pressed send, or `null` when no run exists in this tab. */
export function getMsSinceSend(): number | null {
  return activeRun ? Date.now() - activeRun.sentAt : null;
}

/** Current deep_search stage, or `null` for families that emit no progress. */
export function getCurrentStage(): string | null {
  return activeRun?.stage ?? null;
}

/**
 * Agent family of the run in flight, or `null` when it has not been classified
 * yet. Stamped on every run and visibility event so wait tolerance, return rate
 * and abandonment can all be split by family: a general_qa answer lands in
 * seconds and a deep_search run takes minutes, so the blended distribution
 * describes nobody.
 */
export function getCurrentFamily(): string | null {
  return activeRun?.family ?? null;
}

/** True once `done` has been recorded for this assistant message in this tab. */
export function getDoneAt(messageId: string): number | null {
  return doneAtByMessage.get(messageId) ?? null;
}

// ---------------------------------------------------------------------------
// Run lifecycle
// ---------------------------------------------------------------------------

/**
 * `chat_send` — USER SUBMIT ONLY (T14).
 *
 * Called from `ChatInput` before the POST, so a send the quota gate refuses is
 * still measured. It must never be called from the SSE (re)connect path:
 * use-chat reconnects with exponential backoff, and moving t₀ to a reconnect
 * would make every dropped stream look like a fresh question and would measure
 * wait tolerance against the wrong clock.
 *
 * `message_id` and `family` are `null` here by construction — the assistant
 * message id is minted by the backend at `message_start` and the family is
 * chosen by the router, so neither exists at submit time. A blocked send never
 * acquires either, which is exactly why the event is emitted before the POST.
 * Join a `chat_send` to its run by (session, `conversation_id`, order).
 */
export function trackChatSend(params: {
  conversationId: string | null;
  hasAttachment: boolean;
}): void {
  runTokenSeq += 1;
  activeRun = {
    token: runTokenSeq,
    conversationId: params.conversationId,
    messageId: null,
    sentAt: Date.now(),
    firstTokenSeen: false,
    stage: null,
    family: null,
    state: "in_flight",
    doneAt: null,
  };
  // `message_id` and `family` are null HERE ON PURPOSE — please do not "fix"
  // them. The assistant message id is minted by the backend at
  // `message_start` and the family is chosen by the router (`agent_run_started`),
  // so neither exists at submit time, and a quota-blocked send never acquires
  // either. Inventing a value would be worse than the join that works:
  // (session_key, conversation_id, order). Family-split metrics read `family`
  // off the RUN events instead — see `run_first_token` / `run_done` / … below.
  safeTrack("chat_send", {
    conversation_id: params.conversationId,
    message_id: null,
    family: null,
    has_attachment: params.hasAttachment,
  });
}

/**
 * `message_start` — the backend confirmed the run and named the assistant
 * message. No event of its own; it binds the real message id to the run so
 * every later event can carry it.
 *
 * When a run reaches this point without a `chat_send` (regenerate / retry /
 * edit-and-resend, which submit from the message bubble rather than the
 * composer) t₀ is stamped here instead. That is a beat late but honest — and
 * far better than attributing the run to the previous question's clock. No
 * synthetic `chat_send` is emitted: T14 keeps that event for real submits.
 */
export function trackMessageStart(params: {
  conversationId: string;
  messageId: string;
}): number {
  if (
    activeRun &&
    activeRun.state === "in_flight" &&
    (activeRun.conversationId === null ||
      activeRun.conversationId === params.conversationId)
  ) {
    activeRun.conversationId = params.conversationId;
    activeRun.messageId = params.messageId;
    return activeRun.token;
  }
  runTokenSeq += 1;
  activeRun = {
    token: runTokenSeq,
    conversationId: params.conversationId,
    messageId: params.messageId,
    sentAt: Date.now(),
    firstTokenSeen: false,
    stage: null,
    family: null,
    state: "in_flight",
    doneAt: null,
  };
  return activeRun.token;
}

/** Identity of the run currently being tracked — see `noteRunAborted`. */
export function getRunToken(): number | null {
  return activeRun?.token ?? null;
}

/**
 * A send that never became (or stopped being) a live run: the quota gate
 * refused it, the POST returned 4xx, the transport died past its retry budget,
 * or the user pressed Stop.
 *
 * Emits NOTHING. Its only job is to stop `run_state` from reporting
 * `in_flight` for the rest of the tab's life, which would make every later
 * `tab_hidden` / `page_leave` read as abandoning a run nobody was waiting for.
 *
 * NOT called on the detach-to-background path: a stream that dropped after
 * `message_start` is still running server-side and the user is still waiting —
 * that case is exactly why the tracker, and not `isStreaming`, is the
 * authority on `in_flight`.
 *
 * Token-scoped: a new send aborts the previous stream and that abort lands a
 * tick later, by which time the new run already owns the tracker.
 */
export function noteRunAborted(token: number | null): void {
  if (token === null || !activeRun || activeRun.token !== token) return;
  if (activeRun.state === "in_flight") activeRun.state = "idle";
}

/**
 * `run_first_token` — emitted for the FIRST token of a run and no other
 * (T16: a long run emits thousands; one event per token would be a firehose
 * into the beacon, the table and the reader's battery).
 */
export function trackFirstToken(): void {
  if (!activeRun || activeRun.firstTokenSeen) return;
  activeRun.firstTokenSeen = true;
  safeTrack("run_first_token", {
    conversation_id: activeRun.conversationId,
    message_id: activeRun.messageId,
    ms_since_send: Date.now() - activeRun.sentAt,
    family: activeRun.family,
  });
}

/**
 * `agent_progress` — records the stage, emits NOTHING (T16). The stage matters
 * only as the answer to "which stage was on screen when they gave up", which
 * `tab_hidden` and `run_failed` read back off the run.
 */
export function noteRunStage(stage: string): void {
  if (activeRun) activeRun.stage = stage;
}

/**
 * `agent_run_started` (and `agent_resumed`, which is the same fact for a run
 * that was paused by `ask_user`) — records which family the router picked.
 * Emits NOTHING: it is a property of the run, stamped onto the events that
 * already exist rather than an event of its own.
 */
export function noteRunFamily(family: string): void {
  if (activeRun) activeRun.family = family;
}

/**
 * `run_done` — `was_visible` is read AT THIS MOMENT, not later: an answer that
 * landed in a backgrounded tab is the whole reason this metric exists.
 */
export function trackRunDone(messageId: string | null): void {
  // The pause path emits `done` immediately after `agent_question` — that is
  // the SSE stream closing, not an answer arriving. Treating it as completion
  // would erase the `paused` state the pause metric is built on, and would
  // arm `answer_seen` on a question bubble.
  if (activeRun?.state === "paused") return;
  const now = Date.now();
  const id = messageId ?? activeRun?.messageId ?? null;
  if (id) remember(doneAtByMessage, id, now, MAX_TRACKED_MESSAGES);
  if (!activeRun) return;
  if (id) activeRun.messageId = id;
  activeRun.state = "completed";
  activeRun.doneAt = now;
  safeTrack("run_done", {
    conversation_id: activeRun.conversationId,
    message_id: id,
    ms_since_send: now - activeRun.sentAt,
    was_visible: isVisible(),
    family: activeRun.family,
  });
}

/**
 * `run_failed` — the turn died. The run stops being `in_flight`: nobody is
 * waiting for an answer that is not coming, so a later `page_leave` must not
 * be counted as abandoning a live run.
 */
export function trackRunFailed(): void {
  if (!activeRun) return;
  const stage = activeRun.stage;
  const msSinceSend = Date.now() - activeRun.sentAt;
  activeRun.state = "idle";
  safeTrack("run_failed", {
    conversation_id: activeRun.conversationId,
    message_id: activeRun.messageId,
    ms_since_send: msSinceSend,
    stage,
    family: activeRun.family,
  });
}

/**
 * `run_paused` — the agent asked a clarifying question (`agent_question`) and
 * is waiting on the user. Its own state because abandonment here is a distinct
 * and expensive failure mode: the run burned its retrieval and then stalled.
 */
export function trackRunPaused(): void {
  if (!activeRun) return;
  activeRun.state = "paused";
  safeTrack("run_paused", {
    conversation_id: activeRun.conversationId,
    message_id: activeRun.messageId,
    ms_since_send: Date.now() - activeRun.sentAt,
    family: activeRun.family,
  });
}

// ---------------------------------------------------------------------------
// Visibility — emitted by useRunVisibility, which is their SINGLE owner
// ---------------------------------------------------------------------------

export function trackTabHidden(): void {
  safeTrack("tab_hidden", {
    run_state: getRunState(),
    ms_since_send: getMsSinceSend(),
    stage: getCurrentStage(),
    // Wait tolerance is the distribution of `ms_since_send` at the FIRST
    // tab_hidden(in_flight) — and it is only actionable split by family.
    family: getCurrentFamily(),
  });
}

export function trackTabVisible(msHidden: number | null): void {
  safeTrack("tab_visible", {
    ms_hidden: msHidden,
    run_state: getRunState(),
  });
}

export function trackPageLeave(): void {
  safeTrack("page_leave", {
    run_state: getRunState(),
    ms_since_send: getMsSinceSend(),
    family: getCurrentFamily(),
  });
}

// ---------------------------------------------------------------------------
// answer_seen — T15
// ---------------------------------------------------------------------------

/**
 * `answer_seen` — the metric this section exists to produce, so the guards are
 * deliberately strict (T15). The caller (`useAnswerSeen`) enforces ≥50%
 * intersection, tab visible, held ≥1s; this function enforces the rest:
 *
 * - only AFTER `done` for that message — a bubble that streamed into a
 *   backgrounded tab was never read, and counting it would invert the number;
 * - once per message, ever.
 */
export function trackAnswerSeen(messageId: string, conversationId: string): void {
  if (answerSeenMessages.has(messageId)) return;
  const doneAt = doneAtByMessage.get(messageId);
  if (doneAt === undefined) return;
  answerSeenMessages.add(messageId);
  if (answerSeenMessages.size > MAX_TRACKED_MESSAGES) {
    const oldest = answerSeenMessages.values().next();
    if (!oldest.done) answerSeenMessages.delete(oldest.value);
  }
  safeTrack("answer_seen", {
    conversation_id: conversationId,
    message_id: messageId,
    ms_since_done: Date.now() - doneAt,
  });
}

// ---------------------------------------------------------------------------
// Workspace items
// ---------------------------------------------------------------------------

interface OpenWorkspaceItem {
  wiId: string;
  kind: string;
  openedAt: number;
  /**
   * Set once the chat store is observed holding this item open. Only then may
   * the store watcher below close it — the cases-artifacts surface opens the
   * viewer in local component state and never touches the store, so without
   * this flag the very next unrelated store update would end its dwell.
   */
  viaStore: boolean;
}

let openWi: OpenWorkspaceItem | null = null;
let wiCloseWatcherInstalled = false;

/** `workspace_item_created` — the denominator of WI click-through. */
export function trackWiCreated(wiId: string, kind: WorkspaceItemKind): void {
  remember(wiCreatedAt, wiId, Date.now(), MAX_TRACKED_ITEMS);
  safeTrack("wi_created", { wi_id: wiId, kind });
}

/**
 * `wi_opened` — fired from `WorkspaceCard`'s click, the single funnel point
 * every user-initiated WI open passes through. Deliberately NOT fired by the
 * desktop auto-open on `workspace_item_created`: that is the app opening the
 * pane, not the user choosing to read the card, and counting it would put
 * click-through at ~100% on desktop.
 *
 * `createdAtIso` is the row's own `created_at`, so a card produced in an
 * earlier session still yields a real `ms_since_created`.
 */
export function trackWiOpened(
  wiId: string,
  kind: WorkspaceItemKind,
  createdAtIso?: string | null,
): void {
  // An open supersedes any previous one — close it so its dwell is not lost.
  if (openWi && openWi.wiId !== wiId) trackWiClosed(openWi.wiId);

  let createdAt = wiCreatedAt.get(wiId) ?? null;
  if (createdAt === null && createdAtIso) {
    const parsed = Date.parse(createdAtIso);
    if (!Number.isNaN(parsed)) createdAt = parsed;
  }
  const now = Date.now();
  openWi = { wiId, kind, openedAt: now, viaStore: false };
  installWiCloseWatcher();
  safeTrack("wi_opened", {
    wi_id: wiId,
    kind,
    ms_since_created: createdAt === null ? null : Math.max(0, now - createdAt),
  });
}

/**
 * `wi_dwell` — closing the viewer. Idempotent and id-matched, so the two close
 * signals (the dialog viewer's unmount and the chat pane's store watcher) can
 * both fire without double-counting.
 */
export function trackWiClosed(wiId?: string): void {
  if (!openWi) return;
  if (wiId && openWi.wiId !== wiId) return;
  const { wiId: id, openedAt } = openWi;
  openWi = null;
  safeTrack("wi_dwell", { wi_id: id, dwell_ms: Date.now() - openedAt });
}

/**
 * The chat workspace pane renders its viewers through `WorkspacePane`'s kind
 * router, not through `WorkspaceItemViewer`, so there is no single component
 * whose unmount means "the user closed the card". The store IS that signal:
 * `openItemId` clears on back-to-list, on pane close, and on conversation
 * switch. One lazily-installed subscription covers all three.
 */
function installWiCloseWatcher(): void {
  if (wiCloseWatcherInstalled || typeof window === "undefined") return;
  wiCloseWatcherInstalled = true;
  try {
    useChatStore.subscribe((state) => {
      const current = openWi;
      if (!current) return;
      const isOpenInStore = Object.values(state.workspaceByConversation).some(
        (pane) => pane?.openItemId === current.wiId,
      );
      if (isOpenInStore) {
        current.viaStore = true;
        return;
      }
      if (current.viaStore) trackWiClosed(current.wiId);
    });
  } catch {
    // A failed subscription costs us dwell data, never the pane. T9.
  }
}

/** Close whatever is open — used on `pagehide` so a dwell is not simply lost. */
export function flushOpenWorkspaceItem(): void {
  trackWiClosed();
}

// ---------------------------------------------------------------------------
// conversation_opened
// ---------------------------------------------------------------------------

/**
 * `conversation_opened` — the event that reframes the other four outcomes: a
 * user who closed the tab during a five-minute deep_search and read the answer
 * the next morning has not churned. Chat is authed, so that join works across
 * sessions on `user_id` without any persistent anonymous identifier.
 *
 * `has_unseen_answer` is answered from THIS tab only: a run that completed
 * here and was never confirmed seen. A conversation whose answer landed in a
 * previous session reports `false` — the client has no way to know, and
 * guessing would be worse than a known-conservative value.
 *
 * Deduped by id + a short window so a remount (or React's development double
 * effect) cannot inflate the count.
 */
const CONVERSATION_OPEN_DEDUP_MS = 3000;

export function trackConversationOpened(conversationId: string): void {
  const now = Date.now();
  const last = conversationOpenedAt.get(conversationId);
  if (last !== undefined && now - last < CONVERSATION_OPEN_DEDUP_MS) return;
  remember(conversationOpenedAt, conversationId, now, MAX_TRACKED_ITEMS);

  const hasUnseenAnswer =
    activeRun !== null &&
    activeRun.conversationId === conversationId &&
    activeRun.state === "completed" &&
    activeRun.messageId !== null &&
    !answerSeenMessages.has(activeRun.messageId);

  safeTrack("conversation_opened", {
    conversation_id: conversationId,
    has_unseen_answer: hasUnseenAnswer,
  });
}
