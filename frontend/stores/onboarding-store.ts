import { create } from "zustand";

/**
 * Open/close state for the «اتعرف على ريحان» tour dialog. Lives in a store
 * (not component state) so entry points outside the dialog's subtree can
 * reopen it — first-run auto-open in OnboardingDialog itself, and the
 * sidebar settings popover item.
 */
interface OnboardingState {
  isOpen: boolean;
  open: () => void;
  close: () => void;
}

export const useOnboardingStore = create<OnboardingState>((set) => ({
  isOpen: false,
  open: () => set({ isOpen: true }),
  close: () => set({ isOpen: false }),
}));
