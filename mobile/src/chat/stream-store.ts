import { useStore } from "zustand";
import { createStore, type StoreApi } from "zustand/vanilla";

import type { AgentFamily, SSEAgentProgress, SSEParallelLimit, SSEQuotaExceeded } from "@contracts";

import {
  applyProgress,
  applyStatus,
  sealSummary,
  type DeepSearchProgressState,
  type DeepSearchSummary,
} from "./deep-search-progress";

// Live state of in-flight sends, keyed per conversation (parallel
// conversations never see each other). Server truth stays in TanStack Query;
// this holds only what exists while a stream is open, plus per-conversation
// banners and the auto-open signal.
//
// Render isolation (X4): tokens land in a plain buffer and are flushed into
// `content` at most every ~80 ms, all conversations in ONE set(). Only the
// streaming row should subscribe to `content` (useStreamContent); everything
// else selects narrow fields.

/** `sending` = POST out, no `message_start` yet · `streaming` = the run is
 *  committed and the live bubble renders · `recovering` = the stream dropped
 *  before `message_start`, checking whether the server took the send. */
export type StreamPhase = "sending" | "streaming" | "recovering";

export interface ConversationStream {
  /** Owner of this entry. Every mutation carries it; a superseded send's
   *  late calls no-op. */
  sendId: number;
  phase: StreamPhase;
  assistantMessageId: string | null;
  /** Revealed answer text (flushed from the token buffer). */
  content: string;
  agentFamily: AgentFamily | null;
  agentSubtype: string | null;
  /** Live deep_search tracker (null for other families). */
  progress: DeepSearchProgressState | null;
  /** Parked by `agent_run_finished` so `done` can still seal the chip. */
  sealable: DeepSearchProgressState | null;
}

/** The last live state of a committed run whose stream dropped (background,
 *  network, Stop). The backend has no progress endpoint — the web shows only
 *  the generic phrase after a reload — so the «ريحان يفكّر» row of THIS
 *  placeholder keeps the tracker as it last was instead of losing it (QA N4).
 *  Frozen: no new stages arrive until the poll brings the answer. */
export interface ParkedRun {
  assistantMessageId: string;
  agentFamily: AgentFamily | null;
  progress: DeepSearchProgressState | null;
}

export type ChatNotice =
  | { kind: "quota"; info: SSEQuotaExceeded }
  | { kind: "parallel_limit"; info: SSEParallelLimit }
  | { kind: "duplicate"; detail: string };

export type AutoOpenKind = "agent_search" | "agent_writing";

/** «افتح المخرج تلقائياً» — owner decision 2026-10-15: an agent output opens
 *  as a sheet over the chat, once per item. The chat screen consumes it. */
export interface AutoOpenSignal {
  itemId: string;
  kind: AutoOpenKind;
  title: string;
  /** Monotonic, so the same screen can tell two signals apart. */
  seq: number;
}

export interface StreamStoreState {
  streams: Record<string, ConversationStream>;
  /** Arabic banner per conversation (failed send / run error). */
  errors: Record<string, string>;
  notices: Record<string, ChatNotice>;
  /** Sealed deep_search chips, by assistant message id. */
  summaries: Record<string, DeepSearchSummary>;
  /** `referenced_existing_item` ids, by assistant message id. */
  referencedItems: Record<string, string[]>;
  /** Pending auto-open, by conversation. */
  autoOpen: Record<string, AutoOpenSignal>;
  /** Detached runs still answering server-side, by conversation. */
  parked: Record<string, ParkedRun>;
}

