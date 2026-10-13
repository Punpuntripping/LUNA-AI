import * as Application from "expo-application";
import * as Updates from "expo-updates";
import { Platform } from "react-native";

import type { AppPlatform } from "@contracts";

/** `ios` | `android`. The app does not ship a web build. */
export const APP_PLATFORM: AppPlatform = Platform.OS === "ios" ? "ios" : "android";

/** Native marketing version (`1.0.0`) — what the backend's 426 gate compares.
 *  OTA updates never change it. In Expo Go this is Expo Go's own version.
 *  Empty when unknown — never a fake "0.0.0", which the 426 gate would block. */
export const APP_VERSION: string = Application.nativeApplicationVersion ?? "";

/** Native build number (auto-incremented by EAS). */
export const APP_BUILD: string = Application.nativeBuildVersion ?? "0";

/** Running EAS Update id, or `embedded` for the bundle shipped in the binary
 *  (and for Expo Go / dev, where expo-updates is disabled). */
export const APP_UPDATE_ID: string = Updates.updateId ?? "embedded";
