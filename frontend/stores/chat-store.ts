import { create } from "zustand";
import type {
  DeepSearchStage,
  LibraryItemRef,
  NextStep,
  NextStepKind,
  PendingBlog,
  PendingFile,
  PendingLibraryItem,
  PendingTemplate,
  SSEAgentProgress,
  SSEParallelLimit,
  SSEQuotaExceeded,
} from "@/types";

const DEFAULT_SPLIT_RATIO = 50;
const SPLIT_RATIO_KEY = "luna.workspace.splitRatio";

function loadInitialSplitRatio(): number {
  if (typeof window === "undefined") return DEFAULT_SPLIT_RATIO;
  const raw = window.localStorage.getItem(SPLIT_RATIO_KEY);
  if (!raw) return DEFAULT_SPLIT_RATIO;
  const parsed = Number(raw);
  if (!Number.isFinite(parsed) || parsed < 0 || parsed > 100) {
    return DEFAULT_SPLIT_RATIO;
  }
  return parsed;
}

function persistSplitRatio(ratio: number): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(SPLIT_RATIO_KEY, String(ratio));
  } catch {
    // localStorage can throw (private mode, quota) — ignore.
  }
}

// ---------------------------------------------------------------------------
// Streaming reveal buffers — module-level on purpose.
//
// Raw SSE tokens land here instead of directly in state; ``revealFrame``
// publishes to ``streams[cid].content`` at most once per animation frame, at a
// velocity proportional to the backlog. That coalesces a burst of token
// events into one React render AND smooths the network's stop-and-go rhythm
// into a steady typewriter reveal. Mutating these must never re-render, which
// is why they are not store state.
//
// parallel_conversations plan §3: one buffer PER CONVERSATION (several
// conversations can stream at once), drained by a single shared rAF loop that
// publishes every conversation's piece in one store write.
// ---------------------------------------------------------------------------

const tokenBuffers = new Map<string, string>();
let revealRafId: number | null = null;

/** Floor so a near-empty backlog still visibly advances every frame. */
const REVEAL_MIN_CHARS = 3;
/**
 * Fraction of the backlog revealed per frame. The backlog settles where
 * production = reveal rate (≈ divisor × chars-per-frame), so display lags the
 * network by only ~100ms at typical token rates while bursts get absorbed.
 */
const REVEAL_BACKLOG_DIVISOR = 6;

function stopRevealLoop(): void {
  if (typeof window !== "undefined" && revealRafId !== null) {
    window.cancelAnimationFrame(revealRafId);
  }
  revealRafId = null;
}

/** Drop ONE conversation's unrevealed backlog; stop the loop if nothing is left. */
function cancelReveal(conversationId: string): void {
  tokenBuffers.delete(conversationId);
  if (tokenBuffers.size === 0) stopRevealLoop();
}

/** Drop every conversation's backlog (user switch). */
function cancelAllReveals(): void {
  tokenBuffers.clear();
  stopRevealLoop();
}

function scheduleReveal(): void {
  if (revealRafId === null && typeof window !== "undefined") {
    revealRafId = window.requestAnimationFrame(revealFrame);
  }
}

function revealFrame(): void {
  revealRafId = null;
  if (tokenBuffers.size === 0) return;
  const streams = useChatStore.getState().streams;
  const pieces: Record<string, string> = {};
  let any = false;
  tokenBuffers.forEach((buf, cid) => {
    // The buffer only feeds a LIVE stream of its own conversation; a stream
    // that ended (or was superseded) since the token arrived drops its tail.
    if (!streams[cid]?.isStreaming || buf.length === 0) {
      tokenBuffers.delete(cid);
      return;
    }
    let n = Math.min(
      buf.length,
      Math.max(REVEAL_MIN_CHARS, Math.ceil(buf.length / REVEAL_BACKLOG_DIVISOR)),
    );
    // Never split a surrogate pair (emoji etc.) across frames.
    const cut = buf.charCodeAt(n - 1);
    if (n < buf.length && cut >= 0xd800 && cut <= 0xdbff) n += 1;
    pieces[cid] = buf.slice(0, n);
    const rest = buf.slice(n);
    if (rest.length > 0) tokenBuffers.set(cid, rest);
    else tokenBuffers.delete(cid);
    any = true;
  });
  if (any) {
    useChatStore.setState((state) => {
      const next = { ...state.streams };
      for (const [cid, piece] of Object.entries(pieces)) {
        const cur = next[cid];
        if (cur) next[cid] = { ...cur, content: cur.content + piece };
      }
      return { streams: next };
    });
  }
  if (tokenBuffers.size > 0) scheduleReveal();
}

// ---------------------------------------------------------------------------
// deep_search live progress (deep_search_progress_bar plan)
// ---------------------------------------------------------------------------

/**
 * Live state of the in-flight deep_search run, fed by the ``agent_progress``
 * SSE event (stage/detail/counts) and the pre-existing ``status`` event (free
 * Arabic lines → ``log``). Rendered by ``DeepSearchProgress``; ``null``
 * whenever no deep_search run is in flight.
 */
export interface DeepSearchProgressState {
  stage: DeepSearchStage;
  /** Latest Arabic detail line for the ACTIVE stage (cleared on stage change). */
  text: string | null;
  /** Cumulative counts — monotonic, never reset by an event that omits them. */
  sources: number;
  queries: number;
  /**
   * Count of streamed sub-query TOPIC lines (``"بحث في …"``) seen so far this
   * run. Bumped by ``appendDeepSearchLog`` as topics stream in during the
   * ``searching`` stage, so the tracker can show a live query counter
   * («الاستعلام 3») before the authoritative phase-end ``queries`` count lands.
   */
  topicsSeen: number;
  /** Client clock at the first progress event — drives the elapsed timer. */
  startedAt: number;
  /** Stage detail lines + ``status`` lines, in arrival order (capped). */
  log: string[];
}

