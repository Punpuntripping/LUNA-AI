"use client";

import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { Check, FileCheck2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { WiBadge } from "@/components/workspace/WiBadge";
import {
  FILE_MATCH_OPTIONS,
  fileMatchQuestionParts,
  type FileMatch,
  type FileMatchDecision,
} from "@/lib/contracts";

/** Long enough to see the choice land before the next question replaces it. */
const ADVANCE_DELAY_MS = 220;

/** One picked file the backend recognised (`POST /files/match`). */
export interface FileMatchDialogItem {
  /** The caller's own key for the picked file (e.g. its pending-upload id). */
  key: string;
  match: FileMatch;
}

export interface FileMatchDialogProps {
  open: boolean;
  /**
   * One question per matched file, in this order. May GROW while the popup is
   * open (a second file matched in quick succession joins as one more
   * question) and may SHRINK (a file whose chip was removed leaves).
   */
  items: FileMatchDialogItem[];
  /**
   * Fired once per showing, keyed by `FileMatchDialogItem.key`: on «تأكيد»
   * with the user's answers, or — when the popup is closed any other way —
   * with «رفع نسخة جديدة» for every file. Nothing is reused without approval.
   * The parent closes the popup in response.
   */
  onResolve: (decisions: Record<string, FileMatchDecision>) => void;
}

/**
 * The same-file approval (plan §3.8, Phase 1b) — a sibling of the
 * ask-questions popup made for approvals: one question per matched file, two
 * answers («استخدام الموجود» / «رفع نسخة جديدة»), no «أخرى» box and no skip.
 * Picking an answer moves to the next file on its own; the last step confirms.
 *
 * Asked by the app at the moment a file is picked — no model, no paused turn,
 * and the answer is not a chat message. The upload keeps going while this is
 * open; the caller cancels it only for «استخدام الموجود».
 *
 * Mount it with a `key` per showing so a new set starts clean.
 * Centred dialog from `sm` up, bottom sheet on a phone.
 */
export function FileMatchDialog({ open, items, onResolve }: FileMatchDialogProps) {
  const total = items.length;
  const [stepState, setStep] = useState(0);
  // Keyed by item, not by position, so the list can grow or shrink while open.
  const [answers, setAnswers] = useState<Record<string, FileMatchDecision>>({});
  const step = Math.min(stepState, Math.max(total - 1, 0));

  const headingRef = useRef<HTMLHeadingElement>(null);
  const advanceTimerRef = useRef<number | null>(null);
  const groupId = useId();

  const cancelAdvance = useCallback(() => {
    if (advanceTimerRef.current !== null) {
      window.clearTimeout(advanceTimerRef.current);
      advanceTimerRef.current = null;
    }
  }, []);

  useEffect(() => cancelAdvance, [cancelAdvance]);

  // Each step swaps the body: move focus to the new question so a keyboard
  // user continues from it and a screen reader announces it.
  const isFirstStepRender = useRef(true);
  useEffect(() => {
    if (isFirstStepRender.current) {
      isFirstStepRender.current = false;
      return;
    }
    headingRef.current?.focus();
  }, [step]);

  if (total === 0) return null;

  const current = items[step];
  const parts = fileMatchQuestionParts(current.match);
  const selection = answers[current.key] ?? null;
  const isLast = step === total - 1;
  const answeredCount = items.filter((item) => answers[item.key] !== undefined).length;
  const allAnswered = answeredCount === total;

  const goTo = (next: number) => {
    cancelAdvance();
    setStep(Math.min(Math.max(next, 0), total - 1));
  };

  const pick = (value: FileMatchDecision) => {
    cancelAdvance();
    const key = current.key;
    setAnswers((prev) => ({ ...prev, [key]: value }));
    if (!isLast) {
      advanceTimerRef.current = window.setTimeout(() => {
        advanceTimerRef.current = null;
        setStep((s) => Math.min(s + 1, total - 1));
      }, ADVANCE_DELAY_MS);
    }
  };

  const resolve = (decided: Record<string, FileMatchDecision>) => {
    cancelAdvance();
    const out: Record<string, FileMatchDecision> = {};
    for (const item of items) out[item.key] = decided[item.key] ?? "upload_new";
    onResolve(out);
  };

  // Arrow keys move between the two answers without picking one, since
  // picking advances to the next file.
  const handleGroupKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const radios = Array.from(
      e.currentTarget.querySelectorAll<HTMLButtonElement>('[role="radio"]'),
    );
    const at = radios.indexOf(document.activeElement as HTMLButtonElement);
    if (at === -1) return;
    e.preventDefault();
    const delta = e.key === "ArrowDown" ? 1 : -1;
    radios[(at + delta + radios.length) % radios.length]?.focus();
  };

  const title = total > 1 ? "ملفات موجودة ضمن ملفاتك" : "ملف موجود ضمن ملفاتك";

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // Closing without «تأكيد» = «رفع نسخة جديدة» for every file.
        if (!next) resolve({});
      }}
    >
      <DialogContent
        dir="rtl"
        lang="ar"
        presentation="mobileSheet"
        data-testid="file-match-dialog"
        className="flex flex-col gap-0 overflow-hidden p-0 sm:pb-0"
        onOpenAutoFocus={(e) => {
          e.preventDefault();
          headingRef.current?.focus();
        }}
      >
        {/* Header — `pe-12` keeps clear of the close button. */}
        <div className="shrink-0 border-b px-5 pb-3 pe-12 pt-4">
          <div className="flex items-center gap-1.5">
            <FileCheck2 className="h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
            <DialogTitle className="text-sm font-semibold leading-6 tracking-normal text-primary">
              {title}
            </DialogTitle>
          </div>
          <DialogDescription className="sr-only">
            اختر «استخدام الموجود» أو «رفع نسخة جديدة». إغلاق النافذة يرفع نسخة جديدة.
          </DialogDescription>

          {total > 1 && (
            <div className="mt-2.5 flex items-center gap-3">
              <div className="flex flex-1 items-center gap-1">
                {items.map((item, i) => (
                  <button
                    key={item.key}
                    type="button"
                    onClick={() => goTo(i)}
                    aria-label={`الملف ${i + 1}${answers[item.key] !== undefined ? " (تمت الإجابة)" : ""}`}
                    aria-current={i === step ? "step" : undefined}
                    className="group flex-1 rounded-full py-2 focus-visible:outline-none"
                  >
                    <span
                      className={cn(
                        "block h-1.5 rounded-full transition-colors",
                        "group-focus-visible:ring-2 group-focus-visible:ring-ring group-focus-visible:ring-offset-2 group-focus-visible:ring-offset-background",
                        answers[item.key] !== undefined
                          ? "bg-primary"
                          : i === step
                            ? "bg-primary/40"
                            : "bg-muted-foreground/20 group-hover:bg-muted-foreground/35",
                      )}
                    />
                  </button>
                ))}
              </div>
              <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                {step + 1} من {total}
              </span>
            </div>
          )}
        </div>

        {/* Body */}
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          <h3
            ref={headingRef}
            id={`${groupId}-question`}
            tabIndex={-1}
            className="text-base font-semibold leading-[1.8] text-foreground focus:outline-none"
          >
            {parts.lead} «<span className="text-primary">{parts.title}</span>»
            {parts.wiSeq !== null && (
              <>
                {" "}
                <WiBadge seq={parts.wiSeq} className="align-middle" />
              </>
            )}
            {parts.date && (
              <span className="font-normal text-muted-foreground tabular-nums"> · {parts.date}</span>
            )}{" "}
            — {parts.ask}
          </h3>

          <div
            role="radiogroup"
            aria-labelledby={`${groupId}-question`}
            onKeyDown={handleGroupKeyDown}
            className="mt-3 flex flex-col gap-2"
          >
            {FILE_MATCH_OPTIONS.map((option) => {
              const checked = selection === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  role="radio"
                  aria-checked={checked}
                  onClick={() => pick(option.value)}
                  className={cn(
                    "flex w-full items-start gap-3 rounded-xl border px-4 py-3 text-start transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background",
                    checked
                      ? "border-primary bg-primary/[0.07]"
                      : "border-border hover:border-primary/40 hover:bg-accent/40",
                  )}
                >
                  <RadioMark checked={checked} />
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-medium leading-6 text-foreground">
                      {option.label}
                    </span>
                    {option.description && (
                      <span className="mt-0.5 block text-xs leading-5 text-muted-foreground">
                        {option.description}
                      </span>
                    )}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Footer — back at the start, the forward action at the end. No skip. */}
        <div className="flex shrink-0 items-center gap-2 border-t px-5 pb-[calc(0.75rem+env(safe-area-inset-bottom))] pt-3 sm:pb-3">
          {step > 0 && (
            <Button type="button" variant="ghost" onClick={() => goTo(step - 1)}>
              السابق
            </Button>
          )}
          {isLast && total > 1 && (
            <span className="ms-auto text-xs tabular-nums text-muted-foreground">
              أجبت عن {answeredCount} من {total}
            </span>
          )}
          {isLast ? (
            <Button
              type="button"
              className={cn(total > 1 ? "" : "ms-auto")}
              onClick={() => resolve(answers)}
              disabled={!allAnswered}
            >
              تأكيد
            </Button>
          ) : (
            <Button
              type="button"
              variant={selection !== null ? "default" : "outline"}
              className="ms-auto"
              onClick={() => goTo(step + 1)}
              disabled={selection === null}
            >
              التالي
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function RadioMark({ checked }: { checked: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border transition-colors",
        checked
          ? "border-primary bg-primary text-primary-foreground"
          : "border-muted-foreground/40 bg-background",
      )}
    >
      {checked && <Check className="h-3 w-3" strokeWidth={3} />}
    </span>
  );
}
