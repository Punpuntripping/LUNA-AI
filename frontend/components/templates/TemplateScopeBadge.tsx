import { cn } from "@/lib/utils";
import type { TemplateScope } from "@/types";

interface TemplateScopeBadgeProps {
  scope: TemplateScope;
  /** «عام» / «خاص» instead of «قالب عام» / «قالب خاص» (inline chat line). */
  short?: boolean;
  className?: string;
}

/**
 * Scope pill for a قالب: ``system`` = «قالب عام» (shared, read-only),
 * ``user`` = «قالب خاص» (the user's own). Same shape as ``WiBadge`` so the
 * small metadata pills read as one family, minus the mono font (Arabic text).
 */
export function TemplateScopeBadge({
  scope,
  short = false,
  className,
}: TemplateScopeBadgeProps) {
  const isSystem = scope === "system";
  const label = isSystem
    ? short
      ? "عام"
      : "قالب عام"
    : short
      ? "خاص"
      : "قالب خاص";
  return (
    <span
      title={
        isSystem
          ? "قالب عام من ريحان — للقراءة فقط"
          : "قالب خاص بك"
      }
      className={cn(
        "inline-flex shrink-0 items-center rounded-full border border-border/70",
        "bg-muted/50 px-1.5 py-0.5 text-[10px] font-medium",
        "leading-none text-muted-foreground",
        className,
      )}
    >
      {label}
    </span>
  );
}
