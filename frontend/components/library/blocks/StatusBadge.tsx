import { cn } from "@/lib/utils";
import type { DocStatus, StatusBadgeProps } from "@/types/library";

interface StatusStyle {
  label: string;
  /** Tailwind classes for the pill chrome. */
  chrome: string;
  /** Tailwind class for the status dot. */
  dot: string;
}

const STATUS_STYLE: Record<DocStatus, StatusStyle> = {
  active: {
    label: "ساري",
    chrome: "border-success-fg/20 bg-success text-success-fg",
    dot: "bg-success-fg",
  },
  amended: {
    label: "معدَّل",
    chrome: "border-warning-fg/20 bg-warning text-warning-fg",
    dot: "bg-warning-fg",
  },
  // Repealed must be UNMISSABLE — a repealed law rendered as current is a hard
  // failure. Solid destructive fill + ring + bold, not the soft error tokens.
  repealed: {
    label: "ملغي",
    chrome:
      "border-transparent bg-destructive text-destructive-foreground font-bold ring-2 ring-destructive/40",
    dot: "bg-destructive-foreground",
  },
};

/**
 * Regulation/document lifecycle badge — a dot+label pill: ساري / معدَّل / ملغي.
 * Server component.
 */
export function StatusBadge({ status, className }: StatusBadgeProps) {
  const { label, chrome, dot } = STATUS_STYLE[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium",
        chrome,
        className,
      )}
    >
      <span
        aria-hidden="true"
        className={cn("h-1.5 w-1.5 shrink-0 rounded-full", dot)}
      />
      {label}
    </span>
  );
}
