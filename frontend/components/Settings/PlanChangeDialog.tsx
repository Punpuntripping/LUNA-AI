"use client";

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { PromoTermsLink } from "@/components/pricing/PromoTermsLink";
import { useScheduleDowngrade } from "@/hooks/use-payments";
import { cn } from "@/lib/utils";
import type { DowngradeBlockedReason, DowngradeOption } from "@/types";
import {
  DOWNGRADE_BASIC_NOTE,
  DOWNGRADE_NO_METHOD_CODE,
  DOWNGRADE_NO_METHOD_DISMISS,
  DOWNGRADE_NOTHING_NOW,
  DOWNGRADE_PRICE_CHANGED_FALLBACK,
  DOWNGRADE_PRICE_PROMISE,
  downgradeConfirmSentence,
  downgradeNoMethodNote,
  downgradePricePhrase,
  errorCodeOf,
  isPriceChangedError,
  planChangeErrorMessage,
  scheduleDowngradeBody,
} from "@/components/Settings/plan-change-content";

interface PlanChangeDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** «القصوى» — the plan the user keeps until the renewal. */
  currentPlanName: string;
  /** The renewal date, already formatted (Latin digits); null = unknown. */
  termEndsAt: string | null;
  /** `readDowngradeOptions()` — never empty when this is opened. */
  options: DowngradeOption[];
  /**
   * `downgradeBlockedReason()`. `"no_payment_method"` replaces the whole
   * dialog with `downgradeNoMethodNote()` and a single «حسناً».
   */
  blockedReason: DowngradeBlockedReason | null;
  /**
   * The subscription is being re-read. After a price change the confirm stays
   * disabled until the NEW price is on screen, so a quick second click cannot
   * send the stale one again.
   */
  refreshing: boolean;
}

type Step = "pick" | "confirm";

/**
 * «تغيير الباقة» — pick a LOWER plan, then confirm a switch that happens at the
 * next renewal (`.claude/plans/pro_plus_tier_and_downgrade.md` §4.1 + §4.4).
 *
 * Two steps, because the second one is the disclosure: the confirm sheet
 * states the date the current plan runs to, the plan that follows and the
 * price the renewal will charge — the sentence the user is agreeing to, and
 * the price the server then keeps as its promise. With a single option
 * (pro_plus → pro) there is nothing to pick, so the dialog opens straight on
 * the confirm step.
 *
 * The confirm sends the price it showed (`expected_price_sar`). If the quote
 * has moved the server refuses (`SUBSCRIPTION_DOWNGRADE_PRICE_CHANGED`); the
 * subscription is re-read, this step re-renders with the NEW price and a note
 * saying it changed, and nothing is stored until the user confirms again.
 *
 * With no stored card (`no_payment_method`, or a 409
 * `SUBSCRIPTION_DOWNGRADE_NO_METHOD` whose re-read says so) nothing can renew
 * at all, so there is nothing to pick or confirm: the dialog says the term
 * ends without renewal and the lower plan can be bought afterwards, and offers
 * only «حسناً».
 *
 * Nothing on this surface moves money: the POST only records the switch, and
 * the confirm says so. The server's answer is the new subscription state,
 * which the mutation writes into the cache, so the settings dialog behind this
 * re-renders with the pending banner as soon as this closes.
 */
