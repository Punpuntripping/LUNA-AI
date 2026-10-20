import { createStore, type StoreApi } from "zustand/vanilla";

import type { Attachment, UploadInitResponse, WorkspaceItem } from "@contracts";

import { attachmentTypeFor, MSG_BAD_RESPONSE, type PickedFile, type UploadMime } from "./limits";

// F4 — composer attachments: one app-wide manager (like the chat engine), keyed
// per conversation. Port of the web's ChatInput startUploads +
// runResumableUpload (hooks/use-resumable-upload.ts):
//
//   pick → preparing (HEIC→JPEG, real type, size, PDF page estimate)
//        → init (row + upload_url) → uploading (TUS) → finalizing → completed
//   any failure → failed (Arabic text) + «إعادة المحاولة» (a fresh init)
//   remove      → abort + backend cancel (in flight / failed only — a completed
//                 chip removed before sending stays in the workspace, as web)
//
// Chips leave the composer only when the server took the send
// (`message_start`): `takeForSend().release` is the send's `onCommitted`. A
// refused send keeps them (web use-chat.ts:206-236).
//
// Phone difference: chips live per conversation for the whole app session, so
// stepping back to the list mid-upload does not lose the upload (the web
// cancels on navigation because its chip store is one global slot).
//
// Pure: network, files and AppState are injected — tested in __tests__.

export type UploadStatus = "preparing" | "uploading" | "finalizing" | "completed" | "failed";

export interface PendingAttachment {
  /** Local id (chip key). */
  id: string;
  conversationId: string;
  name: string;
  /** Bytes (after conversion); null until measured. */
  size: number | null;
  mimeType: string | null;
  /** Local image to show as a thumbnail; null for PDFs. */
  previewUri: string | null;
  status: UploadStatus;
  /** 0..1 of the TUS bytes. */
  progress: number;
  /** `workspace_items.item_id` once `/init` answered. */
  itemId: string | null;
  error: string | null;
}

export interface AttachmentsState {
  byConversation: Record<string, PendingAttachment[]>;
}

export interface PreparedFile {
  uri: string;
  name: string;
  size: number;
  mimeType: UploadMime;
  pageCount: number;
  previewUri: string | null;
}

/** A failure whose message is already the Arabic text for the chip. */
export class AttachmentFailure extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AttachmentFailure";
  }
}

export interface UploadManagerDeps {
  /** Convert / measure / sniff / count pages; throw AttachmentFailure to refuse. */
  prepare(pick: PickedFile, signal: AbortSignal): Promise<PreparedFile>;
  initAttachment(
    conversationId: string,
    body: { filename: string; mime_type: string; size_bytes: number; page_count: number },
  ): Promise<UploadInitResponse>;
  /** TUS the prepared bytes to `init.upload_url`. Rejects on abort. */
  upload(
    prepared: PreparedFile,
    init: UploadInitResponse,
    signal: AbortSignal,
    onProgress: (fraction: number) => void,
  ): Promise<void>;
  finalizeAttachment(itemId: string): Promise<WorkspaceItem>;
  cancelAttachment(itemId: string): Promise<unknown>;
  /** Any error → the chip's Arabic text (never raw / English / paths). */
  errorText(err: unknown): string;
  /** Resolves once the app is in the foreground (API steps never run while
   *  iOS has the app suspended). */
  whenActive(signal: AbortSignal): Promise<void>;
  /** A finished upload: refresh the conversation's workspace list. */
  onCompleted?(conversationId: string): void;
  newId?(): string;
}

export interface SendAttachments {
  attachmentIds: string[];
  optimisticAttachments: Attachment[];
  /** Pass as the send's `onCommitted`: drops exactly the chips this send carried. */
  release(): void;
}

