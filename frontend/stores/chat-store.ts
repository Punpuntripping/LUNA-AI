import { create } from "zustand";
import type { AgentFamily, PendingFile } from "@/types";

interface ChatState {
  isStreaming: boolean;
  streamingMessageId: string | null;
  streamingContent: string;
  abortController: AbortController | null;
  pendingFiles: PendingFile[];
  pendingMessage: string | null;
  error: string | null;
  /** Per-message agent family override (set from @ commands or selector, sent with the message) */
  selectedAgentFamily: AgentFamily | null;
  /** Persistent UI agent selector state (null = auto/router). Drives the pill display. */
  selectedAgent: AgentFamily | null;
  modifiers: string[];
  isArtifactPanelOpen: boolean;
  activeArtifactId: string | null;
  activeTaskId: string | null;
  activeTaskType: string | null;
  reconnectAttempts: number;
  maxReconnectAttempts: number;
  isReconnecting: boolean;

  startStreaming: (messageId: string) => void;
  appendToken: (text: string) => void;
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
  openArtifactPanel: (artifactId: string) => void;
  closeArtifactPanel: () => void;
  toggleArtifactPanel: () => void;
  resetAgentSelection: () => void;
  setActiveTask: (taskId: string, taskType: string) => void;
  clearActiveTask: () => void;
  startReconnect: () => void;
  resetReconnect: () => void;
  reset: () => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  isStreaming: false,
  streamingMessageId: null,
  streamingContent: "",
  abortController: null,
  pendingFiles: [],
  pendingMessage: null,
  error: null,
  selectedAgentFamily: null,
  selectedAgent: null,
  modifiers: [],
  isArtifactPanelOpen: false,
  activeArtifactId: null,
  activeTaskId: null,
  activeTaskType: null,
  reconnectAttempts: 0,
  maxReconnectAttempts: 5,
  isReconnecting: false,

  startStreaming: (messageId) =>
    set({ isStreaming: true, streamingMessageId: messageId, streamingContent: "", error: null }),

  appendToken: (text) =>
    set((state) => ({ streamingContent: state.streamingContent + text })),

  stopStreaming: () => {
    const { abortController } = get();
    if (abortController) abortController.abort();
    set({ isStreaming: false, streamingMessageId: null, streamingContent: "", abortController: null });
  },

  finishStreaming: () => {
    // Called when stream completes naturally (done event).
    // Does NOT abort — just clears streaming state.
    // Also resets reconnect counters because the stream completed successfully.
    set({
      isStreaming: false,
      streamingMessageId: null,
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

  setAbortController: (controller) => set({ abortController: controller }),

  setPendingMessage: (message) => set({ pendingMessage: message }),

  clearPendingMessage: () => set({ pendingMessage: null }),

  setSelectedAgentFamily: (family) => set({ selectedAgentFamily: family }),

  setSelectedAgent: (agent) =>
    set({ selectedAgent: agent, selectedAgentFamily: agent }),

  setModifiers: (mods) => set({ modifiers: mods }),

  openArtifactPanel: (artifactId) =>
    set({ isArtifactPanelOpen: true, activeArtifactId: artifactId }),

  closeArtifactPanel: () =>
    set({ isArtifactPanelOpen: false, activeArtifactId: null }),

  toggleArtifactPanel: () =>
    set((state) => ({
      isArtifactPanelOpen: !state.isArtifactPanelOpen,
      activeArtifactId: state.isArtifactPanelOpen ? null : state.activeArtifactId,
    })),

  resetAgentSelection: () =>
    set({ selectedAgentFamily: null, modifiers: [] }),

  setActiveTask: (taskId, taskType) =>
    set({ activeTaskId: taskId, activeTaskType: taskType }),

  clearActiveTask: () =>
    set({ activeTaskId: null, activeTaskType: null }),
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
    set({
      isStreaming: false,
      streamingMessageId: null,
      streamingContent: "",
      abortController: null,
      pendingFiles: [],
      pendingMessage: null,
      error: null,
      selectedAgentFamily: null,
      selectedAgent: null,
      modifiers: [],
      isArtifactPanelOpen: false,
      activeArtifactId: null,
      activeTaskId: null,
      activeTaskType: null,
      reconnectAttempts: 0,
      maxReconnectAttempts: 5,
      isReconnecting: false,
    }),
}));
