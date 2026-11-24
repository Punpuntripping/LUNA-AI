"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useChatStore } from "@/stores/chat-store";
import { useAngleMapList, useAngleMaps, useFilesStatus } from "@/hooks/use-files";
import { AnglesStack, FilesViewSwitch, type FilesView } from "./AnglesViews";

/**
 * The chat pane's «الملفات» tab (owner, 2026-11-24 — angle_map_ui.md rev 3):
 * when the conversation has angle maps, the switch «الخيوط» | «الملفات» shows,
 * open on «الملفات» (the file list, `children`; owner 2026-11-24); «الخيوط» =
 * its maps stacked, newest first. No maps (or the feature off for the user):
 * the file list alone, as before.
 *
 * A chat chip that highlights a file brings «الملفات» forward, so the ringed
 * card is on screen.
 */
export function PaneFilesTab({ conversationId, children }: { conversationId: string; children: ReactNode }) {
  const { angleMaps } = useFilesStatus();
  const list = useAngleMapList(conversationId);
  const summaries = useMemo(() => (angleMaps ? list.data?.maps ?? [] : []), [angleMaps, list.data]);
  const [view, setView] = useState<FilesView>("files");
  // The full maps load only once «الخيوط» is chosen — «الملفات» is the default.
  const ids = useMemo(() => (view === "angles" ? summaries.map((m) => m.map_id) : []), [view, summaries]);
  const details = useAngleMaps(ids);

  const highlighted = useChatStore(
    (s) => s.workspaceByConversation[conversationId]?.highlightedItemId ?? null,
  );
  useEffect(() => {
    if (highlighted) setView("files");
  }, [highlighted]);

  if (summaries.length === 0) return <>{children}</>;

  // A map that no longer opens (stale since the list was read) is left out.
  const maps = details.flatMap((q) => (q.isError ? [] : [q.data ?? null]));
  const allFailed = details.length > 0 && details.every((q) => q.isError);

  return (
    <div>
      <div className="flex justify-center border-b border-border/60 px-2 py-2">
        <FilesViewSwitch value={view} onChange={setView} />
      </div>
      {view === "angles" ? <AnglesStack maps={maps} error={allFailed} /> : children}
    </div>
  );
}
