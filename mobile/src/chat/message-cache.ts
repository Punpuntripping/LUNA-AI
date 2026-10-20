import type {
  Attachment,
  Message,
  MessageListResponse,
  MessageMetadata,
  NextStep,
  TemplateUsed,
  WorkspaceItemKind,
} from "@contracts";

// Pure transforms over the cached messages of ONE conversation (the TanStack
// infinite data under `messageKeys.list(id)`): pages newest → oldest, messages
// inside each page newest first — exactly what the API returns. Every function
// returns a new object (or `old` untouched) — never mutates.

export interface MessagesData {
  pages: MessageListResponse[];
  pageParams: (string | undefined)[];
}

/** What the send engine needs from the query cache. Production wiring is in
 *  chat-engine.ts (TanStack QueryClient); the unit tests use an in-memory fake. */
export interface ChatCacheAdapter {
  getMessages(conversationId: string): MessagesData | undefined;
  setMessages(conversationId: string, updater: (old: MessagesData | undefined) => MessagesData | undefined): void;
  /** Cancel in-flight message fetches so they cannot overwrite an optimistic write. */
  cancelMessages(conversationId: string): Promise<void>;
  invalidateMessages(conversationId: string): void;
  invalidateConversationLists(): void;
  invalidateWorkspace(conversationId: string): void;
  /** One item's detail (the output sheet / item screen) — writer updates, locks. */
  invalidateWorkspaceItem(itemId: string): void;
  /** Fresh read of the newest page (never served from cache). */
  fetchLatestMessages(conversationId: string): Promise<MessageListResponse>;
  /** The conversation's workspace items with these ids (kind + title). */
  lookupWorkspaceItems(conversationId: string, itemIds: string[]): Promise<WorkspaceItemRef[]>;
}

export interface WorkspaceItemRef {
  item_id: string;
  /** May be null on a malformed row — never trusted blindly. */
  kind: WorkspaceItemKind | null;
  title: string;
}

const emptyData = (): MessagesData => ({ pages: [{ messages: [], has_more: false }], pageParams: [undefined] });

function mapMessages(data: MessagesData, fn: (m: Message) => Message | null): MessagesData {
  return {
    ...data,
    pages: data.pages.map((page) => ({
      ...page,
      messages: page.messages.flatMap((m) => {
        const next = fn(m);
        return next ? [next] : [];
      }),
    })),
  };
}

function prependToFirstPage(data: MessagesData | undefined, message: Message): MessagesData {
  const base = data && data.pages.length > 0 ? data : emptyData();
  const pages = [...base.pages];
  pages[0] = { ...pages[0], messages: [message, ...pages[0].messages] };
  return { ...base, pages };
}

export function hasMessage(data: MessagesData | undefined, id: string): boolean {
  return !!data?.pages.some((p) => p.messages.some((m) => m.message_id === id));
}

/** Every message id currently cached (the "known before this send" baseline). */
export function messageIds(data: MessagesData | undefined): Set<string> {
  const ids = new Set<string>();
  for (const page of data?.pages ?? []) for (const m of page.messages) ids.add(m.message_id);
  return ids;
}

/** Oldest → newest, across all pages. */
export function chronological(data: MessagesData | undefined): Message[] {
  const out: Message[] = [];
  const pages = data?.pages ?? [];
  for (let i = pages.length - 1; i >= 0; i--) out.push(...[...pages[i].messages].reverse());
  return out;
}

export function newestMessage(data: MessagesData | undefined): Message | undefined {
  return data?.pages[0]?.messages[0];
}

export function addOptimistic(data: MessagesData | undefined, message: Message): MessagesData {
  return prependToFirstPage(data, message);
}

/**
 * `message_start`: the optimistic row becomes the real user row. When an
 * empty first fetch of a brand-new conversation already wiped the optimistic
 * row, re-add it so the user's message never disappears mid-run.
 */
export function confirmUserMessage(
  data: MessagesData | undefined,
  optimisticId: string,
  real: { id: string; content: string; attachments: Attachment[]; conversationId: string; createdAt: string },
): MessagesData {
  if (data && (hasMessage(data, optimisticId) || hasMessage(data, real.id))) {
    return mapMessages(data, (m) =>
      m.message_id === optimisticId ? { ...m, message_id: real.id, isOptimistic: false } : m,
    );
  }
  return prependToFirstPage(data, {
    message_id: real.id,
    conversation_id: real.conversationId,
    role: "user",
    content: real.content,
    attachments: real.attachments,
    created_at: real.createdAt,
  });
}

export function markFailed(data: MessagesData | undefined, id: string): MessagesData | undefined {
  if (!data) return data;
  return mapMessages(data, (m) => (m.message_id === id ? { ...m, isFailed: true, isOptimistic: false } : m));
}

export function removeMessage(data: MessagesData | undefined, id: string): MessagesData | undefined {
  if (!data) return data;
  return mapMessages(data, (m) => (m.message_id === id ? null : m));
}

/** Merge live chips into metadata without touching its other keys. `[]`
 *  next-steps = nothing to add; `templates_used: []` IS meaningful (drafted
 *  without a template) and `undefined` leaves the key absent. */
export function withLiveMetadata(
  metadata: MessageMetadata | undefined,
  nextSteps: NextStep[],
  templatesUsed: TemplateUsed[] | undefined,
): MessageMetadata | undefined {
  let out = metadata;
  if (nextSteps.length > 0) out = { ...(out ?? {}), next_steps: nextSteps };
  if (templatesUsed !== undefined) out = { ...(out ?? {}), templates_used: templatesUsed };
  return out;
}

export interface FinalAssistant {
  id: string;
  conversationId: string;
  content: string;
  artifactIds: string[] | null;
  referencedItemIds: string[] | null;
  nextSteps: NextStep[];
  templatesUsed: TemplateUsed[] | undefined;
  createdAt: string;
}

