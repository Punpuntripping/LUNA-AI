import {
  parseNextSteps,
  parseTemplatesUsed,
  PLACEHOLDER_LIVE_CAP_MS,
  type Attachment,
  type Message,
  type MessageListResponse,
  type NextStep,
  type SendMessagePayload,
  type SSETypedEvent,
  type TemplateUsed,
  type WorkspaceItemKind,
} from "@contracts";

import { MSG_UNKNOWN } from "@/api/errors";
import { readSSE, STREAM_IDLE_TIMEOUT_MS } from "@/api/sse-reader";
import { StreamAbortedError, StreamNotSentError } from "@/api/stream-errors";

import {
  addOptimistic,
  chronological,
  confirmUserMessage,
  ensureAssistantPlaceholder,
  findAdoptable,
  markFailed,
  mergeLatestPage,
  messageIds,
  removeMessage,
  writeFinalAssistant,
  type ChatCacheAdapter,
} from "./message-cache";
import type { AutoOpenKind, ChatNotice, StreamStore } from "./stream-store";

// X3 — the send state machine (port of frontend/hooks/use-chat.ts runSend).
// Pure: no React / React Native imports. The network (`openStream`) and the
// query cache are injected, so the unit tests drive it with a fake
// ReadableStream and an in-memory cache. Production wiring: chat-engine.ts.
//
// THE rule: once the POST may have left the phone, it is NEVER sent again by
// the app. The only re-POST is inside openMessageStream on a 401, which the
// backend raises before writing anything.
//
//   no message_start yet, stream lost  → wait 1.5 s → read the newest page →
//                                        our row is there? adopt (poll) : failed + «إعادة المحاولة»
//   after message_start, stream lost   → the run keeps going server-side
//                                        (detached) → «ريحان يفكّر» + poll
//   Stop                               → abort + invalidate (the run still
//                                        finishes and bills — Stop = detach)

export const MSG_SEND_FAILED = "تعذّر إرسال الرسالة — تحقق من اتصالك ثم أعد المحاولة";
export const MSG_RUN_FAILED = "حدث خطأ أثناء المعالجة";

/** Back-off before the adopt check (attempt n waits delay × 2^n). */
export const ADOPT_DELAY_MS = 1_500;
export const ADOPT_CHECKS = 3;
/** AppState → active with no byte for this long ⇒ the OS killed the socket. */
export const RESUME_STALE_MS = 10_000;

const ATTACHMENT_ONLY_CONTENT = "مرفق";
const AUTO_OPEN_KINDS: ReadonlySet<WorkspaceItemKind> = new Set<WorkspaceItemKind>(["agent_search", "agent_writing"]);

export type OpenStream = (
  conversationId: string,
  payload: SendMessagePayload,
  signal: AbortSignal,
) => Promise<ReadableStream<Uint8Array>>;

export interface ChatEngineDeps {
  openStream: OpenStream;
  cache: ChatCacheAdapter;
  store: StreamStore;
  now?: () => number;
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
  idleTimeoutMs?: number;
  adoptDelayMs?: number;
  adoptChecks?: number;
  resumeStaleMs?: number;
  newOptimisticId?: () => string;
}

export interface SendParams {
  conversationId: string;
  /** Exactly what the user typed (restored into the composer on a refusal). */
  content: string;
  /** Uploaded workspace item ids (2b). */
  attachmentIds?: string[];
  /** What the optimistic bubble shows for those attachments. */
  optimisticAttachments?: Attachment[];
  /** `message_start` (or an adopted send): the server took the send — release
   *  the composer chips now, never earlier. */
  onCommitted?: () => void;
}

export type RefusalReason = "quota" | "parallel_limit" | "duplicate";

export type SendResult =
  /** Answer complete and written into the cache. */
  | { status: "done"; assistantMessageId: string }
  /** The agent asked a question (`agent_question`); the reply is a new send. */
  | { status: "paused" }
  /** Server persisted nothing. Put `restoreText` back into the composer. */
  | { status: "refused"; reason: RefusalReason; notice: ChatNotice; restoreText: string | null }
  /** Error banner shown; a not-committed row is marked failed (retry). */
  | { status: "failed"; message: string }
  /** Committed, stream lost: the run finishes server-side, the list polls. */
  | { status: "detached" }
  /** Stream lost before `message_start`, but the server had the send. */
  | { status: "adopted"; settled: boolean }
  | { status: "stopped" }
  /** A newer send in the same conversation took over. */
  | { status: "superseded" }
  /** Nothing to send (empty text, no attachments) or unknown message. */
  | { status: "skipped" };