/**
 * Sealed totals of a FINISHED deep_search run, keyed by assistant message id
 * in ``deepSearchSummaries``. Session-only: never persisted, never sent to the
 * backend — a reload drops it and the assistant bubble simply renders without
 * its chip.
 */
export interface DeepSearchSummary {
  sources: number;
  elapsedMs: number;
  log: string[];
}

/** Hard cap on the log so a chatty run can't grow the store without bound. */
const MAX_DEEP_SEARCH_LOG = 200;

/**
 * Prefix the backend stamps on every live sub-query ``status`` line during the
 * searching stage (``"بحث في الأنظمة واللوائح: …"`` / ``"بحث في السوابق
 * القضائية: …"``). Exported so ``DeepSearchProgress`` can detect and strip it
 * with the exact same string the store keys off. Trailing space is
 * intentional — it must not match a bare ``"بحث في"`` with no topic.
 */
export const DEEP_SEARCH_TOPIC_PREFIX = "بحث في ";

/**
 * Pipeline order of the deep_search stages, used to keep the tracker's stage
 * MONOTONIC. The two executors (reg_compliance/case) rerank in parallel, so
 * their events interleave: a slow phase can emit `searching` after a fast one already
 * reached `evaluating`. Rank comparison lets the late event's counts merge while
 * its stale stage is ignored.
 */
export const DEEP_SEARCH_STAGE_ORDER: Record<DeepSearchStage, number> = {
  planning: 0,
  searching: 1,
  evaluating: 2,
  aggregating: 3,
  writing: 4,
  done: 5,
};

interface WorkspaceUiState {
  isOpen: boolean;
  openItemId: string | null;
  /**
   * When set, ``ReferencePanel`` scrolls reference ``n`` into view and
   * briefly flashes it. Cleared by ``clearFocusedReference`` on
   * animation-end so re-clicking the same marker fires the animation again.
   */
  focusedReferenceN: number | null;
  /**
   * Phase E (full_redesign §9 O5): when set, the ``WorkspaceList`` scrolls
   * the matching ``<div id="workspace-item-{id}">`` into view and the
   * ``WorkspaceCard`` for that id renders a ring highlight for ~2s. Set by
   * ``highlightWorkspaceItem`` (chip click on the assistant bubble) and
   * cleared via a setTimeout in the same action.
   */
  highlightedItemId: string | null;
}

/**
 * Live state of ONE conversation's in-flight send (parallel_conversations plan
 * §3). An entry exists in ``streams`` from the moment ``beginSend`` claims the
 * conversation until the send reaches a terminal state, so its mere presence
 * means "this conversation has a run in flight in this tab".
 */
export interface StreamState {
  /**
   * Identity of the send that owns this entry. A re-send in the SAME
   * conversation (regenerate / edit / retry) replaces the entry with a new
   * ``sendId``; the superseded send checks it and stops touching state.
   */
  sendId: number;
  /** True once ``message_start`` confirmed the run (the live bubble renders). */
  isStreaming: boolean;
  /** Assistant message id — known from ``message_start``. */
  messageId: string | null;
  /** Revealed assistant text so far (fed by the paced reveal buffer). */
  content: string;
  abortController: AbortController | null;
  reconnectAttempts: number;
  isReconnecting: boolean;
  /**
   * Live progress of this conversation's deep_search run (``null`` otherwise).
   * The ONLY subscriber is ``DeepSearchProgress`` — keep it that way, or every
   * progress event re-renders the message list and regresses the fluid-
   * streaming render isolation.
   */
  deepSearchProgress: DeepSearchProgressState | null;
  /**
   * Carry slot between ``finishAgentRun`` and the SSE ``done`` handler.
   *
   * ``agent_run_finished`` arrives BEFORE ``done``, and the tracker must
   * disappear the moment the run ends — but ``done`` is where the assistant
   * message id is final and the summary gets sealed. So ``finishAgentRun``
   * parks the run here instead of dropping it. Dropped with the entry by
   * ``finishStreaming`` / ``stopStreaming``, which is also what kills the chip
   * on the pause path (``agent_question`` calls ``finishAgentRun`` then
   * ``finishStreaming`` → nothing left to seal).
   */
  deepSearchSealable: DeepSearchProgressState | null;
  isAgentRunning: boolean;
  runningAgentFamily: string | null;
  runningAgentSubtype: string | null;
}

/**
 * Per-conversation refusal banner (``QuotaBanner``): either the quota gate
 * (``quota_exceeded``) or the per-plan parallel-runs cap (``parallel_limit``).
 */
export type ChatNotice =
  | { kind: "quota"; info: SSEQuotaExceeded }
  | { kind: "parallel_limit"; info: SSEParallelLimit };

