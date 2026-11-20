import { useCallback, useRef, useState } from "react";
import { filesApi } from "@/lib/api";
import type { FileMatch, FileMatchDecision, FileMatchRequest } from "@/lib/contracts";
import type {
  FileMatchDialogItem,
  FileMatchDialogProps,
} from "@/components/files/FileMatchDialog";

interface FileMatchQueueOptions {
  /** `useFilesEnabled()` — false → `check` does nothing (no request). */
  enabled: boolean;
  /**
   * Is this picked file still waiting in the composer / pane? Asked when the
   * match answer lands: a file already removed — or already SENT — never
   * opens the popup.
   */
  isStillPending: (key: string) => boolean;
  /** One call per file when the popup resolves (closed = «رفع نسخة جديدة»). */
  onDecision: (key: string, match: FileMatch, decision: FileMatchDecision) => void;
  /** A question for this file just joined the popup. */
  onAsk?: (key: string) => void;
}

/**
 * The app-side half of the same-file approval (plan §3.8, D12): ask
 * `POST /files/match` for a picked file and queue a question per match in ONE
 * popup. Files matched in quick succession join the open popup; once it
 * resolves, the next match opens a fresh one.
 *
 * Never in the upload's way: `check` is fire-and-forget, runs beside the
 * upload, and a failed or slow call is dropped silently. Phase 1b acts only on
 * a copy in the SAME conversation (`same_conversation`).
 */
export function useFileMatchQueue({
  enabled,
  isStillPending,
  onDecision,
  onAsk,
}: FileMatchQueueOptions) {
  const [queue, setQueue] = useState<FileMatchDialogItem[]>([]);
  const [showing, setShowing] = useState(0);
  // Latest callbacks without re-creating `check` on every render.
  const optionsRef = useRef({ isStillPending, onDecision, onAsk });
  optionsRef.current = { isStillPending, onDecision, onAsk };
  const queueRef = useRef<FileMatchDialogItem[]>([]);
  queueRef.current = queue;

  const check = useCallback(
    (key: string, request: FileMatchRequest | null) => {
      if (!enabled || !request) return;
      filesApi
        .match(request)
        .then((res) => {
          const match = res?.match;
          if (!match || !match.same_conversation) return;
          if (!optionsRef.current.isStillPending(key)) return;
          optionsRef.current.onAsk?.(key);
          setQueue((prev) => (prev.some((i) => i.key === key) ? prev : [...prev, { key, match }]));
        })
        .catch(() => undefined);
    },
    [enabled],
  );

  const resolve = useCallback((decisions: Record<string, FileMatchDecision>) => {
    const items = queueRef.current;
    setQueue([]);
    setShowing((n) => n + 1);
    for (const item of items) {
      optionsRef.current.onDecision(item.key, item.match, decisions[item.key] ?? "upload_new");
    }
  }, []);

  /** Close without answers — every queued file keeps its new upload. */
  const dismissAll = useCallback(() => {
    if (queueRef.current.length > 0) resolve({});
  }, [resolve]);

  /** A file left the composer (chip removed): drop its question. */
  const drop = useCallback((key: string) => {
    const current = queueRef.current;
    if (!current.some((i) => i.key === key)) return;
    const next = current.filter((i) => i.key !== key);
    setQueue(next);
    // The last question left: the next popup must start clean.
    if (next.length === 0) setShowing((n) => n + 1);
  }, []);

  const dialogProps: FileMatchDialogProps = {
    open: queue.length > 0,
    items: queue,
    onResolve: resolve,
  };

  return {
    check,
    dismissAll,
    drop,
    /** Spread onto `<FileMatchDialog key={dialogKey} … />`. */
    dialogProps,
    /** Changes per showing, so each popup starts clean. */
    dialogKey: `file-match-${showing}`,
  };
}
