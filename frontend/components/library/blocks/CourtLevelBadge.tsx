import { cn } from "@/lib/utils";
import { courtLevelLabel } from "@/lib/library/court-levels";
import type { CourtLevelBadgeProps } from "@/types/library";

/**
 * درجة التقاضي pill — ابتدائي / استئناف / المحكمة العليا.
 *
 * Deliberately NOT `StatusBadge`: that block's three labels are hardcoded to a
 * regulation's lifecycle (ساري / معدَّل / ملغي), so pointing it at a court level
 * would print «ساري» on a judgment — a false statement about a court ruling, not
 * a styling gap. This mirrors StatusBadge's chrome (border + dot + pill) so the
 * two read as one badge family, with tone rising by judicial seniority: a
 * supreme-court ruling is the highest-authority precedent on the page and gets
 * the solid fill. Server component.
 *
 * Renders NOTHING when neither the backend label nor the local map resolves —
 * an unlabelled badge would be noise, and guessing a level would be a lie.
 */
export function CourtLevelBadge({
  level,
  label,
  className,
}: CourtLevelBadgeProps) {
  const text = courtLevelLabel(level, label);
  if (!text) return null;

  const chrome =
    level === "supreme"
      ? "border-transparent bg-primary font-semibold text-primary-foreground"
      : level === "appeal"
        ? "border-primary/20 bg-accent-soft text-primary"
        : "border-border bg-surface-2 text-text-secondary";

  const dot =
    level === "supreme"
      ? "bg-primary-foreground"
      : level === "appeal"
        ? "bg-primary"
        : "bg-text-muted";

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
      {text}
    </span>
  );
}