interface ChatState {
  /**
   * In-flight sends keyed by conversation id. EVERY read of live stream state
   * must go through the reader's own conversation id — that keying is what
   * keeps one conversation's tokens out of another.
   */
  streams: Record<string, StreamState>;
  pendingFiles: PendingFile[];
  // New-chat handoff: files picked before a conversation exists are stashed
  // here (raw File objects, not persisted) and the optional composer draft text
  // is carried in ``pendingComposerDraft`` — both consumed by the destination
  // ChatInput after the create-conversation navigation so attachments work
  // before the first message.
  pendingAttachFiles: File[];
  pendingComposerDraft: string | null;
  // Live composer injection (onboarding starter questions): unlike
  // ``pendingComposerDraft`` (consumed once on ChatInput mount), this slot is
  // observed by an effect on the already-mounted ChatInput, which copies the
  // text into the textarea and clears the slot. ``nonce`` bumps on every
  // injection so picking the same question twice still re-triggers.
  // ``conversationId`` (optional) targets ONE conversation's composer: a
  // refused send hands its text back to its own conversation even if the user
  // has since switched to another (the slot waits until that composer mounts).
  composerInjection: {
    text: string;
    nonce: number;
    conversationId?: string;
  } | null;
  /**
   * next_step_suggestions plan §3.8: the next-step chip whose prompt was last
   * pasted into the composer, so the NEXT send can report `next_step_sent
   * {kind, edited}` (edited = the sent text differs from the pasted text).
   * Set by ``pasteNextStep``, consumed (and cleared) by ChatInput's send;
   * cleared on conversation switch.
   */
  pastedNextStep: { kind: NextStepKind; text: string } | null;
  pendingMessage: string | null;
  // Blog share-links pasted into the composer, shown as chips next to file
  // attachments (blog_import plan §D4). ``pendingBlogs`` are the live chips;
  // ``pendingBlogTokens`` is the new-chat carry slot (tokens pasted before a
  // conversation exists — the ``pendingAttachFiles`` twin), consumed by the
  // destination ChatInput after the create-conversation navigation.
  pendingBlogs: PendingBlog[];
  pendingBlogTokens: string[];
  // Library pages carried in from «تحدّث مع ريحان عن هذه الصفحة»
  // (.claude/plans/simple_search_family.md §8) — the blog pair, one level up:
  // ``pendingLibraryItems`` are the live chips, ``pendingLibraryRefs`` is the
  // new-chat carry slot (pages picked before a conversation exists, the
  // ``pendingBlogTokens`` twin), drained by the destination ChatInput after the
  // create-then-navigate hop.
  pendingLibraryItems: PendingLibraryItem[];
  pendingLibraryRefs: LibraryItemRef[];
  // قالب chip picked from the composer's «+» menu. Single-slot — the planner
  // drafts from ONE template, so picking another replaces it. Cleared on
  // conversation switch (same discipline as files/blogs); ``pendingTemplateCarry``
  // is the new-chat carry slot (the pendingBlogTokens twin) so a chip attached
  // on the empty page survives the create-on-attach navigation.
  pendingTemplate: PendingTemplate | null;
  pendingTemplateCarry: PendingTemplate | null;
  /** Stream error banner text, keyed by conversation id. */
  errorByConversation: Record<string, string>;
  // Per-conversation workspace pane state, keyed by conversation_id, so the
  // pane follows conversation navigation instead of leaking across them.
  workspaceByConversation: Record<string, WorkspaceUiState>;
  /**
   * Phase E (full_redesign §9 O5): item ids the planner flagged as
   * "already covers this question" for a given assistant message. Keyed by
   * ``assistant_message_id``. The MessageBubble for that message renders a
   * chip per id; clicking the chip invokes ``highlightWorkspaceItem`` and
   * opens the workspace pane to that item. Survives the messages-cache
   * invalidate that happens at stream completion (the cache is keyed by
   * conversation, this map is keyed by message_id and lives on the store).
   */
  referencedItemsByMessage: Record<string, string[]>;
  /**
   * writer_planner_user_templates plan, Wave E (D6): the "save attachment as
   * template" offer the writer pipeline emitted at the end of a writing turn,
   * keyed by ``assistant_message_id``. The MessageBubble for that message
   * renders an inline «احفظ المرفق كقالب؟ [نعم]» chip; clicking it ingests the
   * attached item via ``/templates/ingest``. Mirrors
   * ``referencedItemsByMessage``: keyed by message_id and living on the store
   * so it survives the messages-cache invalidate at stream completion.
   *
   * Ephemeral (v1): live session only — not persisted to the message row, so
   * a page reload drops the offer. The save itself is durable once clicked.
   */
  templateOffersByMessage: Record<
    string,
    { itemId: string; titleHint: string }
  >;
  /**
   * next_step_suggestions plan §3.7: next-step chips received live via the
   * ``next_steps`` SSE event, keyed by ``assistant_message_id``. The ``done``
   * handler also writes them into the cached message's ``metadata``; this map
   * is the fallback that survives a post-stream refetch landing before the
   * server row carries ``metadata.next_steps``. After a reload the persisted
   * metadata is the only source.
   */
  nextStepsByMessage: Record<string, NextStep[]>;
  // Global layout preference (persisted to localStorage) — NOT per-conversation.
  splitRatio: number;
  /** Per-send SSE reconnect budget (each send counts its own attempts). */
  maxReconnectAttempts: number;
  /**
   * Refusal banner per conversation — set when the backend rejects a send via
   * the quota gate (``quota_exceeded``) or the parallel-runs cap
   * (``parallel_limit``). ``QuotaBanner`` renders it for its own conversation.
   * Cleared by the banner's dismiss button OR by the next confirmed send in
   * that conversation (``startStreaming`` clears it).
   */
  noticeByConversation: Record<string, ChatNotice>;
  /**
   * Sealed deep_search summaries keyed by assistant ``message_id``. Drives
   * ``DeepSearchSummaryChip`` above the assistant bubble. Session-only — no
   * persistence, no DB column; ``reset()`` (user switch) clears it.
   */
  deepSearchSummaries: Record<string, DeepSearchSummary>;

