import { create } from "zustand";
import type { AgentFamily, Citation, PendingFile } from "@/types";

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
  splitRatio: number;
}

interface ChatState {
  isStreaming: boolean;
  streamingMessageId: string | null;
  streamingContent: string;
  streamingCitations: Citation[];
  abortController: AbortController | null;
  pendingFiles: PendingFile[];
  pendingMessage: string | null;
  error: string | null;
  /** Per-message agent family override (set from @ commands or selector, sent with the message) */
  selectedAgentFamily: AgentFamily | null;
  /** Persistent UI agent selector state (null = auto/router). Drives the pill display. */
  selectedAgent: AgentFamily | null;
  modifiers: string[];
  workspace: WorkspaceUiState;
  isAgentRunning: boolean;
  runningAgentFamily: string | null;
  runningAgentSubtype: string | null;
  reconnectAttempts: number;
  maxReconnectAttempts: number;
  isReconnecting: boolean;

  startStreaming: (messageId: string) => void;
  appendToken: (text: string) => void;
  setStreamingCitations: (citations: Citation[]) => void;
  stopStreaming: () => void;
  finishStreaming: () => void;
  setError: (error: string | null) => void;
  addPendingFile: (file: PendingFile) => void;
  removePendingFile: (id: string) => void;
  clearPendingFiles: () => void;
  setAbortController: (controller: AbortController | null) => void;
  setPendingMessage: (message: string | null) => void;
  clearPendingMessage: () => void;
  setSelectedAgentFamily: (family: AgentFamily | null) => void;
  /** Set the persistent UI agent selector (null = auto/router) */
  setSelectedAgent: (agent: AgentFamily | null) => void;
  setModifiers: (mods: string[]) => void;
  openWorkspaceItem: (itemId: string) => void;
  closeWorkspaceItem: () => void;
  closeWorkspace: () => void;
  toggleWorkspace: () => void;
  setSplitRatio: (ratio: number) => void;
  resetAgentSelection: () => void;
  startAgentRun: (agentFamily: string, subtype?: string | null) => void;
  finishAgentRun: () => void;
  startReconnect: () => void;
  resetReconnect: () => void;
  reset: () => void;
}

const INITIAL_WORKSPACE: WorkspaceUiState = {
  isOpen: false,
  openItemId: null,
  splitRatio: DEFAULT_SPLIT_RATIO,
};

export const useChatStore = create<ChatState>((set, get) => ({
  isStreaming: false,
  streamingMessageId: null,
  streamingContent: "",
  streamingCitations: [],
  abortController: null,
  pendingFiles: [],
  pendingMessage: null,
  error: null,
  selectedAgentFamily: null,
  selectedAgent: null,
  modifiers: [],
  workspace: { ...INITIAL_WORKSPACE, splitRatio: loadInitialSplitRatio() },
  isAgentRunning: false,
  runningAgentFamily: null,
  runningAgentSubtype: null,
  reconnectAttempts: 0,
  maxReconnectAttempts: 5,
  isReconnecting: false,

  startStreaming: (messageId) =>
    set({
      isStreaming: true,
      streamingMessageId: messageId,
      streamingContent: "",
      streamingCitations: [],
      error: null,
    }),

  appendToken: (text) =>
    set((state) => ({ streamingContent: state.streamingContent + text })),

  setStreamingCitations: (citations) => set({ streamingCitations: citations }),

  stopStreaming: () => {
    const { abortController } = get();
    if (abortController) abortController.abort();
    set({
      isStreaming: false,
      streamingMessageId: null,
      streamingContent: "",
      streamingCitations: [],
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
      streamingContent: "",
      streamingCitations: [],
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

  setAbortController: (controller) => set({ abortController: controller }),

  setPendingMessage: (message) => set({ pendingMessage: message }),

  clearPendingMessage: () => set({ pendingMessage: null }),

  setSelectedAgentFamily: (family) => set({ selectedAgentFamily: family }),

  setSelectedAgent: (agent) =>
    set({ selectedAgent: agent, selectedAgentFamily: agent }),

  setModifiers: (mods) => set({ modifiers: mods }),

  openWorkspaceItem: (itemId) =>
    set((state) => ({
      workspace: { ...state.workspace, isOpen: true, openItemId: itemId },
    })),

  closeWorkspaceItem: () =>
    set((state) => ({
      // Returns the workspace pane from item-detail view back to the list view.
      // Pane stays open; just clears the focused item.
      workspace: { ...state.workspace, openItemId: null },
    })),

  closeWorkspace: () =>
    set((state) => ({
      workspace: { ...state.workspace, isOpen: false, openItemId: null },
    })),

  toggleWorkspace: () =>
    set((state) => ({
      workspace: {
        ...state.workspace,
        isOpen: !state.workspace.isOpen,
        openItemId: state.workspace.isOpen ? null : state.workspace.openItemId,
      },
    })),

  setSplitRatio: (ratio) => {
    const clamped = Math.max(0, Math.min(100, ratio));
    persistSplitRatio(clamped);
    set((state) => ({
      workspace: { ...state.workspace, splitRatio: clamped },
    }));
  },

  resetAgentSelection: () =>
    set({ selectedAgentFamily: null, modifiers: [] }),

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
  // Note: resetAgentSelection does NOT clear selectedAgent — that's the persistent
  // UI selector. Only reset() clears the UI selector (e.g., on conversation switch).

  startReconnect: () =>
    set((state) => ({
      isReconnecting: true,
      reconnectAttempts: state.reconnectAttempts + 1,
    })),

  resetReconnect: () =>
    set({ reconnectAttempts: 0, isReconnecting: false }),

  reset: () =>
    set((state) => ({
      isStreaming: false,
      streamingMessageId: null,
      streamingContent: "",
      streamingCitations: [],
      abortController: null,
      pendingFiles: [],
      pendingMessage: null,
      error: null,
      selectedAgentFamily: null,
      selectedAgent: null,
      modifiers: [],
      workspace: {
        isOpen: false,
        openItemId: null,
        splitRatio: state.workspace.splitRatio,
      },
      isAgentRunning: false,
      runningAgentFamily: null,
      runningAgentSubtype: null,
      reconnectAttempts: 0,
      maxReconnectAttempts: 5,
      isReconnecting: false,
    })),
}));
