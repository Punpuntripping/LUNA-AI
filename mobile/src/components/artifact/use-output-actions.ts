import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { wiPreviewVisibleContent, type WorkspaceFeedback, type WorkspaceItem } from "@contracts";

import { ApiError, errorMessage, isWiPreviewLockedError } from "@/api/errors";
import { setWorkspaceFeedback, updateWorkspaceItem } from "@/api/workspace";
import { workspaceKeys } from "@/chat/keys";
import { showToast } from "@/stores/toast";

import {
  AUTOSAVE_DELAY_MS,
  draftPatch,
  initialViewMode,
  LOCK_CONFLICT_FALLBACK,
  shouldAdoptServer,
  type ViewMode,
} from "./action-bar";

/**
 * 👍/👎 — `PATCH /workspace/{id}/feedback`. Optimistic on the detail cache
 * (the only cache that renders `feedback`), rolled back on error — the web's
 * `useSetWorkspaceItemFeedback`.
 */
export function useSetFeedback() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId, feedback }: { itemId: string; feedback: WorkspaceFeedback }) =>
      setWorkspaceFeedback(itemId, feedback),
    onMutate: async ({ itemId, feedback }) => {
      await qc.cancelQueries({ queryKey: workspaceKeys.detail(itemId) });
      const prev = qc.getQueryData<WorkspaceItem>(workspaceKeys.detail(itemId));
      if (prev) qc.setQueryData<WorkspaceItem>(workspaceKeys.detail(itemId), { ...prev, feedback });
      return { prev, itemId };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.prev) qc.setQueryData(workspaceKeys.detail(ctx.itemId), ctx.prev);
    },
    onSuccess: (updated) => {
      qc.setQueryData(workspaceKeys.detail(updated.item_id), updated);
    },
  });
}

export interface DraftState {
  /** The LIVE body (what the editor shows, preview renders and نسخ copies). */
  content: string;
  setContent: (next: string) => void;
  mode: ViewMode;
  setMode: (mode: ViewMode) => void;
  saving: boolean;
  savedAt: number | null;
  /** 409 from the agent lock — shown as a dismissible banner. */
  conflict: string | null;
  clearConflict: () => void;
}

/**
 * An `agent_writing` draft under edit — the web MarkdownDocEditor + NoteEditor
 * rules: 800 ms debounced autosave of `content_md`; while the agent holds the
 * lock the edit stays queued and is sent the moment the lock clears; leaving
 * the draft (sheet closed / item switched) sends a queued edit at once instead
 * of dropping it. A body changed elsewhere (the agent still writing) is
 * adopted only when the user has nothing unsaved.
 *
 * `readOnly` = a preview-locked item (wi_preview_lock.md §1.3): its body is
 * only the visible part, so it is never edited and NOTHING is saved — a
 * queued edit is dropped, not sent (the server would answer 402).
 *
 * Mount once per item (the host is keyed by `item_id`).
 */
export function useDraft(item: WorkspaceItem, locked: boolean, readOnly = false): DraftState {
  const qc = useQueryClient();
  const itemId = item.item_id;
  const server = wiPreviewVisibleContent(item);

  const [content, setContentState] = useState(server);
  const [mode, setMode] = useState<ViewMode>(() => initialViewMode(server));
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [conflict, setConflict] = useState<string | null>(null);

  /** What the server holds (the dirty baseline). */
  const lastSent = useRef(server);
  /** The edit waiting out its debounce (or the lock). */
  const pending = useRef<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** Only the newest save may move the UI. */
  const seq = useRef(0);
  const alive = useRef(true);
  const lockedRef = useRef(locked);
  const readOnlyRef = useRef(readOnly);

  const flush = useCallback(
    (teardown: boolean) => {
      if (timer.current !== null) {
        clearTimeout(timer.current);
        timer.current = null;
      }
      const next = pending.current;
      if (next === null) return;
      // Preview-locked: never saved, whatever is queued.
      if (readOnlyRef.current) {
        pending.current = null;
        return;
      }
      // Locked: keep it queued (the effect below re-arms it on release). On
      // teardown it is dropped — the server would 409 it anyway.
      if (lockedRef.current && !teardown) return;
      pending.current = null;
      if (lockedRef.current) return;
      const patch = draftPatch(lastSent.current, next);
      if (!patch) return;

      const mine = ++seq.current;
      const latest = () => mine === seq.current;
      if (alive.current) setSaving(true);
      updateWorkspaceItem(itemId, patch)
        .then((updated) => {
          if (!latest()) return;
          // Baseline first: the cache write below re-renders with this body,
          // and the adoption check must read it as our own echo.
          lastSent.current = next;
          qc.setQueryData(workspaceKeys.detail(updated.item_id), updated);
          void qc.invalidateQueries({ queryKey: workspaceKeys.all });
          if (alive.current) {
            setSavedAt(Date.now());
            setConflict(null);
          }
        })
        .catch((err: unknown) => {
          // Locked under us (the plan ended mid-edit): the item comes back
          // with its wall and the read-only bar.
          if (isWiPreviewLockedError(err)) void qc.invalidateQueries({ queryKey: workspaceKeys.detail(itemId) });
          if (!latest() || !alive.current) return;
          if (err instanceof ApiError && err.kind === "http" && err.status === 409) {
            setConflict(err.message || LOCK_CONFLICT_FALLBACK);
          } else {
            showToast(errorMessage(err));
          }
        })
        .finally(() => {
          if (latest() && alive.current) setSaving(false);
        });
    },
    [itemId, qc],
  );

  const arm = useCallback(() => {
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = setTimeout(() => flush(false), AUTOSAVE_DELAY_MS);
  }, [flush]);

  const setContent = useCallback(
    (next: string) => {
      if (readOnlyRef.current) return;
      setContentState(next);
      pending.current = next;
      arm();
    },
    [arm],
  );

  // Turned read-only (the item came back preview-locked): drop what is queued.
  useEffect(() => {
    readOnlyRef.current = readOnly;
    if (!readOnly) return;
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = null;
    pending.current = null;
  }, [readOnly]);

  // Leaving the draft sends its queued edit right away.
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      flush(true);
    };
  }, [flush]);

  // Lock released with an edit still queued → save it.
  useEffect(() => {
    lockedRef.current = locked;
    if (!locked && pending.current !== null && timer.current === null) arm();
  }, [locked, arm]);

  // Adopt a body that changed outside this editor (state adjusted in an
  // effect, keyed on the server value only — never on our own keystrokes).
  const contentRef = useRef(content);
  useEffect(() => {
    contentRef.current = content;
  }, [content]);
  useEffect(() => {
    const local = contentRef.current;
    if (!shouldAdoptServer(server, local, lastSent.current)) return;
    lastSent.current = server;
    setContentState(server);
    // A draft that opened empty (so in the editor) and just got its first
    // text while the agent holds the lock presents as a rendered read.
    if (lockedRef.current && local.trim().length === 0 && server.trim().length > 0) setMode("preview");
  }, [server]);

  return {
    content,
    setContent,
    mode,
    setMode,
    saving,
    savedAt,
    conflict,
    clearConflict: () => setConflict(null),
  };
}