  /**
   * Claim ``conversationId`` for a new send and return its ``sendId``. A send
   * still in flight in the SAME conversation is aborted and replaced (the
   * regenerate / edit / retry paths); other conversations are untouched.
   */
  beginSend: (conversationId: string) => number;
  /**
   * Drop the conversation's entry iff ``sendId`` still owns it. Every send
   * calls this on its way out, so no terminal path can leave a stale entry
   * counting toward the parallel cap.
   */
  endSend: (conversationId: string, sendId: number) => void;
  startStreaming: (conversationId: string, messageId: string) => void;
  appendToken: (conversationId: string, text: string) => void;
  /**
   * Synchronously publish any text still waiting in the conversation's
   * paced-reveal buffer. MUST be called before reading ``content`` as the
   * final answer (the SSE ``done`` handler) — otherwise the buffered tail is
   * lost.
   */
  flushStreamBuffer: (conversationId: string) => void;
  /** Abort + drop the conversation's stream (composer Stop button). */
  stopStreaming: (conversationId: string) => void;
  /** Drop the conversation's stream WITHOUT aborting (natural completion). */
  finishStreaming: (conversationId: string) => void;
  setError: (conversationId: string, error: string | null) => void;
  addPendingFile: (file: PendingFile) => void;
  removePendingFile: (id: string) => void;
  clearPendingFiles: () => void;
  /**
   * Patch a pending file in place — used by the resumable-upload hook to
   * report progress, status flips (queued → uploading → completed), the
   * `itemId` once /init returns, and the Arabic `errorMessage` on failure.
   * No-op when the file id is no longer in the list (race vs. user removal).
   */
  updatePendingFile: (id: string, partial: Partial<PendingFile>) => void;
  setAbortController: (
    conversationId: string,
    controller: AbortController | null,
  ) => void;
  setPendingMessage: (message: string | null) => void;
  clearPendingMessage: () => void;
  setPendingAttachFiles: (files: File[]) => void;
  clearPendingAttachFiles: () => void;
  setPendingComposerDraft: (text: string | null) => void;
  /** Put ``text`` into the live composer textarea (does NOT send). */
  injectComposerText: (text: string, conversationId?: string) => void;
  clearComposerInjection: () => void;
  /**
   * Next-step chip click (D4): paste ``step.prompt`` into the live composer —
   * REPLACING its text, never sending — and remember the chip for the
   * ``next_step_sent`` analytics event.
   */
  pasteNextStep: (step: NextStep) => void;
  /** Return the remembered pasted chip (if any) and clear it. */
  consumePastedNextStep: () => { kind: NextStepKind; text: string } | null;
  clearPastedNextStep: () => void;
  addPendingBlog: (blog: PendingBlog) => void;
  removePendingBlog: (id: string) => void;
  clearPendingBlogs: () => void;
  /** Patch a blog chip in place; no-op when the id is gone (user removal race). */
  updatePendingBlog: (id: string, partial: Partial<PendingBlog>) => void;
  setPendingBlogTokens: (tokens: string[]) => void;
  clearPendingBlogTokens: () => void;
  addPendingLibraryItem: (item: PendingLibraryItem) => void;
  removePendingLibraryItem: (id: string) => void;
  clearPendingLibraryItems: () => void;
  /** Patch a library chip in place; no-op when the id is gone (removal race). */
  updatePendingLibraryItem: (
    id: string,
    partial: Partial<PendingLibraryItem>,
  ) => void;
  setPendingLibraryRefs: (refs: LibraryItemRef[]) => void;
  clearPendingLibraryRefs: () => void;
  setPendingTemplate: (template: PendingTemplate | null) => void;
  setPendingTemplateCarry: (template: PendingTemplate | null) => void;
  openWorkspaceItem: (conversationId: string, itemId: string) => void;
  /**
   * Open ``itemId`` in the pane AND mark reference ``n`` as focused so the
   * panel scroll-into-views + flashes it. Used by citation marker clicks.
   */
  openWorkspaceItemAtReference: (
    conversationId: string,
    itemId: string,
    n: number,
  ) => void;
  /** Clear the focused reference flag (called on animation-end). */
  clearFocusedReference: (conversationId: string) => void;
  /**
   * Phase E (§9 O5): record that the planner referenced ``itemId`` for the
   * assistant message ``messageId``. Idempotent — repeat calls add to the
   * list without duplicates. Called by the ``referenced_existing_item`` SSE
   * handler.
   */
  recordReferencedItem: (messageId: string, itemId: string) => void;
  /**
   * Wave E (writer_planner_user_templates): record the "save attachment as
   * template" offer for the assistant message ``messageId``. Idempotent —
   * a repeat call for the same message overwrites with the latest payload.
   * Called by the ``template_save_offer`` SSE handler.
   */
  recordTemplateOffer: (
    messageId: string,
    itemId: string,
    titleHint: string,
  ) => void;
  /** Record the ``next_steps`` SSE items for assistant message ``messageId``. */
  recordNextSteps: (messageId: string, items: NextStep[]) => void;
  /**
   * Phase E (§9 O5): open the workspace pane to ``itemId`` AND briefly
   * highlight the matching ``WorkspaceCard`` so the user sees which prior
   * card the planner referred to. The highlight clears itself after ~2.5s
   * via setTimeout in the action. Called when the user clicks the chip on
   * an assistant bubble.
   */
  highlightWorkspaceItem: (conversationId: string, itemId: string) => void;
  /**
   * Phase E (§9 O5): clear the highlighted item id for ``conversationId``.
   * Used internally by ``highlightWorkspaceItem``'s setTimeout; exposed so
   * unit tests can clear it manually.
   */
  clearHighlightedItem: (conversationId: string) => void;
  closeWorkspaceItem: (conversationId: string) => void;
  closeWorkspace: (conversationId: string) => void;
  toggleWorkspace: (conversationId: string) => void;
  setSplitRatio: (ratio: number) => void;
  startAgentRun: (
    conversationId: string,
    agentFamily: string,
    subtype?: string | null,
  ) => void;
  finishAgentRun: (conversationId: string) => void;
  /** Mirror a reconnect attempt (``attempts`` = this send's count so far). */
  startReconnect: (conversationId: string, attempts: number) => void;
  resetReconnect: (conversationId: string) => void;
  setNotice: (conversationId: string, notice: ChatNotice | null) => void;
  /**
   * Fold an ``agent_progress`` SSE event into the live slice. Creates the
   * slice (stamping ``startedAt``) on the first event of a run. Counts are
   * merged monotonically — an event that omits ``sources``/``queries`` leaves
   * them untouched rather than zeroing them.
   */
  setDeepSearchProgress: (conversationId: string, event: SSEAgentProgress) => void;
  /**
   * Append a free-text ``status`` line to the live log. No-op when no
   * deep_search run is in flight (status events fire for every family) and on
   * an exact repeat of the previous line.
   *
   * A topic line (``"بحث في …"``) — one streamed per sub-query during the
   * ``searching`` stage — additionally drives the ACTIVE detail line (``text``)
   * and bumps ``topicsSeen``, so the user watches the sub-queries scroll by in
   * real time instead of seeing them only in the terminal batch.
   */
  appendDeepSearchLog: (conversationId: string, text: string) => void;
  /**
   * Freeze the conversation's current (or just-finished) run into
   * ``deepSearchSummaries[messageId]``. No-op when there is nothing to seal —
   * which is exactly what makes the pause path chip-free.
   */
  sealDeepSearchSummary: (conversationId: string, messageId: string) => void;
  reset: () => void;
}

