import { router, useRootNavigationState } from "expo-router";
import { useEffect } from "react";

import { useSession } from "@/auth/session-store";

import { getVisibleConversation, usePushTap } from "./handling";
import { Notifications } from "./notifications";
import { onDevicePushToken, syncPushRegistration } from "./registration";

/**
 * Signed-in push work, mounted once in the (app) layout (renders nothing):
 * - registration on sign-in / app start and on token rotation (P3) — silent,
 *   and only for a device that opted in with the permission granted;
 * - opening a tapped push's conversation (P5), including one tapped while
 *   signed out, once this account's screens are reachable.
 * Its own component so the navigation-state subscription re-renders nothing else.
 */
export function PushRuntime(): null {
  const userId = useSession((s) => s.user?.user_id ?? null);
  const deletionPending = useSession((s) => s.user?.deletion_pending === true);
  const target = usePushTap((s) => s.target);
  const navReady = Boolean(useRootNavigationState()?.key);

  useEffect(() => {
    if (userId) void syncPushRegistration(userId);
  }, [userId]);

  useEffect(() => {
    const N = Notifications;
    if (!N) return;
    const sub = N.addPushTokenListener((device) => {
      const current = useSession.getState().user?.user_id;
      if (current) void onDevicePushToken(device, current);
    });
    return () => sub.remove();
  }, []);

  useEffect(() => {
    if (!target || deletionPending || !navReady) return;
    usePushTap.getState().clear();
    if (getVisibleConversation() === target.params.id) return;
    router.push(target);
  }, [target, deletionPending, navReady]);

  return null;
}
