import { create } from "zustand";
import type { DetailLevel, UserPreferencesData } from "@/types";
import { preferencesApi, ApiClientError } from "@/lib/api";
import { useEduStore } from "@/stores/edu-store";

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
   * «اتعرف على ريحان» — *the intro tour has been shown*. Since the retiming in
   * `.claude/plans/edu_series.md` §8 this no longer means "onboarding is done":
   * signup opens the profession step alone and deliberately does NOT set this
   * flag (that would permanently block the post-payment tour for everyone), so
   * it is written ONLY by a dismissal of the full tour.
   *
   * Fail-closed, unchanged: defaults to true (= shown) so the tour never
   * flashes for existing users before/without a successful hydrate — only an
   * explicit absent/false value from the backend opens it.
   */
  onboardingSeen: boolean;
  /**
   * «جولة المخرجات» — the coach-mark tour over the shared demo conversation.
   * Fail-closed exactly like `onboardingSeen`: defaults to true (= seen) so an
   * API blip can never re-nag an existing user. Only an explicit absent/false
   * value from a SUCCESSFUL hydrate opens it, and only after «اتعرف على ريحان»
   * has been dismissed (never both on screen at once).
   */
  tourWorkspaceSeen: boolean;
  /**
   * D8: the user pressed «إخفاء» on the shared demo conversation. Fail-OPEN
   * (defaults to false = visible) — the opposite of the flag above on purpose:
   * this one hides a row rather than opening a modal, and wrongly hiding a
   * conversation off a failed hydrate is worse than showing furniture twice.
   */
  demoConversationHidden: boolean;
  /**
   * «عندك رمز تفعيل؟» — the two-week activation-code popup has been resolved
   * for this account (redeemed OR dismissed; see `PromoCodePopup`).
   *
   * Fail-closed like the two tour flags: defaults to true (= resolved) so a
   * failed hydrate can never flash a promo modal at someone, and only an
   * explicit absent/false from a SUCCESSFUL read opens it.
   */
  promoCodePopupSeen: boolean;
  isHydrated: boolean;
  isSaving: boolean;
  error: string | null;

  /** One-shot hydration from the backend. Safe to call multiple times. */
  hydrate: () => Promise<void>;
  /** Optimistically update detail level; PATCH /preferences; rollback on failure. */
  setDetailLevel: (level: DetailLevel) => Promise<void>;
  /** Optimistically update وضع السرية; PATCH /preferences; rollback on failure. */
  setPrivacyMasking: (enabled: boolean) => Promise<void>;
  /** Mark the intro tour as shown; PATCH /preferences (no rollback — worst case
   *  the tour shows once more next session). Call this ONLY from a dismissal of
   *  the FULL «اتعرف على ريحان» tour, never from the profession-alone run. */
  markOnboardingSeen: () => Promise<void>;
  /** Mark «جولة المخرجات» as seen (finish OR skip); same no-rollback contract. */
  markTourWorkspaceSeen: () => Promise<void>;
  /** «إخفاء» the shared demo conversation for THIS user only (D8). */
  hideDemoConversation: () => Promise<void>;
  /** Resolve the «عندك رمز تفعيل؟» popup — written on EVERY exit path, so the
   *  campaign asks at most once per account. */
  markPromoCodePopupSeen: () => Promise<void>;
  /** Clear the last error (e.g. after the user dismisses a toast). */
  clearError: () => void;
  /** Reset to defaults (used on logout). */
  reset: () => void;
}

