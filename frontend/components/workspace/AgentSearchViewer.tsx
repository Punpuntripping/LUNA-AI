"use client";

import { ArtifactPreview } from "./ArtifactPreview";
import { ReferencePanel } from "./ReferencePanel";
import type { AgentSearchMetadata, WorkspaceItem } from "@/types";

interface AgentSearchViewerProps {
  item: WorkspaceItem;
  /**
   * Window C: when set the matching reference card scrolls into view and
   * flashes once. Set by ``openWorkspaceItemAtReference`` in the chat store
   * (citation marker click); cleared via ``onFlashDone``.
   */
  focusedReferenceN?: number | null;
  onFlashDone?: () => void;
}

/**
 * Read-only render for ``agent_search`` items.
 *
 * The synthesis body (``content_md``) renders via the shared ``ArtifactPreview``
 * (markdown + copy button). The reference list renders inside the same scroll
 * viewport as a JSON-driven ``ReferencePanel`` fed from ``metadata.references``
 * — references live entirely on the artifact, never in the chat.
 *
 * deep_search produces immutable synthesis output -- if the user wants to
 * modify it, the writer pipeline produces a separate ``agent_writing`` row.
 */
export function AgentSearchViewer({
  item,
  focusedReferenceN,
  onFlashDone,
}: AgentSearchViewerProps) {
  const metadata = (item.metadata ?? {}) as AgentSearchMetadata;
  const references = metadata.references ?? [];

  return (
    <ArtifactPreview
      content={item.content_md ?? ""}
      footer={
        <ReferencePanel
          references={references}
          focusedReferenceN={focusedReferenceN}
          onFlashDone={onFlashDone}
        />
      }
    />
  );
}
