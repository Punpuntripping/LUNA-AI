import { cn } from "@/lib/utils";
import { fileTypePillLabel } from "@/lib/contracts";

interface FileTypePillProps {
  /** `doc_type` as it came off the wire — untyped / unknown / `other` render nothing. */
  docType: unknown;
  className?: string;
}

/**
 * The document type on a file card and in the viewer header («حكم», «عقد»…).
 *
 * One quiet style for every type — the pill chrome tokens (`bg-pill` /
 * `text-pill-fg`) the conversation list already uses for «تجريبية» — rather
 * than a colour per type: fourteen hues on one list would be the rainbow the
 * workspace badges were retoned away from.
 */
export function FileTypePill({ docType, className }: FileTypePillProps) {
  const label = fileTypePillLabel(docType);
  if (!label) return null;
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center rounded-full bg-pill px-2 py-0.5 text-xs font-medium leading-5 text-pill-fg",
        className,
      )}
    >
      {label}
    </span>
  );
}