export interface UploadManager {
  store: StoreApi<AttachmentsState>;
  list(conversationId: string): PendingAttachment[];
  /** Picks must already be validated (`validatePicks`). */
  add(conversationId: string, picks: readonly PickedFile[]): void;
  remove(conversationId: string, id: string): void;
  retry(conversationId: string, id: string): void;
  /** Any chip still preparing / uploading / finalizing. */
  hasInFlight(conversationId: string): boolean;
  /** Completed chips (the send gate counts them like typed text). */
  sendableCount(conversationId: string): number;
  /** What a send carries now. Empty lists when nothing is attached. */
  takeForSend(conversationId: string): SendAttachments;
  /** Sign-out: abort everything, cancel nothing server-side, forget all. */
  reset(): void;
}

const EMPTY: PendingAttachment[] = [];
const IN_FLIGHT: ReadonlySet<UploadStatus> = new Set<UploadStatus>(["preparing", "uploading", "finalizing"]);

export interface AttachmentGate {
  /** Chips still preparing / uploading / finalizing — send is blocked. */
  uploading: boolean;
  /** Completed chips — they make a send possible without typed text. */
  sendable: number;
}

export function attachmentGate(chips: readonly PendingAttachment[]): AttachmentGate {
  let uploading = false;
  let sendable = 0;
  for (const c of chips) {
    if (IN_FLIGHT.has(c.status)) uploading = true;
    else if (c.status === "completed" && c.itemId) sendable++;
  }
  return { uploading, sendable };
}

interface Runtime {
  controller: AbortController;
  itemId: string | null;
}

function isAbort(err: unknown, signal: AbortSignal): boolean {
  return signal.aborted || (err instanceof Error && (err.name === "AbortError" || err.name === "TusAbortedError"));
}

let seq = 0;
const defaultId = () => `att-${Date.now().toString(36)}-${(seq++).toString(36)}`;

