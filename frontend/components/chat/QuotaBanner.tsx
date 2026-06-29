"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useChatStore } from "@/stores/chat-store";

const HOUR_MS = 60 * 60 * 1000;
const MIN_MS = 60 * 1000;

function formatReset(resetsAt: string, now: number): string {
  const target = Date.parse(resetsAt);
  if (Number.isNaN(target)) return "";
  const delta = target - now;
  if (delta <= 0) return "خلال لحظات";
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

export function QuotaBanner() {
  const quotaInfo = useChatStore((s) => s.quotaInfo);
  const setQuotaInfo = useChatStore((s) => s.setQuotaInfo);
  const [now, setNow] = useState<number>(() => Date.now());

  useEffect(() => {
    if (!quotaInfo) return;
    const id = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(id);
  }, [quotaInfo]);

  const resetText = useMemo(
    () => (quotaInfo ? formatReset(quotaInfo.resets_at, now) : ""),
    [quotaInfo, now],
  );

  const handleDismiss = useCallback(() => setQuotaInfo(null), [setQuotaInfo]);

  if (!quotaInfo) return null;

  return (
    <div
      dir="rtl"
      lang="ar"
      role="alert"
      className="flex items-center justify-between gap-2 border-b border-warning-fg/25 bg-warning px-4 py-2"
    >
      <div className="flex flex-col gap-0.5">
        <p className="text-sm text-warning-fg">
          {quotaInfo.message_ar}
        </p>
        {resetText && (
          <p className="text-xs text-warning-fg/80">
            يُعاد الاحتساب {resetText}.
          </p>
        )}
      </div>
      <Button
        variant="ghost"
        size="icon"
        className="h-6 w-6 shrink-0 text-warning-fg hover:text-warning-fg"
        onClick={handleDismiss}
        aria-label="إغلاق"
      >
        <X className="h-4 w-4" />
      </Button>
    </div>
  );
}
