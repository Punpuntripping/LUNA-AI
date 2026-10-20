import type { WorkspaceItem } from "@contracts";

import { errorMessage } from "@/api/errors";
import type { ManageActions } from "@/components/artifact/WorkspaceItemActionBar";
import { showToast } from "@/stores/toast";

import { useDeleteWorkspaceItem, useSetVisibility } from "./use-workspace";
import { manageSpec, MANAGE_TOASTS } from "./workspace-model";

/**
 * The action bar's «المزيد» for an open item (plan 05 §W3): إخفاء عن ريحان /
 * إظهار لريحان and حذف (confirmed by the bar). `undefined` where nothing is
 * allowed (demo conversation, convo_context) — the bar then renders no button.
 * `onDeleted` runs after a successful delete (the screen leaves).
 */
export function useItemManage(item: WorkspaceItem | undefined, isDemo: boolean, onDeleted: () => void): ManageActions | undefined {
  const visibility = useSetVisibility();
  const remove = useDeleteWorkspaceItem();
  if (!item) return undefined;
  const spec = manageSpec(item.kind, isDemo);
  if (!spec.hide && !spec.remove) return undefined;

  return {
    isVisible: item.is_visible !== false,
    pending: visibility.isPending || remove.isPending,
    onToggleVisibility: spec.hide
      ? () => {
          const isVisible = item.is_visible === false;
          visibility.mutate(
            { item, isVisible },
            {
              onSuccess: () => showToast(isVisible ? MANAGE_TOASTS.shown : MANAGE_TOASTS.hidden),
              onError: (err) => showToast(errorMessage(err)),
            },
          );
        }
      : undefined,
    onDelete: spec.remove
      ? () => {
          remove.mutate(item, {
            onSuccess: () => {
              showToast(MANAGE_TOASTS.deleted);
              onDeleted();
            },
            onError: (err) => showToast(errorMessage(err)),
          });
        }
      : undefined,
  };
}
