import { create } from "zustand";
import type { DetailLevel, UserPreferencesData } from "@/types";
import { preferencesApi, ApiClientError } from "@/lib/api";

const DEFAULT_DETAIL_LEVEL: DetailLevel = "medium";
const VALID_DETAIL_LEVELS: readonly DetailLevel[] = ["low", "medium", "high"] as const;

// وضع السرية defaults to ON (privacy-by-default decision). Absent/undefined in
// the stored JSONB blob → true. Only an explicit `false` disables it.
const DEFAULT_PRIVACY_MASKING = true;

function coerceDetailLevel(value: unknown): DetailLevel {
  if (typeof value === "string" && (VALID_DETAIL_LEVELS as readonly string[]).includes(value)) {
    return value as DetailLevel;
  }
  return DEFAULT_DETAIL_LEVEL;
}

function coercePrivacyMasking(value: unknown): boolean {
  if (typeof value === "boolean") {
    return value;
  }
  return DEFAULT_PRIVACY_MASKING;
}

interface PreferencesState {
  detailLevel: DetailLevel;
  privacyMasking: boolean;
  /**
   * «اتعرف على ريحان» first-run tour flag. Fail-closed: defaults to true
   * (= seen) so the tour never flashes for existing users before/without a
   * successful hydrate — only an explicit absent/false value from the
   * backend opens it.
   */
  onboardingSeen: boolean;
  isHydrated: boolean;
  isSaving: boolean;
  error: string | null;

  /** One-shot hydration from the backend. Safe to call multiple times. */
  hydrate: () => Promise<void>;
  /** Optimistically update detail level; PATCH /preferences; rollback on failure. */
  setDetailLevel: (level: DetailLevel) => Promise<void>;
  /** Optimistically update وضع السرية; PATCH /preferences; rollback on failure. */
  setPrivacyMasking: (enabled: boolean) => Promise<void>;
  /** Mark the onboarding tour as seen; PATCH /preferences (no rollback — worst case the tour shows once more next session). */
  markOnboardingSeen: () => Promise<void>;
  /** Clear the last error (e.g. after the user dismisses a toast). */
  clearError: () => void;
  /** Reset to defaults (used on logout). */
  reset: () => void;
}

export const usePreferencesStore = create<PreferencesState>((set, get) => ({
  detailLevel: DEFAULT_DETAIL_LEVEL,
  privacyMasking: DEFAULT_PRIVACY_MASKING,
  onboardingSeen: true,
  isHydrated: false,
  isSaving: false,
  error: null,

  clearError: () => set({ error: null }),

  reset: () =>
    set({
      detailLevel: DEFAULT_DETAIL_LEVEL,
      privacyMasking: DEFAULT_PRIVACY_MASKING,
      onboardingSeen: true,
      isHydrated: false,
      isSaving: false,
      error: null,
    }),

  hydrate: async () => {
    try {
      const data = await preferencesApi.get();
      const prefs: UserPreferencesData = data.preferences ?? {};
      set({
        detailLevel: coerceDetailLevel(prefs.detail_level),
        privacyMasking: coercePrivacyMasking(prefs.privacy_masking),
        // Only an explicit true counts as seen — a brand-new user has no
        // stored key, which is exactly the "open the tour" signal.
        onboardingSeen: prefs.onboarding_seen === true,
        isHydrated: true,
        error: null,
      });
    } catch (err) {
      // Hydration failures are silent — fall back to defaults but mark hydrated
      // so the toggle is not stuck in a loading state.
      set({
        detailLevel: DEFAULT_DETAIL_LEVEL,
        privacyMasking: DEFAULT_PRIVACY_MASKING,
        // Fail-closed: never open the tour off a failed hydrate.
        onboardingSeen: true,
        isHydrated: true,
        error:
          err instanceof ApiClientError
            ? err.message
            : "تعذر تحميل إعدادات المستخدم",
      });
    }
  },

  setDetailLevel: async (level: DetailLevel) => {
    const previous = get().detailLevel;
    if (previous === level) return;

    // Optimistic update
    set({ detailLevel: level, isSaving: true, error: null });

    try {
      await preferencesApi.update({ detail_level: level });
      set({ isSaving: false });
    } catch (err) {
      // Rollback on failure
      const message =
        err instanceof ApiClientError
          ? err.message
          : "تعذر حفظ مستوى التفصيل. حاول مرة أخرى.";
      set({ detailLevel: previous, isSaving: false, error: message });
    }
  },

  markOnboardingSeen: async () => {
    if (get().onboardingSeen) return;
    set({ onboardingSeen: true });
    try {
      await preferencesApi.update({ onboarding_seen: true });
    } catch {
      // Swallow — keep the local flag so the tour doesn't reappear this
      // session; worst case it shows once more next login.
    }
  },

  setPrivacyMasking: async (enabled: boolean) => {
    const previous = get().privacyMasking;
    if (previous === enabled) return;

    // Optimistic update
    set({ privacyMasking: enabled, isSaving: true, error: null });

    try {
      // Same JSONB /preferences endpoint as detail_level — payload key is
      // exactly `privacy_masking` (the backend phase reads this key).
      await preferencesApi.update({ privacy_masking: enabled });
      set({ isSaving: false });
    } catch (err) {
      // Rollback on failure
      const message =
        err instanceof ApiClientError
          ? err.message
          : "تعذر حفظ وضع السرية. حاول مرة أخرى.";
      set({ privacyMasking: previous, isSaving: false, error: message });
    }
  },
}));
