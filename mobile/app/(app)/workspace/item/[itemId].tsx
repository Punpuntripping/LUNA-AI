import { Redirect, useLocalSearchParams } from "expo-router";

import { WorkspaceItemScreen } from "@/components/workspace/WorkspaceItemScreen";

/** `/workspace/item/{itemId}` — one workspace item, any kind (plan 05 §W2). */
export default function WorkspaceItemRoute() {
  const { itemId } = useLocalSearchParams<{ itemId: string }>();
  if (typeof itemId !== "string" || !itemId) return <Redirect href="/" />;
  return <WorkspaceItemScreen key={itemId} itemId={itemId} />;
}
