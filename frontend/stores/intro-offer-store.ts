import { create } from "zustand";
import { paymentsApi } from "@/lib/api";
import type { IntroOfferState } from "@/types";

/**
 * عرض الشهر الأول — the caller's profession intro offer
 * (`.claude/plans/profession_intro_offer.md` §4.1).
 *
 * Two jobs: hold the SERVER's eligibility answer, and own the open state of
 * `IntroOfferDialog` (a store, not component state, so the onboarding dialog
 * can open it after a profession save and `TourOverlay` can wait on it).
 *
 * ⚠ THE DEFAULT IS "NOT ELIGIBLE", and that is the safety property — same rule
 * as `early-adopter-store`. Nothing shows 1.99 / 9.90 until the server has said
 * so, and a slow, missing or 404ing endpoint degrades to the ordinary prices,
 * never to an offer the checkout will not honour.
 *
 * ⚠ Never copy these prices into `PRICING_PLANS.promoPrice`: the plan ladder
 * ranks on it (`pricing.ts` — `pricingPlansAbove`), and a 1.99 pro would sort
 * below basic and drop out of every basic user's upgrade list.
 */

export type IntroOfferTrigger = "profession" | "quota";

/** An eligible answer with every price present, or nothing. */
export interface IntroOffer {
  profession_group: string;
  first_month_sar: string;
  then_sar: string;
  then_months: number;
  list_sar: string;
}

const MONEY_RE = /^\d+(\.\d{1,2})?$/;

/** Fail-closed parse: anything short of a complete eligible answer is "no". */
function normalize(raw: IntroOfferState | null | undefined): IntroOffer | null {
  if (!raw || raw.eligible !== true) return null;
  const { profession_group, first_month_sar, then_sar, list_sar } = raw;
  if (
    !profession_group ||
    !first_month_sar ||
    !then_sar ||
    !list_sar ||
    !MONEY_RE.test(first_month_sar) ||
    !MONEY_RE.test(then_sar) ||
    !MONEY_RE.test(list_sar)
  ) {
    return null;
  }
  const months = Number(raw.then_months);
  return {
    profession_group,
    first_month_sar,
    then_sar,
    then_months: Number.isFinite(months) && months > 0 ? months : 2,
    list_sar,
  };
}

interface IntroOfferStore {
  /** The eligible offer, or null. Starts — and stays, on any failure — null. */
  offer: IntroOffer | null;
  isOpen: boolean;
  /**
   * A profession was just saved and we are asking the server whether to open
   * the offer. `TourOverlay` waits on this as well as on `isOpen`: its root
   * sits ABOVE every Radix layer, so a tour started in this gap would bury the
   * dialog that opens a moment later.
   */
  checking: boolean;
  /** What opened the dialog — rides the analytics events. */
  trigger: IntroOfferTrigger | null;
  /**
   * Re-ask the server. Concurrent callers share one request. Resolves to the
   * fresh offer (null = not eligible) so a caller can decide to open on it.
   */
  refresh: () => Promise<IntroOffer | null>;
  /** Open the dialog — a no-op while not eligible. */
  open: (trigger: IntroOfferTrigger) => void;
  /**
   * After `saved` resolves `true`, re-ask the server and open the dialog if the
   * caller is eligible. `checking` is held for the whole wait.
   */
  openAfterSave: (saved: Promise<boolean>, trigger: IntroOfferTrigger) => Promise<void>;
  close: () => void;
  /** Back to "not eligible" — on sign-out / account switch. */
  reset: () => void;
}

let inflight: Promise<IntroOffer | null> | null = null;
// Bumped by reset(): an answer that was in flight for the PREVIOUS account
// must not land on the next one signing in on this browser.
let generation = 0;

export const useIntroOfferStore = create<IntroOfferStore>((set, get) => ({
  offer: null,
  isOpen: false,
  checking: false,
  trigger: null,

  refresh: () => {
    if (inflight) return inflight;
    const gen = generation;
    const request = (async () => {
      let offer: IntroOffer | null = null;
      try {
        offer = normalize(await paymentsApi.introOffer());
      } catch {
        // Silent and fail-closed: an offer we cannot confirm is not advertised.
        offer = null;
      }
      if (gen !== generation) return null;
      // A purchase can turn eligibility off while the dialog is up; never leave
      // an offer on screen the server has withdrawn.
      set(offer ? { offer } : { offer: null, isOpen: false });
      return offer;
    })();
    inflight = request;
    void request.finally(() => {
      if (inflight === request) inflight = null;
    });
    return request;
  },

  open: (trigger) => {
    if (!get().offer) return;
    set({ isOpen: true, trigger });
  },

  openAfterSave: async (saved, trigger) => {
    const gen = generation;
    set({ checking: true });
    try {
      // The offer is priced off the DB profession — never off an optimistic
      // value the server did not store.
      if (!(await saved)) return;
      const offer = await get().refresh();
      if (offer && gen === generation) get().open(trigger);
    } finally {
      if (gen === generation) set({ checking: false });
    }
  },

  close: () => set({ isOpen: false }),

  reset: () => {
    generation += 1;
    inflight = null;
    set({ offer: null, isOpen: false, checking: false, trigger: null });
  },
}));
