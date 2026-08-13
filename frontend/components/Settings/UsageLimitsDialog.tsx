"use client";

import { useEffect, useMemo, useState } from "react";
import { Info, Lock } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useUsageLimits } from "@/hooks/use-usage";
import { usageBarCopy } from "@/lib/library/gate-copy";
import type { UsageBar } from "@/types";

const HOUR_MS = 60 * 60 * 1000;
const MIN_MS = 60 * 1000;
const DAY_MS = 24 * HOUR_MS;

function formatReset(resetsAt: string | null, now: number): string {
  if (!resetsAt) return "";
  const target = Date.parse(resetsAt);
  if (Number.isNaN(target)) return "";
  const delta = target - now;
  if (delta <= 0) return "خلال لحظات";
  if (delta >= DAY_MS) {
    const days = Math.floor(delta / DAY_MS);
    return days === 1 ? "خلال يوم" : `خلال ${days} يوم`;
  }
  const hours = Math.floor(delta / HOUR_MS);
  const minutes = Math.floor((delta % HOUR_MS) / MIN_MS);
  if (hours >= 1) {
    return minutes > 0
      ? `خلال ${hours} ساعة و${minutes} دقيقة`
      : `خلال ${hours} ساعة`;
  }
  if (minutes >= 1) return `خلال ${minutes} دقيقة`;
  return "خلال أقل من دقيقة";
}

/** Points may be fractional (1$ = 100 pts) — show one decimal, trimmed. */
function formatAmount(value: number, fractionDigits: number): string {
  if (fractionDigits > 0) {
    return Number(value.toFixed(fractionDigits)).toLocaleString("ar-EG", {
      maximumFractionDigits: fractionDigits,
    });
  }
  return Math.round(value).toLocaleString("ar-EG");
}

interface BarRowProps {
  label: string;
  unit: string; // "نقطة" | "صفحة" | "بحثة"
  bar: UsageBar | null;
  now: number;
  fractionDigits?: number;
  /**
   * `true` for a FIXED-window meter — one whose period boundary exists whether or
   * not it has been used (the library allowance: a UTC calendar month for free,
   * the subscription window for a paid plan).
   *
   * The points and OCR bars are ROLLING windows anchored on first use, so at zero
   * usage they have no meaningful reset instant and the backend sends none —
   * hence «متاحة بالكامل». The library bar is different: the backend deliberately
   * sends `resets_at` even at zero usage, and suppressing it here would throw away
   * the one behaviour implemented specifically for this meter.
   */
  fixedWindow?: boolean;
}