export interface StreamStore {
  api: StoreApi<StreamStoreState>;
  getState: () => StreamStoreState;
  isActive: (conversationId: string) => boolean;
  begin: (conversationId: string, sendId: number) => void;
  setPhase: (conversationId: string, sendId: number, phase: StreamPhase) => void;
  start: (conversationId: string, sendId: number, assistantMessageId: string) => void;
  appendToken: (conversationId: string, sendId: number, text: string) => void;
  /** Publish this conversation's buffered tokens now (`done` calls it first). */
  flush: (conversationId: string) => void;
  /** Drop the entry (and its unrevealed buffer) iff `sendId` still owns it. */
  finish: (conversationId: string, sendId: number) => void;
  agentStarted: (conversationId: string, sendId: number, family: AgentFamily | null, subtype: string | null) => void;
  agentFinished: (conversationId: string, sendId: number) => void;
  progress: (conversationId: string, sendId: number, event: SSEAgentProgress) => void;
  status: (conversationId: string, sendId: number, text: string) => void;
  seal: (conversationId: string, sendId: number, messageId: string) => void;
  /** The stream of a committed run dropped: keep its agent + tracker for the
   *  placeholder (call BEFORE `finish`). */
  park: (conversationId: string, sendId: number) => void;
  /** The parked run of `assistantMessageId` is over. `seal` = it answered:
   *  turn the frozen tracker into the summary chip, as a live `done` would. */
  unpark: (conversationId: string, assistantMessageId: string, seal: boolean) => void;
  setError: (conversationId: string, message: string | null) => void;
  setNotice: (conversationId: string, notice: ChatNotice | null) => void;
  recordReferencedItem: (messageId: string, itemId: string) => void;
  /** False when this item already opened (or was requested) this session. */
  requestAutoOpen: (conversationId: string, signal: Omit<AutoOpenSignal, "seq">) => boolean;
  consumeAutoOpen: (conversationId: string, itemId: string) => void;
  /** Sign-out / user switch: drop everything. */
  reset: () => void;
}

export interface StreamStoreOptions {
  flushMs?: number;
  now?: () => number;
}

export const TOKEN_FLUSH_MS = 80;

const initialState = (): StreamStoreState => ({
  streams: {},
  errors: {},
  notices: {},
  summaries: {},
  referencedItems: {},
  autoOpen: {},
  parked: {},
});

function without<V>(map: Record<string, V>, key: string): Record<string, V> {
  if (!(key in map)) return map;
  const next = { ...map };
  delete next[key];
  return next;
}

