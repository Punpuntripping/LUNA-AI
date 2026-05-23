"use client";

import { useCallback, useRef, useState } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { documentsApi, workspaceApi, getAccessToken } from "@/lib/api";
import { startTusUpload, type UploadHandle } from "@/lib/upload-client";
import { documentKeys } from "@/hooks/use-documents";
import { workspaceKeys } from "@/hooks/use-workspace";
import type { Document, WorkspaceItem } from "@/types";

/**
 * Discriminated union that picks which backend endpoints the upload
 * talks to. `kind: 'document'` uploads to `case_documents`, `kind:
 * 'attachment'` uploads to `workspace_items` scoped to a conversation.
 */
export type UploadTarget =
  | { kind: "document"; caseId: string }
  | { kind: "attachment"; conversationId: string };

export type ResumableUploadStatus =
  | "idle"
  | "initializing"
  | "uploading"
  | "finalizing"
  | "completed"
  | "failed"
  | "cancelled";

export interface ResumableUploadState {
  status: ResumableUploadStatus;
  /** 0..1 — covers tus PATCH progress only. Init/finalize are near-instant. */
  progress: number;
  sentBytes: number;
  totalBytes: number;
  /** Arabic-language error when status === 'failed'. */
  error: string | null;
  /** document_id or item_id once /init returned; null before that. */
  resourceId: string | null;
  /** The materialised row from /finalize; null until status === 'completed'. */
  result: Document | WorkspaceItem | null;
}

export interface ResumableUpload {
  state: ResumableUploadState;
  start: (file: File) => Promise<void>;
  cancel: () => void;
}

const INITIAL_STATE: ResumableUploadState = {
  status: "idle",
  progress: 0,
  sentBytes: 0,
  totalBytes: 0,
  error: null,
  resourceId: null,
  result: null,
};

function arabicErrorFromUnknown(err: unknown): string {
  if (err instanceof Error) {
    // Default to a generic Arabic message; surface backend Arabic detail
    // when it came through `ApiClientError`.
    const msg = err.message;
    if (msg && /[؀-ۿ]/.test(msg)) return msg;
  }
  return "فشل رفع الملف. يرجى المحاولة مرة أخرى.";
}

/**
 * Imperative variant — exposed so callers that need to fan out to many
 * files (chat input, multi-file dropzone) can drive uploads without
 * spinning a hook per slot. Returns a handle the caller can use to
 * cancel mid-upload + a promise that resolves with the terminal state.
 *
 * On terminal success it invalidates the relevant TanStack Query cache
 * for the list view (documents-by-case or workspace-by-conversation).
 */
export interface ImperativeUploadCallbacks {
  /** Fires once /init returns. */
  onInitialized?: (resourceId: string) => void;
  /** Fires on every tus chunk progress event. */
  onProgress?: (state: ResumableUploadState) => void;
  /** Fires whenever status transitions. */
  onStatusChange?: (status: ResumableUploadStatus) => void;
  /** Fires once finalize returns 200. */
  onCompleted?: (row: Document | WorkspaceItem) => void;
  /** Fires on any unrecoverable error after retries. */
  onFailed?: (error: string) => void;
  /** Fires once cancel() has finished (best-effort backend cleanup). */
  onCancelled?: () => void;
}

export interface ImperativeUploadHandle {
  cancel: () => void;
  done: Promise<ResumableUploadState>;
}

