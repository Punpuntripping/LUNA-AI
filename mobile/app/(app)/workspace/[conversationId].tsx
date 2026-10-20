import { Redirect, useLocalSearchParams } from "expo-router";

import { WorkspaceScreen } from "@/components/workspace/WorkspaceScreen";

/** `/workspace/{conversationId}` — «مساحة العمل» of a conversation (plan 05 §W1). */
export default function WorkspaceRoute() {
  const { conversationId } = useLocalSearchParams<{ conversationId: string }>();
  if (typeof conversationId !== "string" || !conversationId) return <Redirect href="/" />;
  return <WorkspaceScreen key={conversationId} conversationId={conversationId} />;
}
