import { useQuery } from "@tanstack/react-query";

import type { WorkspaceItem, WorkspaceItemReferencesResponse } from "@contracts";

import { getWorkspaceItem, listReferences } from "@/api/workspace";
import { workspaceKeys } from "@/chat/keys";

/** One workspace item (its `content_md` — on a preview-locked item only the
 *  visible part: read it through `wiPreviewVisibleContent`). `null` id = idle.
 *  Invalidated by the chat's `workspace_item_*` events through `workspaceKeys.all`. */
export function useWorkspaceItem(itemId: string | null) {
  return useQuery<WorkspaceItem>({
    queryKey: workspaceKeys.detail(itemId ?? ""),
    queryFn: () => getWorkspaceItem(itemId as string),
    enabled: itemId !== null,
  });
}

/** The FREE citation list of an agent output (no source bodies) + the locked
 *  item's `hidden_count`. The reader route reads the same cache entry, so a
 *  card opens with no request. */
export function useReferences(itemId: string | null, enabled = true) {
  return useQuery<WorkspaceItemReferencesResponse>({
    queryKey: workspaceKeys.references(itemId ?? "", false),
    queryFn: () => listReferences(itemId as string),
    enabled: enabled && itemId !== null,
  });
}

/** The agent's edit lock (`metadata.locked_until`, or the column when the
 *  backend sends it) — same rule as the web NoteEditor. */
export function isAgentLocked(item: WorkspaceItem, now: number = Date.now()): boolean {
  const column = (item as WorkspaceItem & { locked_by_agent_until?: unknown }).locked_by_agent_until;
  const meta = item.metadata?.locked_until;
  const raw = typeof column === "string" ? column : typeof meta === "string" ? meta : null;
  if (!raw) return false;
  const ts = Date.parse(raw);
  return Number.isFinite(ts) && ts > now;
}
