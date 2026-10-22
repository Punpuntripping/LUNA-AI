"use client";

import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useAuthStore } from "@/stores/auth-store";
import { usePreferencesStore } from "@/stores/preferences-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { useIntroOfferStore } from "@/stores/intro-offer-store";
import { usePromoPopupHold } from "@/components/promo/use-promo-popup";
import { STEP_PROFESSION } from "./onboarding-content";
import {
  StepProfession,
  type ProfessionSelection,
} from "./steps/StepProfession";

const PROFESSION_GROUP_KEYS = [
  "legal",
  "entrepreneur",
  "specialist",
  "individual",
  "declined",
] as const;

/** The groups «عرض الشهر الأول» can apply to. Only a HINT that asking the
 *  server is worthwhile — eligibility itself is decided server-side. */
const INTRO_OFFER_GROUPS: ReadonlySet<string> = new Set([
  "legal",
  "specialist",
  "entrepreneur",
]);

/** Map the stored users.profession_* back into a step selection — stored
 *  "unknown" (degraded read) and null (never asked) both start untouched. */
function selectionFromUser(): ProfessionSelection {
  const user = useAuthStore.getState().user;
  const group = user?.profession_group;
  if (
    group &&
    (PROFESSION_GROUP_KEYS as readonly string[]).includes(group)
  ) {
    return {
      group: group as ProfessionSelection["group"],
      label: user?.profession_label ?? null,
    };
  }
  return { group: null, label: null };
}

/**
 * «وش أقرب وصف لك؟» — the profession question, and since 2026-10-21 the ONLY
 * thing this dialog does: the 3-step «اتعرف على ريحان» tour that used to follow
 * it was retired (`.claude/plans/profession_intro_offer.md` §4.7) and
 * «المحادثة التجريبية» is the walkthrough.
 *
 * Auto-opens per user:
 * - whenever `users.profession_group` is exactly NULL — every brand-new signup,
 *   and the pre-115 accounts that predate the question. The column IS the gate.
 * - ONCE more for an account whose stored answer is «declined», while
 *   `preferences.profession_reask_done` is not set (§4.8). Only the exact
 *   string counts: `"unknown"` (degraded read) and `undefined` never prompt.
 *
 * ANY dismissal (X, ESC, «حفظ») resolves the answer — the picked selection if
 * there is one, «declined» otherwise — AND closes the re-ask for good, so the
 * question never nags. A pick in an offer group then hands over to
 * `IntroOfferDialog` (§4.3).
 *
 * Reopenable anytime from the sidebar settings popover («تغيير المهنة») via
 * `useOnboardingStore.open()`, with the stored answer pre-selected. Mounted in
 * ChatLayoutClient so it only ever renders inside the authenticated shell.
 */
export function OnboardingDialog() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  // undefined = unknown/degraded (fail-closed); ONLY exactly null prompts.
  const professionGroup = useAuthStore((s) => s.user?.profession_group);
  const isHydrated = usePreferencesStore((s) => s.isHydrated);
  const professionReaskDone = usePreferencesStore((s) => s.professionReaskDone);

  // «عندك رمز تفعيل؟» outranks this dialog while its campaign is open — see
  // `components/promo/PromoCodePopup`. Derived from the same stores that popup
  // reads, so the two agree on the FIRST render and never stack; it goes false
  // the instant the popup is resolved, which re-runs the effect below.
  //
  // ⚠ HOLD, not "owed". It stays true through the window where we do not YET
  // know the plan — `/auth/login` returns no `plan_id`, so a brand-new account
  // reads as neither free nor paid until /auth/me answers.
  const promoPopupHold = usePromoPopupHold();

  const open = useOnboardingStore((s) => s.isOpen);
  const [selection, setSelection] = useState<ProfessionSelection>({
    group: null,
    label: null,
  });

  // The stored answer is pre-selected so a reopen doubles as "change my answer".
  useEffect(() => {
    if (open) setSelection(selectionFromUser());
  }, [open]);

  // Preferences hydration is otherwise lazy (settings dialogs trigger it) —
  // kick it here so the re-ask flag is known right after login.
  useEffect(() => {
    if (isAuthenticated && !isHydrated) {
      void usePreferencesStore.getState().hydrate();
    }
  }, [isAuthenticated, isHydrated]);

  useEffect(() => {
    if (!isAuthenticated || !isHydrated) return;
    if (promoPopupHold) return;
    if (
      professionGroup === null ||
      (professionGroup === "declined" && !professionReaskDone)
    ) {
      useOnboardingStore.getState().open();
    }
  }, [
    isAuthenticated,
    isHydrated,
    professionGroup,
    professionReaskDone,
    promoPopupHold,
  ]);

  /** Persist the answer if it changed; a wholly untouched step on a never-asked
   *  account records «declined» (dismissal = declining). Returns the save in
   *  flight, or null when nothing needed saving. */
  const resolveProfession = (): Promise<boolean> | null => {
    const user = useAuthStore.getState().user;
    const stored = user?.profession_group;
    const storedLabel = user?.profession_label ?? null;
    if (selection.group) {
      const label = selection.label?.trim() || null;
      if (selection.group !== stored || label !== storedLabel) {
        return useAuthStore.getState().saveProfession(selection.group, label);
      }
      return null;
    }
    if (stored === null) {
      return useAuthStore.getState().saveProfession("declined", null);
    }
    return null;
  };

  /**
   * Every exit — X, ESC, «حفظ». The ORDER is load-bearing: `saveProfession`
   * and `markProfessionReaskDone` both update their stores synchronously, so
   * by the time the auto-open effect re-runs it already sees the new answer
   * and the closed re-ask. Marking the re-ask on EVERY exit (not only the
   * «declined» run) is what stops a brand-new user who declines at signup from
   * being asked again on the very next render.
   */
  const finish = () => {
    const group = selection.group;
    const saving = resolveProfession();
    void usePreferencesStore.getState().markProfessionReaskDone();
    useOnboardingStore.getState().close();
    // Once the server has STORED an offer-group answer, ask whether the caller
    // is eligible for «عرض الشهر الأول». This dialog is already closed by then,
    // so the two never share the screen. An UNCHANGED offer-group answer (no
    // save — the «تغيير المهنة» reopen pre-selects it) still asks: otherwise an
    // account that answered before the offer existed could never see it here.
    // Eligibility is the server's call either way (it reads the FIRST answer).
    if (group && INTRO_OFFER_GROUPS.has(group)) {
      void useIntroOfferStore
        .getState()
        .openAfterSave(saving ?? Promise.resolve(true), "profession");
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) finish();
      }}
    >
      <DialogContent dir="rtl" className="max-h-[85dvh] max-w-2xl overflow-y-auto">
        {/* The step renders its own visible heading; these two exist for the
            dialog's accessible name and are not drawn a second time. */}
        <DialogHeader className="sr-only">
          <DialogTitle>{STEP_PROFESSION.heading}</DialogTitle>
          <DialogDescription>{STEP_PROFESSION.intro}</DialogDescription>
        </DialogHeader>

        <StepProfession value={selection} onChange={setSelection} />

        <div className="flex items-center justify-end pt-2">
          <Button size="sm" onClick={finish}>
            {STEP_PROFESSION.saveLabel}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
