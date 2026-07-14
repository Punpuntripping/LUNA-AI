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
import { StepAgents } from "./steps/StepAgents";
import { StepWorkspace } from "./steps/StepWorkspace";
import { StepQuestions } from "./steps/StepQuestions";

const TOTAL_STEPS = 3;

/**
 * «اتعرف على ريحان» — 3-step tour. Auto-opens once per user: gated on
 * `preferences.onboarding_seen` (absent/false after a successful hydrate →
 * open). ANY dismissal (skip, X, ESC, finish, picking a question) marks the
 * flag so the tour never nags. Reopenable anytime from the sidebar settings
 * popover via `useOnboardingStore.open()`. Mounted in ChatLayoutClient so it
 * only ever renders inside the authenticated /chat shell.
 */
export function OnboardingDialog() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isHydrated = usePreferencesStore((s) => s.isHydrated);
  const onboardingSeen = usePreferencesStore((s) => s.onboardingSeen);

  const open = useOnboardingStore((s) => s.isOpen);
  const [step, setStep] = useState(0);

  // Always restart from the first step when (re)opened — a manual reopen
  // from settings should never resume mid-tour.
  useEffect(() => {
    if (open) setStep(0);
  }, [open]);

  // Preferences hydration is otherwise lazy (settings dialogs trigger it) —
  // kick it here so the first-run flag is known right after login.
  useEffect(() => {
    if (isAuthenticated && !isHydrated) {
      void usePreferencesStore.getState().hydrate();
    }
  }, [isAuthenticated, isHydrated]);

  useEffect(() => {
    if (isAuthenticated && isHydrated && !onboardingSeen) {
      useOnboardingStore.getState().open();
    }
  }, [isAuthenticated, isHydrated, onboardingSeen]);

  const finish = () => {
    useOnboardingStore.getState().close();
    void usePreferencesStore.getState().markOnboardingSeen();
  };

  const handlePickQuestion = (question: string) => {
    useChatStore.getState().injectComposerText(question);
    finish();
  };

  const isLast = step === TOTAL_STEPS - 1;

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
            جولة سريعة في ثلاث خطوات قبل أول سؤال.
          </DialogDescription>
        </DialogHeader>

        {step === 0 && <StepAgents />}
        {step === 1 && <StepWorkspace />}
        {step === 2 && <StepQuestions onPickQuestion={handlePickQuestion} />}

        <div className="flex items-center justify-between pt-2">
          {/* progress dots */}
          <div className="flex items-center gap-1.5" aria-hidden>
            {Array.from({ length: TOTAL_STEPS }, (_, i) => (
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
              <Button variant="ghost" size="sm" onClick={finish}>
                تخطّي
              </Button>
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
                ابدأ الاستخدام
              </Button>
            ) : (
              <Button
                size="sm"
                onClick={() => setStep((s) => Math.min(TOTAL_STEPS - 1, s + 1))}
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