// Pane state for a conversation with nothing stored yet — used as the base
// when an action mutates a conversation absent from the map.
const DEFAULT_WORKSPACE: WorkspaceUiState = {
  isOpen: false,
  openItemId: null,
  focusedReferenceN: null,
  highlightedItemId: null,
};

// Duration of the WorkspaceCard ring highlight triggered by the Phase E chip.
// 2.5s gives the user time to notice the card without overstaying — same
// rough budget as the existing ref-flash animation.
const HIGHLIGHT_ITEM_MS = 2500;

/** Monotonic ``sendId`` source — module-level, never reset (ids stay unique). */
let sendIdSeq = 0;

function newStreamState(sendId: number): StreamState {
  return {
    sendId,
    isStreaming: false,
    messageId: null,
    content: "",
    abortController: null,
    reconnectAttempts: 0,
    isReconnecting: false,
    deepSearchProgress: null,
    deepSearchSealable: null,
    isAgentRunning: false,
    runningAgentFamily: null,
    runningAgentSubtype: null,
  };
}

/**
 * Immutable patch of ONE conversation's stream. Returns ``null`` when the
 * conversation has no entry, so callers can no-op instead of resurrecting a
 * stream that already ended.
 */
function patchStream(
  streams: Record<string, StreamState>,
  conversationId: string,
  patch: (cur: StreamState) => Partial<StreamState>,
): { streams: Record<string, StreamState> } | null {
  const cur = streams[conversationId];
  if (!cur) return null;
  return {
    streams: { ...streams, [conversationId]: { ...cur, ...patch(cur) } },
  };
}

function withoutKey<V>(map: Record<string, V>, key: string): Record<string, V> {
  if (!(key in map)) return map;
  const next = { ...map };
  delete next[key];
  return next;
}

