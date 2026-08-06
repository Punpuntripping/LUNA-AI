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
 * «اتعرف على ريحان» — profession step + 3-step tour. Auto-opens per user:
 * - full tour when `preferences.onboarding_seen` is absent/false after a
 *   successful hydrate (first run);
 * - profession step ALONE when the tour was already seen but
 *   `users.profession_group` is still NULL (existing users predating
 *   migration 115 — asked exactly once).
 * ANY dismissal (skip, X, ESC, finish, picking a question) marks the tour
 * seen AND resolves the profession answer — the picked selection if there is
 * one, «declined» otherwise — so neither screen ever nags. Reopenable anytime
 * from the sidebar settings popover via `useOnboardingStore.open()` (full
 * tour, stored profession pre-selected and editable). Mounted in
 * ChatLayoutClient so it only ever renders inside the authenticated shell.
 */
export function OnboardingDialog() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  // undefined = unknown/degraded (fail-closed); ONLY exactly null prompts.
  const professionGroup = useAuthStore((s) => s.user?.profession_group);
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

  useEffect(() => {
    if (!isAuthenticated || !isHydrated) return;
    if (!onboardingSeen) {
      useOnboardingStore.getState().open("full");
    } else if (professionGroup === null) {
      // Tour already seen but the profession question never asked (users row
      // NULL, pre-115 account) — ask it once, alone.
      useOnboardingStore.getState().open("profession");
    }
  }, [isAuthenticated, isHydrated, onboardingSeen, professionGroup]);

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

  const finish = () => {
    resolveProfession();
    useOnboardingStore.getState().close();
    void usePreferencesStore.getState().markOnboardingSeen();
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
      <DialogContent dir="rtl" className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>اتعرف على ريحان</DialogTitle>
          <DialogDescription>
            {mode === "profession"
              ? "سؤال واحد سريع يساعدنا نطوّر ريحان."
              : "جولة سريعة في أربع خطوات قبل أول سؤال."}
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