export function runResumableUpload(
  target: UploadTarget,
  file: File,
  qc: QueryClient,
  callbacks: ImperativeUploadCallbacks = {},
): ImperativeUploadHandle {
  let state: ResumableUploadState = {
    ...INITIAL_STATE,
    status: "initializing",
    totalBytes: file.size,
  };
  let tusHandle: UploadHandle | null = null;
  let cancelled = false;

  const patch = (partial: Partial<ResumableUploadState>) => {
    state = { ...state, ...partial };
    callbacks.onProgress?.(state);
    if (partial.status) callbacks.onStatusChange?.(partial.status);
  };

  const done = (async () => {
    const accessToken = getAccessToken();
    if (!accessToken) {
      patch({
        status: "failed",
        error: "انتهت الجلسة. يرجى تسجيل الدخول مرة أخرى.",
      });
      callbacks.onFailed?.(state.error!);
      return state;
    }

    // ── 1. init ────────────────────────────────────────────────────────
    let initRes;
    try {
      if (target.kind === "document") {
        initRes = await documentsApi.initUpload(target.caseId, {
          filename: file.name,
          mime_type: file.type,
          size_bytes: file.size,
        });
      } else {
        initRes = await workspaceApi.initAttachment(target.conversationId, {
          filename: file.name,
          mime_type: file.type,
          size_bytes: file.size,
        });
      }
    } catch (err) {
      const errorMsg = arabicErrorFromUnknown(err);
      patch({ status: "failed", error: errorMsg });
      callbacks.onFailed?.(errorMsg);
      return state;
    }

    if (cancelled) return state;

    const resourceId =
      target.kind === "document" ? initRes.document_id : initRes.item_id;
    if (!resourceId) {
      patch({ status: "failed", error: "استجابة غير صالحة من الخادم" });
      callbacks.onFailed?.(state.error!);
      return state;
    }
    patch({ resourceId, status: "uploading" });
    callbacks.onInitialized?.(resourceId);

    // ── 2. tus PATCH chunks ───────────────────────────────────────────
    // Track the terminal outcome via a local — we can't rely on closure
    // state, the setState calls are batched. The explicit alias widens
    // the inferred type so TS' control-flow analysis doesn't collapse it
    // back to the initial literal after the await.
    type TusOutcome = "success" | "error" | "cancel";
    let tusOutcome: TusOutcome = "success";
    await new Promise<void>((resolve) => {
      tusHandle = startTusUpload({
        url: initRes!.upload_url,
        file,
        bucket: initRes!.bucket,
        objectName: initRes!.storage_path,
        contentType: file.type,
        accessToken,
        onProgress: (sent, total) => {
          patch({
            progress: total > 0 ? sent / total : 0,
            sentBytes: sent,
            totalBytes: total,
          });
        },
        onError: (err) => {
          tusOutcome = "error";
          patch({ status: "failed", error: arabicErrorFromUnknown(err) });
          resolve();
        },
        onSuccess: () => {
          patch({
            progress: 1,
            sentBytes: state.totalBytes,
            status: "finalizing",
          });
          resolve();
        },
      });
      if (cancelled) {
        tusOutcome = "cancel";
        tusHandle.abort();
        resolve();
      }
    });

    // Cast through TusOutcome — TS' control-flow analysis decides
    // tusOutcome is still narrowed to "success" because the callbacks
    // are evaluated lazily, but they fire before `resolve()` so at the
    // top of this block the variable may genuinely hold any value.
    const outcome = tusOutcome as TusOutcome;
    if (outcome !== "success" || cancelled) {
      if (outcome === "error") callbacks.onFailed?.(state.error!);
      return state;
    }

    // ── 3. finalize ───────────────────────────────────────────────────
    try {
      let row: Document | WorkspaceItem;
      if (target.kind === "document") {
        row = await documentsApi.finalizeUpload(resourceId);
      } else {
        row = await workspaceApi.finalizeAttachment(resourceId);
      }
      patch({ status: "completed", result: row, error: null });

      if (target.kind === "document") {
        void qc.invalidateQueries({
          queryKey: documentKeys.list(target.caseId),
        });
      } else {
        void qc.invalidateQueries({
          queryKey: workspaceKeys.byConversation(target.conversationId),
        });
      }
      callbacks.onCompleted?.(row);
    } catch (err) {
      const errorMsg = arabicErrorFromUnknown(err);
      patch({ status: "failed", error: errorMsg });
      callbacks.onFailed?.(errorMsg);
    }
    return state;
  })();

  return {
    cancel: () => {
      cancelled = true;
      patch({ status: "cancelled" });
      if (tusHandle) tusHandle.abort();
      const resourceId = state.resourceId;
      if (resourceId) {
        const cancelPromise =
          target.kind === "document"
            ? documentsApi.cancelUpload(resourceId)
            : workspaceApi.cancelAttachment(resourceId);
        cancelPromise.catch(() => undefined).finally(() => {
          callbacks.onCancelled?.();
        });
      } else {
        callbacks.onCancelled?.();
      }
    },
    done,
  };
}

/**
 * Hook variant — convenient for the single-file dropzone surface. For
 * multi-file flows (chat input) prefer the imperative `runResumableUpload`
 * since hooks can't be called in a loop.
 *
 * The hook owns the live cancel handle via a ref so cancellation works
 * even mid-PATCH. React Query caches for the relevant list are invalidated
 * on completion.
 */
export function useResumableUpload(target: UploadTarget): ResumableUpload {
  const qc = useQueryClient();
  const [state, setState] = useState<ResumableUploadState>(INITIAL_STATE);
  const handleRef = useRef<ImperativeUploadHandle | null>(null);

  const start = useCallback(
    async (file: File) => {
      setState({
        ...INITIAL_STATE,
        status: "initializing",
        totalBytes: file.size,
      });

      handleRef.current = runResumableUpload(target, file, qc, {
        onProgress: (s) => setState(s),
        // onProgress already covers the rest of the lifecycle transitions
        // since `patch` re-emits state. No need to duplicate.
      });

      await handleRef.current.done;
      handleRef.current = null;
    },
    [target, qc],
  );

  const cancel = useCallback(() => {
    handleRef.current?.cancel();
  }, []);

  return { state, start, cancel };
}
