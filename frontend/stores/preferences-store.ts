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
  isHydrated: boolean;
  isSaving: boolean;
  error: string | null;

  /** One-shot hydration from the backend. Safe to call multiple times. */
  hydrate: () => Promise<void>;
  /** Optimistically update detail level; PATCH /preferences; rollback on failure. */
  setDetailLevel: (level: DetailLevel) => Promise<void>;
  /** Optimistically update وضع السرية; PATCH /preferences; rollback on failure. */
  setPrivacyMasking: (enabled: boolean) => Promise<void>;
  /** Clear the last error (e.g. after the user dismisses a toast). */
  clearError: () => void;
  /** Reset to defaults (used on logout). */
  reset: () => void;
}

export const usePreferencesStore = create<PreferencesState>((set, get) => ({
  detailLevel: DEFAULT_DETAIL_LEVEL,
  privacyMasking: DEFAULT_PRIVACY_MASKING,
  isHydrated: false,
  isSaving: false,
  error: null,

  clearError: () => set({ error: null }),

  reset: () =>
    set({
      detailLevel: DEFAULT_DETAIL_LEVEL,
      privacyMasking: DEFAULT_PRIVACY_MASKING,
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
        isHydrated: true,
        error: null,
      });
    } catch (err) {
      // Hydration failures are silent — fall back to defaults but mark hydrated
      // so the toggle is not stuck in a loading state.
      set({
        detailLevel: DEFAULT_DETAIL_LEVEL,
        privacyMasking: DEFAULT_PRIVACY_MASKING,
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