export interface ChatEngine {
  send(params: SendParams): Promise<SendResult>;
  /** Stop = abort + invalidate. The server keeps running the turn. */
  stop(conversationId: string): void;
  /** Re-send a failed user row's text (a NEW POST — server dedup still guards).
   *  The failed row is removed first; a quota / parallel refusal puts it BACK
   *  (still failed, «إعادة المحاولة» again) so its text is never lost (QA F4) —
   *  `restoreText` is then null (the composer may hold newer text). A
   *  `duplicate` refusal does not: the server already has that message. */
  retry(conversationId: string, messageId: string): Promise<SendResult>;
  /** Re-send the user message that preceded this assistant message. A refusal
   *  never restores text (`restoreText: null`): the question is still in the
   *  list and the user did not type anything. */
  regenerate(conversationId: string, assistantMessageId: string): Promise<SendResult>;
  /** AppState → active: streams silent for >10 s are dead — recover them. */
  onAppActive(): void;
  /** Feed every newest-page result (useMessages). Finishes recoveries and
   *  fires the auto-open for agent outputs of a recovered turn. */
  noteMessagesPage(conversationId: string, page: MessageListResponse): void;
  isSending(conversationId: string): boolean;
  /** Sign-out: abort everything, forget everything. */
  reset(): void;
}

interface SendCtx {
  conversationId: string;
  sendId: number;
  params: SendParams;
  /** What is POSTed (attachments-only → «مرفق»). */
  content: string;
  optimisticId: string;
  attachments: Attachment[];
  /** Aborts the HTTP stream (Stop, supersede, idle, resume). */
  stream: AbortController;
  /** Aborts the recovery waits (Stop, supersede only). */
  cancel: AbortController;
  cancelled: "stop" | "superseded" | null;
  dropped: "idle" | "resume" | null;
  recovering: boolean;
  committed: boolean;
  paused: boolean;
  assistantId: string | null;
  sentAt: number;
  lastByteAt: number;
  knownIds: Set<string>;
  hasBaseline: boolean;
  nextSteps: NextStep[];
  templatesUsed: TemplateUsed[] | undefined;
  outcome: SendResult | null;
}

interface RecoveryWatch {
  assistantId: string;
  since: number;
}

const ARABIC_LETTER = /[؀-ۿ]/;

function arabicOr(value: unknown, fallback: string): string {
  return typeof value === "string" && ARABIC_LETTER.test(value) ? value.trim() : fallback;
}

function defaultSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new StreamAbortedError());
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      reject(new StreamAbortedError());
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

let optimisticSeq = 0;

