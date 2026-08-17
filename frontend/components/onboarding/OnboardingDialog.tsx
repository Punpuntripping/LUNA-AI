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
import { useChatStore } from "@/stores/chat-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { cn } from "@/lib/utils";
import { STEP_PROFESSION } from "./onboarding-content";
import {
  StepProfession,
  type ProfessionSelection,
} from "./steps/StepProfession";
import { StepAgents } from "./steps/StepAgents";
import { StepWorkspace } from "./steps/StepWorkspace";
import { StepQuestions } from "./steps/StepQuestions";

const FULL_STEPS = ["profession", "agents", "workspace", "questions"] as const;
const PROFESSION_ONLY_STEPS = ["profession"] as const;

const PROFESSION_GROUP_KEYS = [
  "legal",
  "entrepreneur",
  "specialist",
  "individual",
  "declined",
] as const;

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
 * «اتعرف على ريحان» — profession step + 3-step tour, retimed by
 * `.claude/plans/edu_series.md` §8 so the two halves no longer arrive together.
 *
 * Auto-opens per user, in this priority order:
 * - **profession step ALONE** whenever `users.profession_group` is exactly NULL
 *   — which is every brand-new signup (A1), and also the pre-115 accounts that
 *   predate the question. Asked exactly once ever; the column IS the gate.
 * - **full tour** on the first render after the account turns paid (A2):
 *   `isPaid && !onboarding_seen && profession_group !== null`.
 *
 * ANY dismissal (skip, X, ESC, finish, picking a question) resolves the
 * profession answer — the picked selection if there is one, «declined»
 * otherwise — so neither screen ever nags. Only a dismissal of the FULL tour
 * additionally marks `onboarding_seen`; see `finish()`.
 *
 * Reopenable anytime from the sidebar settings popover via
 * `useOnboardingStore.open()` (full tour, stored profession pre-selected and
 * editable). Mounted in ChatLayoutClient so it only ever renders inside the
 * authenticated shell.
 */
