import { create } from "zustand";

/**
 * Open/close state for «وش أقرب وصف لك؟» (web `useOnboardingStore`). A store,
 * not component state, so «تغيير المهنة» in settings can open it, and
 * self-presenting popups can stand down while it is up. Session only — reset
 * on sign-out (session-store `signedOut`).
 */
interface ProfessionPromptState {
  /** Wanted on screen (it presents once no other modal is up or settling). */
  isOpen: boolean;
  /** Bumped on every open — the host seeds the step once per open. */
  openCount: number;
  /** Answered or dismissed in this session → no further auto-open. */
  resolvedThisSession: boolean;
  open: () => void;
  /** Every exit: close, and stop the auto-open for the rest of the session. */
  resolve: () => void;
  reset: () => void;
}

export const useProfessionPrompt = create<ProfessionPromptState>((set) => ({
  isOpen: false,
  openCount: 0,
  resolvedThisSession: false,
  open: () => set((s) => (s.isOpen ? s : { isOpen: true, openCount: s.openCount + 1 })),
  resolve: () => set({ isOpen: false, resolvedThisSession: true }),
  reset: () => set({ isOpen: false, resolvedThisSession: false }),
}));
