import { create } from "zustand";
import type { DetailLevel, UserPreferencesData } from "@/types";
import { preferencesApi, ApiClientError } from "@/lib/api";

const DEFAULT_DETAIL_LEVEL: DetailLevel = "medium";
const VALID_DETAIL_LEVELS: readonly DetailLevel[] = ["low", "medium", "high"] as const;

function coerceDetailLevel(value: unknown): DetailLevel {
  if (typeof value === "string" && (VALID_DETAIL_LEVELS as readonly string[]).includes(value)) {
    return value as DetailLevel;
  }
  return DEFAULT_DETAIL_LEVEL;
}

interface PreferencesState {
  detailLevel: DetailLevel;
  isHydrated: boolean;
  isSaving: boolean;
  error: string | null;

  /** One-shot hydration from the backend. Safe to call multiple times. */
  hydrate: () => Promise<void>;
  /** Optimistically update detail level; PATCH /preferences; rollback on failure. */
  setDetailLevel: (level: DetailLevel) => Promise<void>;
  /** Clear the last error (e.g. after the user dismisses a toast). */
  clearError: () => void;
  /** Reset to defaults (used on logout). */
  reset: () => void;
}

export const usePreferencesStore = create<PreferencesState>((set, get) => ({
  detailLevel: DEFAULT_DETAIL_LEVEL,
  isHydrated: false,
  isSaving: false,
  error: null,

  clearError: () => set({ error: null }),

  reset: () =>
    set({
      detailLevel: DEFAULT_DETAIL_LEVEL,
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
        isHydrated: true,
        error: null,
      });
    } catch (err) {
      // Hydration failures are silent — fall back to defaults but mark hydrated
      // so the toggle is not stuck in a loading state.
      set({
        detailLevel: DEFAULT_DETAIL_LEVEL,
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
}));
