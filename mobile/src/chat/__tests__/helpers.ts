/// <reference types="node" />
// Test doubles for the send engine: a scripted ReadableStream (the server's
// SSE body) and an in-memory query cache. Node-only — never imported by the app.

import type { Message, MessageListResponse, SSEEventMap, SSEEventName } from "@contracts";

import type { ChatCacheAdapter, MessagesData, WorkspaceItemRef } from "../message-cache";
import { createChatEngine, type ChatEngineDeps, type OpenStream } from "../run-send";
import { createStreamStore, type StreamStore } from "../stream-store";

const encoder = new TextEncoder();

export function frame<K extends SSEEventName>(event: K, data: SSEEventMap[K]): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

export interface ScriptedStream {
  stream: ReadableStream<Uint8Array>;
  push(text: string): void;
  pushBytes(bytes: Uint8Array): void;
  close(): void;
  fail(err?: unknown): void;
  readonly cancelled: boolean;
}

export function scriptedStream(): ScriptedStream {
  let ctrl!: ReadableStreamDefaultController<Uint8Array>;
  let cancelled = false;
  let closed = false;
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      ctrl = c;
    },
    cancel() {
      cancelled = true;
    },
  });
  return {
    stream,
    push: (text) => {
      if (!closed && !cancelled) ctrl.enqueue(encoder.encode(text));
    },
    pushBytes: (bytes) => {
      if (!closed && !cancelled) ctrl.enqueue(bytes);
    },
    close: () => {
      if (closed || cancelled) return;
      closed = true;
      ctrl.close();
    },
    fail: (err = new TypeError("Network request failed")) => {
      if (closed || cancelled) return;
      closed = true;
      ctrl.error(err);
    },
    get cancelled() {
      return cancelled;
    },
  };
}

/** Let pending promise callbacks run (stream reads, awaits in the engine). */
export async function tick(times = 5): Promise<void> {
  for (let i = 0; i < times; i++) await new Promise<void>((r) => setImmediate(r));
}

export function msg(partial: Partial<Message> & Pick<Message, "message_id" | "role">): Message {
  return {
    conversation_id: "c1",
    content: "",
    attachments: [],
    created_at: new Date().toISOString(),
    ...partial,
  };
}

export class FakeCache implements ChatCacheAdapter {
  data = new Map<string, MessagesData>();
  log: string[] = [];
  /** What GET newest page answers (recovery). Throw to simulate offline. */
  server: (conversationId: string) => Promise<MessageListResponse> = async () => ({
    messages: [],
    has_more: false,
  });
  workspace: WorkspaceItemRef[] = [];

  seed(conversationId: string, messages: Message[]): void {
    this.data.set(conversationId, { pages: [{ messages, has_more: false }], pageParams: [undefined] });
  }

  rows(conversationId: string): Message[] {
    return this.data.get(conversationId)?.pages.flatMap((p) => p.messages) ?? [];
  }

  getMessages(id: string) {
    return this.data.get(id);
  }
  setMessages(id: string, updater: (old: MessagesData | undefined) => MessagesData | undefined) {
    const next = updater(this.data.get(id));
    if (next) this.data.set(id, next);
    else this.data.delete(id);
  }
  async cancelMessages(id: string) {
    this.log.push(`cancel:${id}`);
  }
  invalidateMessages(id: string) {
    this.log.push(`invalidate:messages:${id}`);
  }
  invalidateConversationLists() {
    this.log.push("invalidate:lists");
  }
  invalidateWorkspace(id: string) {
    this.log.push(`invalidate:workspace:${id}`);
  }
  invalidateWorkspaceItem(itemId: string) {
    this.log.push(`invalidate:item:${itemId}`);
  }
  fetchLatestMessages(id: string) {
    this.log.push(`fetch:${id}`);
    return this.server(id);
  }
  async lookupWorkspaceItems(_id: string, itemIds: string[]) {
    return this.workspace.filter((w) => itemIds.includes(w.item_id));
  }

  count(entry: string): number {
    return this.log.filter((l) => l === entry).length;
  }
}

export interface Harness {
  cache: FakeCache;
  store: StreamStore;
  engine: ReturnType<typeof createChatEngine>;
  /** One scripted stream per POST, in order. */
  streams: ScriptedStream[];
  posts: { conversationId: string; content: string }[];
  clock: { now: number };
}

export function harness(overrides: Partial<ChatEngineDeps> & { openStream?: OpenStream } = {}): Harness {
  const cache = new FakeCache();
  const clock = { now: Date.parse("2026-10-15T10:00:00Z") };
  const store = createStreamStore({ flushMs: 5, now: () => clock.now });
  const streams: ScriptedStream[] = [];
  const posts: Harness["posts"] = [];
  const openStream: OpenStream =
    overrides.openStream ??
    (async (conversationId, payload) => {
      posts.push({ conversationId, content: payload.content });
      const s = scriptedStream();
      streams.push(s);
      return s.stream;
    });
  const engine = createChatEngine({
    cache,
    store,
    now: () => clock.now,
    sleep: () => tick(1),
    adoptDelayMs: 1,
    idleTimeoutMs: 5_000,
    ...overrides,
    openStream: async (...args) => {
      if (overrides.openStream) posts.push({ conversationId: args[0], content: args[1].content });
      return openStream(...args);
    },
  });
  return { cache, store, engine, streams, posts, clock };
}
