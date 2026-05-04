"use client";

import { useEffect } from "react";
import { cn } from "@/lib/utils";
import { usePreferencesStore } from "@/stores/preferences-store";
import type { DetailLevel } from "@/types";

interface DetailLevelOption {
  value: DetailLevel;
  label: string;
}

const OPTIONS: readonly DetailLevelOption[] = [
  { value: "low", label: "مختصر" },
  { value: "medium", label: "متوسط" },
  { value: "high", label: "مفصّل" },
] as const;

interface DetailLevelToggleProps {
  /** Optional extra classes for the outer wrapper */
  className?: string;
  /** Hides the caption; useful when embedding in compact UI */
  hideCaption?: boolean;
}

/**
 * Segmented control for the deep-search verbosity preference.
 * Persists to `user_preferences.preferences.detail_level` via PATCH /preferences.
 * Arabic-first (RTL-friendly) — labels are `مختصر / متوسط / مفصّل`.
 */
export function DetailLevelToggle({ className, hideCaption = false }: DetailLevelToggleProps) {
  const detailLevel = usePreferencesStore((s) => s.detailLevel);
  const isHydrated = usePreferencesStore((s) => s.isHydrated);
  const isSaving = usePreferencesStore((s) => s.isSaving);
  const hydrate = usePreferencesStore((s) => s.hydrate);
  const setDetailLevel = usePreferencesStore((s) => s.setDetailLevel);

  // One-shot hydration on mount. `hydrate` guards against double-loads internally
  // by setting isHydrated=true on both success and failure.
  useEffect(() => {
    if (!isHydrated) {
      void hydrate();
    }
  }, [isHydrated, hydrate]);

  return (
    <div className={cn("flex flex-col gap-2", className)} dir="rtl">
      <div
        role="radiogroup"
        aria-label="مستوى التفصيل"
        className={cn(
          "inline-flex h-10 items-center justify-center rounded-md bg-muted p-1 text-muted-foreground",
          "w-full",
          isSaving && "opacity-80",
        )}
      >
        {OPTIONS.map((opt) => {
          const active = detailLevel === opt.value;
          return (
            <button
              key={opt.value}
              type="button"
              role="radio"
              aria-checked={active}
              aria-label={opt.label}
              data-value={opt.value}
              data-state={active ? "active" : "inactive"}
              disabled={isSaving}
              onClick={() => void setDetailLevel(opt.value)}
              className={cn(
                "inline-flex flex-1 items-center justify-center whitespace-nowrap rounded-sm px-3 py-1.5",
                "text-sm font-medium ring-offset-background transition-all",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                "disabled:pointer-events-none disabled:opacity-50",
                active
                  ? "bg-background text-foreground shadow-sm"
                  : "hover:text-foreground",
              )}
            >
              {opt.label}
            </button>
          );
        })}
      </div>

      {!hideCaption && (
        <p className="text-xs text-muted-foreground">
          اختر مستوى التفصيل للإجابة
        </p>
      )}
    </div>
  );
}