function BarRow({
  label,
  unit,
  bar,
  now,
  fractionDigits = 0,
  fixedWindow = false,
}: BarRowProps) {
  if (!bar) return null;

  // limit 0 → the feature is not included in the plan at all.
  if (bar.limit === 0) {
    return (
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm font-medium text-foreground">{label}</span>
        <span className="text-xs text-muted-foreground">
          غير متاحة في باقتك الحالية
        </span>
      </div>
    );
  }

  // limit null → unlimited window: show consumption without a bar.
  if (bar.limit === null) {
    return (
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm font-medium text-foreground">{label}</span>
        <span className="text-xs tabular-nums text-muted-foreground">
          {formatAmount(bar.used, fractionDigits)} {unit} — بلا حد
        </span>
      </div>
    );
  }

  // used === 0 → a ROLLING window is fully available and the backend sends no
  // reset (a "now + window" countdown would be meaningless and clock-skew-
  // fragile). A FIXED window still has a real boundary at zero usage, so it
  // keeps showing it.
  const reset = formatReset(bar.resets_at, now);
  const fullyAvailable = bar.used <= 0 && !(fixedWindow && reset);
  const tone =
    bar.pct >= 100
      ? "bg-destructive"
      : bar.pct >= 80
      ? "bg-warning-fg"
      : "bg-primary";

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-2">
        <div className="flex flex-col">
          <span className="text-sm font-medium text-foreground">{label}</span>
          {fullyAvailable ? (
            <span className="text-xs text-muted-foreground">متاحة بالكامل</span>
          ) : reset ? (
            <span className="text-xs text-muted-foreground">
              يُعاد الاحتساب {reset}
            </span>
          ) : null}
        </div>
        <span className="text-xs tabular-nums text-muted-foreground">
          {bar.pct}% — {formatAmount(bar.used, fractionDigits)} /{" "}
          {formatAmount(bar.limit, 0)} {unit}
        </span>
      </div>
      <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${tone}`}
          style={{ width: `${Math.max(2, Math.min(100, bar.pct))}%` }}
        />
      </div>
    </div>
  );
}

interface UsageLimitsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function UsageLimitsDialog({
  open,
  onOpenChange,
}: UsageLimitsDialogProps) {
  const { data, isLoading, isError } = useUsageLimits(open);
  const [now, setNow] = useState<number>(() => Date.now());

  useEffect(() => {
    if (!open) return;
    const id = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(id);
  }, [open]);

  const body = useMemo(() => {
    if (isLoading) {
      return (
        <p className="text-sm text-muted-foreground">جارٍ تحميل الاستخدام…</p>
      );
    }
    if (isError || !data) {
      return (
        <p className="text-sm text-destructive">
          تعذّر تحميل بيانات الاستخدام. حاول لاحقًا.
        </p>
      );
    }

    if (data.locked) {
      return (
        <div className="flex items-start gap-3 rounded-md border border-warning-fg/25 bg-warning p-4">
          <Lock className="mt-0.5 h-4 w-4 shrink-0 text-warning-fg" />
          <div className="flex flex-col gap-1">
            <p className="text-sm font-medium text-warning-fg">
              حسابك غير مفعّل بعد
            </p>
            <p className="text-xs leading-relaxed text-warning-fg/80">
              تواصل معنا لتفعيل اشتراكك والبدء في استخدام ريحان.
            </p>
          </div>
        </div>
      );
    }

    const plan = data.plan;
    const expiryText =
      plan?.expires_at && !plan.expired
        ? formatReset(plan.expires_at, now)
        : "";

    return (
      <div className="flex flex-col gap-6">
        {plan && (
          <div className="flex flex-col gap-1 rounded-md border border-muted-foreground/20 bg-muted/40 p-3">
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-sm font-semibold text-foreground">
                الباقة: {plan.name_ar ?? plan.plan_id}
              </span>
              {expiryText && (
                <span className="text-xs text-muted-foreground">
                  ينتهي الاشتراك {expiryText}
                </span>
              )}
            </div>
            {plan.expired && (
              <p className="text-xs text-destructive">
                انتهى اشتراكك — تُطبَّق حدود الباقة المجانية حتى التجديد.
              </p>
            )}
          </div>
        )}

        <section className="flex flex-col gap-3">
          <h3 className="text-sm font-semibold text-foreground">
            نقاط الاستخدام
          </h3>
          <BarRow
            label="الجلسة (٥ ساعات)"
            unit="نقطة"
            bar={data.points.session}
            now={now}
            fractionDigits={1}
          />
          <BarRow
            label="الأسبوعي (٧ أيام)"
            unit="نقطة"
            bar={data.points.weekly}
            now={now}
            fractionDigits={1}
          />
          {/* Migration 129: the free plan's ONLY window. Its session and weekly
              limits are NULL, so the two rows above render «بلا حد» and this is
              the one that actually stops them — omitting it would leave a free
              user unable to see their own limit anywhere in the app. Paid plans
              are the mirror image: points_monthly is NULL, so this row reads
              «بلا حد» and the two above carry the real numbers. */}
          <BarRow
            label="الشهري (٣٠ يوماً)"
            unit="نقطة"
            bar={data.points.monthly}
            now={now}
            fractionDigits={1}
          />
        </section>

        <section className="flex flex-col gap-3">
          <h3 className="text-sm font-semibold text-foreground">
            استخراج النص (شهري)
          </h3>
          <BarRow
            label="آخر 30 يوماً"
            unit="صفحة"
            bar={data.ocr.monthly}
            now={now}
          />
        </section>

        {/* «فتح المصادر» — the library unlock allowance (access tiers Phase B).
            Reuses BarRow verbatim, which already handles limit === null («بلا
            حد») and limit === 0 («غير متاحة في باقتك الحالية»). Unlike the
            rolling points/OCR windows this bar's period is a fixed calendar /
            subscription window, so `resets_at` is present even at zero usage —
            hence `fixedWindow`, which keeps «يُعاد الاحتساب …» visible on an
            untouched account instead of collapsing to «متاحة بالكامل».
            `library` is optional on the payload — a backend that predates the
            field simply hides the section. */}
        {data.library && (
          <section className="flex flex-col gap-3">
            <h3 className="text-sm font-semibold text-foreground">
              {usageBarCopy.sectionTitle}
            </h3>
            <BarRow
              label={usageBarCopy.barLabel}
              unit={usageBarCopy.unit}
              bar={data.library.period}
              now={now}
              fractionDigits={0}
              fixedWindow
            />
            <p className="text-xs leading-relaxed text-muted-foreground">
              {usageBarCopy.note}
            </p>
          </section>
        )}

        <div className="flex items-start gap-2 rounded-md border border-muted-foreground/20 bg-muted/40 p-3 text-xs text-muted-foreground">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <p className="leading-relaxed">
            تبدأ الجلسة عند إرسال أول رسالة وتستمر ٥ ساعات ثم تتجدّد تلقائيًا.
            ويُحتسب الحد الأسبوعي على استهلاكك خلال آخر ٧ أيام.
          </p>
        </div>
      </div>
    );
  }, [data, isError, isLoading, now]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-md"
        presentation="mobileSheet"
        dir="rtl"
        lang="ar"
        data-testid="usage-limits-dialog"
      >
        <DialogHeader>
          <DialogTitle>حدود الاستخدام</DialogTitle>
          <DialogDescription>
            باقتك الحالية واستهلاك النقاط حسب الجلسة والأسبوع والشهر.
          </DialogDescription>
        </DialogHeader>
        {body}
      </DialogContent>
    </Dialog>
  );
}
