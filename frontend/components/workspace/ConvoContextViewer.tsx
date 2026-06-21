"use client";

import { ArtifactPreview } from "./ArtifactPreview";
import { WorkspaceItemActionBar } from "./WorkspaceItemActionBar";
import type { WorkspaceItem } from "@/types";

interface ConvoContextViewerProps {
  item: WorkspaceItem;
}

/**
 * Read-only render of the running conversation summary.
 *
 * Wave 8D will wire generation cadence; for now this just displays whatever
 * the backend stored. Uses the shared ``ArtifactPreview`` so the copy button
 * and markdown rendering match every other artifact surface.
 */
export function ConvoContextViewer({ item }: ConvoContextViewerProps) {
  if (!item.content_md) {
    return (
      <div className="flex flex-1 items-center justify-center p-8 text-center">
        <p className="text-sm text-muted-foreground">
          لم يُولَّد ملخّص للمحادثة بعد
        </p>
      </div>
    );
  }

  return (
    <div className="relative flex flex-1 min-h-0 flex-col">
      <ArtifactPreview content={item.content_md} hideToolbar />
      <WorkspaceItemActionBar floating copyText={item.content_md} />
    </div>
  );
}
