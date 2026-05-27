"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Lock, Save, Loader2, AlertTriangle, Eye, Pencil } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { ArtifactPreview } from "@/components/workspace/ArtifactPreview";
import { ReferencePanel } from "@/components/workspace/ReferencePanel";
import { useUpdateWorkspaceItem } from "@/hooks/use-workspace";
import { useWorkspaceItemReferences } from "@/hooks/use-workspace-item-references";
import { useDebounce } from "@/hooks/use-debounce";
import { ApiClientError } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { Reference, WorkspaceItem, WriterMetadataReferenceView } from "@/types";

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
 * Read the writer-publisher's ``metadata.references`` projection off an
 * ``agent_writing`` workspace item. The publisher writes one entry per body
 * citation with ``{n, source_wi, source_n, ref_id, domain}`` — enough to map
 * each rich Reference (fetched from ``/workspace/{id}/references``) back to
 * the source research WI it was pulled from.
 *
 * Returns ``[]`` when the field is missing, malformed, or this isn't an
 * agent_writing item — callers fall back to the un-attributed list.
 */
function readWriterMetadataRefsView(
  item: WorkspaceItem,
): WriterMetadataReferenceView[] {
  if (item.kind !== "agent_writing") return [];
  const raw = (item.metadata as { references?: unknown } | undefined)?.references;
  if (!Array.isArray(raw)) return [];
  const out: WriterMetadataReferenceView[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== "object") continue;
    const e = entry as Record<string, unknown>;
    const n = typeof e.n === "number" ? e.n : Number(e.n);
    const sourceN = typeof e.source_n === "number" ? e.source_n : Number(e.source_n);
    if (!Number.isFinite(n) || !Number.isFinite(sourceN)) continue;
    const sourceWi =
      typeof e.source_wi === "string" && e.source_wi.length > 0
        ? e.source_wi
        : null;
    const refId = typeof e.ref_id === "string" ? e.ref_id : "";
    const domain = e.domain;
    if (domain !== "regulations" && domain !== "cases" && domain !== "compliance") {
      continue;
    }
    out.push({
      n,
      source_wi: sourceWi,
      source_n: sourceN,
      ref_id: refId,
      domain,
    });
  }
  return out;
}

/**
 * Merge the writer-publisher's metadata view onto the rich ``Reference[]``
 * returned by the backend. Keys on ``n`` (writer body order) — that's the
 * same number both shapes use to identify a citation, so the merge is a
 * direct lookup. Refs without a matching metadata entry pass through
 * untouched, which is the correct behaviour for ``agent_search`` items.
 */
function overlayWriterAttribution(
  references: Reference[],
  view: WriterMetadataReferenceView[],
): Reference[] {
  if (view.length === 0) return references;
  const byN = new Map<number, WriterMetadataReferenceView>();
  for (const entry of view) byN.set(entry.n, entry);
  return references.map((ref) => {
    const overlay = byN.get(ref.n);
    if (!overlay) return ref;
    return {
      ...ref,
      source_wi: overlay.source_wi,
      source_n: overlay.source_n,
    };
  });
}

/**
 * Markdown editor used for ``note`` items (always editable) and
 * ``agent_writing`` items (editable when the agent lock is not held).
 *
 * Behaviour:
 * - 800 ms debounced autosave on change.
 * - When the agent holds the lock, the textarea becomes read-only and a
 *   "ريحان يحرر…" banner shows above it.
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
  // Preview/edit toggle. Default: preview when there's existing content (so
  // opening an existing note feels like a clean read), edit when the note is
  // empty (so the user can start typing immediately). The same toggle covers
  // ``note`` and ``agent_writing`` kinds — the markdown renderer doesn't care
  // who authored the body.
  const initialMode: "edit" | "preview" =
    (item.content_md ?? "").trim().length > 0 ? "preview" : "edit";
  const [mode, setMode] = useState<"edit" | "preview">(initialMode);
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
    setMode((item.content_md ?? "").trim().length > 0 ? "preview" : "edit");
  }, [item.item_id, item.title, item.content_md]);

  const debouncedTitle = useDebounce(title, AUTOSAVE_DELAY_MS);
  const debouncedContent = useDebounce(content, AUTOSAVE_DELAY_MS);

  const lockedUntilTs = getLockedUntil(item);
  const isLocked = lockedUntilTs !== null;
  const titleEditable = item.kind === "note";

  // Bug #1 (publisher-side fix): for ``agent_writing`` items the writer
  // publisher writes BOTH ``workspace_item_references`` rows AND a thin
  // ``metadata.references`` projection that carries the source-WI alias for
  // each citation. We fetch the rich Reference[] from the backend (same
  // endpoint as ``agent_search``) and overlay ``source_wi`` / ``source_n``
  // from the metadata blob so the panel can show provenance.
  const { data: rawReferences = [], isLoading: isLoadingReferences } =
    useWorkspaceItemReferences(item.item_id, {
      enabled: item.kind === "agent_writing",
    });
  const metadataRefsView = useMemo(
    () => readWriterMetadataRefsView(item),
    [item],
  );
  const references = useMemo(
    () => overlayWriterAttribution(rawReferences, metadataRefsView),
    [rawReferences, metadataRefsView],
  );

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
            setConflict(err.message || "ريحان يحرر هذا الملف الآن، حاول مجدداً");
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
          <span>ريحان يحرر هذا الملف الآن…</span>
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

      <div className="flex items-center gap-2 border-b px-4 py-3">
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          readOnly={!titleEditable || isLocked}
          dir="rtl"
          className="flex-1 bg-transparent text-sm font-semibold text-foreground focus:outline-none disabled:cursor-not-allowed read-only:cursor-default"
          placeholder="عنوان الملاحظة..."
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
            disabled={isLocked && mode !== "edit"}
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

      {mode === "edit" ? (
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
      ) : (
        <ArtifactPreview
          content={content}
          footer={
            item.kind === "agent_writing" ? (
              <ReferencePanel
                references={references}
                isLoading={isLoadingReferences}
              />
            ) : null
          }
        />
      )}

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