export function createUploadManager(deps: UploadManagerDeps): UploadManager {
  const store = createStore<AttachmentsState>()(() => ({ byConversation: {} }));
  /** In-flight work per chip id. */
  const runtimes = new Map<string, Runtime>();
  /** The original pick per chip id — «إعادة المحاولة» starts over from it. */
  const picks = new Map<string, PickedFile>();
  const newId = deps.newId ?? defaultId;

  const list = (conversationId: string) => store.getState().byConversation[conversationId] ?? EMPTY;

  const patch = (conversationId: string, id: string, partial: Partial<PendingAttachment>) => {
    store.setState((s) => {
      const current = s.byConversation[conversationId];
      if (!current?.some((a) => a.id === id)) return s;
      return {
        byConversation: {
          ...s.byConversation,
          [conversationId]: current.map((a) => (a.id === id ? { ...a, ...partial } : a)),
        },
      };
    });
  };

  const drop = (conversationId: string, ids: ReadonlySet<string>) => {
    store.setState((s) => {
      const current = s.byConversation[conversationId];
      if (!current) return s;
      const next = current.filter((a) => !ids.has(a.id));
      const byConversation = { ...s.byConversation };
      if (next.length) byConversation[conversationId] = next;
      else delete byConversation[conversationId];
      return { byConversation };
    });
  };

  const cancelOnServer = (itemId: string | null) => {
    if (itemId) deps.cancelAttachment(itemId).catch(() => undefined);
  };

  const run = async (conversationId: string, id: string, pick: PickedFile, rt: Runtime) => {
    const { signal } = rt.controller;
    const alive = () => !signal.aborted && runtimes.get(id) === rt;
    try {
      const prepared = await deps.prepare(pick, signal);
      if (!alive()) return;
      patch(conversationId, id, {
        name: prepared.name,
        size: prepared.size,
        mimeType: prepared.mimeType,
        previewUri: prepared.previewUri,
      });

      await deps.whenActive(signal);
      const init = await deps.initAttachment(conversationId, {
        filename: prepared.name,
        mime_type: prepared.mimeType,
        size_bytes: prepared.size,
        page_count: prepared.pageCount,
      });
      if (!init.item_id) throw new AttachmentFailure(MSG_BAD_RESPONSE);
      rt.itemId = init.item_id;
      // Removed while /init was in flight: the row exists now — cancel it.
      if (!alive()) {
        cancelOnServer(init.item_id);
        return;
      }
      patch(conversationId, id, { itemId: init.item_id, status: "uploading", progress: 0 });

      await deps.upload(prepared, init, signal, (fraction) => {
        if (alive()) patch(conversationId, id, { progress: Math.max(0, Math.min(1, fraction)) });
      });
      if (!alive()) return;
      patch(conversationId, id, { status: "finalizing", progress: 1 });

      await deps.whenActive(signal);
      const row = await deps.finalizeAttachment(init.item_id);
      if (!alive()) return;
      runtimes.delete(id);
      patch(conversationId, id, { status: "completed", progress: 1, itemId: row.item_id || init.item_id, error: null });
      deps.onCompleted?.(conversationId);
    } catch (err) {
      if (isAbort(err, signal) || runtimes.get(id) !== rt) return;
      runtimes.delete(id);
      patch(conversationId, id, { status: "failed", error: deps.errorText(err) });
    }
  };

  const start = (conversationId: string, id: string, pick: PickedFile) => {
    const rt: Runtime = { controller: new AbortController(), itemId: null };
    runtimes.set(id, rt);
    void run(conversationId, id, pick, rt);
  };

  const forget = (id: string) => {
    runtimes.get(id)?.controller.abort();
    runtimes.delete(id);
    picks.delete(id);
  };

  const manager: UploadManager = {
    store,
    list,
    add(conversationId, files) {
      if (!files.length) return;
      const added: PendingAttachment[] = files.map((pick) => ({
        id: newId(),
        conversationId,
        name: pick.name,
        size: pick.size,
        mimeType: pick.mimeType,
        previewUri: null,
        status: "preparing",
        progress: 0,
        itemId: null,
        error: null,
      }));
      store.setState((s) => ({
        byConversation: { ...s.byConversation, [conversationId]: [...(s.byConversation[conversationId] ?? []), ...added] },
      }));
      added.forEach((a, i) => {
        picks.set(a.id, files[i]);
        start(conversationId, a.id, files[i]);
      });
    },
    remove(conversationId, id) {
      const chip = list(conversationId).find((a) => a.id === id);
      const rt = runtimes.get(id);
      forget(id);
      drop(conversationId, new Set([id]));
      if (!chip || chip.status === "completed") return;
      // In flight or failed: the half-made row goes (a pending /init cancels
      // itself when it lands — see run()).
      cancelOnServer(chip.itemId ?? rt?.itemId ?? null);
    },
    retry(conversationId, id) {
      const chip = list(conversationId).find((a) => a.id === id);
      const pick = picks.get(id);
      if (!chip || chip.status !== "failed" || !pick) return;
      // The failed attempt's row is abandoned (soft-deleted) — retry opens a new one.
      cancelOnServer(chip.itemId);
      patch(conversationId, id, { status: "preparing", progress: 0, itemId: null, error: null });
      start(conversationId, id, pick);
    },
    hasInFlight(conversationId) {
      return list(conversationId).some((a) => IN_FLIGHT.has(a.status));
    },
    sendableCount(conversationId) {
      return list(conversationId).filter((a) => a.status === "completed" && a.itemId).length;
    },
    takeForSend(conversationId) {
      const chips = list(conversationId);
      const ready = chips.filter((a) => a.status === "completed" && a.itemId);
      // Released on commit: what the user saw at send time (completed + failed —
      // the web drops failed chips with the send too). In-flight ones stay.
      const sent = new Set(chips.filter((a) => !IN_FLIGHT.has(a.status)).map((a) => a.id));
      return {
        attachmentIds: ready.map((a) => a.itemId as string),
        optimisticAttachments: ready.map((a) => ({
          id: a.id,
          document_id: a.itemId as string,
          attachment_type: attachmentTypeFor(a.mimeType ?? ""),
          filename: a.name,
          file_size: a.size ?? 0,
          kind: "attachment" as const,
        })),
        release: () => {
          for (const chipId of sent) forget(chipId);
          drop(conversationId, sent);
        },
      };
    },
    reset() {
      for (const rt of runtimes.values()) rt.controller.abort();
      runtimes.clear();
      picks.clear();
      store.setState({ byConversation: {} });
    },
  };

  return manager;
}
