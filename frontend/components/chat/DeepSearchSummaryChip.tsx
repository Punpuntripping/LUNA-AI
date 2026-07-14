"use client";

import { memo, useState } from "react";
import { ChevronDown, Search } from "lucide-react";

import { cn } from "@/lib/utils";
import type { DeepSearchSummary } from "@/stores/chat-store";
import {
  formatCountAr,
  formatElapsedAr,
  toArabicDigits,
  type ArabicPlural,
} from "@/components/chat/DeepSearchProgress";

// ─────────────────────────────────────────────────────────────────────────────
// EDITABLE CONFIG — all Arabic copy for the collapsed chip (no i18n framework).
// ─────────────────────────────────────────────────────────────────────────────

const LABEL = "بحث معمّق";

/** Sources counted in the chip ("٢٤ مصدرًا"). */
const SOURCE_FORMS: ArabicPlural = {
  one: "مصدر واحد",
  two: "مصدران",
  few: "مصادر",
  many: "مصدرًا",
};

const EXPAND_LABEL = "عرض خطوات البحث المعمّق";
const COLLAPSE_LABEL = "إخفاء خطوات البحث المعمّق";
const EMPTY_LOG = "لا توجد تفاصيل إضافية";

/** Separator between the chip's parts: «بحث معمّق · ٢٤ مصدرًا · ١:٥٠». */
const SEP = "·";

// ─────────────────────────────────────────────────────────────────────────────

interface DeepSearchSummaryChipProps {
  /** Sealed totals of the finished run (chat-store.deepSearchSummaries[id]). */
  summary: DeepSearchSummary;
  className?: string;
}

/**
 * Collapsed, session-only receipt of a finished `deep_search` run — what the
 * live `DeepSearchProgress` tracker becomes once the answer lands.
 *
 * Rendered directly above the assistant bubble whose message id the summary is
 * keyed by; clicking it expands the full stage + status log the run produced.
 * Nothing here is persisted: a reload simply drops the chip, the answer stays.
 */
export const DeepSearchSummaryChip = memo(function DeepSearchSummaryChip({
  summary,
  className,
}: DeepSearchSummaryChipProps) {
  const [isOpen, setIsOpen] = useState(false);

  const parts: string[] = [LABEL];
  if (summary.sources > 0) {
    parts.push(formatCountAr(summary.sources, SOURCE_FORMS));
  }
  parts.push(formatElapsedAr(summary.elapsedMs));

  return (
    // Same alignment as the assistant bubble it captions (RTL start = right).
    <div
      dir="rtl"
      lang="ar"
      className={cn("flex w-full justify-start", className)}
    >
      {/* Fixed-height row: the chip is exactly as tall as the collapsed button,
          so mounting it above the bubble never shifts the settled layout. */}
      <div className="max-w-[85%] min-w-0">
        <div className="flex h-7 items-center">
          <button
            type="button"
            onClick={() => setIsOpen((open) => !open)}
            aria-expanded={isOpen}
            aria-label={isOpen ? COLLAPSE_LABEL : EXPAND_LABEL}
            className={cn(
              "flex h-7 items-center gap-1.5 rounded-full border bg-muted/40 px-2.5",
              "text-[11px] text-muted-foreground transition-colors",
              "hover:bg-muted hover:text-foreground",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            )}
          >
            <ChevronDown
              aria-hidden="true"
              className={cn(
                "h-3 w-3 shrink-0 transition-transform duration-200",
                // Collapsed: caret points along the RTL reading direction.
                isOpen ? "rotate-0" : "rotate-90",
              )}
            />
            <Search className="h-3 w-3 shrink-0" aria-hidden="true" />
            <span className="truncate tabular-nums">
              {parts.join(` ${SEP} `)}
            </span>
          </button>
        </div>

        {isOpen && (
          <div className="mt-1.5 mb-1 max-h-64 overflow-y-auto rounded-2xl border bg-card px-3 py-2">
            {summary.log.length === 0 ? (
              <p className="text-[11px] leading-4 text-muted-foreground">
                {EMPTY_LOG}
              </p>
            ) : (
              <ol className="space-y-1">
                {summary.log.map((line, i) => (
                  <li
                    key={`${i}-${line}`}
                    className="flex gap-1.5 text-[11px] leading-4 text-muted-foreground"
                  >
                    <span className="shrink-0 tabular-nums text-muted-foreground/60">
                      {toArabicDigits(i + 1)}.
                    </span>
                    <span className="min-w-0 flex-1 whitespace-pre-wrap break-words">
                      {line}
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </div>
        )}
      </div>
    </div>
  );
});