export function createChatEngine(deps: ChatEngineDeps): ChatEngine {
  const { openStream, cache, store } = deps;
  const now = deps.now ?? Date.now;
  const sleep = deps.sleep ?? defaultSleep;
  const idleTimeoutMs = deps.idleTimeoutMs ?? STREAM_IDLE_TIMEOUT_MS;
  const adoptDelayMs = deps.adoptDelayMs ?? ADOPT_DELAY_MS;
  const adoptChecks = deps.adoptChecks ?? ADOPT_CHECKS;
  const resumeStaleMs = deps.resumeStaleMs ?? RESUME_STALE_MS;
  const newOptimisticId = deps.newOptimisticId ?? (() => `optimistic-${now()}-${++optimisticSeq}`);

  const active = new Map<string, SendCtx>();
  const watches = new Map<string, RecoveryWatch>();
  let sendSeq = 0;

  const owns = (ctx: SendCtx) => active.get(ctx.conversationId) === ctx;

  /** The send is over for display: drop the live entry so the list (and the
   *  poll, which is off while a stream is registered) takes over. */
  const release = (ctx: SendCtx) => {
    if (owns(ctx)) active.delete(ctx.conversationId);
    store.finish(ctx.conversationId, ctx.sendId);
  };

  const committed = (ctx: SendCtx) => {
    if (ctx.committed) return;
    ctx.committed = true;
    ctx.params.onCommitted?.();
  };

  const fail = (ctx: SendCtx, message: string): SendResult => {
    release(ctx);
    if (!ctx.committed) cache.setMessages(ctx.conversationId, (d) => markFailed(d, ctx.optimisticId));
    store.setError(ctx.conversationId, message);
    return { status: "failed", message };
  };

  const refuse = (ctx: SendCtx, reason: RefusalReason, notice: ChatNotice): SendResult => {
    // Gate refusals run BEFORE any write: no server row will ever replace the
    // optimistic one, so drop it and hand the text back.
    release(ctx);
    cache.setMessages(ctx.conversationId, (d) => removeMessage(d, ctx.optimisticId));
    store.setNotice(ctx.conversationId, notice);
    return { status: "refused", reason, notice, restoreText: ctx.params.content.trim() ? ctx.params.content : null };
  };

  const watch = (conversationId: string, assistantId: string) => {
    watches.set(conversationId, { assistantId, since: now() });
  };

  const onCancelled = (ctx: SendCtx): SendResult => {
    if (ctx.cancelled === "superseded") {
      // The newer send owns the conversation now; touch only our own row.
      if (!ctx.committed) cache.setMessages(ctx.conversationId, (d) => removeMessage(d, ctx.optimisticId));
      return { status: "superseded" };
    }
    // Stop = detach: the run goes on server-side, keep its tracker (N4).
    if (ctx.committed && ctx.assistantId && !ctx.paused) store.park(ctx.conversationId, ctx.sendId);
    release(ctx);
    if (ctx.committed && ctx.assistantId && !ctx.paused) {
      const assistantId = ctx.assistantId;
      cache.setMessages(ctx.conversationId, (d) =>
        ensureAssistantPlaceholder(d, {
          id: assistantId,
          conversationId: ctx.conversationId,
          createdAt: new Date(now()).toISOString(),
        }),
      );
      watch(ctx.conversationId, assistantId);
    }
    cache.invalidateMessages(ctx.conversationId);
    cache.invalidateConversationLists();
    return { status: "stopped" };
  };

  /** Committed run, stream lost. */
  const detach = (ctx: SendCtx): SendResult => {
    if (ctx.paused) {
      release(ctx);
      cache.invalidateMessages(ctx.conversationId);
      return { status: "paused" };
    }
    // Before release (which drops the live entry): the placeholder's
    // «ريحان يفكّر» row keeps the last tracker state (QA N4).
    store.park(ctx.conversationId, ctx.sendId);
    release(ctx);
    if (ctx.assistantId) {
      const assistantId = ctx.assistantId;
      cache.setMessages(ctx.conversationId, (d) =>
        ensureAssistantPlaceholder(d, {
          id: assistantId,
          conversationId: ctx.conversationId,
          createdAt: new Date(now()).toISOString(),
        }),
      );
      watch(ctx.conversationId, assistantId);
    }
    cache.invalidateMessages(ctx.conversationId);
    cache.invalidateConversationLists();
    return { status: "detached" };
  };

  /** Not committed, stream lost: did the server take it anyway? */
  const recoverPreStart = async (ctx: SendCtx): Promise<SendResult> => {
    ctx.recovering = true;
    store.setPhase(ctx.conversationId, ctx.sendId, "recovering");
    for (let attempt = 0; attempt < adoptChecks; attempt++) {
      try {
        await sleep(adoptDelayMs * 2 ** attempt, ctx.cancel.signal);
      } catch {
        return onCancelled(ctx);
      }
      let fetched: MessageListResponse;
      try {
        fetched = await cache.fetchLatestMessages(ctx.conversationId);
      } catch {
        if (ctx.cancelled) return onCancelled(ctx);
        continue; // still offline — try again
      }
      if (ctx.cancelled) return onCancelled(ctx);
      const match = findAdoptable(fetched, {
        content: ctx.content,
        sentAtMs: ctx.sentAt,
        knownIds: ctx.knownIds,
        hasBaseline: ctx.hasBaseline,
      });
      if (!match) break;
      committed(ctx);
      const settled = !!match.assistant && (match.assistant.content ?? "").trim() !== "";
      if (match.assistant && !settled) watch(ctx.conversationId, match.assistant.message_id);
      // Release BEFORE the cache write: the write re-evaluates the list's
      // refetchInterval, which stays off while a stream is registered.
      release(ctx);
      cache.setMessages(ctx.conversationId, (d) => mergeLatestPage(d, fetched, [ctx.optimisticId]));
      cache.invalidateConversationLists();
      return { status: "adopted", settled };
    }
    return fail(ctx, MSG_SEND_FAILED);
  };

  const onStreamLost = (ctx: SendCtx): Promise<SendResult> | SendResult => {
    if (ctx.cancelled) return onCancelled(ctx);
    return ctx.committed ? detach(ctx) : recoverPreStart(ctx);
  };

  /** Returns true for a terminal event (stop reading). */
  const handleEvent = (ctx: SendCtx, ev: SSETypedEvent): boolean => {
    if (!owns(ctx) || ctx.cancelled) return true;
    const conv = ctx.conversationId;
    const sid = ctx.sendId;

    switch (ev.event) {
      case "message_start": {
        const d = ev.data;
        ctx.assistantId = d.assistant_message_id;
        committed(ctx);
        cache.setMessages(conv, (data) =>
          confirmUserMessage(data, ctx.optimisticId, {
            id: d.user_message_id,
            content: ctx.content,
            attachments: ctx.attachments,
            conversationId: conv,
            createdAt: new Date(now()).toISOString(),
          }),
        );
        store.start(conv, sid, d.assistant_message_id);
        return false;
      }
      case "token":
        store.appendToken(conv, sid, typeof ev.data.text === "string" ? ev.data.text : "");
        return false;
      case "duplicate": {
        const detail = arabicOr(ev.data.detail, MSG_RUN_FAILED);
        ctx.outcome = refuse(ctx, "duplicate", { kind: "duplicate", detail });
        // The earlier run is still answering — refetch so it shows (and polls).
        cache.invalidateMessages(conv);
        return true;
      }
      case "quota_exceeded":
        ctx.outcome = refuse(ctx, "quota", { kind: "quota", info: ev.data });
        return true;
      case "parallel_limit":
        ctx.outcome = refuse(ctx, "parallel_limit", { kind: "parallel_limit", info: ev.data });
        return true;
      case "error": {
        const message = arabicOr(ev.data.detail, MSG_RUN_FAILED);
        ctx.outcome = fail(ctx, message);
        if (ctx.committed) {
          cache.invalidateMessages(conv);
          cache.invalidateConversationLists();
        }
        return true;
      }
      case "done": {
        const d = ev.data;
        const assistantId = ctx.assistantId ?? d.message_id;
        store.flush(conv);
        if (!ctx.paused && assistantId) {
          store.seal(conv, sid, assistantId);
          const content = store.getState().streams[conv]?.content ?? "";
          if (content) {
            cache.setMessages(conv, (data) =>
              writeFinalAssistant(data, {
                id: assistantId,
                conversationId: conv,
                content,
                artifactIds: d.artifact_ids ?? null,
                referencedItemIds: d.referenced_item_ids ?? null,
                nextSteps: ctx.nextSteps,
                templatesUsed: ctx.templatesUsed,
                createdAt: new Date(now()).toISOString(),
              }),
            );
          }
        }
        release(ctx);
        cache.invalidateMessages(conv);
        cache.invalidateConversationLists();
        if (d.artifact_ids?.length) cache.invalidateWorkspace(conv);
        ctx.outcome = ctx.paused || !assistantId ? { status: "paused" } : { status: "done", assistantMessageId: assistantId };
        return true;
      }
      case "agent_question":
        // The question row is persisted server-side and arrives with the
        // post-`done` refetch. Tear down the live bubble + tracker now; the
        // pause seals nothing.
        ctx.paused = true;
        store.agentFinished(conv, sid);
        store.finish(conv, sid);
        return false;
      case "agent_progress":
        store.progress(conv, sid, ev.data);
        if (ev.data.stage === "done" && ctx.assistantId) store.seal(conv, sid, ctx.assistantId);
        return false;
      case "status":
        store.status(conv, sid, typeof ev.data.text === "string" ? ev.data.text : "");
        return false;
      case "agent_run_started":
        store.agentStarted(conv, sid, ev.data.agent_family ?? null, ev.data.subtype ?? null);
        return false;
      case "agent_resumed":
        store.agentStarted(conv, sid, ev.data.agent_family ?? null, null);
        return false;
      case "agent_run_finished":
        store.agentFinished(conv, sid);
        return false;
      case "workspace_item_created": {
        cache.invalidateWorkspace(conv);
        const { item_id: itemId, kind } = ev.data;
        if (itemId && kind && AUTO_OPEN_KINDS.has(kind)) {
          store.requestAutoOpen(conv, { itemId, kind: kind as AutoOpenKind, title: ev.data.title ?? "" });
        }
        return false;
      }
      case "workspace_item_updated":
      case "workspace_item_locked":
      case "workspace_item_unlocked":
        cache.invalidateWorkspace(conv);
        if (ev.data.item_id) cache.invalidateWorkspaceItem(ev.data.item_id);
        return false;
      case "referenced_existing_item":
        if (ctx.assistantId && ev.data.item_id) store.recordReferencedItem(ctx.assistantId, ev.data.item_id);
        return false;
      case "next_steps": {
        const items = parseNextSteps(ev.data.items);
        if (items.length > 0) ctx.nextSteps = items;
        return false;
      }
      case "templates_used": {
        const items = parseTemplatesUsed(ev.data.items);
        if (items !== undefined) ctx.templatesUsed = items;
        return false;
      }
      case "heartbeat":
      case "agent_selected":
        return false;
      default:
        return false;
    }
  };

  const run = async (ctx: SendCtx): Promise<SendResult> => {
    const conv = ctx.conversationId;
    await cache.cancelMessages(conv);
    if (ctx.cancelled) return onCancelled(ctx);

    const before = cache.getMessages(conv);
    ctx.knownIds = messageIds(before);
    ctx.hasBaseline = !!before;
    const optimistic: Message = {
      message_id: ctx.optimisticId,
      conversation_id: conv,
      role: "user",
      content: ctx.content,
      attachments: ctx.attachments,
      created_at: new Date(now()).toISOString(),
      isOptimistic: true,
    };
    cache.setMessages(conv, (d) => addOptimistic(d, optimistic));
    ctx.sentAt = now();
    ctx.lastByteAt = ctx.sentAt;

    let body: ReadableStream<Uint8Array>;
    try {
      body = await openStream(
        conv,
        { content: ctx.content, attachment_ids: ctx.params.attachmentIds?.length ? ctx.params.attachmentIds : null },
        ctx.stream.signal,
      );
    } catch (err) {
      if (ctx.cancelled) return onCancelled(ctx);
      if (err instanceof StreamNotSentError) return fail(ctx, err.message || MSG_UNKNOWN);
      return onStreamLost(ctx);
    }
    if (ctx.cancelled) {
      body.cancel().catch(() => undefined);
      return onCancelled(ctx);
    }

    try {
      await readSSE(body, (ev) => handleEvent(ctx, ev), {
        signal: ctx.stream.signal,
        idleTimeoutMs,
        onBytes: () => {
          ctx.lastByteAt = now();
        },
        onIdle: () => {
          ctx.dropped = "idle";
          ctx.stream.abort();
        },
      });
    } catch {
      if (ctx.outcome) return ctx.outcome;
      return onStreamLost(ctx);
    }
    if (ctx.outcome) return ctx.outcome;
    if (ctx.cancelled) return onCancelled(ctx);
    // Closed without a terminal event (proxy cut, deploy restart).
    return onStreamLost(ctx);
  };

  const engine: ChatEngine = {
    async send(params) {
      const attachments = params.optimisticAttachments ?? [];
      const hasAttachments = attachments.length > 0 || (params.attachmentIds?.length ?? 0) > 0;
      const content = params.content.trim() ? params.content : hasAttachments ? ATTACHMENT_ONLY_CONTENT : "";
      if (!content) return { status: "skipped" };

      const conv = params.conversationId;
      const prev = active.get(conv);
      if (prev) {
        prev.cancelled = "superseded";
        prev.stream.abort();
        prev.cancel.abort();
      }

      sendSeq += 1;
      const ctx: SendCtx = {
        conversationId: conv,
        sendId: sendSeq,
        params,
        content,
        optimisticId: newOptimisticId(),
        attachments,
        stream: new AbortController(),
        cancel: new AbortController(),
        cancelled: null,
        dropped: null,
        recovering: false,
        committed: false,
        paused: false,
        assistantId: null,
        sentAt: now(),
        lastByteAt: now(),
        knownIds: new Set(),
        hasBaseline: false,
        nextSteps: [],
        templatesUsed: undefined,
        outcome: null,
      };
      active.set(conv, ctx);
      store.begin(conv, ctx.sendId);
      try {
        return await run(ctx);
      } finally {
        release(ctx);
      }
    },

    stop(conversationId) {
      const ctx = active.get(conversationId);
      if (!ctx) return;
      ctx.cancelled = "stop";
      ctx.stream.abort();
      ctx.cancel.abort();
    },

    async retry(conversationId, messageId) {
      const failed = chronological(cache.getMessages(conversationId)).find((m) => m.message_id === messageId);
      if (!failed || failed.role !== "user") return { status: "skipped" };
      cache.setMessages(conversationId, (d) => removeMessage(d, messageId));
      store.setError(conversationId, null);
      const ids = failed.attachments.map((a) => a.document_id).filter(Boolean);
      const result = await engine.send({
        conversationId,
        content: failed.content,
        attachmentIds: ids.length ? ids : undefined,
        optimisticAttachments: failed.attachments,
      });
      if (result.status !== "refused" || result.reason === "duplicate") return result;
      // Not optimistic (same as markFailed): an adopt merge drops optimistic rows.
      cache.setMessages(conversationId, (d) => addOptimistic(d, { ...failed, isOptimistic: false, isFailed: true }));
      return { ...result, restoreText: null };
    },

    async regenerate(conversationId, assistantMessageId) {
      const data = cache.getMessages(conversationId);
      // A detached run we are still waiting for is NOT dead: re-sending its
      // question would be the duplicate of a send the server already has (QA
      // N2). Only the server (`run_active: false`) or the age cap can kill it.
      const w = watches.get(conversationId);
      if (
        w?.assistantId === assistantMessageId &&
        data?.pages[0]?.run_active !== false &&
        now() - w.since <= PLACEHOLDER_LIVE_CAP_MS
      ) {
        const row = chronological(data).find((m) => m.message_id === assistantMessageId);
        if (row && (row.content ?? "").trim() === "") return { status: "skipped" };
      }
      const msgs = chronological(data);
      const idx = msgs.findIndex((m) => m.message_id === assistantMessageId);
      for (let i = idx - 1; i >= 0; i--) {
        if (msgs[i].role !== "user") continue;
        const result = await engine.send({ conversationId, content: msgs[i].content });
        return result.status === "refused" ? { ...result, restoreText: null } : result;
      }
      return { status: "skipped" };
    },

    onAppActive() {
      const t = now();
      for (const ctx of active.values()) {
        if (ctx.recovering || ctx.cancelled || ctx.dropped) continue;
        if (t - ctx.lastByteAt > resumeStaleMs) {
          ctx.dropped = "resume";
          ctx.stream.abort();
        }
      }
    },

    noteMessagesPage(conversationId, page) {
      const w = watches.get(conversationId);
      if (!w) return;
      const row = page.messages.find((m) => m.message_id === w.assistantId);
      if (!row) {
        // Gone (a paused turn deletes its placeholder) — nothing to wait for.
        watches.delete(conversationId);
        store.unpark(conversationId, w.assistantId, false);
        return;
      }
      if ((row.content ?? "").trim() === "") {
        if (now() - w.since > PLACEHOLDER_LIVE_CAP_MS) {
          watches.delete(conversationId);
          store.unpark(conversationId, w.assistantId, false);
        }
        return;
      }
      watches.delete(conversationId);
      // Answered while detached: the frozen tracker becomes the summary chip.
      store.unpark(conversationId, w.assistantId, true);
      const ids = row.artifact_ids ?? [];
      if (ids.length === 0) return;
      cache
        .lookupWorkspaceItems(conversationId, ids)
        .then((items) => {
          for (const item of items) {
            if (item.kind && AUTO_OPEN_KINDS.has(item.kind)) {
              store.requestAutoOpen(conversationId, {
                itemId: item.item_id,
                kind: item.kind as AutoOpenKind,
                title: item.title,
              });
            }
          }
        })
        .catch(() => undefined);
    },

    isSending(conversationId) {
      return active.has(conversationId);
    },

    reset() {
      for (const ctx of active.values()) {
        ctx.cancelled = "superseded";
        ctx.stream.abort();
        ctx.cancel.abort();
      }
      active.clear();
      watches.clear();
      store.reset();
    },
  };

  return engine;
}
