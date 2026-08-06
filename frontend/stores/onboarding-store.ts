import { create } from "zustand";

/**
 * Open/close state for the «اتعرف على ريحان» tour dialog. Lives in a store
 * (not component state) so entry points outside the dialog's subtree can
 * reopen it — first-run auto-open in OnboardingDialog itself, and the
 * sidebar settings popover item.
 *
 * `mode` controls what the dialog shows:
 *  - "full"       — profession step + the 3 tour steps (first run, manual reopen)
 *  - "profession" — profession step alone, for existing users who already saw
 *                   the tour but predate the profession question (users row
 *                   still NULL after migration 115).
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