export const useChatStore = create<ChatState>((set, get) => ({
  streams: {},
  pendingFiles: [],
  pendingAttachFiles: [],
  pendingComposerDraft: null,
  composerInjection: null,
  pastedNextStep: null,
  pendingMessage: null,
  pendingBlogs: [],
  pendingBlogTokens: [],
  pendingLibraryItems: [],
  pendingLibraryRefs: [],
  pendingTemplate: null,
  pendingTemplateCarry: null,
  errorByConversation: {},
  workspaceByConversation: {},
  referencedItemsByMessage: {},
  templateOffersByMessage: {},
  nextStepsByMessage: {},
  splitRatio: loadInitialSplitRatio(),
  maxReconnectAttempts: 5,
  noticeByConversation: {},
  deepSearchSummaries: {},

  beginSend: (conversationId) => {
    // Supersede ONLY this conversation's previous send. Other conversations'
    // streams keep running — that is the whole point of per-conversation state.
    const prev = get().streams[conversationId];
    if (prev?.abortController) prev.abortController.abort();
    cancelReveal(conversationId);
    sendIdSeq += 1;
    const sendId = sendIdSeq;
    set((state) => ({
      streams: { ...state.streams, [conversationId]: newStreamState(sendId) },
    }));
    return sendId;
  },

  endSend: (conversationId, sendId) => {
    if (get().streams[conversationId]?.sendId !== sendId) return;
    cancelReveal(conversationId);
    set((state) => ({ streams: withoutKey(state.streams, conversationId) }));
  },

  startStreaming: (conversationId, messageId) => {
    // Drop any reveal backlog a superseded stream left behind.
    cancelReveal(conversationId);
    set((state) => {
      const patched = patchStream(state.streams, conversationId, () => ({
        isStreaming: true,
        messageId,
        content: "",
        // A new run owns the tracker: drop any progress/carry left behind
        // (sealed summaries are keyed by message id and survive).
        deepSearchProgress: null,
        deepSearchSealable: null,
      }));
      // No entry → the send was stopped before the server confirmed it.
      // Never resurrect it.
      if (!patched) return state;
      return {
        ...patched,
        errorByConversation: withoutKey(state.errorByConversation, conversationId),
        // A new stream means the gate let this send through — drop any stale
        // banner from a previous rejection in this conversation.
        noticeByConversation: withoutKey(
          state.noticeByConversation,
          conversationId,
        ),
      };
    });
  },

  appendToken: (conversationId, text) => {
    if (!get().streams[conversationId]?.isStreaming) return;
    if (typeof window === "undefined") {
      set(
        (state) =>
          patchStream(state.streams, conversationId, (cur) => ({
            content: cur.content + text,
          })) ?? state,
      );
      return;
    }
    tokenBuffers.set(
      conversationId,
      (tokenBuffers.get(conversationId) ?? "") + text,
    );
    scheduleReveal();
  },

  flushStreamBuffer: (conversationId) => {
    const rest = tokenBuffers.get(conversationId) ?? "";
    cancelReveal(conversationId);
    // Other conversations' backlogs keep revealing.
    if (tokenBuffers.size > 0) scheduleReveal();
    if (rest.length === 0) return;
    set(
      (state) =>
        patchStream(state.streams, conversationId, (cur) => ({
          content: cur.content + rest,
        })) ?? state,
    );
  },

  stopStreaming: (conversationId) => {
    cancelReveal(conversationId);
    const cur = get().streams[conversationId];
    if (!cur) return;
    if (cur.abortController) cur.abortController.abort();
    // Cancelled (composer Stop button) → the tracker goes away and nothing
    // is sealed: an aborted run gets no summary chip.
    set((state) => ({ streams: withoutKey(state.streams, conversationId) }));
  },

  finishStreaming: (conversationId) => {
    // Called when stream completes naturally (done event).
    // Does NOT abort — just drops the conversation's stream entry (which also
    // resets its reconnect counters).
    // Any unrevealed buffer is intentionally discarded: the done handler
    // flushes before reading, and the agent_question path discards by design.
    // The `done` handler seals the summary BEFORE calling this, so dropping
    // the progress slots with the entry is safe — and it is what clears the
    // tracker on the ``agent_question`` pause path (which seals nothing).
    cancelReveal(conversationId);
    set((state) => ({ streams: withoutKey(state.streams, conversationId) }));
  },

  setError: (conversationId, error) =>
    set((state) => {
      if (error === null) {
        return {
          errorByConversation: withoutKey(
            state.errorByConversation,
            conversationId,
          ),
        };
      }
      // The run is over for display purposes: the live bubble goes away. The
      // entry itself stays until the send exits (``endSend``).
      const patched = patchStream(state.streams, conversationId, () => ({
        isStreaming: false,
      }));
      return {
        ...(patched ?? {}),
        errorByConversation: {
          ...state.errorByConversation,
          [conversationId]: error,
        },
      };
    }),

  addPendingFile: (file) =>
    set((state) => ({ pendingFiles: [...state.pendingFiles, file] })),

  removePendingFile: (id) =>
    set((state) => {
      const file = state.pendingFiles.find((f) => f.id === id);
      if (file) URL.revokeObjectURL(file.previewUrl);
      return { pendingFiles: state.pendingFiles.filter((f) => f.id !== id) };
    }),

  clearPendingFiles: () =>
    set((state) => {
      state.pendingFiles.forEach((f) => URL.revokeObjectURL(f.previewUrl));
      return { pendingFiles: [] };
    }),

  updatePendingFile: (id, partial) =>
    set((state) => ({
      pendingFiles: state.pendingFiles.map((f) =>
        f.id === id ? { ...f, ...partial } : f,
      ),
    })),

  setAbortController: (conversationId, controller) =>
    set(
      (state) =>
        patchStream(state.streams, conversationId, () => ({
          abortController: controller,
        })) ?? state,
    ),

  setPendingMessage: (message) => set({ pendingMessage: message }),

  clearPendingMessage: () => set({ pendingMessage: null }),

  setPendingAttachFiles: (files) => set({ pendingAttachFiles: files }),

  clearPendingAttachFiles: () => set({ pendingAttachFiles: [] }),

  setPendingComposerDraft: (text) => set({ pendingComposerDraft: text }),

  injectComposerText: (text, conversationId) =>
    set((state) => ({
      composerInjection: {
        text,
        conversationId,
        nonce: (state.composerInjection?.nonce ?? 0) + 1,
      },
    })),

  clearComposerInjection: () => set({ composerInjection: null }),

  pasteNextStep: (step) => {
    set({ pastedNextStep: { kind: step.kind, text: step.prompt } });
    get().injectComposerText(step.prompt);
  },

  consumePastedNextStep: () => {
    const pasted = get().pastedNextStep;
    if (pasted) set({ pastedNextStep: null });
    return pasted;
  },

  clearPastedNextStep: () => set({ pastedNextStep: null }),

  addPendingBlog: (blog) =>
    set((state) => ({ pendingBlogs: [...state.pendingBlogs, blog] })),

  removePendingBlog: (id) =>
    set((state) => ({
      pendingBlogs: state.pendingBlogs.filter((b) => b.id !== id),
    })),

  clearPendingBlogs: () => set({ pendingBlogs: [] }),

  updatePendingBlog: (id, partial) =>
    set((state) => ({
      pendingBlogs: state.pendingBlogs.map((b) =>
        b.id === id ? { ...b, ...partial } : b,
      ),
    })),

  setPendingBlogTokens: (tokens) => set({ pendingBlogTokens: tokens }),

  clearPendingBlogTokens: () => set({ pendingBlogTokens: [] }),

  addPendingLibraryItem: (item) =>
    set((state) => ({
      pendingLibraryItems: [...state.pendingLibraryItems, item],
    })),

  removePendingLibraryItem: (id) =>
    set((state) => ({
      pendingLibraryItems: state.pendingLibraryItems.filter((i) => i.id !== id),
    })),

  clearPendingLibraryItems: () => set({ pendingLibraryItems: [] }),

  updatePendingLibraryItem: (id, partial) =>
    set((state) => ({
      pendingLibraryItems: state.pendingLibraryItems.map((i) =>
        i.id === id ? { ...i, ...partial } : i,
      ),
    })),

  setPendingLibraryRefs: (refs) => set({ pendingLibraryRefs: refs }),

  clearPendingLibraryRefs: () => set({ pendingLibraryRefs: [] }),

  setPendingTemplate: (template) => set({ pendingTemplate: template }),

  setPendingTemplateCarry: (template) =>
    set({ pendingTemplateCarry: template }),

  openWorkspaceItem: (conversationId, itemId) =>
    set((state) => {
      const cur = state.workspaceByConversation[conversationId] ?? DEFAULT_WORKSPACE;
      return {
        workspaceByConversation: {
          ...state.workspaceByConversation,
          [conversationId]: {
            isOpen: true,
            openItemId: itemId,
            focusedReferenceN: null,
            // Preserve an active highlight so a chip click that targets a
            // card in the list view can keep ringing it after the pane opens.
            highlightedItemId: cur.highlightedItemId,
          },
        },
      };
    }),

  openWorkspaceItemAtReference: (conversationId, itemId, n) =>
    set((state) => {
      const cur = state.workspaceByConversation[conversationId] ?? DEFAULT_WORKSPACE;
      return {
        workspaceByConversation: {
          ...state.workspaceByConversation,
          [conversationId]: {
            isOpen: true,
            openItemId: itemId,
            focusedReferenceN: n,
            highlightedItemId: cur.highlightedItemId,
          },
        },
      };
    }),

  clearFocusedReference: (conversationId) =>
    set((state) => {
      const cur = state.workspaceByConversation[conversationId] ?? DEFAULT_WORKSPACE;
      return {
        workspaceByConversation: {
          ...state.workspaceByConversation,
          [conversationId]: { ...cur, focusedReferenceN: null },
        },
      };
    }),

  recordReferencedItem: (messageId, itemId) =>
    set((state) => {
      const cur = state.referencedItemsByMessage[messageId] ?? [];
      if (cur.includes(itemId)) return state;
      return {
        referencedItemsByMessage: {
          ...state.referencedItemsByMessage,
          [messageId]: [...cur, itemId],
        },
      };
    }),

  recordTemplateOffer: (messageId, itemId, titleHint) =>
    set((state) => ({
      templateOffersByMessage: {
        ...state.templateOffersByMessage,
        [messageId]: { itemId, titleHint },
      },
    })),

  recordNextSteps: (messageId, items) =>
    set((state) => ({
      nextStepsByMessage: {
        ...state.nextStepsByMessage,
        [messageId]: items,
      },
    })),

  highlightWorkspaceItem: (conversationId, itemId) => {
    set((state) => ({
      workspaceByConversation: {
        ...state.workspaceByConversation,
        [conversationId]: {
          // Force the pane open + drop back to list mode so the highlighted
          // card is visible. If the user already had a different item open
          // in detail mode, navigating to the list lets the ring be seen.
          isOpen: true,
          openItemId: null,
          focusedReferenceN: null,
          highlightedItemId: itemId,
        },
      },
    }));
    // Auto-clear after the ring animation budget so re-clicking the same
    // chip re-fires the highlight. Guarded against double-set: if the user
    // clicks a different chip mid-flight, only the matching id is cleared.
    if (typeof window !== "undefined") {
      window.setTimeout(() => {
        const cur =
          get().workspaceByConversation[conversationId] ?? DEFAULT_WORKSPACE;
        if (cur.highlightedItemId === itemId) {
          get().clearHighlightedItem(conversationId);
        }
      }, HIGHLIGHT_ITEM_MS);
    }
  },

  clearHighlightedItem: (conversationId) =>
    set((state) => {
      const cur = state.workspaceByConversation[conversationId] ?? DEFAULT_WORKSPACE;
      return {
        workspaceByConversation: {
          ...state.workspaceByConversation,
          [conversationId]: { ...cur, highlightedItemId: null },
        },
      };
    }),

  closeWorkspaceItem: (conversationId) =>
    set((state) => {
      // Return the pane from item-detail view to the list view: the pane
      // stays open, just clear the focused item.
      const cur = state.workspaceByConversation[conversationId] ?? DEFAULT_WORKSPACE;
      return {
        workspaceByConversation: {
          ...state.workspaceByConversation,
          [conversationId]: { ...cur, openItemId: null, focusedReferenceN: null },
        },
      };
    }),

  closeWorkspace: (conversationId) =>
    set((state) => ({
      workspaceByConversation: {
        ...state.workspaceByConversation,
        [conversationId]: {
          isOpen: false,
          openItemId: null,
          focusedReferenceN: null,
          highlightedItemId: null,
        },
      },
    })),

  toggleWorkspace: (conversationId) =>
    set((state) => {
      const cur = state.workspaceByConversation[conversationId] ?? DEFAULT_WORKSPACE;
      return {
        workspaceByConversation: {
          ...state.workspaceByConversation,
          [conversationId]: {
            isOpen: !cur.isOpen,
            openItemId: cur.isOpen ? null : cur.openItemId,
            focusedReferenceN: null,
            highlightedItemId: cur.isOpen ? null : cur.highlightedItemId,
          },
        },
      };
    }),

  setSplitRatio: (ratio) => {
    const clamped = Math.max(0, Math.min(100, ratio));
    persistSplitRatio(clamped);
    set({ splitRatio: clamped });
  },

  startAgentRun: (conversationId, agentFamily, subtype) =>
    set(
      (state) =>
        patchStream(state.streams, conversationId, () => ({
          isAgentRunning: true,
          runningAgentFamily: agentFamily,
          runningAgentSubtype: subtype ?? null,
        })) ?? state,
    ),

  finishAgentRun: (conversationId) =>
    set(
      (state) =>
        patchStream(state.streams, conversationId, (cur) => ({
          isAgentRunning: false,
          runningAgentFamily: null,
          runningAgentSubtype: null,
          // The run is over → the tracker must stop showing "searching". The
          // totals are parked (not dropped) so the `done` handler can still
          // seal the chip; see ``deepSearchSealable``.
          deepSearchProgress: null,
          deepSearchSealable: cur.deepSearchProgress ?? cur.deepSearchSealable,
        })) ?? state,
    ),

  startReconnect: (conversationId, attempts) =>
    set(
      (state) =>
        patchStream(state.streams, conversationId, () => ({
          isReconnecting: true,
          reconnectAttempts: attempts,
        })) ?? state,
    ),

  resetReconnect: (conversationId) =>
    set(
      (state) =>
        patchStream(state.streams, conversationId, () => ({
          reconnectAttempts: 0,
          isReconnecting: false,
        })) ?? state,
    ),

  setNotice: (conversationId, notice) =>
    set((state) => ({
      noticeByConversation:
        notice === null
          ? withoutKey(state.noticeByConversation, conversationId)
          : { ...state.noticeByConversation, [conversationId]: notice },
    })),

  setDeepSearchProgress: (conversationId, event) =>
    set((state) => {
      const stream = state.streams[conversationId];
      if (!stream) return state;
      const prev = stream.deepSearchProgress;
      const detail = (event.text ?? "").trim() || null;
      const base: DeepSearchProgressState = prev ?? {
        stage: event.stage,
        text: null,
        sources: 0,
        queries: 0,
        topicsSeen: 0,
        startedAt: Date.now(),
        log: [],
      };

      // Counts arrive on phase boundaries only; an event without them must
      // not zero what a previous phase already reported. Monotonic so a
      // late-arriving smaller count can't make the tracker count backwards.
      const nextSources =
        typeof event.data?.sources === "number"
          ? Math.max(base.sources, event.data.sources)
          : base.sources;
      const nextQueries =
        typeof event.data?.queries === "number"
          ? Math.max(base.queries, event.data.queries)
          : base.queries;

      // Stage is MONOTONIC. The two executors (reg_compliance/case) run in
      // parallel, so a slow phase can report `searching` (its phase-end counts) after a
      // fast one already pushed the run to `evaluating` — and the bar must
      // never walk backwards. A stale stage is ignored, but its COUNTS above
      // still merge, which is exactly what those late events carry.
      const nextStage =
        DEEP_SEARCH_STAGE_ORDER[event.stage] >=
        DEEP_SEARCH_STAGE_ORDER[base.stage]
          ? event.stage
          : base.stage;

      // The detail line belongs to the stage it arrived with — a stage change
      // without a new line clears the stale one rather than carrying it over.
      // A stage-regressing event must not repaint the current stage's line.
      const isStale = nextStage !== event.stage;
      const nextText = isStale
        ? base.text
        : (detail ?? (event.stage === base.stage ? base.text : null));

      const log =
        detail && base.log[base.log.length - 1] !== detail
          ? [...base.log, detail].slice(-MAX_DEEP_SEARCH_LOG)
          : base.log;

      return (
        patchStream(state.streams, conversationId, () => ({
          deepSearchProgress: {
            ...base,
            stage: nextStage,
            text: nextText,
            sources: nextSources,
            queries: nextQueries,
            log,
          },
        })) ?? state
      );
    }),

  appendDeepSearchLog: (conversationId, text) =>
    set((state) => {
      const prev = state.streams[conversationId]?.deepSearchProgress ?? null;
      const line = text.trim();
      // No live run → this status line belongs to another family (writer,
      // memory, …) which has no tracker. Drop it.
      if (!prev || !line) return state;
      // Exact repeat of the last line → nothing to record.
      if (prev.log[prev.log.length - 1] === line) return state;

      const log = [...prev.log, line].slice(-MAX_DEEP_SEARCH_LOG);

      // A "بحث في …" line is a live sub-query topic streamed during the
      // searching stage. Beyond logging it, promote it to the ACTIVE detail
      // line so the topics drive the evidence line, and bump ``topicsSeen`` so
      // the tracker can show live query progress before the phase-end counts
      // arrive.
      if (line.startsWith(DEEP_SEARCH_TOPIC_PREFIX)) {
        return (
          patchStream(state.streams, conversationId, () => ({
            deepSearchProgress: {
              ...prev,
              text: line,
              topicsSeen: prev.topicsSeen + 1,
              log,
            },
          })) ?? state
        );
      }

      return (
        patchStream(state.streams, conversationId, () => ({
          deepSearchProgress: { ...prev, log },
        })) ?? state
      );
    }),

  sealDeepSearchSummary: (conversationId, messageId) =>
    set((state) => {
      const stream = state.streams[conversationId];
      const run = stream
        ? (stream.deepSearchProgress ?? stream.deepSearchSealable)
        : null;
      if (!run || !messageId) return state;
      return {
        deepSearchSummaries: {
          ...state.deepSearchSummaries,
          [messageId]: {
            sources: run.sources,
            elapsedMs: Math.max(0, Date.now() - run.startedAt),
            log: run.log,
          },
        },
      };
    }),

  reset: () => {
    cancelAllReveals();
    // A user switch ends EVERY in-flight send in this tab.
    for (const stream of Object.values(get().streams)) {
      stream.abortController?.abort();
    }
    // splitRatio is intentionally preserved — it is a global layout preference.
    set({
      streams: {},
      pendingFiles: [],
      pendingAttachFiles: [],
      pendingComposerDraft: null,
      composerInjection: null,
      pastedNextStep: null,
      pendingMessage: null,
      pendingBlogs: [],
      pendingBlogTokens: [],
      pendingLibraryItems: [],
      pendingLibraryRefs: [],
      pendingTemplate: null,
      pendingTemplateCarry: null,
      errorByConversation: {},
      workspaceByConversation: {},
      referencedItemsByMessage: {},
      templateOffersByMessage: {},
      nextStepsByMessage: {},
      maxReconnectAttempts: 5,
      noticeByConversation: {},
      deepSearchSummaries: {},
    });
  },
}));