/**
 * `done`: put the final answer into the cache BEFORE the live bubble is
 * cleared (no flash of «ريحان يفكّر» between the two). Fills the empty
 * placeholder in place when a refetch already brought it in; a row that
 * already has content (the refetch beat us) is left alone.
 */
export function writeFinalAssistant(data: MessagesData | undefined, final: FinalAssistant): MessagesData {
  if (data && hasMessage(data, final.id)) {
    return mapMessages(data, (m) =>
      m.message_id === final.id && (m.content ?? "").trim() === ""
        ? {
            ...m,
            content: final.content,
            artifact_ids: final.artifactIds,
            referenced_item_ids: final.referencedItemIds,
            metadata: withLiveMetadata(m.metadata, final.nextSteps, final.templatesUsed),
          }
        : m,
    );
  }
  return prependToFirstPage(data, {
    message_id: final.id,
    conversation_id: final.conversationId,
    role: "assistant",
    content: final.content,
    attachments: [],
    created_at: final.createdAt,
    artifact_ids: final.artifactIds,
    referenced_item_ids: final.referencedItemIds,
    metadata: withLiveMetadata(undefined, final.nextSteps, final.templatesUsed),
  });
}

/**
 * A committed run lost its stream (drop / Stop / background): make sure the
 * empty assistant row exists so the list shows «ريحان يفكّر» and the poll
 * (placeholderPollMs on the newest row) starts right away.
 *
 * The newest page's `run_active` is the server's snapshot from the LAST fetch
 * — usually taken before this send, when nothing ran (`false`). Left as is,
 * `isDeadPlaceholder` reads that stale `false` and flips the fresh placeholder
 * to «تعذّر إكمال الرد» + «إعادة المحاولة» at once; while offline no refetch
 * corrects it, and its retry (= regenerate) re-POSTs the question (QA N2). We
 * KNOW the server owns this run, so mark it live until the next fetch answers.
 */
export function ensureAssistantPlaceholder(
  data: MessagesData | undefined,
  placeholder: { id: string; conversationId: string; createdAt: string },
): MessagesData {
  if (data && hasMessage(data, placeholder.id)) return markRunActive(data);
  return markRunActive(
    prependToFirstPage(data, {
      message_id: placeholder.id,
      conversation_id: placeholder.conversationId,
      role: "assistant",
      content: "",
      attachments: [],
      created_at: placeholder.createdAt,
    }),
  );
}

function markRunActive(data: MessagesData): MessagesData {
  if (data.pages.length === 0 || data.pages[0].run_active === true) return data;
  const pages = [...data.pages];
  pages[0] = { ...pages[0], run_active: true };
  return { ...data, pages };
}

/**
 * Fold a freshly fetched newest page into the cache. The fetched rows win;
 * cached first-page rows the fetch did not return are kept AFTER them (they
 * are older — dropping them would open a gap before page 2's cursor). Rows in
 * `dropIds` (our optimistic row) are removed.
 */
export function mergeLatestPage(
  data: MessagesData | undefined,
  fetched: MessageListResponse,
  dropIds: string[] = [],
): MessagesData {
  const drop = new Set(dropIds);
  const fresh = new Set(fetched.messages.map((m) => m.message_id));
  if (!data || data.pages.length === 0) {
    return { pages: [{ ...fetched }], pageParams: [undefined] };
  }
  const keep = data.pages[0].messages.filter(
    (m) => !fresh.has(m.message_id) && !drop.has(m.message_id) && !m.isOptimistic,
  );
  const pages = [...data.pages];
  pages[0] = {
    messages: [...fetched.messages, ...keep],
    has_more: data.pages[0].has_more || fetched.has_more,
    run_active: fetched.run_active,
  };
  return {
    ...data,
    pages: pages.map((p, i) => (i === 0 ? p : { ...p, messages: p.messages.filter((m) => !drop.has(m.message_id)) })),
  };
}

/** Allowed client/server clock difference for the adopt check (plan 05 §X3). */
export const ADOPT_CLOCK_TOLERANCE_MS = 5_000;

export interface AdoptQuery {
  content: string;
  /** Client clock when the POST went out. */
  sentAtMs: number;
  /** Ids cached before the send — anything new since then is a candidate. */
  knownIds: Set<string>;
  /** The cache held this conversation's messages before the send (the
   *  baseline is meaningful). */
  hasBaseline: boolean;
}

export interface AdoptMatch {
  user: Message;
  /** The assistant row right after it (placeholder or answer), if any. */
  assistant: Message | null;
}

/**
 * Pre-`message_start` drop: did the server commit our send anyway? Looks for
 * the NEWEST user row with our exact text that we did not know before the
 * send, followed by an assistant row (the backend inserts both atomically).
 *
 * "New" is decided by the cached-id baseline when there is one (immune to the
 * phone's clock being off), else by `created_at ≥ sentAt − 5 s`.
 */
export function findAdoptable(fetched: MessageListResponse, q: AdoptQuery): AdoptMatch | null {
  const text = q.content.trim();
  const msgs = fetched.messages; // newest first
  for (let i = 0; i < msgs.length; i++) {
    const m = msgs[i];
    if (m.role !== "user") continue;
    if (q.knownIds.has(m.message_id)) return null; // older than our send: stop
    if ((m.content ?? "").trim() !== text) continue;
    const fresh = q.hasBaseline || new Date(m.created_at).getTime() >= q.sentAtMs - ADOPT_CLOCK_TOLERANCE_MS;
    if (!fresh) return null;
    const next = i > 0 ? msgs[i - 1] : null;
    return { user: m, assistant: next && next.role === "assistant" ? next : null };
  }
  return null;
}
