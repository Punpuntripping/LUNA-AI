import { create } from "zustand";

/**
 * Open/close state for the profession dialog («وش أقرب وصف لك؟»). Lives in a
 * store (not component state) so entry points outside the dialog's subtree can
 * open it — the first-run auto-open in OnboardingDialog itself, and the
 * «تغيير المهنة» item in the sidebar settings popover.
 *
 * The 4-step «اتعرف على ريحان» tour this used to drive was retired on
 * 2026-10-21 (`.claude/plans/profession_intro_offer.md` §4.7) — «المحادثة
 * التجريبية» is the walkthrough now — so there is no longer a mode to choose:
 * the dialog only ever asks the profession question.
 *
 * Other surfaces read `isOpen` to stand down while it is up (TourOverlay, the
 * edu series, the install nudge, the intro offer).
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
