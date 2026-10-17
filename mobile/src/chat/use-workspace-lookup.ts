import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import type { Message, WorkspaceItemKind } from "@contracts";

import { listConversationWorkspace } from "@/api/workspace";

import { workspaceKeys } from "./keys";

// id → kind for the conversation's workspace items, so a bubble knows which
// of its `artifact_ids` is the `agent_search` its `[n]` citations point at
// (plan R1, web MessageList.tsx `artifactLookup`). Same query key the send
// engine reads/invalidates, so a `workspace_item_created` refreshes it.
// Fetched only once some message carries artifact ids.

export type ArtifactKinds = ReadonlyMap<string, WorkspaceItemKind>;

/** What the «افتح ال…» button needs to name a card (web MessageList `artifactLookup`). */
export interface ArtifactInfo {
  kind: WorkspaceItemKind;
  subtype?: string;
  title: string;
}
export type ArtifactInfoMap = ReadonlyMap<string, ArtifactInfo>;

export function useArtifactKinds(conversationId: string | undefined, enabled: boolean): ArtifactKinds {
  const info = useArtifactInfo(conversationId, enabled);
  return useMemo(() => {
    const map = new Map<string, WorkspaceItemKind>();
    for (const [id, entry] of info) map.set(id, entry.kind);
    return map;
  }, [info]);
}

export function useArtifactInfo(conversationId: string | undefined, enabled: boolean): ArtifactInfoMap {
  const { data } = useQuery({
    queryKey: workspaceKeys.byConversation(conversationId ?? ""),
    queryFn: () => listConversationWorkspace(conversationId as string),
    enabled: !!conversationId && enabled,
    staleTime: 30_000,
  });
  return useMemo(() => {
    const map = new Map<string, ArtifactInfo>();
    for (const item of data?.items ?? []) {
      if (!item.kind) continue;
      const subtype = (item.metadata as { subtype?: unknown } | undefined)?.subtype;
      map.set(item.item_id, { kind: item.kind, subtype: typeof subtype === "string" ? subtype : undefined, title: item.title });
    }
    return map;
  }, [data]);
}

/** First `agent_search` id among the message's artifacts (R1), or null. */
export function citationTarget(message: Pick<Message, "artifact_ids">, kinds: ArtifactKinds): string | null {
  for (const id of message.artifact_ids ?? []) if (kinds.get(id) === "agent_search") return id;
  return null;
}