export function PlanChangeDialog({
  open,
  onOpenChange,
  currentPlanName,
  termEndsAt,
  options,
  blockedReason,
  refreshing,
}: PlanChangeDialogProps) {
  const schedule = useScheduleDowngrade();
  const { reset: resetSchedule } = schedule;
  const [step, setStep] = useState<Step>("pick");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Every opening starts fresh. A single option skips the picker. Keyed on the
  // plan id STRING, not the option object: the parent re-parses options from
  // each subscription read, and an object dependency would re-run this (and
  // wipe a pending request's state, or the price-changed note) on every
  // re-render.
  const onlyOption = options.length === 1 ? options[0] : null;
  const onlyOptionId = onlyOption?.plan_id ?? null;
  useEffect(() => {
    if (!open) return;
    resetSchedule();
    setSelectedId(onlyOptionId);
    setStep(onlyOptionId ? "confirm" : "pick");
  }, [open, onlyOptionId, resetSchedule]);

  // Looked up on every render: a re-read that drops the chosen plan (it was
  // changed from another device) falls back to the picker rather than
  // confirming a plan the server no longer offers — and a re-read that moved
  // its price shows the new one here, which is what the next confirm sends.
  const selected = options.find((o) => o.plan_id === selectedId) ?? null;
  const blocked = blockedReason === "no_payment_method";
  const onConfirmStep = !blocked && step === "confirm" && selected !== null;

  const priceChanged = schedule.isError && isPriceChangedError(schedule.error);
  // A no-card refusal is said by the blocked note once the re-read lands;
  // until then (or if it fails) the server's own message shows.
  const showError =
    schedule.isError &&
    !priceChanged &&
    !(blocked && errorCodeOf(schedule.error) === DOWNGRADE_NO_METHOD_CODE);

  const close = () => {
    if (schedule.isPending) return;
    onOpenChange(false);
  };

  const handleConfirm = () => {
    if (!selected) return;
    // Plan id + the price and offer months this sheet just showed.
    schedule.mutate(scheduleDowngradeBody(selected), {
      onSuccess: () => onOpenChange(false),
    });
  };

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) close();
      }}
    >
      <AlertDialogContent dir="rtl" lang="ar" data-testid="plan-change-dialog">
        <AlertDialogHeader>
          <AlertDialogTitle>
            {onConfirmStep ? "تأكيد تغيير الباقة" : "تغيير الباقة"}
          </AlertDialogTitle>
          <AlertDialogDescription asChild>
            {/* asChild + div: the body holds radio inputs and several lines —
                the same reason the cancel dialog does it. */}
            <div className="flex flex-col gap-3 text-start">
              {blocked ? (
                <span
                  className="text-sm leading-relaxed text-foreground"
                  data-testid="plan-change-no-method"
                >
                  {downgradeNoMethodNote(termEndsAt)}
                </span>
              ) : onConfirmStep && selected ? (
                <>
                  <span
                    className="text-sm font-medium leading-relaxed text-foreground"
                    data-testid="plan-change-confirm-sentence"
                  >
                    {downgradeConfirmSentence(
                      currentPlanName,
                      termEndsAt,
                      selected,
                    )}
                  </span>
                  {/* The quote moved since it was shown: the sentence above
                      is already the re-read price; this says why it differs. */}
                  {priceChanged && (
                    <span
                      className="rounded-lg border border-primary/30 bg-primary/5 px-3 py-2 text-sm leading-relaxed text-foreground"
                      role="status"
                      data-testid="plan-change-price-changed"
                    >
                      {planChangeErrorMessage(
                        schedule.error,
                        DOWNGRADE_PRICE_CHANGED_FALLBACK,
                      )}
                    </span>
                  )}
                  <span className="text-sm leading-relaxed text-muted-foreground">
                    {DOWNGRADE_NOTHING_NOW}
                  </span>
                  {/* The price just shown is reserved (owner rule
                      2026-11-20): said for every price, list or offer. An
                      offer price also carries its conditions with it
                      (PromoTermsLink's rule). */}
                  <span
                    className="text-xs leading-relaxed text-muted-foreground"
                    data-testid="plan-change-price-promise"
                  >
                    {DOWNGRADE_PRICE_PROMISE}
                    {selected.promo_price_sar !== null && (
                      <PromoTermsLink className="mt-1 block" />
                    )}
                  </span>
                </>
              ) : (
                <>
                  <span className="text-sm leading-relaxed text-muted-foreground">
                    {termEndsAt
                      ? `اختر الباقة التي تنتقل إليها عند التجديد. تبقى باقة ${currentPlanName} كما هي حتى ${termEndsAt}.`
                      : `اختر الباقة التي تنتقل إليها عند التجديد. تبقى باقة ${currentPlanName} كما هي حتى نهاية مدتها.`}
                  </span>
                  <div
                    role="radiogroup"
                    aria-label="الباقة الجديدة"
                    className="flex flex-col gap-2"
                  >
                    {options.map((option) => (
                      <label
                        key={option.plan_id}
                        className={cn(
                          "flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5 transition-colors",
                          selectedId === option.plan_id
                            ? "border-primary bg-primary/5"
                            : "border-border hover:bg-muted/50",
                        )}
                      >
                        <input
                          type="radio"
                          name="plan-change-target"
                          value={option.plan_id}
                          checked={selectedId === option.plan_id}
                          onChange={() => setSelectedId(option.plan_id)}
                          className="mt-1 h-4 w-4 shrink-0 accent-primary"
                          data-testid={`plan-change-option-${option.plan_id}`}
                        />
                        <span className="flex flex-col gap-0.5">
                          <span className="text-sm font-medium text-foreground">
                            باقة {option.name_ar}
                          </span>
                          <span className="text-xs leading-relaxed text-muted-foreground">
                            {downgradePricePhrase(option)}
                          </span>
                        </span>
                      </label>
                    ))}
                  </div>
                  {options.some((o) => o.promo_price_sar !== null) && (
                    <PromoTermsLink className="w-fit" />
                  )}
                  <span className="text-xs leading-relaxed text-muted-foreground">
                    {DOWNGRADE_BASIC_NOTE}
                  </span>
                </>
              )}

              {showError && (
                <span
                  className="text-sm text-destructive"
                  role="alert"
                  data-testid="plan-change-error"
                >
                  {/* The backend's own Arabic message (409s included) when it
                      sent one; the fallback covers a transport failure or a
                      body we could not parse. */}
                  {planChangeErrorMessage(
                    schedule.error,
                    "تعذّر تغيير الباقة. حاول مجددًا.",
                  )}
                </span>
              )}
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          {blocked ? (
            // The only button: nothing here can be confirmed (it closes —
            // AlertDialogAction's default).
            <AlertDialogAction data-testid="plan-change-no-method-dismiss">
              {DOWNGRADE_NO_METHOD_DISMISS}
            </AlertDialogAction>
          ) : (
            <>
              {onConfirmStep && !onlyOption ? (
                <Button
                  variant="outline"
                  // Same footer spacing as AlertDialogCancel, which it stands
                  // in for.
                  className="mt-2 sm:mt-0"
                  onClick={() => {
                    schedule.reset();
                    setStep("pick");
                  }}
                  disabled={schedule.isPending}
                  data-testid="plan-change-back"
                >
                  رجوع
                </Button>
              ) : (
                <AlertDialogCancel disabled={schedule.isPending}>
                  إلغاء
                </AlertDialogCancel>
              )}
              {onConfirmStep ? (
                <AlertDialogAction
                  onClick={(e) => {
                    // Held open while the request is in flight so the error
                    // has somewhere to render — AlertDialogAction closes on
                    // click.
                    e.preventDefault();
                    handleConfirm();
                  }}
                  disabled={schedule.isPending || refreshing}
                  data-testid="plan-change-confirm"
                >
                  {(schedule.isPending || (priceChanged && refreshing)) && (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  )}
                  تأكيد التغيير
                </AlertDialogAction>
              ) : (
                <AlertDialogAction
                  onClick={(e) => {
                    e.preventDefault();
                    if (selected) setStep("confirm");
                  }}
                  disabled={!selected}
                  data-testid="plan-change-next"
                >
                  متابعة
                </AlertDialogAction>
              )}
            </>
          )}
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
