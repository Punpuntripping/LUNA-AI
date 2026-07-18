import { create } from "zustand";
import type {
  DeepSearchStage,
  PendingBlog,
  PendingFile,
  PendingTemplate,
  SSEAgentProgress,
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
// Streaming reveal buffer — module-level on purpose.
//
// Raw SSE tokens land here instead of directly in state; ``revealFrame``
// publishes to ``streamingContent`` at most once per animation frame, at a
// velocity proportional to the backlog. That coalesces a burst of token
// events into one React render AND smooths the network's stop-and-go rhythm
// into a steady typewriter reveal. Mutating these must never re-render, which
// is why they are not store state.
// ---------------------------------------------------------------------------

let tokenBuffer = "";
let revealRafId: number | null = null;

/** Floor so a near-empty backlog still visibly advances every frame. */
const REVEAL_MIN_CHARS = 3;
/**
 * Fraction of the backlog revealed per frame. The backlog settles where
 * production = reveal rate (≈ divisor × chars-per-frame), so display lags the
 * network by only ~100ms at typical token rates while bursts get absorbed.
 */
const REVEAL_BACKLOG_DIVISOR = 6;

function cancelReveal(): void {
  if (typeof window !== "undefined" && revealRafId !== null) {
    window.cancelAnimationFrame(revealRafId);
  }
  revealRafId = null;
  tokenBuffer = "";
}

function revealFrame(): void {
  revealRafId = null;
  if (!useChatStore.getState().isStreaming) {
    tokenBuffer = "";
    return;
  }
  if (tokenBuffer.length === 0) return;
  let n = Math.min(
    tokenBuffer.length,
    Math.max(REVEAL_MIN_CHARS, Math.ceil(tokenBuffer.length / REVEAL_BACKLOG_DIVISOR)),
  );
  // Never split a surrogate pair (emoji etc.) across frames.
  const cut = tokenBuffer.charCodeAt(n - 1);
  if (n < tokenBuffer.length && cut >= 0xd800 && cut <= 0xdbff) n += 1;
  const piece = tokenBuffer.slice(0, n);
  tokenBuffer = tokenBuffer.slice(n);
  useChatStore.setState((state) => ({
    streamingContent: state.streamingContent + piece,
  }));
  if (tokenBuffer.length > 0) {
    revealRafId = window.requestAnimationFrame(revealFrame);
  }
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
   * («الاستعلام ٣») before the authoritative phase-end ``queries`` count lands.
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

interface ChatState {
  isStreaming: boolean;
  streamingMessageId: string | null;
  // Conversation the active stream belongs to. The streaming buffer is a
  // single global value; consumers MUST check this against their own
  // conversation id before rendering, or one conversation's stream leaks
  // into another.
  streamingConversationId: string | null;
  streamingContent: string;
  abortController: AbortController | null;
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
  composerInjection: { text: string; nonce: number } | null;
  pendingMessage: string | null;
  // Blog share-links pasted into the composer, shown as chips next to file
  // attachments (blog_import plan §D4). ``pendingBlogs`` are the live chips;
  // ``pendingBlogTokens`` is the new-chat carry slot (tokens pasted before a
  // conversation exists — the ``pendingAttachFiles`` twin), consumed by the
  // destination ChatInput after the create-conversation navigation.
  pendingBlogs: PendingBlog[];
  pendingBlogTokens: string[];
  // قالب chip picked from the composer's «+» menu. Single-slot — the planner
  // drafts from ONE template, so picking another replaces it. Cleared on
  // conversation switch (same discipline as files/blogs); ``pendingTemplateCarry``
  // is the new-chat carry slot (the pendingBlogTokens twin) so a chip attached
  // on the empty page survives the create-on-attach navigation.
  pendingTemplate: PendingTemplate | null;
  pendingTemplateCarry: PendingTemplate | null;
  error: string | null;
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
  // Global layout preference (persisted to localStorage) — NOT per-conversation.
  splitRatio: number;
  isAgentRunning: boolean;
  runningAgentFamily: string | null;
  runningAgentSubtype: string | null;
  reconnectAttempts: number;
  maxReconnectAttempts: number;
  isReconnecting: boolean;
  /**
   * Set when the backend rejects a send via the per-user quota gate (SSE
   * ``quota_exceeded`` event). The chat layout renders ``QuotaBanner`` while
   * this is non-null. Cleared by the banner's dismiss button OR by the next
   * successful send (``startStreaming`` clears it).
   */
  quotaInfo: SSEQuotaExceeded | null;
  /**
   * Live progress of the in-flight deep_search run (``null`` otherwise). The
   * ONLY subscriber is ``DeepSearchProgress`` — keep it that way, or every
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
   * parks the run here instead of dropping it. Cleared by
   * ``finishStreaming`` / ``stopStreaming`` / ``startStreaming``, which is
   * also what kills the chip on the pause path (``agent_question`` calls
   * ``finishAgentRun`` then ``finishStreaming`` → nothing left to seal).
   */
  deepSearchSealable: DeepSearchProgressState | null;
  /**
   * Sealed deep_search summaries keyed by assistant ``message_id``. Drives
   * ``DeepSearchSummaryChip`` above the assistant bubble. Session-only — no
   * persistence, no DB column; ``reset()`` (user switch) clears it.
   */
  deepSearchSummaries: Record<string, DeepSearchSummary>;

  startStreaming: (messageId: string, conversationId: string) => void;
  appendToken: (text: string) => void;
  /**
   * Synchronously publish any text still waiting in the paced-reveal buffer.
   * MUST be called before reading ``streamingContent`` as the final answer
   * (the SSE ``done`` handler) — otherwise the buffered tail is lost.
   */
  flushStreamBuffer: () => void;
  stopStreaming: () => void;
  finishStreaming: () => void;
  setError: (error: string | null) => void;
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
  setAbortController: (controller: AbortController | null) => void;
  setPendingMessage: (message: string | null) => void;
  clearPendingMessage: () => void;
  setPendingAttachFiles: (files: File[]) => void;
  clearPendingAttachFiles: () => void;
  setPendingComposerDraft: (text: string | null) => void;
  /** Put ``text`` into the live composer textarea (does NOT send). */
  injectComposerText: (text: string) => void;
  clearComposerInjection: () => void;
  addPendingBlog: (blog: PendingBlog) => void;
  removePendingBlog: (id: string) => void;
  clearPendingBlogs: () => void;
  /** Patch a blog chip in place; no-op when the id is gone (user removal race). */
  updatePendingBlog: (id: string, partial: Partial<PendingBlog>) => void;
  setPendingBlogTokens: (tokens: string[]) => void;
  clearPendingBlogTokens: () => void;
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
  startAgentRun: (agentFamily: string, subtype?: string | null) => void;
  finishAgentRun: () => void;
  startReconnect: () => void;
  resetReconnect: () => void;
  setQuotaInfo: (info: SSEQuotaExceeded | null) => void;
  /**
   * Fold an ``agent_progress`` SSE event into the live slice. Creates the
   * slice (stamping ``startedAt``) on the first event of a run. Counts are
   * merged monotonically — an event that omits ``sources``/``queries`` leaves
   * them untouched rather than zeroing them.
   */
  setDeepSearchProgress: (event: SSEAgentProgress) => void;
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
  appendDeepSearchLog: (text: string) => void;
  /**
   * Freeze the current (or just-finished) run into
   * ``deepSearchSummaries[messageId]``. No-op when there is nothing to seal —
   * which is exactly what makes the pause path chip-free.
   */
  sealDeepSearchSummary: (messageId: string) => void;
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

export const useChatStore = create<ChatState>((set, get) => ({
  isStreaming: false,
  streamingMessageId: null,
  streamingConversationId: null,
  streamingContent: "",
  abortController: null,
  pendingFiles: [],
  pendingAttachFiles: [],
  pendingComposerDraft: null,
  composerInjection: null,
  pendingMessage: null,
  pendingBlogs: [],
  pendingBlogTokens: [],
  pendingTemplate: null,
  pendingTemplateCarry: null,
  error: null,
  workspaceByConversation: {},
  referencedItemsByMessage: {},
  templateOffersByMessage: {},
  splitRatio: loadInitialSplitRatio(),
  isAgentRunning: false,
  runningAgentFamily: null,
  runningAgentSubtype: null,
  reconnectAttempts: 0,
  maxReconnectAttempts: 5,
  isReconnecting: false,
  quotaInfo: null,
  deepSearchProgress: null,
  deepSearchSealable: null,
  deepSearchSummaries: {},

  startStreaming: (messageId, conversationId) => {
    // Drop any reveal backlog a superseded stream left behind.
    cancelReveal();
    set({
      isStreaming: true,
      streamingMessageId: messageId,
      streamingConversationId: conversationId,
      streamingContent: "",
      error: null,
      // A new stream means the gate let this send through — drop any stale
      // banner from a previous rejection.
      quotaInfo: null,
      // A new run owns the tracker: drop any progress/carry a superseded run
      // left behind (sealed summaries are keyed by message id and survive).
      deepSearchProgress: null,
      deepSearchSealable: null,
    });
  },

  appendToken: (text) => {
    if (typeof window === "undefined") {
      set((state) => ({ streamingContent: state.streamingContent + text }));
      return;
    }
    tokenBuffer += text;
    if (revealRafId === null) {
      revealRafId = window.requestAnimationFrame(revealFrame);
    }
  },

  flushStreamBuffer: () => {
    if (typeof window !== "undefined" && revealRafId !== null) {
      window.cancelAnimationFrame(revealRafId);
    }
    revealRafId = null;
    if (tokenBuffer.length === 0) return;
    const rest = tokenBuffer;
    tokenBuffer = "";
    set((state) => ({ streamingContent: state.streamingContent + rest }));
  },

  stopStreaming: () => {
    cancelReveal();
    const { abortController } = get();
    if (abortController) abortController.abort();
    set({
      isStreaming: false,
      streamingMessageId: null,
      streamingConversationId: null,
      streamingContent: "",
      abortController: null,
      // Cancelled (composer Stop button) → the tracker goes away and nothing
      // is sealed: an aborted run gets no summary chip.
      deepSearchProgress: null,
      deepSearchSealable: null,
    });
  },

  finishStreaming: () => {
    // Called when stream completes naturally (done event).
    // Does NOT abort — just clears streaming state.
    // Also resets reconnect counters because the stream completed successfully.
    // Any unrevealed buffer is intentionally discarded: the done handler
    // flushes before reading, and the agent_question path discards by design.
    cancelReveal();
    set({
      isStreaming: false,
      streamingMessageId: null,
      streamingConversationId: null,
      streamingContent: "",
      abortController: null,
      reconnectAttempts: 0,
      isReconnecting: false,
      // The `done` handler seals the summary BEFORE calling this, so dropping
      // both slots here is safe — and it is what clears the tracker on the
      // ``agent_question`` pause path (which seals nothing).
      deepSearchProgress: null,
      deepSearchSealable: null,
    });
  },

  setError: (error) => set({ error, isStreaming: false }),

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

  setAbortController: (controller) => set({ abortController: controller }),

  setPendingMessage: (message) => set({ pendingMessage: message }),

  clearPendingMessage: () => set({ pendingMessage: null }),

  setPendingAttachFiles: (files) => set({ pendingAttachFiles: files }),

  clearPendingAttachFiles: () => set({ pendingAttachFiles: [] }),

  setPendingComposerDraft: (text) => set({ pendingComposerDraft: text }),

  injectComposerText: (text) =>
    set((state) => ({
      composerInjection: {
        text,
        nonce: (state.composerInjection?.nonce ?? 0) + 1,
      },
    })),

  clearComposerInjection: () => set({ composerInjection: null }),

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

  startAgentRun: (agentFamily, subtype) =>
    set({
      isAgentRunning: true,
      runningAgentFamily: agentFamily,
      runningAgentSubtype: subtype ?? null,
    }),

  finishAgentRun: () =>
    set((state) => ({
      isAgentRunning: false,
      runningAgentFamily: null,
      runningAgentSubtype: null,
      // The run is over → the tracker must stop showing "searching". The
      // totals are parked (not dropped) so the `done` handler can still seal
      // the chip; see ``deepSearchSealable``.
      deepSearchProgress: null,
      deepSearchSealable: state.deepSearchProgress ?? state.deepSearchSealable,
    })),

  startReconnect: () =>
    set((state) => ({
      isReconnecting: true,
      reconnectAttempts: state.reconnectAttempts + 1,
    })),

  resetReconnect: () =>
    set({ reconnectAttempts: 0, isReconnecting: false }),

  setQuotaInfo: (info) => set({ quotaInfo: info }),

  setDeepSearchProgress: (event) =>
    set((state) => {
      const prev = state.deepSearchProgress;
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

      return {
        deepSearchProgress: {
          ...base,
          stage: nextStage,
          text: nextText,
          sources: nextSources,
          queries: nextQueries,
          log,
        },
      };
    }),

  appendDeepSearchLog: (text) =>
    set((state) => {
      const prev = state.deepSearchProgress;
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
        return {
          deepSearchProgress: {
            ...prev,
            text: line,
            topicsSeen: prev.topicsSeen + 1,
            log,
          },
        };
      }

      return {
        deepSearchProgress: { ...prev, log },
      };
    }),

  sealDeepSearchSummary: (messageId) =>
    set((state) => {
      const run = state.deepSearchProgress ?? state.deepSearchSealable;
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
    cancelReveal();
    // splitRatio is intentionally preserved — it is a global layout preference.
    set({
      isStreaming: false,
      streamingMessageId: null,
      streamingConversationId: null,
      streamingContent: "",
      abortController: null,
      pendingFiles: [],
      pendingAttachFiles: [],
      pendingComposerDraft: null,
      composerInjection: null,
      pendingMessage: null,
      pendingBlogs: [],
      pendingBlogTokens: [],
      pendingTemplate: null,
      pendingTemplateCarry: null,
      error: null,
      workspaceByConversation: {},
      referencedItemsByMessage: {},
      templateOffersByMessage: {},
      isAgentRunning: false,
      runningAgentFamily: null,
      runningAgentSubtype: null,
      reconnectAttempts: 0,
      maxReconnectAttempts: 5,
      isReconnecting: false,
      quotaInfo: null,
      deepSearchProgress: null,
      deepSearchSealable: null,
      deepSearchSummaries: {},
    });
  },
}));
