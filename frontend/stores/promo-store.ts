import { create } from "zustand";

/**
 * Is «عندك رمز تفعيل؟» currently on screen?
 *
 * A one-field store, and it exists for one reason: the popup outlives its own
 * gate. Redeeming a code writes `promo_code_popup_seen` immediately — the plan
 * is already applied server-side, so a user who redeems and closes the tab must
 * never be asked again — which flips `usePromoPopupOwed()` false while the
 * success card is still being read. Derived state alone therefore cannot tell
 * «اتعرف على ريحان» to keep waiting, and it opened straight through the success
 * card, stealing the pointer (its own overlay sits at the same z-layer).
 *
 * So: the gate answers "is it owed", this answers "is it up". Onboarding holds
 * on both. Nothing else may write this.
 */
interface PromoState {
  isOpen: boolean;
  setOpen: (open: boolean) => void;
}

export const usePromoStore = create<PromoState>((set) => ({
  isOpen: false,
  setOpen: (open) => set({ isOpen: open }),
}));
