import { requireOptionalNativeModule } from "expo";

// expo-notifications, loaded only when its native module is in the binary.
// It is a native module added in Phase 3: a dev build made before it (or Expo
// Go) would crash on a plain `import` — there the push features stay off
// instead. Release builds always carry it (fingerprint runtime version).

type NotificationsModule = typeof import("expo-notifications");

function load(): NotificationsModule | null {
  if (!requireOptionalNativeModule("ExpoPushTokenManager")) return null;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- guarded native load
    return require("expo-notifications") as NotificationsModule;
  } catch {
    return null;
  }
}

/** null = push unavailable in this binary. */
export const Notifications: NotificationsModule | null = load();
