import { track } from "@/lib/analytics/client";
import { detectInstallPlatform, isStandalone } from "@/lib/install-app";

/**
 * Per-device install state + install measurement — the LIGHT half of the
 * nudge engine. Split from `stores/install-nudge-store.ts` so the public `/app`
 * page can share it without pulling the chat/edu/onboarding stores into a
 * marketing bundle.
 *
 * State is in localStorage, NOT `user_preferences`: installing is per-DEVICE.
 * A user who installed on their iPhone must still be offered it on their
 * Android, and a server flag would suppress that.
 */

const STORAGE_KEY = "rayhan.install_nudge";
const STANDALONE_SESSION_KEY = "rayhan.standalone_session_tracked";

export interface InstallDeviceState {
  impressions: number;
  snoozedUntil: number | null;
  /** Installed (`appinstalled`) — never nudge this device again. */
  done: boolean;
}

const EMPTY: InstallDeviceState = {
  impressions: 0,
  snoozedUntil: null,
  done: false,
};

export function readInstallState(): InstallDeviceState {
  if (typeof window === "undefined") return EMPTY;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return EMPTY;
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return EMPTY;
    const p = parsed as Partial<InstallDeviceState>;
    return {
      impressions:
        typeof p.impressions === "number" && Number.isFinite(p.impressions)
          ? p.impressions
          : 0,
      snoozedUntil: typeof p.snoozedUntil === "number" ? p.snoozedUntil : null,
      done: p.done === true,
    };
  } catch {
    // Private mode / quota / corrupt JSON — treat as a fresh device. The
    // session cap still bounds the nag to once per load.
    return EMPTY;
  }
}

export function writeInstallState(patch: Partial<InstallDeviceState>): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ ...readInstallState(), ...patch }),
    );
  } catch {
    // Swallow — see above.
  }
}

/**
 * Replay Chrome's install prompt and record the outcome. Shared by the nudge,
 * the settings dialog and `/app` so `install_prompt_result` has one emitter.
 */
export async function runInstallPrompt(
  install: () => Promise<boolean>,
): Promise<boolean> {
  const accepted = await install();
  track("install_prompt_result", {
    outcome: accepted ? "accepted" : "dismissed",
  });
  return accepted;
}

const installedListeners = new Set<() => void>();

/** Notified when `appinstalled` fires — the nudge store closes its card. */
export function onAppInstalled(listener: () => void): () => void {
  installedListeners.add(listener);
  return () => installedListeners.delete(listener);
}

// ---------------------------------------------------------------------------
// Module-load measurement. Imported by the chat shell (via the nudge store) and
// by `/app`, so it runs on every load of either.
// ---------------------------------------------------------------------------

if (typeof window !== "undefined") {
  // `appinstalled` fires on Android/desktop Chrome only — Safari fires nothing.
  window.addEventListener("appinstalled", () => {
    writeInstallState({ done: true });
    installedListeners.forEach((l) => l());
    track("app_installed", { platform: detectInstallPlatform() });
  });

  // The ONLY way to count iOS installs: a session opened from the home-screen
  // icon. Once per tab session (sessionStorage), matching the analytics
  // session key's lifetime.
  if (isStandalone()) {
    let alreadyTracked = false;
    try {
      alreadyTracked =
        window.sessionStorage.getItem(STANDALONE_SESSION_KEY) === "1";
      if (!alreadyTracked) {
        window.sessionStorage.setItem(STANDALONE_SESSION_KEY, "1");
      }
    } catch {
      // Storage blocked — `track()` is a no-op without a session key anyway.
    }
    if (!alreadyTracked) {
      track("standalone_session", { platform: detectInstallPlatform() });
    }
  }
}
