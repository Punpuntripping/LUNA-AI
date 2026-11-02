import { create } from "zustand";
import { plansApi } from "@/lib/api";
import { FREE_TRIAL_DEFAULT_DAYS } from "@/components/promo/free-trial-content";

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
  /** How long this account's trial runs. Only meaningful while eligible. */
  days: number;
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
  days: FREE_TRIAL_DEFAULT_DAYS,

  refresh: () => {
    if (inflight) return inflight;
    const gen = generation;
    const request = (async () => {
      let eligible = false;
      let days = FREE_TRIAL_DEFAULT_DAYS;
      try {
        const answer = await plansApi.freeTrial();
        eligible = answer.eligible === true;
        days = answer.days ?? FREE_TRIAL_DEFAULT_DAYS;
      } catch {
        eligible = false;
      }
      if (gen !== generation) return false;
      set({ eligible, days });
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
