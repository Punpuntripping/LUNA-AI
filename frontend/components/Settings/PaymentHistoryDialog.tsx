"use client";

import { useMemo, useState } from "react";
import { Loader2, Receipt } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
import { Button, buttonVariants } from "@/components/ui/button";
import { RiyalSymbol } from "@/components/icons/RiyalSymbol";
import { cn } from "@/lib/utils";
import { usePaymentHistory, useRefundPayment } from "@/hooks/use-payments";
import {
  REFUND_FEE_SAR,
  REFUND_POLICY_NOTE,
  REFUND_WINDOW_HOURS,
  formatFeeSar,
  formatSar,
} from "@/lib/pricing";
import type { PaymentHistoryItem, PaymentStatus } from "@/types";

const REFUND_WINDOW_MS = REFUND_WINDOW_HOURS * 60 * 60 * 1000;

const DATE_FORMAT = new Intl.DateTimeFormat("ar-EG", {
  year: "numeric",
  month: "long",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return "—";
  return DATE_FORMAT.format(new Date(at));
}

/** Arabic label + tone per status. `initiated` is a checkout the user never
 *  completed — shown, because a silently-hidden row looks like a lost payment. */
const STATUS_META: Record<PaymentStatus, { label: string; tone: string }> = {
  initiated: { label: "لم تكتمل", tone: "text-muted-foreground" },
  pending: { label: "قيد المعالجة", tone: "text-warning-fg" },
  paid: { label: "مدفوعة", tone: "text-success-fg" },
  failed: { label: "فاشلة", tone: "text-destructive" },
  refunded: { label: "مستردّة", tone: "text-muted-foreground" },
};

/**
 * Is this row still inside the self-serve refund window?
 *
 * The server's `refundable` flag WINS whenever it is present — it is the party
 * that will actually enforce the window, and a client clock that runs fast
 * would otherwise render a button that always 4xxs. The `paid_at` arithmetic is
 * only a fallback for a backend that does not send the flag.
 */
function isRefundable(item: PaymentHistoryItem): boolean {
  if (typeof item.refundable === "boolean") return item.refundable;
  if (item.status !== "paid" || !item.paid_at) return false;
  const paidAt = Date.parse(item.paid_at);
  if (Number.isNaN(paidAt)) return false;
  return Date.now() - paidAt <= REFUND_WINDOW_MS;
}

interface PaymentRowProps {
  item: PaymentHistoryItem;
  onRefund: (item: PaymentHistoryItem) => void;
  refundingId: string | null;
}

function PaymentRow({ item, onRefund, refundingId }: PaymentRowProps) {
  const status = STATUS_META[item.status] ?? STATUS_META.initiated;
  // SAR fields are 2-dp strings on the wire — Number() before the > 0 gate.
  const credit = Number(item.upgrade_credit_sar ?? 0);
  const refundable = isRefundable(item);
  const isRefunding = refundingId === item.payment_id;

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border p-3">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm font-medium text-foreground">
          باقة {item.plan_name_ar ?? item.plan_id}
        </span>
        <span className="flex items-center gap-1 text-sm font-semibold tabular-nums text-foreground">
          {formatSar(item.amount_sar)}
          <RiyalSymbol className="h-3.5 w-auto" />
        </span>
      </div>

      <div className="flex items-baseline justify-between gap-3">
        <span className="text-xs text-muted-foreground">
          {formatDate(item.paid_at ?? item.created_at)}
        </span>
        <span className={cn("text-xs font-medium", status.tone)}>
          {status.label}
        </span>
      </div>

      {credit > 0 && (
        <p className="text-xs text-muted-foreground">
          خُصمت القيمة المتبقية من باقتك السابقة: −{formatSar(credit)} ريال
        </p>
      )}

      {item.status === "refunded" && item.refunded_amount_sar != null && (
        <p className="text-xs text-muted-foreground">
          أُعيد إليك {formatSar(item.refunded_amount_sar)} ريال
          {item.refund_fee_sar != null
            ? ` · رسوم معالجة ${formatFeeSar(item.refund_fee_sar)} ريال`
            : ""}
        </p>
      )}

      {refundable && (
        <Button
          variant="outline"
          size="sm"
          className="w-fit"
          disabled={isRefunding}
          onClick={() => onRefund(item)}
          data-testid={`payment-refund-${item.payment_id}`}
        >
          {isRefunding && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          طلب استرداد
        </Button>
      )}
    </div>
  );
}

interface PaymentHistoryDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * سجل المدفوعات — the receipts list, and the surface that makes the 24-hour
 * refund promise real rather than a favour granted over email.
 *
 * Mirrors `UsageLimitsDialog`: fetched only while open, Arabic throughout,
 * RTL-scoped `DialogContent`.
 */
export function PaymentHistoryDialog({
  open,
  onOpenChange,
}: PaymentHistoryDialogProps) {
  const { data, isLoading, isError } = usePaymentHistory(open);
  const refund = useRefundPayment();

  const [confirming, setConfirming] = useState<PaymentHistoryItem | null>(null);
  const [refundError, setRefundError] = useState<string | null>(null);

  const payments = data?.payments ?? [];

  const handleOpenChange = (next: boolean) => {
    if (!next) {
      setConfirming(null);
      setRefundError(null);
      refund.reset();
    }
    onOpenChange(next);
  };

  const confirmRefund = async () => {
    if (!confirming) return;
    setRefundError(null);
    try {
      await refund.mutateAsync(confirming.payment_id);
      setConfirming(null);
    } catch (err) {
      setRefundError(
        err instanceof Error && err.message
          ? err.message
          : "تعذّر تنفيذ الاسترداد. حاول مجددًا.",
      );
    }
  };

  // The exact arithmetic for the row being confirmed, shown BEFORE the user
  // commits: someone who expects 49.90 back and receives 45.02 files a
  // complaint; someone who agreed to 45.02 does not.
  //
  // ⚠ The numbers come from the SERVER (`refund_quote_*`). The deduction is
  // not a flat constant — it recovers the provider fee Moyasar actually
  // charged for THAT payment (mada vs Visa, 49.90 vs 189.90) plus their flat
  // refund-execution fee, plus our margin. The client cannot compute it, and
  // guessing would put a wrong number in front of a user about to lose money.
  const refundMath = useMemo(() => {
    if (!confirming) return null;
    const gross = Number(confirming.amount_sar);
    if (confirming.refund_quote_amount_sar != null) {
      return {
        gross,
        net: Number(confirming.refund_quote_amount_sar),
        fee: Number(confirming.refund_quote_fee_sar ?? 0),
      };
    }
    // No quote (older row / server without the field): fall back to the
    // margin-only figure rather than blocking the refund outright.
    const net = Math.max(0, gross - REFUND_FEE_SAR);
    return { gross, net, fee: REFUND_FEE_SAR };
  }, [confirming]);

  const body = (() => {
    if (isLoading) {
      return (
        <p className="text-sm text-muted-foreground">جارٍ تحميل السجل…</p>
      );
    }
    if (isError) {
      return (
        <p className="text-sm text-destructive">
          تعذّر تحميل سجل المدفوعات. حاول لاحقًا.
        </p>
      );
    }
    if (payments.length === 0) {
      return (
        <div className="flex flex-col items-center gap-2 py-6 text-center">
          <Receipt className="h-6 w-6 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">
            لا توجد عمليات دفع حتى الآن.
          </p>
        </div>
      );
    }
    return (
      <div className="flex flex-col gap-3">
        {payments.map((item) => (
          <PaymentRow
            key={item.payment_id}
            item={item}
            onRefund={(row) => {
              setRefundError(null);
              setConfirming(row);
            }}
            refundingId={refund.isPending ? confirming?.payment_id ?? null : null}
          />
        ))}
      </div>
    );
  })();

  return (
    <>
      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent
          className="max-h-[85dvh] max-w-md overflow-y-auto"
          presentation="mobileSheet"
          dir="rtl"
          lang="ar"
          data-testid="payment-history-dialog"
        >
          <DialogHeader>
            <DialogTitle>سجل المدفوعات</DialogTitle>
            <DialogDescription>
              عمليات الدفع السابقة وحالتها، وطلب الاسترداد خلال المدة المسموحة.
            </DialogDescription>
          </DialogHeader>

          {body}

          <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
            {REFUND_POLICY_NOTE}
          </p>
        </DialogContent>
      </Dialog>

      <AlertDialog
        open={confirming !== null}
        onOpenChange={(next) => {
          if (!next && !refund.isPending) {
            setConfirming(null);
            setRefundError(null);
          }
        }}
      >
        <AlertDialogContent dir="rtl" lang="ar">
          <AlertDialogHeader>
            <AlertDialogTitle>تأكيد طلب الاسترداد</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="flex flex-col gap-2 text-start">
                {refundMath && (
                  <span
                    className="text-sm font-medium text-foreground"
                    data-testid="refund-arithmetic"
                  >
                    سيُعاد إليك {formatSar(refundMath.net)} من أصل{" "}
                    {formatSar(refundMath.gross)} · رسوم معالجة{" "}
                    {formatFeeSar(refundMath.fee)} ريال
                  </span>
                )}
                <span className="text-sm text-muted-foreground">
                  سيتم إلغاء اشتراكك الحالي فور تنفيذ الاسترداد.
                </span>
                {refundError && (
                  <span
                    className="text-sm text-destructive"
                    data-testid="refund-error"
                  >
                    {refundError}
                  </span>
                )}
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={refund.isPending}>
              إلغاء
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                // Keep the dialog open while the request is in flight so the
                // error (a closed window, a provider failure) has somewhere to
                // render. AlertDialogAction closes on click by default.
                e.preventDefault();
                void confirmRefund();
              }}
              disabled={refund.isPending}
              className={cn(buttonVariants({ variant: "destructive" }))}
              data-testid="refund-confirm"
            >
              {refund.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              تأكيد الاسترداد
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
