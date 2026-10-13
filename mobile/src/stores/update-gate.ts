import { create } from "zustand";

import type { AppConfig } from "@contracts";

import { APP_PLATFORM, APP_VERSION } from "@/lib/app-info";
import { isVersionBelow } from "@/lib/semver";

const ANDROID_STORE_FALLBACK = "https://play.google.com/store/apps/details?id=com.rayhanai.app";

interface UpdateGateState {
  /** Installed native version is below the backend minimum (or any 426 seen). */
  required: boolean;
  /** A newer version exists but this one is still supported. */
  available: boolean;
  bannerDismissed: boolean;
  storeUrl: string | null;
  applyConfig: (config: AppConfig) => void;
  markUpdateRequired: () => void;
  dismissBanner: () => void;
}

export const useUpdateGate = create<UpdateGateState>((set) => ({
  required: false,
  available: false,
  bannerDismissed: false,
  storeUrl: APP_PLATFORM === "android" ? ANDROID_STORE_FALLBACK : null,

  applyConfig: (config) => {
    const min = config.min_supported_version[APP_PLATFORM];
    const latest = config.latest_version[APP_PLATFORM];
    const url = config.store_urls[APP_PLATFORM]?.trim();
    set((s) => ({
      // Never un-require within a session: a 426 already proved it.
      required: s.required || isVersionBelow(APP_VERSION, min),
      available: isVersionBelow(APP_VERSION, latest),
      storeUrl: url || s.storeUrl,
    }));
  },

  markUpdateRequired: () => set({ required: true }),
  dismissBanner: () => set({ bannerDismissed: true }),
}));
