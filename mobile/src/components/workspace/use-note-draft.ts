import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { wiPreviewVisibleContent, type WorkspaceItem } from "@contracts";

import { ApiError, errorMessage, isWiPreviewLockedError } from "@/api/errors";
import { updateWorkspaceItem } from "@/api/workspace";
import { workspaceKeys } from "@/chat/keys";
import {
  AUTOSAVE_DELAY_MS,
  initialViewMode,
  LOCK_CONFLICT_FALLBACK,
  shouldAdoptServer,
  type ViewMode,
} from "@/components/artifact/action-bar";
import { showToast } from "@/stores/toast";

import { notePatch, type NoteBaseline } from "./workspace-model";

export interface NoteDraft {
  title: string;
  setTitle: (next: string) => void;
  content: string;
  setContent: (next: string) => void;
  mode: ViewMode;
  setMode: (mode: ViewMode) => void;
  saving: boolean;
  savedAt: number | null;
  conflict: string | null;
  clearConflict: () => void;
}

/**
 * A `note` under edit — the web NoteEditor → MarkdownDocEditor rules, with the
 * title editable too: 800 ms debounced autosave of the changed fields (title
 * trimmed; a blank title waits), queued while an agent lock is held, flushed
 * at once when the note is left. Text changed elsewhere is adopted only when
 * nothing local is unsaved. The draft twin for `agent_writing` is
 * `useDraft` (src/components/artifact/use-output-actions.ts).
 *
 * `bodyReadOnly` = a preview-locked item (wi_preview_lock.md §1.3): the body
 * is only the visible part and is never edited or sent (the server would
 * answer 402); a title edit still saves, as the server allows it.
 *
 * Mount once per item (the host is keyed by `item_id`).
 */
export function useNoteDraft(item: WorkspaceItem, locked: boolean, bodyReadOnly = false): NoteDraft {
  const qc = useQueryClient();
  const itemId = item.item_id;
  const serverTitle = item.title;
  const serverContent = wiPreviewVisibleContent(item);

  const [title, setTitleState] = useState(serverTitle);
  const [content, setContentState] = useState(serverContent);
  const [mode, setMode] = useState<ViewMode>(() => initialViewMode(serverContent));
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [conflict, setConflict] = useState<string | null>(null);

  const lastSent = useRef<NoteBaseline>({ title: serverTitle, content: serverContent });
  const pending = useRef<NoteBaseline | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const seq = useRef(0);
  const alive = useRef(true);
  const lockedRef = useRef(locked);
  const bodyReadOnlyRef = useRef(bodyReadOnly);

  const flush = useCallback(
    (teardown: boolean) => {
      if (timer.current !== null) {
        clearTimeout(timer.current);
        timer.current = null;
      }
      const next = pending.current;
      if (next === null) return;
      if (lockedRef.current && !teardown) return;
      pending.current = null;
      if (lockedRef.current) return;
      const patch = notePatch(lastSent.current, next.title, next.content, bodyReadOnlyRef.current);
      if (!patch) return;

      const sentTitle = next.title.trim();
      const mine = ++seq.current;
      const latest = () => mine === seq.current;
      if (alive.current) setSaving(true);
      updateWorkspaceItem(itemId, patch)
        .then((updated) => {
          if (!latest()) return;
          lastSent.current = { title: sentTitle, content: next.content };
          qc.setQueryData(workspaceKeys.detail(updated.item_id), updated);
          if (updated.conversation_id) {
            void qc.invalidateQueries({ queryKey: workspaceKeys.byConversation(updated.conversation_id) });
          }
          if (alive.current) {
            setSavedAt(Date.now());
            setConflict(null);
          }
        })
        .catch((err: unknown) => {
          // Locked under us: the item comes back with its wall, body read-only.
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

  // Latest local values, for the setters and the adoption effect.
  const local = useRef<NoteBaseline>({ title: serverTitle, content: serverContent });

  const setTitle = useCallback(
    (next: string) => {
      setTitleState(next);
      local.current = { ...local.current, title: next };
      pending.current = { ...local.current };
      arm();
    },
    [arm],
  );

  const setContent = useCallback(
    (next: string) => {
      if (bodyReadOnlyRef.current) return;
      setContentState(next);
      local.current = { ...local.current, content: next };
      pending.current = { ...local.current };
      arm();
    },
    [arm],
  );

  useEffect(() => {
    bodyReadOnlyRef.current = bodyReadOnly;
  }, [bodyReadOnly]);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      flush(true);
    };
  }, [flush]);

  useEffect(() => {
    lockedRef.current = locked;
    if (!locked && pending.current !== null && timer.current === null) arm();
  }, [locked, arm]);

  // Adopt outside changes per field (the echo of our own save compares equal).
  useEffect(() => {
    if (shouldAdoptServer(serverTitle, local.current.title.trim(), lastSent.current.title) && pending.current === null) {
      lastSent.current = { ...lastSent.current, title: serverTitle };
      local.current = { ...local.current, title: serverTitle };
      setTitleState(serverTitle);
    }
  }, [serverTitle]);
  useEffect(() => {
    if (shouldAdoptServer(serverContent, local.current.content, lastSent.current.content) && pending.current === null) {
      lastSent.current = { ...lastSent.current, content: serverContent };
      local.current = { ...local.current, content: serverContent };
      setContentState(serverContent);
    }
  }, [serverContent]);

  return {
    title,
    setTitle,
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