export function createStreamStore(opts: StreamStoreOptions = {}): StreamStore {
  const flushMs = opts.flushMs ?? TOKEN_FLUSH_MS;
  const now = opts.now ?? Date.now;
  const api = createStore<StreamStoreState>()(initialState);
  const { getState, setState } = api;

  const buffers = new Map<string, string>();
  let timer: ReturnType<typeof setTimeout> | null = null;
  const opened = new Set<string>();
  let autoSeq = 0;

  /** Patch the entry iff `sendId` owns it. */
  const patch = (
    conversationId: string,
    sendId: number,
    fn: (cur: ConversationStream) => Partial<ConversationStream>,
  ) => {
    const cur = getState().streams[conversationId];
    if (!cur || cur.sendId !== sendId) return;
    setState((s) => ({ streams: { ...s.streams, [conversationId]: { ...cur, ...fn(cur) } } }));
  };

  const flushAll = () => {
    timer = null;
    if (buffers.size === 0) return;
    const pending = new Map(buffers);
    buffers.clear();
    setState((s) => {
      const streams = { ...s.streams };
      for (const [id, text] of pending) {
        const cur = streams[id];
        if (cur) streams[id] = { ...cur, content: cur.content + text };
      }
      return { streams };
    });
  };

  const dropBuffer = (conversationId: string) => {
    buffers.delete(conversationId);
    if (buffers.size === 0 && timer) {
      clearTimeout(timer);
      timer = null;
    }
  };

  return {
    api,
    getState,
    isActive: (conversationId) => conversationId in getState().streams,

    begin: (conversationId, sendId) => {
      dropBuffer(conversationId);
      setState((s) => ({
        streams: {
          ...s.streams,
          [conversationId]: {
            sendId,
            phase: "sending",
            assistantMessageId: null,
            content: "",
            agentFamily: null,
            agentSubtype: null,
            progress: null,
            sealable: null,
          },
        },
        // A new attempt replaces the previous banner of THIS conversation.
        errors: without(s.errors, conversationId),
        notices: without(s.notices, conversationId),
        parked: without(s.parked, conversationId),
      }));
    },

    setPhase: (conversationId, sendId, phase) => patch(conversationId, sendId, () => ({ phase })),

    start: (conversationId, sendId, assistantMessageId) => {
      dropBuffer(conversationId);
      patch(conversationId, sendId, () => ({
        phase: "streaming",
        assistantMessageId,
        content: "",
        progress: null,
        sealable: null,
      }));
    },

    appendToken: (conversationId, sendId, text) => {
      const cur = getState().streams[conversationId];
      if (!cur || cur.sendId !== sendId || cur.phase !== "streaming" || !text) return;
      buffers.set(conversationId, (buffers.get(conversationId) ?? "") + text);
      if (!timer) timer = setTimeout(flushAll, flushMs);
    },

    flush: (conversationId) => {
      const rest = buffers.get(conversationId);
      dropBuffer(conversationId);
      if (!rest) return;
      setState((s) => {
        const cur = s.streams[conversationId];
        return cur ? { streams: { ...s.streams, [conversationId]: { ...cur, content: cur.content + rest } } } : s;
      });
    },

    finish: (conversationId, sendId) => {
      const cur = getState().streams[conversationId];
      if (!cur || cur.sendId !== sendId) return;
      dropBuffer(conversationId);
      setState((s) => ({ streams: without(s.streams, conversationId) }));
    },

    agentStarted: (conversationId, sendId, family, subtype) =>
      patch(conversationId, sendId, () => ({ agentFamily: family, agentSubtype: subtype })),

    agentFinished: (conversationId, sendId) =>
      patch(conversationId, sendId, (cur) => ({
        agentFamily: null,
        agentSubtype: null,
        progress: null,
        sealable: cur.progress ?? cur.sealable,
      })),

    progress: (conversationId, sendId, event) =>
      patch(conversationId, sendId, (cur) => ({ progress: applyProgress(cur.progress, event, now()) })),

    status: (conversationId, sendId, text) =>
      patch(conversationId, sendId, (cur) => ({ progress: applyStatus(cur.progress, text) })),

    seal: (conversationId, sendId, messageId) => {
      const cur = getState().streams[conversationId];
      if (!cur || cur.sendId !== sendId || !messageId) return;
      const run = cur.progress ?? cur.sealable;
      if (!run) return;
      setState((s) => ({ summaries: { ...s.summaries, [messageId]: sealSummary(run, now()) } }));
    },

    park: (conversationId, sendId) => {
      const cur = getState().streams[conversationId];
      if (!cur || cur.sendId !== sendId || !cur.assistantMessageId) return;
      // After agent_run_finished the run sits in `sealable` (answer tokens next).
      const progress = cur.progress ?? cur.sealable;
      const agentFamily = cur.agentFamily ?? (progress ? "deep_search" : null);
      if (!agentFamily && !progress) return;
      const run: ParkedRun = { assistantMessageId: cur.assistantMessageId, agentFamily, progress };
      setState((s) => ({ parked: { ...s.parked, [conversationId]: run } }));
    },

    unpark: (conversationId, assistantMessageId, seal) => {
      const run = getState().parked[conversationId];
      if (!run || run.assistantMessageId !== assistantMessageId) return;
      setState((s) => ({
        parked: without(s.parked, conversationId),
        summaries:
          seal && run.progress && !s.summaries[assistantMessageId]
            ? { ...s.summaries, [assistantMessageId]: sealSummary(run.progress, now()) }
            : s.summaries,
      }));
    },

    setError: (conversationId, message) =>
      setState((s) => ({
        errors: message === null ? without(s.errors, conversationId) : { ...s.errors, [conversationId]: message },
      })),

    setNotice: (conversationId, notice) =>
      setState((s) => ({
        notices: notice === null ? without(s.notices, conversationId) : { ...s.notices, [conversationId]: notice },
      })),

    recordReferencedItem: (messageId, itemId) =>
      setState((s) => {
        const cur = s.referencedItems[messageId] ?? [];
        if (cur.includes(itemId)) return s;
        return { referencedItems: { ...s.referencedItems, [messageId]: [...cur, itemId] } };
      }),

    requestAutoOpen: (conversationId, signal) => {
      if (opened.has(signal.itemId)) return false;
      opened.add(signal.itemId);
      autoSeq += 1;
      const next: AutoOpenSignal = { ...signal, seq: autoSeq };
      setState((s) => ({ autoOpen: { ...s.autoOpen, [conversationId]: next } }));
      return true;
    },

    consumeAutoOpen: (conversationId, itemId) =>
      setState((s) =>
        s.autoOpen[conversationId]?.itemId === itemId ? { autoOpen: without(s.autoOpen, conversationId) } : s,
      ),

    reset: () => {
      buffers.clear();
      if (timer) clearTimeout(timer);
      timer = null;
      opened.clear();
      setState(initialState());
    },
  };
}

