"use client";

import { useCallback, useState } from "react";
import { ArtifactPreview } from "./ArtifactPreview";
import { ReferencePanel } from "./ReferencePanel";
import type { AgentSearchMetadata, WorkspaceItem } from "@/types";

interface AgentSearchViewerProps {
  item: WorkspaceItem;
  /**
   * Window C: when set the matching reference card scrolls into view and
   * flashes once. Set by ``openWorkspaceItemAtReference`` in the chat store
   * (chat-bubble citation marker click); cleared via ``onFlashDone``.
   */
  focusedReferenceN?: number | null;
  onFlashDone?: () => void;
}

/**
 * Read-only render for ``agent_search`` items.
 *
 * The synthesis body (``content_md``) renders via the shared ``ArtifactPreview``
 * (markdown + copy button + intra-body citation clicks). The reference list
 * renders inside the same scroll viewport as a JSON-driven ``ReferencePanel``
 * fed from ``metadata.references`` — references live entirely on the
 * artifact, never in the chat.
 *
 * Two citation surfaces both target the SAME reference cards:
 * - Chat-bubble ``[n]`` → ``openWorkspaceItemAtReference`` (store-driven,
 *   may also open the pane). Drives ``focusedReferenceN`` prop.
 * - Synthesis-body ``[n]`` (inside this viewer) → local state. No store
 *   round-trip needed; the pane is already open.
 *
 * Both flows feed ReferencePanel via a single coalesced ``focusedN`` value.
 * Whichever fires most recently wins; ``onFlashDone`` clears BOTH.
 */
export function AgentSearchViewer({
  item,
  focusedReferenceN,
  onFlashDone,
}: AgentSearchViewerProps) {
  const metadata = (item.metadata ?? {}) as AgentSearchMetadata;
  const references = metadata.references ?? [];
  const [localFocusedN, setLocalFocusedN] = useState<number | null>(null);

  // Intra-artifact citation click: when the user clicks ``[n]`` inside the
  // synthesis body, focus reference ``n`` in the panel below. No need to go
  // through the chat store — we're already inside the artifact.
  const handleBodyCitationClick = useCallback((n: number) => {
    // Re-arm by clearing first when clicking the same N consecutively; the
    // useEffect in ReferencePanel only fires when the value changes.
    setLocalFocusedN(null);
    // Defer to next tick so React processes the null first, then the new N.
    window.requestAnimationFrame(() => setLocalFocusedN(n));
  }, []);

  // ReferencePanel takes only one focusedReferenceN. Local intra-body click
  // wins over the store value — both clear together via handleFlashDone.
  const focusedN = localFocusedN ?? focusedReferenceN ?? null;

  const handleFlashDone = useCallback(() => {
    setLocalFocusedN(null);
    onFlashDone?.();
  }, [onFlashDone]);

  return (
    <ArtifactPreview
      content={item.content_md ?? ""}
      onCitationClick={handleBodyCitationClick}
      footer={
        <ReferencePanel
          references={references}
          focusedReferenceN={focusedN}
          onFlashDone={handleFlashDone}
        />
      }
    />
  );
}
