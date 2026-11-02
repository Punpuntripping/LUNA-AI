import { create } from "zustand";
import { plansApi } from "@/lib/api";

/**
 * الأسبوع المجاني (migration 205): the SERVER's answer to "may this account claim
 * the free week?". Loaded once for the chat shell so the limit dialog opens with
 * the right cards, and re-asked every time that dialog opens.
 *
 * ⚠ THE DEFAULT IS "NOT ELIGIBLE", the same safety property as
 * `intro-offer-store`: a slow, missing or failing endpoint degrades to the
 * ordinary plans, never to an offer the server will refuse.
 */

interface FreeTrialStore {
  eligible: boolean;
  /** Re-ask the server. Concurrent callers share one request. */
  refresh: () => Promise<boolean>;
  /** The week was just granted: stop offering it without another round trip. */
  markClaimed: () => void;
  /** Back to "not eligible" on sign-out / account switch. */
  reset: () => void;
}

let inflight: Promise<boolean> | null = null;
// Bumped by reset(): an answer in flight for the PREVIOUS account must not land
// on the next one signing in on this browser.
let generation = 0;

export const useFreeTrialStore = create<FreeTrialStore>((set) => ({
  eligible: false,

  refresh: () => {
    if (inflight) return inflight;
    const gen = generation;
    const request = (async () => {
      let eligible = false;
      try {
        eligible = (await plansApi.freeTrial()).eligible === true;
      } catch {
        eligible = false;
      }
      if (gen !== generation) return false;
      set({ eligible });
      return eligible;
    })();
    inflight = request;
    void request.finally(() => {
      if (inflight === request) inflight = null;
    });
    return request;
  },

  markClaimed: () => set({ eligible: false }),

  reset: () => {
    generation += 1;
    inflight = null;
    set({ eligible: false });
  },
}));
