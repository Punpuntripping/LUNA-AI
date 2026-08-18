import { create } from "zustand";
import { paymentsApi } from "@/lib/api";
import {
  EARLY_ADOPTER_CAMPAIGN_CLOSED,
  normalizeEarlyAdopterCampaign,
} from "@/lib/pricing";
import type { EarlyAdopterCampaign } from "@/types";

/**
 * المشتركون الأوائل campaign state for the CLIENT surfaces — the quota upgrade
 * dialog and إعدادات الحساب (`.claude/plans/early_adopters.md` §6).
 *
 * The server-rendered price surfaces (`/pricing`, the landing teaser) do NOT use
 * this: they await `fetchEarlyAdopterCampaign()` during the render and cache the
 * answer with ISR. This store exists for the two dialogs, which mount long after
 * the page did and would otherwise each grow their own fetch.
 *
 * ⚠ THE DEFAULT IS "CLOSED", and that is the whole safety property. Nothing
 * shows a discount, a strikethrough or «المقاعد محدودة» until the answer has
 * actually arrived — so the failure mode of a slow, missing or 404ing endpoint
 * is a list price, never a promo price the checkout will not honour.
 *
 * ⚠ NO COUNT LIVES HERE. The payload carries no remaining-seat number by design
 * and this store must never derive, cache or log one (plan §1.10).
 */
interface EarlyAdopterStore {
  /** The campaign answer. Starts — and stays, on any failure — closed. */
  campaign: EarlyAdopterCampaign;
  /** True once a successful answer has been stored. */
  isLoaded: boolean;
  /** A probe is in flight; a second caller must not start another. */
  isLoading: boolean;
  /**
   * Fetch once per session. Safe to call from every consumer's mount effect:
   * concurrent callers collapse onto the first probe, and a completed one is
   * never repeated.
   *
   * A FAILED probe leaves `isLoaded` false so the next consumer to mount may try
   * again. That is deliberately not a retry loop — nothing here polls; the only
   * thing that triggers a second attempt is the user opening another dialog —
   * and the alternative (latching a network blip at app boot into "no campaign
   * for the whole session") silently suppresses a live offer.
   */
  ensureLoaded: () => void;
}

export const useEarlyAdopterStore = create<EarlyAdopterStore>((set, get) => ({
  campaign: EARLY_ADOPTER_CAMPAIGN_CLOSED,
  isLoaded: false,
  isLoading: false,

  ensureLoaded: () => {
    const { isLoaded, isLoading } = get();
    if (isLoaded || isLoading) return;
    set({ isLoading: true });
    void (async () => {
      try {
        const raw = await paymentsApi.getEarlyAdopter();
        // Parsed by the SAME normaliser the server fetcher uses, so a malformed
        // or partial payload degrades to list prices identically on both paths.
        set({
          campaign: normalizeEarlyAdopterCampaign(raw),
          isLoaded: true,
          isLoading: false,
        });
      } catch {
        // Silent: a campaign that cannot be read is a campaign that is not
        // advertised. There is nothing here worth interrupting the user for,
        // and the endpoint simply 404s on a backend older than this feature.
        set({
          campaign: EARLY_ADOPTER_CAMPAIGN_CLOSED,
          isLoaded: false,
          isLoading: false,
        });
      }
    })();
  },
}));