export const usePreferencesStore = create<PreferencesState>((set, get) => ({
  detailLevel: DEFAULT_DETAIL_LEVEL,
  privacyMasking: DEFAULT_PRIVACY_MASKING,
  onboardingSeen: true,
  tourWorkspaceSeen: true,
  demoConversationHidden: false,
  promoCodePopupSeen: true,
  isHydrated: false,
  isSaving: false,
  error: null,

  clearError: () => set({ error: null }),

  reset: () => {
    set({
      detailLevel: DEFAULT_DETAIL_LEVEL,
      privacyMasking: DEFAULT_PRIVACY_MASKING,
      onboardingSeen: true,
      tourWorkspaceSeen: true,
      demoConversationHidden: false,
      promoCodePopupSeen: true,
      isHydrated: false,
      isSaving: false,
      error: null,
    });
    // Logout. Piggy-backing on this reset (rather than editing auth-store)
    // covers every teardown path at once — `teardownSession()` and the
    // no-session branch of the restore probe both land here. Without it the
    // next user to sign in on this browser inherits the previous one's turn
    // count and lesson history.
    useEduStore.getState().reset();
  },

  hydrate: async () => {
    try {
      const data = await preferencesApi.get();
      const prefs: UserPreferencesData = data.preferences ?? {};
      set({
        detailLevel: coerceDetailLevel(prefs.detail_level),
        privacyMasking: coercePrivacyMasking(prefs.privacy_masking),
        // Only an explicit true counts as shown — a brand-new user has no
        // stored key, which is what leaves the intro tour still owed until
        // the account turns paid (edu_series §8, A2).
        onboardingSeen: prefs.onboarding_seen === true,
        tourWorkspaceSeen: prefs.tour_workspace_seen === true,
        demoConversationHidden: prefs.demo_conversation_hidden === true,
        promoCodePopupSeen: prefs.promo_code_popup_seen === true,
        isHydrated: true,
        error: null,
      });
      // «سلسلة تعلّم ريحان» rides THIS read — the edu keys (`edu_turns`,
      // `edu_last_shown_at`, `edu_<lesson>`) are in the same blob, so the
      // series costs zero extra requests.
      useEduStore.getState().hydrateFrom(prefs);
    } catch (err) {
      // Hydration failures are silent — fall back to defaults but mark hydrated
      // so the toggle is not stuck in a loading state.
      set({
        detailLevel: DEFAULT_DETAIL_LEVEL,
        privacyMasking: DEFAULT_PRIVACY_MASKING,
        // Fail-closed: never open a tour off a failed hydrate.
        onboardingSeen: true,
        tourWorkspaceSeen: true,
        // Fail-open: never hide the user's demo row off a failed hydrate.
        demoConversationHidden: false,
        // Fail-closed again: never open the promo popup off a failed hydrate.
        promoCodePopupSeen: true,
        isHydrated: true,
        error:
          err instanceof ApiClientError
            ? err.message
            : "تعذر تحميل إعدادات المستخدم",
      });
      // Fail CLOSED, same principle as the two tour flags above: `null` marks
      // every lesson seen so the series stays silent this session. Teaching off
      // a failed read would re-nag users who finished the syllabus long ago.
      useEduStore.getState().hydrateFrom(null);
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

  markTourWorkspaceSeen: async () => {
    if (get().tourWorkspaceSeen) return;
    set({ tourWorkspaceSeen: true });
    try {
      // FLAT key — `merge_preferences` is a SHALLOW merge, so nesting this
      // under a `tour: {…}` object would let one tab's write clobber every
      // sibling preference another tab set ([[project_edu_popups]]).
      await preferencesApi.update({ tour_workspace_seen: true });
    } catch {
      // Same contract as markOnboardingSeen: keep the local flag; worst case
      // the tour offers itself once more next login.
    }
  },

  hideDemoConversation: async () => {
    if (get().demoConversationHidden) return;
    // Optimistic — the row leaves the sidebar on the click, not on the 200.
    set({ demoConversationHidden: true });
    try {
      await preferencesApi.update({ demo_conversation_hidden: true });
    } catch {
      // Swallow, and deliberately do NOT roll back: «إخفاء» is a dismissal,
      // and a row springing back into the sidebar because a PATCH lost a race
      // reads as a bug. Worst case it returns next login.
    }
  },

  markPromoCodePopupSeen: async () => {
    if (get().promoCodePopupSeen) return;
    set({ promoCodePopupSeen: true });
    try {
      // FLAT key — `merge_preferences` is a SHALLOW merge, so nesting this
      // under a `promo: {…}` object would let this write clobber every sibling
      // preference another tab set ([[project_edu_popups]]).
      await preferencesApi.update({ promo_code_popup_seen: true });
    } catch {
      // Same contract as the tour flags: keep the local flag so the popup stays
      // shut this session; worst case it offers itself once more next login.
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