// ---------------------------------------------------------------------------
// Per-conversation selectors (parallel_conversations plan §3)
//
// Each returns a primitive (or the conversation's own entry), so a component
// re-renders only when ITS conversation changes — never on another
// conversation's reveal frames.
// ---------------------------------------------------------------------------

/** Number of sends in flight in this tab, across every conversation. */
export function selectRunningCount(state: ChatState): number {
  return Object.keys(state.streams).length;
}

/** True when any conversation is actively streaming an answer. */
export function selectIsAnyStreaming(state: ChatState): boolean {
  return Object.values(state.streams).some((s) => s.isStreaming);
}

/** The conversation's live stream entry (re-renders on every reveal frame). */
export function useStreamState(
  conversationId: string | null | undefined,
): StreamState | undefined {
  return useChatStore((s) =>
    conversationId ? s.streams[conversationId] : undefined,
  );
}

/** True once the conversation's run is confirmed and its answer is streaming. */
export function useIsStreaming(conversationId: string | null | undefined): boolean {
  return useChatStore((s) =>
    conversationId ? (s.streams[conversationId]?.isStreaming ?? false) : false,
  );
}

/**
 * True while the conversation has a send in flight in this tab — from the POST
 * until a terminal event (wider than ``useIsStreaming``, which only flips at
 * ``message_start``). Drives the composer lock and the sidebar live dot.
 */
export function useIsConversationRunning(
  conversationId: string | null | undefined,
): boolean {
  return useChatStore((s) =>
    conversationId ? conversationId in s.streams : false,
  );
}

/** Number of sends in flight in this tab, across every conversation. */
export function useRunningCount(): number {
  return useChatStore(selectRunningCount);
}
