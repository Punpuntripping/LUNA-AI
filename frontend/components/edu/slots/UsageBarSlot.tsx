"use client";

import { useUsageLimits } from "@/hooks/use-usage";
import { AR_NUM_LOCALE } from "@/lib/format/numerals";
import type { UsageBar, UsageReport } from "@/types";

/**
 * The one live bar on the حدود الاستخدام lesson card.
 *
 * `UsageLimitsDialog` shows up to five meters; a 320px card shows exactly one —
 * the BINDING window, i.e. the one that will actually stop this user:
 *
 *  • free plan  → `points.monthly`. Migration 129 made the free tier a single
 *    5-point/30-day window and set session+weekly to NULL, so those two render
 *    «بلا حد» and the monthly one is the real ceiling.
 *  • paid plan  → `points.session`. The mirror image: `points_monthly` is NULL
 *    and the 5-hour session window is what a paid user runs into first.
 *
 * Anything unexpected (locked account, a backend that sends neither) renders
 * NOTHING rather than a guess — the lesson's copy and its two exits still stand
 * on their own, and a wrong number here would be worse than no number.
 */

function bindingBar(report: UsageReport): { label: string; bar: UsageBar } | null {
  if (report.locked) return null;

  // `effective_plan_id` already has the expiry fallback applied — an expired
  // paid plan resolves to "free" and must be metered as one.
  const isFree =
    report.plan === null || report.plan.effective_plan_id === "free";

  const candidate = isFree
    ? report.points.monthly ?? report.points.session ?? report.points.weekly
    : report.points.session ?? report.points.weekly ?? report.points.monthly;

  if (!candidate) return null;
  // limit null = unlimited, limit 0 = not in this plan. Neither is a meter.
  if (candidate.limit === null || candidate.limit === 0) return null;

  const label =
    candidate === report.points.monthly
      ? "نقاطك هذا الشهر"
      : candidate === report.points.session
      ? "نقاط الجلسة (5 ساعات)"
      : "نقاطك هذا الأسبوع";

  return { label, bar: candidate };
}

/** One decimal, trimmed — points are fractional (1$ = 100 نقطة). */
function formatPoints(value: number): string {
  return Number(value.toFixed(1)).toLocaleString(AR_NUM_LOCALE, {
    maximumFractionDigits: 1,
  });
}

export function UsageBarSlot() {
  // The card owns its own fetch. The 10s `staleTime` on this hook means opening
  // the full dialog straight after (the card's primary action) reuses this same
  // cached row instead of refetching.
  const { data } = useUsageLimits(true);

  if (!data) return null;
  const binding = bindingBar(data);
  if (!binding) return null;

  const { label, bar } = binding;
  const pct = Math.max(0, Math.min(100, bar.pct));
  // Same thresholds as UsageLimitsDialog — the card and the dialog must never
  // disagree about whether a user is in trouble.
  const tone =
    bar.pct >= 100
      ? "bg-destructive"
      : bar.pct >= 80
      ? "bg-warning-fg"
      : "bg-primary";

  return (
    <div className="flex flex-col gap-1.5" data-testid="edu-usage-bar">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs font-medium text-foreground">{label}</span>
        <span className="text-xs tabular-nums text-muted-foreground">
          {formatPoints(bar.used)} / {formatPoints(bar.limit as number)} نقطة
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
        <div
          className={`h-full rounded-full transition-all ${tone}`}
          style={{ width: `${Math.max(2, pct)}%` }}
        />
      </div>
    </div>
  );
}
