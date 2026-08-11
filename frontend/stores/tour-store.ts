import { create } from "zustand";

/**
 * Open/step state for «جولة المخرجات» — the coach-mark tour that runs on the
 * real UI of the demo conversation (plan §5.1).
 *
 * Lives in a store rather than component state for the same reason
 * `onboarding-store.ts` does: entry points outside the overlay's subtree have
 * to be able to (re)open it — the sidebar settings popover, beside
 * «اتعرف على ريحان» — and the first-run trigger fires from a component that is
 * not the overlay.
 *
 * Deliberately dumb: no persistence, no step table, no knowledge of the
 * script's length. `TourOverlay` owns the script (`components/tour/tour-content.ts`)
 * and closes the tour when `stepIndex` walks past its end, so adding a step is
 * a one-file change.
 */
interface TourState {
  isOpen: boolean;
  /** 0-based index into `TOUR_STEPS`. */
  stepIndex: number;
  /** Open at step 0. Reopening ALWAYS restarts — never resumes mid-tour (§8). */
  open: () => void;
  close: () => void;
  /**
   * Advance one step.
   *
   * `from` is a concurrency guard, not decoration: the stall-guard «التالي»
   * clicks the real anchor and then advances, while the store/DOM watcher may
   * observe that same transition in the same tick and try to advance too.
   * Passing the index the caller believes is current makes the second call a
   * no-op instead of a double skip.
   */
  next: (from?: number) => void;
  prev: () => void;
  goTo: (index: number) => void;
}

export const useTourStore = create<TourState>((set) => ({
  isOpen: false,
  stepIndex: 0,

  open: () => set({ isOpen: true, stepIndex: 0 }),

  close: () => set({ isOpen: false }),

  next: (from) =>
    set((state) => {
      if (from !== undefined && state.stepIndex !== from) return state;
      return { stepIndex: state.stepIndex + 1 };
    }),

  prev: () =>
    set((state) => ({ stepIndex: Math.max(0, state.stepIndex - 1) })),

  goTo: (index) => set({ stepIndex: Math.max(0, index) }),
}));
