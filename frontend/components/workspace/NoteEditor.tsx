"use client";

import { useEffect, useRef, useState } from "react";
import { Lock, Save, Loader2, AlertTriangle } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useUpdateWorkspaceItem } from "@/hooks/use-workspace";
import { useDebounce } from "@/hooks/use-debounce";
import { ApiClientError } from "@/lib/api";
import type { WorkspaceItem } from "@/types";

interface NoteEditorProps {
  item: WorkspaceItem;
}

const AUTOSAVE_DELAY_MS = 800;

function getLockedUntil(item: WorkspaceItem): number | null {
  const lockedColumn = (item as unknown as { locked_by_agent_until?: string })
    .locked_by_agent_until;
  const lockedMeta = (item.metadata as { locked_until?: string } | undefined)
    ?.locked_until;
  const candidate = lockedColumn ?? lockedMeta;
  if (!candidate) return null;
  const ts = Date.parse(candidate);
  if (!Number.isFinite(ts) || ts <= Date.now()) return null;
  return ts;
}

/**
 * Markdown editor used for ``note`` items (always editable) and
 * ``agent_writing`` items (editable when the agent lock is not held).
 *
 * Behaviour:
 * - 800 ms debounced autosave on change.
 * - When the agent holds the lock, the textarea becomes read-only and a
 *   "Luna يحرر…" banner shows above it.
 * - Title is editable in a small input above the body for ``note`` items.
 *   For ``agent_writing`` items the title comes from the agent and is
 *   read-only here.
 */
export function NoteEditor({ item }: NoteEditorProps) {
  const update = useUpdateWorkspaceItem();
  const [title, setTitle] = useState(item.title);
  const [content, setContent] = useState(item.content_md ?? "");
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [conflict, setConflict] = useState<string | null>(null);
  const lastSent = useRef<{ title: string; content: string }>({
    title: item.title,
    content: item.content_md ?? "",
  });

  // When the underlying item changes (e.g. user switches chip), reset state.
  useEffect(() => {
    setTitle(item.title);
    setContent(item.content_md ?? "");
    lastSent.current = { title: item.title, content: item.content_md ?? "" };
    setSavedAt(null);
    setConflict(null);
  }, [item.item_id, item.title, item.content_md]);

  const debouncedTitle = useDebounce(title, AUTOSAVE_DELAY_MS);
  const debouncedContent = useDebounce(content, AUTOSAVE_DELAY_MS);

  const lockedUntilTs = getLockedUntil(item);
  const isLocked = lockedUntilTs !== null;
  const titleEditable = item.kind === "note";

  useEffect(() => {
    if (isLocked) return;
    const titleChanged = debouncedTitle !== lastSent.current.title;
    const contentChanged = debouncedContent !== lastSent.current.content;
    if (!titleChanged && !contentChanged) return;
    if (!debouncedTitle.trim()) return;

    update.mutate(
      {
        itemId: item.item_id,
        data: {
          title: titleChanged ? debouncedTitle.trim() : undefined,
          content_md: contentChanged ? debouncedContent : undefined,
        },
      },
      {
        onSuccess: () => {
          lastSent.current = {
            title: debouncedTitle,
            content: debouncedContent,
          };
          setSavedAt(Date.now());
          setConflict(null);
        },
        onError: (err) => {
          if (err instanceof ApiClientError && err.status === 409) {
            setConflict(err.message || "Luna يحرر هذا الملف الآن، حاول مجدداً");
          }
        },
      },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedTitle, debouncedContent, isLocked, item.item_id]);

  return (
    <div className="flex flex-1 flex-col min-h-0">
      {isLocked && (
        <div className="flex items-center gap-2 border-b bg-amber-500/10 px-4 py-2 text-xs text-amber-700 dark:text-amber-400">
          <Lock className="h-3.5 w-3.5" />
          <span>Luna يحرر هذا الملف الآن…</span>
        </div>
      )}
      {conflict && !isLocked && (
        <div className="flex items-center justify-between gap-2 border-b bg-destructive/10 px-4 py-2 text-xs text-destructive">
          <span className="inline-flex items-center gap-2">
            <AlertTriangle className="h-3.5 w-3.5" />
            {conflict}
          </span>
          <button
            onClick={() => setConflict(null)}
            className="text-[11px] underline hover:opacity-80"
          >
            إخفاء
          </button>
        </div>
      )}

      <div className="border-b px-4 py-3">
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          readOnly={!titleEditable || isLocked}
          dir="rtl"
          className="w-full bg-transparent text-sm font-semibold text-foreground focus:outline-none disabled:cursor-not-allowed read-only:cursor-default"
          placeholder="عنوان الملاحظة..."
        />
      </div>

      <ScrollArea className="flex-1">
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          readOnly={isLocked}
          dir="rtl"
          className="block h-full min-h-[400px] w-full resize-none border-0 bg-transparent p-4 text-sm leading-relaxed focus:outline-none read-only:cursor-default"
          placeholder={
            item.kind === "note"
              ? "اكتب ملاحظاتك هنا..."
              : "محتوى المسودة..."
          }
        />
      </ScrollArea>

      <div className="flex items-center justify-between border-t px-4 py-2 text-[11px] text-muted-foreground">
        <span>
          {update.isPending ? (
            <span className="inline-flex items-center gap-1.5">
              <Loader2 className="h-3 w-3 animate-spin" />
              جارٍ الحفظ
            </span>
          ) : savedAt ? (
            <span className="inline-flex items-center gap-1.5">
              <Save className="h-3 w-3" />
              تم الحفظ تلقائياً
            </span>
          ) : (
            <span>التغييرات تُحفظ تلقائياً</span>
          )}
        </span>
        <span>
          آخر تحديث:{" "}
          {new Intl.DateTimeFormat("ar-SA", {
            dateStyle: "medium",
            timeStyle: "short",
          }).format(new Date(item.updated_at))}
        </span>
      </div>
    </div>
  );
}