/** The app's one store (tests build their own with createStreamStore). */
export const streamStore = createStreamStore();

function useStreamSelector<T>(selector: (s: StreamStoreState) => T): T {
  return useStore(streamStore.api, selector);
}

/** For the streaming row ONLY — re-renders on every ~80 ms flush. */
export function useStreamContent(conversationId: string): string | null {
  return useStreamSelector((s) => s.streams[conversationId]?.content ?? null);
}

export function useStreamPhase(conversationId: string): StreamPhase | null {
  return useStreamSelector((s) => s.streams[conversationId]?.phase ?? null);
}

/** The assistant message being streamed right now (null before message_start). */
export function useStreamingMessageId(conversationId: string): string | null {
  return useStreamSelector((s) => s.streams[conversationId]?.assistantMessageId ?? null);
}

/** «يعمل» — this conversation has a send in flight in this app. */
export function useIsConversationRunning(conversationId: string): boolean {
  return useStreamSelector((s) => conversationId in s.streams);
}

/** The live stream's agent, else the parked one of a detached run. */
export function selectRunningAgent(s: StreamStoreState, conversationId: string): AgentFamily | null {
  const live = s.streams[conversationId];
  if (live) return live.agentFamily;
  return s.parked[conversationId]?.agentFamily ?? null;
}

/** The live tracker, else the last known one of a detached run (frozen). */
export function selectDeepSearchProgress(s: StreamStoreState, conversationId: string): DeepSearchProgressState | null {
  const live = s.streams[conversationId];
  if (live) return live.progress;
  return s.parked[conversationId]?.progress ?? null;
}

export function useRunningAgent(conversationId: string): AgentFamily | null {
  return useStreamSelector((s) => selectRunningAgent(s, conversationId));
}

/** Deep-search tracker — subscribe from the tracker component only. */
export function useDeepSearchProgress(conversationId: string): DeepSearchProgressState | null {
  return useStreamSelector((s) => selectDeepSearchProgress(s, conversationId));
}

export function useDeepSearchSummary(messageId: string): DeepSearchSummary | null {
  return useStreamSelector((s) => s.summaries[messageId] ?? null);
}

export function useChatError(conversationId: string): string | null {
  return useStreamSelector((s) => s.errors[conversationId] ?? null);
}

export function useChatNotice(conversationId: string): ChatNotice | null {
  return useStreamSelector((s) => s.notices[conversationId] ?? null);
}

export function useReferencedItems(messageId: string): string[] | null {
  return useStreamSelector((s) => s.referencedItems[messageId] ?? null);
}

/** Pending auto-open for this conversation; call `streamStore.consumeAutoOpen`
 *  once the sheet is shown. */
export function useAutoOpen(conversationId: string): AutoOpenSignal | null {
  return useStreamSelector((s) => s.autoOpen[conversationId] ?? null);
}

/** How many conversations have a send in flight in this app (parallel cap). */
export function useStreamCount(): number {
  return useStreamSelector((s) => Object.keys(s.streams).length);
}
