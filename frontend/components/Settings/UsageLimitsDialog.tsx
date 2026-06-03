"use client";

import { useEffect, useMemo, useState } from "react";
import { Info } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useUsageLimits } from "@/hooks/use-usage";
import type { UsageBar } from "@/types";

const HOUR_MS = 60 * 60 * 1000;
const MIN_MS = 60 * 1000;
const DAY_MS = 24 * HOUR_MS;

function formatReset(resetsAt: string, now: number): string {
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

interface BarRowProps {
  label: string;
  unit: string; // "USD" | "صفحة" | "بحثة"
  bar: UsageBar;
  now: number;
  fractionDigits?: number;
}

function BarRow({ label, unit, bar, now, fractionDigits = 0 }: BarRowProps) {
  const reset = formatReset(bar.resets_at, now);
  const usedDisplay =
    fractionDigits > 0
      ? bar.used.toFixed(fractionDigits)
      : Math.round(bar.used).toLocaleString("ar-EG");
  const limitDisplay =
    fractionDigits > 0
      ? bar.limit.toFixed(fractionDigits)
      : Math.round(bar.limit).toLocaleString("ar-EG");

  const tone =
    bar.pct >= 100
      ? "bg-destructive"
      : bar.pct >= 80
      ? "bg-amber-500"
      : "bg-primary";

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-2">
        <div className="flex flex-col">
          <span className="text-sm font-medium text-foreground">{label}</span>
          {reset && (
            <span className="text-xs text-muted-foreground">
              يُعاد الاحتساب {reset}
            </span>
          )}
        </div>
        <span className="text-xs tabular-nums text-muted-foreground">
          {bar.pct}% — {usedDisplay} / {limitDisplay} {unit}
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
    return (
      <div className="flex flex-col gap-6">
        <section className="flex flex-col gap-3">
          <h3 className="text-sm font-semibold text-foreground">
            الاستهلاك العادي
          </h3>
          <BarRow
            label="الاستخدام اليومي"
            unit="$"
            bar={data.ord.daily}
            now={now}
            fractionDigits={4}
          />
          <BarRow
            label="الاستخدام الأسبوعي"
            unit="$"
            bar={data.ord.weekly}
            now={now}
            fractionDigits={4}
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

        <section className="flex flex-col gap-3">
          <h3 className="text-sm font-semibold text-foreground">
            البحث على الإنترنت (شهري)
          </h3>
          <BarRow
            label="آخر 30 يوماً"
            unit="بحثة"
            bar={data.web.monthly}
            now={now}
          />
        </section>

        <div className="flex items-start gap-2 rounded-md border border-muted-foreground/20 bg-muted/40 p-3 text-xs text-muted-foreground">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <p className="leading-relaxed">
            الحدّان اليومي والأسبوعي يخصّان الاستهلاك العادي للنموذج فقط.
            استخراج النص والبحث على الإنترنت يُحتسبان شهريًا (آخر 30 يوماً).
          </p>
        </div>
      </div>
    );
  }, [data, isError, isLoading, now]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-md"
        dir="rtl"
        lang="ar"
        data-testid="usage-limits-dialog"
      >
        <DialogHeader>
          <DialogTitle>حدود الاستخدام</DialogTitle>
          <DialogDescription>
            متابعة استخدامك اليومي والأسبوعي والشهري.
          </DialogDescription>
        </DialogHeader>
        {body}
      </DialogContent>
    </Dialog>
  );
}
