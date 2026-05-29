"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Save, Loader2, Eye, Pencil } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { ArtifactPreview } from "@/components/workspace/ArtifactPreview";
import { useDebounce } from "@/hooks/use-debounce";
import { cn } from "@/lib/utils";

interface MarkdownDocEditorProps {
  /** Stable identity of the document being edited. Changing it resets the
   *  local title/content/savedAt state (e.g. user switches to another doc). */
  docId: string;
  initialTitle: string;
  initialContent: string;
  /**
   * Called by the debounced autosave with only the changed fields. If the
   * returned promise rejects, the error is surfaced via ``onSaveError``; on
   * resolve the savedAt indicator updates and the dirty baseline advances.
   */
  onSave: (patch: { title?: string; content_md?: string }) => Promise<unknown>;
  /** ISO timestamp shown in the footer ("آخر تحديث"). */
  updatedAt: string;
  /** When true the body textarea is read-only and autosave is suspended. */
  readOnly?: boolean;
  /** When true the title input is read-only (independent of ``readOnly``). */
  titleReadOnly?: boolean;
  /** When true a blank title is flagged as required (opt-in; notes leave it off). */
  titleRequired?: boolean;
  /** Placeholder for the title input. */
  titlePlaceholder?: string;
  /** Placeholder for the body textarea. */
  bodyPlaceholder?: string;
  /** Optional banner(s) rendered above the title bar (e.g. lock / conflict). */
  headerSlot?: ReactNode;
  /** Optional content appended inside the preview viewport (e.g. references). */
  footerSlot?: ReactNode;
  /**
   * Invoked when ``onSave`` rejects. Lets the host show its own banner
   * (e.g. a 409 conflict). The error is passed through untouched.
   */
  onSaveError?: (error: unknown) => void;
}

const AUTOSAVE_DELAY_MS = 800;

/**
 * Generic markdown document editor: title input + edit/preview toggle + RTL
 * textarea + ArtifactPreview-based preview + debounced autosave footer.
 *
 * This is the shared core extracted from ``NoteEditor`` — it knows nothing
 * about workspace items or templates. All persistence flows through the
 * ``onSave`` prop, so the same component drives both ``note`` workspace items
 * (via ``useUpdateWorkspaceItem``) and user templates (via ``useUpdateTemplate``).
 */
export function MarkdownDocEditor({
  docId,
  initialTitle,
  initialContent,
  onSave,
  updatedAt,
  readOnly = false,
  titleReadOnly = false,
  titleRequired = false,
  titlePlaceholder = "العنوان...",
  bodyPlaceholder = "اكتب المحتوى هنا...",
  headerSlot,
  footerSlot,
  onSaveError,
}: MarkdownDocEditorProps) {
  const [title, setTitle] = useState(initialTitle);
  const [content, setContent] = useState(initialContent);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  // Preview/edit toggle. Default: preview when there's existing content (so
  // opening an existing doc feels like a clean read), edit when it's empty
  // (so the user can start typing immediately).
  const initialMode: "edit" | "preview" =
    initialContent.trim().length > 0 ? "preview" : "edit";
  const [mode, setMode] = useState<"edit" | "preview">(initialMode);
  const lastSent = useRef<{ title: string; content: string }>({
    title: initialTitle,
    content: initialContent,
  });

  // When the underlying doc changes, reset local state.
  useEffect(() => {
    setTitle(initialTitle);
    setContent(initialContent);
    lastSent.current = { title: initialTitle, content: initialContent };
    setSavedAt(null);
    setIsSaving(false);
    setMode(initialContent.trim().length > 0 ? "preview" : "edit");
  }, [docId, initialTitle, initialContent]);

  const debouncedTitle = useDebounce(title, AUTOSAVE_DELAY_MS);
  const debouncedContent = useDebounce(content, AUTOSAVE_DELAY_MS);

  useEffect(() => {
    if (readOnly) return;
    const titleChanged = debouncedTitle !== lastSent.current.title;
    const contentChanged = debouncedContent !== lastSent.current.content;
    if (!titleChanged && !contentChanged) return;
    if (!debouncedTitle.trim()) return;

    let cancelled = false;
    setIsSaving(true);
    void onSave({
      title: titleChanged ? debouncedTitle.trim() : undefined,
      content_md: contentChanged ? debouncedContent : undefined,
    })
      .then(() => {
        if (cancelled) return;
        lastSent.current = { title: debouncedTitle, content: debouncedContent };
        setSavedAt(Date.now());
      })
      .catch((err) => {
        if (cancelled) return;
        onSaveError?.(err);
      })
      .finally(() => {
        if (cancelled) return;
        setIsSaving(false);
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedTitle, debouncedContent, readOnly, docId]);

  const titleEditable = !titleReadOnly && !readOnly;
  const titleMissing = titleRequired && !title.trim();

  return (
    <div className="flex flex-1 flex-col min-h-0">
      {headerSlot}

      <div className="border-b">
      <div className="flex items-center gap-2 px-4 py-3">
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          readOnly={!titleEditable}
          dir="rtl"
          aria-invalid={titleMissing}
          className={cn(
            "flex-1 bg-transparent text-sm font-semibold focus:outline-none disabled:cursor-not-allowed read-only:cursor-default",
            titleMissing
              ? "text-destructive placeholder:text-destructive/60"
              : "text-foreground",
          )}
          placeholder={titlePlaceholder}
        />
        <div
          className={cn(
            "flex shrink-0 items-center gap-0.5 rounded-md border border-border bg-muted/30 p-0.5",
          )}
          role="tablist"
          aria-label="وضع العرض"
        >
          <Button
            type="button"
            variant={mode === "edit" ? "default" : "ghost"}
            size="sm"
            className="h-6 gap-1 px-2 text-[11px]"
            role="tab"
            aria-selected={mode === "edit"}
            onClick={() => setMode("edit")}
            disabled={readOnly && mode !== "edit"}
          >
            <Pencil className="h-3 w-3" />
            تحرير
          </Button>
          <Button
            type="button"
            variant={mode === "preview" ? "default" : "ghost"}
            size="sm"
            className="h-6 gap-1 px-2 text-[11px]"
            role="tab"
            aria-selected={mode === "preview"}
            onClick={() => setMode("preview")}
          >
            <Eye className="h-3 w-3" />
            معاينة
          </Button>
        </div>
      </div>
      {titleMissing && (
        <p className="px-4 pb-2 -mt-1 text-[11px] text-destructive">
          العنوان مطلوب
        </p>
      )}
      </div>

      {mode === "edit" ? (
        <ScrollArea className="flex-1">
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            readOnly={readOnly}
            dir="rtl"
            className="block h-full min-h-[400px] w-full resize-none border-0 bg-transparent p-4 text-sm leading-relaxed focus:outline-none read-only:cursor-default"
            placeholder={bodyPlaceholder}
          />
        </ScrollArea>
      ) : (
        <ArtifactPreview content={content} footer={footerSlot} />
      )}

      <div className="flex items-center justify-between border-t px-4 py-2 text-[11px] text-muted-foreground">
        <span>
          {isSaving ? (
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
          }).format(new Date(updatedAt))}
        </span>
      </div>
    </div>
  );
}
