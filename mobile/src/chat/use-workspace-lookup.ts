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

export function useArtifactKinds(conversationId: string | undefined, enabled: boolean): ArtifactKinds {
  const { data } = useQuery({
    queryKey: workspaceKeys.byConversation(conversationId ?? ""),
    queryFn: () => listConversationWorkspace(conversationId as string),
    enabled: !!conversationId && enabled,
    staleTime: 30_000,
  });
  return useMemo(() => {
    const map = new Map<string, WorkspaceItemKind>();
    for (const item of data?.items ?? []) if (item.kind) map.set(item.item_id, item.kind);
    return map;
  }, [data]);
}

/** First `agent_search` id among the message's artifacts (R1), or null. */
export function citationTarget(message: Pick<Message, "artifact_ids">, kinds: ArtifactKinds): string | null {
  for (const id of message.artifact_ids ?? []) if (kinds.get(id) === "agent_search") return id;
  return null;
}
