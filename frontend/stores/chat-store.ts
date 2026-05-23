import { create } from "zustand";
import type { PendingFile } from "@/types";

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
  pendingMessage: string | null;
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
  // Global layout preference (persisted to localStorage) — NOT per-conversation.
  splitRatio: number;
  isAgentRunning: boolean;
  runningAgentFamily: string | null;
  runningAgentSubtype: string | null;
  reconnectAttempts: number;
  maxReconnectAttempts: number;
  isReconnecting: boolean;

  startStreaming: (messageId: string, conversationId: string) => void;
  appendToken: (text: string) => void;
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
  pendingMessage: null,
  error: null,
  workspaceByConversation: {},
  referencedItemsByMessage: {},
  splitRatio: loadInitialSplitRatio(),
  isAgentRunning: false,
  runningAgentFamily: null,
  runningAgentSubtype: null,
  reconnectAttempts: 0,
  maxReconnectAttempts: 5,
  isReconnecting: false,

  startStreaming: (messageId, conversationId) =>
    set({
      isStreaming: true,
      streamingMessageId: messageId,
      streamingConversationId: conversationId,
      streamingContent: "",
      error: null,
    }),

  appendToken: (text) =>
    set((state) => ({ streamingContent: state.streamingContent + text })),

  stopStreaming: () => {
    const { abortController } = get();
    if (abortController) abortController.abort();
    set({
      isStreaming: false,
      streamingMessageId: null,
      streamingConversationId: null,
      streamingContent: "",
      abortController: null,
    });
  },

  finishStreaming: () => {
    // Called when stream completes naturally (done event).
    // Does NOT abort — just clears streaming state.
    // Also resets reconnect counters because the stream completed successfully.
    set({
      isStreaming: false,
      streamingMessageId: null,
      streamingConversationId: null,
      streamingContent: "",
      abortController: null,
      reconnectAttempts: 0,
      isReconnecting: false,
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
    set({
      isAgentRunning: false,
      runningAgentFamily: null,
      runningAgentSubtype: null,
    }),

  startReconnect: () =>
    set((state) => ({
      isReconnecting: true,
      reconnectAttempts: state.reconnectAttempts + 1,
    })),

  resetReconnect: () =>
    set({ reconnectAttempts: 0, isReconnecting: false }),

  reset: () =>
    // splitRatio is intentionally preserved — it is a global layout preference.
    set({
      isStreaming: false,
      streamingMessageId: null,
      streamingConversationId: null,
      streamingContent: "",
      abortController: null,
      pendingFiles: [],
      pendingMessage: null,
      error: null,
      workspaceByConversation: {},
      referencedItemsByMessage: {},
      isAgentRunning: false,
      runningAgentFamily: null,
      runningAgentSubtype: null,
      reconnectAttempts: 0,
      maxReconnectAttempts: 5,
      isReconnecting: false,
    }),
}));