export function OnboardingDialog() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  // undefined = unknown/degraded (fail-closed); ONLY exactly null prompts.
  const professionGroup = useAuthStore((s) => s.user?.profession_group);
  // The paid signal for A2, read off the user object /auth/me already returns
  // (sourced from the user_subscriptions SSoT). Deliberately DERIVED state, not
  // an event handler on the payment callback: `/pay/callback` is a cold boot
  // after a full-page 3DS redirect and lives outside ChatLayoutClient — this
  // dialog is not even mounted there — and a `processing` payment's grant lands
  // later via webhook, which an on-success handler would miss entirely. Reading
  // plan_id on the next /chat render covers both, plus existing paid users.
  const planId = useAuthStore((s) => s.user?.plan_id);
  const isPaid = planId != null && planId !== "free";
  const isHydrated = usePreferencesStore((s) => s.isHydrated);
  const onboardingSeen = usePreferencesStore((s) => s.onboardingSeen);

  const open = useOnboardingStore((s) => s.isOpen);
  const mode = useOnboardingStore((s) => s.mode);
  const [step, setStep] = useState(0);
  const [selection, setSelection] = useState<ProfessionSelection>({
    group: null,
    label: null,
  });

  const stepKeys = mode === "profession" ? PROFESSION_ONLY_STEPS : FULL_STEPS;
  const totalSteps = stepKeys.length;

  // Always restart from the first step when (re)opened — a manual reopen
  // from settings should never resume mid-tour. The stored profession answer
  // is pre-selected so a reopen doubles as "change my answer".
  useEffect(() => {
    if (open) {
      setStep(0);
      setSelection(selectionFromUser());
    }
  }, [open]);

  // Preferences hydration is otherwise lazy (settings dialogs trigger it) —
  // kick it here so the first-run flag is known right after login.
  useEffect(() => {
    if (isAuthenticated && !isHydrated) {
      void usePreferencesStore.getState().hydrate();
    }
  }, [isAuthenticated, isHydrated]);

  // Two first-runs, deliberately far apart in time (plan §8):
  //
  //   A1 · signup  → the profession step ALONE. One question, before the user
  //                  has done anything, instead of a 4-step modal.
  //   A2 · payment → the full tour, once the user has actually bought in.
  //
  // The profession branch is tested FIRST, so the null case can never fall
  // through to the tour: a paid account that still owes the question answers it
  // first and gets the tour on the very next pass (the effect re-runs when
  // `saveProfession` fills the column) rather than both stacking at once.
  // Reaching the second branch therefore already implies `professionGroup`
  // is not null.
  useEffect(() => {
    if (!isAuthenticated || !isHydrated) return;
    if (professionGroup === null) {
      useOnboardingStore.getState().open("profession");
    } else if (isPaid && !onboardingSeen) {
      useOnboardingStore.getState().open("full");
    }
  }, [isAuthenticated, isHydrated, isPaid, onboardingSeen, professionGroup]);

  /** Persist the profession answer if it changed; a wholly untouched step on
   *  a never-asked account records «declined» (dismissal = declining). */
  const resolveProfession = () => {
    const user = useAuthStore.getState().user;
    const stored = user?.profession_group;
    const storedLabel = user?.profession_label ?? null;
    if (selection.group) {
      const label = selection.label?.trim() || null;
      if (selection.group !== stored || label !== storedLabel) {
        void useAuthStore.getState().saveProfession(selection.group, label);
      }
    } else if (stored === null) {
      void useAuthStore.getState().saveProfession("declined", null);
    }
  };

  /**
   * Dismissal — skip, X, ESC, «حفظ» / «ابدأ الاستخدام», or picking a question.
   *
   * ⚠ THE FLAG SPLIT. `onboarding_seen` now means «the intro tour has been
   * shown», and ONLY a dismissal of the FULL tour may set it. Marking it from
   * the profession-alone run — which is what signup opens after A1 — would
   * retire the flag before the tour had ever run and permanently block A2 for
   * every user: the post-payment tour would then never fire for anyone.
   *
   * The profession run needs no flag of its own to stay once-only; its gate is
   * `users.profession_group`, which `resolveProfession()` fills on every exit
   * («declined» when untouched). So the two screens keep independent gates.
   */
  const finish = () => {
    // Read through the store rather than the render-time `mode`: it is set at
    // open() and never changes while open, and close() below leaves it intact.
    const wasFullTour = useOnboardingStore.getState().mode !== "profession";
    resolveProfession();
    useOnboardingStore.getState().close();
    if (wasFullTour) {
      void usePreferencesStore.getState().markOnboardingSeen();
    }
  };

  const handlePickQuestion = (question: string) => {
    useChatStore.getState().injectComposerText(question);
    finish();
  };

  const isLast = step === totalSteps - 1;
  const current = stepKeys[step];

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) finish();
      }}
    >
      <DialogContent dir="rtl" className="max-h-[85dvh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>اتعرف على ريحان</DialogTitle>
          <DialogDescription>
            {/* The full tour no longer runs «قبل أول سؤال» — after A2 it opens
                once the user has paid, and the settings item reopens it at any
                time — so the description no longer claims it does. */}
            {mode === "profession"
              ? "سؤال واحد سريع يساعدنا نطوّر ريحان."
              : "جولة سريعة في أربع خطوات للتعرّف على ريحان."}
          </DialogDescription>
        </DialogHeader>

        {current === "profession" && (
          <StepProfession value={selection} onChange={setSelection} />
        )}
        {current === "agents" && <StepAgents />}
        {current === "workspace" && <StepWorkspace />}
        {current === "questions" && (
          <StepQuestions onPickQuestion={handlePickQuestion} />
        )}

        <div className="flex items-center justify-between pt-2">
          {/* progress dots — pointless for the single-step profession ask */}
          <div className="flex items-center gap-1.5" aria-hidden>
            {totalSteps > 1 &&
              Array.from({ length: totalSteps }, (_, i) => (
                <span
                  key={i}
                  className={cn(
                    "h-2 rounded-full transition-all",
                    i === step ? "w-6 bg-primary" : "w-2 bg-border",
                  )}
                />
              ))}
          </div>

          <div className="flex items-center gap-2">
            {step === 0 ? (
              mode !== "profession" && (
                <Button variant="ghost" size="sm" onClick={finish}>
                  تخطّي
                </Button>
              )
            ) : (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setStep((s) => Math.max(0, s - 1))}
              >
                السابق
              </Button>
            )}
            {isLast ? (
              <Button size="sm" onClick={finish}>
                {mode === "profession"
                  ? STEP_PROFESSION.saveLabel
                  : "ابدأ الاستخدام"}
              </Button>
            ) : (
              <Button
                size="sm"
                onClick={() => setStep((s) => Math.min(totalSteps - 1, s + 1))}
              >
                التالي
              </Button>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
