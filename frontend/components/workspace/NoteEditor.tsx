"use client";

import { useMemo, useState } from "react";
import { Lock, AlertTriangle } from "lucide-react";
import { MarkdownDocEditor } from "@/components/workspace/MarkdownDocEditor";
import { ReferencePanel } from "@/components/workspace/ReferencePanel";
import { useUpdateWorkspaceItem } from "@/hooks/use-workspace";
import { useWorkspaceItemReferences } from "@/hooks/use-workspace-item-references";
import { ApiClientError } from "@/lib/api";
import type { Reference, WorkspaceItem, WriterMetadataReferenceView } from "@/types";

interface NoteEditorProps {
  item: WorkspaceItem;
}

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
  const [conflict, setConflict] = useState<string | null>(null);

  const lockedUntilTs = getLockedUntil(item);
  const isLocked = lockedUntilTs !== null;
  // ``note`` titles are user-editable; ``agent_writing`` titles come from the
  // agent and are read-only here.
  const titleReadOnly = item.kind !== "note";

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

  const handleSave = async (patch: { title?: string; content_md?: string }) => {
    const updated = await update.mutateAsync({
      itemId: item.item_id,
      data: patch,
    });
    // Clear any stale conflict banner once a save lands cleanly.
    setConflict(null);
    return updated;
  };

  const handleSaveError = (err: unknown) => {
    if (err instanceof ApiClientError && err.status === 409) {
      setConflict(err.message || "ريحان يحرر هذا الملف الآن، حاول مجدداً");
    }
  };

  const headerSlot = (
    <>
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
    </>
  );

  const footerSlot =
    item.kind === "agent_writing" ? (
      <ReferencePanel references={references} isLoading={isLoadingReferences} />
    ) : null;

  return (
    <MarkdownDocEditor
      docId={item.item_id}
      initialTitle={item.title}
      initialContent={item.content_md ?? ""}
      updatedAt={item.updated_at}
      onSave={handleSave}
      onSaveError={handleSaveError}
      readOnly={isLocked}
      titleReadOnly={titleReadOnly}
      titlePlaceholder="عنوان الملاحظة..."
      bodyPlaceholder={
        item.kind === "note" ? "اكتب ملاحظاتك هنا..." : "محتوى المسودة..."
      }
      headerSlot={headerSlot}
      footerSlot={footerSlot}
    />
  );
}
