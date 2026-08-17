import { create } from "zustand";

/**
 * Open/close state for the «اتعرف على ريحان» tour dialog. Lives in a store
 * (not component state) so entry points outside the dialog's subtree can
 * reopen it — first-run auto-open in OnboardingDialog itself, and the
 * sidebar settings popover item.
 *
 * `mode` controls what the dialog shows — and, after the retiming in
 * `.claude/plans/edu_series.md` §8, WHICH first-run it is:
 *  - "profession" — profession step alone. What SIGNUP opens (A1), and what
 *                   pre-115 accounts still owing the question get. Gated on
 *                   `users.profession_group === null`, so it is asked once ever.
 *  - "full"       — profession step + the 3 tour steps. Opened after a
 *                   successful payment (A2) and by the sidebar settings item.
 *
 * The mode is also what decides whether a dismissal writes
 * `preferences.onboarding_seen` — see `OnboardingDialog.finish()`. Only the
 * full tour may write it; the profession run must not, or A2 is dead on
 * arrival for every user.
 */
export type OnboardingMode = "full" | "profession";

interface OnboardingState {
  isOpen: boolean;
  mode: OnboardingMode;
  open: (mode?: OnboardingMode) => void;
  close: () => void;
}

export const useOnboardingStore = create<OnboardingState>((set) => ({
  isOpen: false,
  mode: "full",
  open: (mode = "full") => set({ isOpen: true, mode }),
  close: () => set({ isOpen: false }),
}));
