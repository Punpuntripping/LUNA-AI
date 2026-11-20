import { Paperclip } from "lucide-react";
import { cn } from "@/lib/utils";
import { FILES_COPY } from "./copy";

interface ReattachAdviceProps {
  /** `inline`: a small tag on a card. `banner`: a strip across a viewer. */
  variant?: "inline" | "banner";
  className?: string;
}

/**
 * «ننصح بإعادة إرفاق هذا الملف» — shown on a file whose stored extraction is
 * known to be shorter than the file (`reattach_advised`, decided on the server:
 * `files_service.reattach_advised`). The owner's rule (2026-11-20): advise, give
 * no reason. A re-attached copy is extracted afresh and is never matched to
 * this one by the same-file popup.
 *
 * Deliberately not behind the «الملفات» flag: the old attachment view shows it
 * too, so the advice reaches users whatever the rollout state.
 */
export function ReattachAdvice({ variant = "inline", className }: ReattachAdviceProps) {
  if (variant === "banner") {
    return (
      <div
        role="note"
        className={cn(
          "flex items-center gap-2 border-b bg-warning px-3 py-2 text-xs font-medium text-warning-fg",
          className,
        )}
      >
        <Paperclip className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span>{FILES_COPY.reattachAdvised}</span>
      </div>
    );
  }
  return (
    <p
      role="note"
      className={cn(
        "inline-flex max-w-full items-center gap-1.5 rounded-md bg-warning px-2 py-1 text-xs font-medium text-warning-fg",
        className,
      )}
    >
      <Paperclip className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <span className="min-w-0 truncate">{FILES_COPY.reattachAdvised}</span>
    </p>
  );
}
