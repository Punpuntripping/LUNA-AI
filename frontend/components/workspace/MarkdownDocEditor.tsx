"use client";

import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Save, Loader2 } from "lucide-react";
import { ArtifactPreview } from "@/components/workspace/ArtifactPreview";
import { WorkspaceItemActionBar } from "@/components/workspace/WorkspaceItemActionBar";
import { useDebounce } from "@/hooks/use-debounce";
import { cn } from "@/lib/utils";
import type { WorkspaceFeedback } from "@/types";

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
  /** Optional content appended below the body (e.g. the references panel + the
   *  AI disclaimer). Rendered inside the SAME scroll column in BOTH modes —
   *  preview (inside ``ArtifactPreview``'s viewport) and edit (under the
   *  textarea). Switching to «تحرير» must never make المراجع disappear. */
  footerSlot?: ReactNode;
  /**
   * Action-bar wiring. The معاينة/تحرير toggle + نسخ are always present; the
   * host opts into مشاركة / 👍👎 by passing these (agent outputs only).
   */
  onShare?: () => void;
  /**
   * When set, the action bar renders «حفظ كمدونة» (publishable agent outputs
   * only). Forwarded to ``WorkspaceItemActionBar.onSaveBlog``.
   */
  onSaveBlog?: () => void;
  feedback?: WorkspaceFeedback;
  onFeedback?: (next: WorkspaceFeedback) => void;
  feedbackPending?: boolean;
  /**
   * When set, ``[n]`` markers in the preview body become clickable and call
   * this with the citation number (agent_writing wires it to focus the
   * ReferencePanel). Omit for notes/templates — markers stay plain text.
   */
  onBodyCitationClick?: (n: number) => void;
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
  onShare,
  onSaveBlog,
  feedback,
  onFeedback,
  feedbackPending,
  onBodyCitationClick,
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
  const bodyRef = useRef<HTMLTextAreaElement | null>(null);

  // Auto-grow the edit textarea to its full content height.
  //
  // The textarea is NOT its own scroller — it grows and the surrounding column
  // scrolls, so ``footerSlot`` (المراجع + the AI disclaimer) stays reachable
  // below it. A fixed height (the old ``min-h-[400px]``) left dead space in a
  // tall pane and nested a second scrollbar inside a long draft.
  //
  // ``useLayoutEffect``, not ``useEffect``: measuring after paint flashes the
  // collapsed one-row height for a frame every time the editor opens.
  useLayoutEffect(() => {
    const el = bodyRef.current;
    if (!el || mode !== "edit") return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [content, mode, docId]);

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

  const actionBar = (
    <WorkspaceItemActionBar
      floating
      copyText={content}
      mode={mode}
      onModeChange={setMode}
      editDisabled={readOnly}
      onShare={onShare}
      onSaveBlog={onSaveBlog}
      feedback={feedback}
      onFeedback={onFeedback}
      feedbackPending={feedbackPending}
    />
  );

  return (
    <div className="relative flex flex-1 flex-col min-h-0">
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
      </div>
      {titleMissing && (
        <p className="px-4 pb-2 -mt-1 text-[11px] text-destructive">
          العنوان مطلوب
        </p>
      )}
      </div>

      {mode === "edit" ? (
        // ONE native scroll column holding the textarea AND the footer. A Radix
        // ScrollArea cannot host a filling child — its viewport wrapper is
        // ``display: table`` with auto height, so ``h-full`` on the textarea
        // computed to ``auto`` and it fell back to a fixed 400px box.
        <div
          className="flex-1 min-h-0 overflow-y-auto"
          // The textarea grows to its content now, so an empty note is a ~1-row
          // click target in a full-height pane — and an empty note is precisely
          // what ``initialMode`` opens in edit mode. Clicking the blank column
          // below it puts the caret at the end of the body, which is what the
          // old fixed-height box did by accident.
          //
          // ``e.target !== e.currentTarget`` keeps this to the column's OWN
          // blank area: clicks landing inside ``footerSlot`` (reference cards,
          // «عرض المصدر», the إحالات toggle) keep their own behaviour.
          onMouseDown={(e) => {
            if (readOnly || e.target !== e.currentTarget) return;
            const el = bodyRef.current;
            if (!el) return;
            e.preventDefault(); // no focus flicker, no stray selection
            el.focus();
            el.setSelectionRange(el.value.length, el.value.length);
          }}
        >
          <textarea
            ref={bodyRef}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            readOnly={readOnly}
            dir="rtl"
            rows={1}
            className="block w-full resize-none overflow-hidden border-0 bg-transparent p-4 text-sm leading-relaxed focus:outline-none read-only:cursor-default"
            placeholder={bodyPlaceholder}
          />
          {footerSlot ? <div className="px-4 pb-4">{footerSlot}</div> : null}
        </div>
      ) : (
        <ArtifactPreview
          content={content}
          hideToolbar
          onCitationClick={onBodyCitationClick}
          footer={footerSlot}
        />
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

      {/* Floating, draggable action bar overlaying the viewer. */}
      {actionBar}
    </div>
  );
}
