"use client";

import { useCallback, useMemo, useState } from "react";
import { ArtifactPreview } from "./ArtifactPreview";
import { ReferencePanel, referenceLabel } from "./ReferencePanel";
import { ShareArtifactDialog } from "./ShareArtifactDialog";
import { AgentOutputDisclaimer } from "./AgentOutputDisclaimer";
import { WorkspaceItemActionBar } from "./WorkspaceItemActionBar";
import { useSetWorkspaceItemFeedback } from "@/hooks/use-workspace";
import { useWorkspaceItemReferences } from "@/hooks/use-workspace-item-references";
import type { WorkspaceFeedback, WorkspaceItem } from "@/types";

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
 * renders inside the same scroll viewport as a JSON-driven ``ReferencePanel``.
 *
 * Migration 049: references no longer live on ``metadata.references``. They
 * are fetched on demand from the relational ``workspace_item_references``
 * table via ``useWorkspaceItemReferences``. The response shape matches the
 * pre-049 ``Reference[]`` so the panel renders identically.
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
  const { data: references = [], isLoading: isLoadingReferences } =
    useWorkspaceItemReferences(item.item_id);
  const setFeedback = useSetWorkspaceItemFeedback();
  const [localFocusedN, setLocalFocusedN] = useState<number | null>(null);
  const [shareOpen, setShareOpen] = useState(false);

  const handleFeedback = useCallback(
    (next: WorkspaceFeedback) => {
      setFeedback.mutate({ itemId: item.item_id, feedback: next });
    },
    [setFeedback, item.item_id],
  );

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

  // The copy button copies the synthesis body PLUS the reference list. The
  // refs render in a sibling panel (footer), not in ``content_md``, so without
  // this the user would copy [n] markers with no titles to resolve them. Each
  // reference is appended as a plain ``{n}-{title}`` line (e.g.
  // "1-نظام إيرادات الدولة") under a «المراجع» heading — number + title only,
  // no snippets/domains/links.
  const copyContent = useMemo(() => {
    const body = item.content_md ?? "";
    if (references.length === 0) return body;
    const refLines = [...references]
      .sort((a, b) => a.n - b.n)
      .map((ref) => `${ref.n}-${referenceLabel(ref)}`)
      .join("\n");
    return body.trim().length > 0
      ? `${body}\n\nالمراجع\n${refLines}`
      : `المراجع\n${refLines}`;
  }, [item.content_md, references]);

  return (
    <div className="relative flex flex-1 min-h-0 flex-col">
      <ArtifactPreview
        content={item.content_md ?? ""}
        copyContent={copyContent}
        hideToolbar
        onCitationClick={handleBodyCitationClick}
        footer={
          <>
            <ReferencePanel
              references={references}
              focusedReferenceN={focusedN}
              onFlashDone={handleFlashDone}
              isLoading={isLoadingReferences}
            />
            <AgentOutputDisclaimer />
          </>
        }
      />
      <WorkspaceItemActionBar
        floating
        copyText={copyContent}
        onShare={() => setShareOpen(true)}
        feedback={item.feedback}
        onFeedback={handleFeedback}
        feedbackPending={setFeedback.isPending}
      />
      <ShareArtifactDialog
        itemId={item.item_id}
        open={shareOpen}
        onOpenChange={setShareOpen}
      />
    </div>
  );
}
