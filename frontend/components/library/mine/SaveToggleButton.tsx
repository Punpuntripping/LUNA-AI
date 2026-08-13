"use client";

import { Bookmark, BookmarkCheck, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  useSaveLibraryItem,
  useUnsaveLibraryItem,
} from "@/hooks/use-my-library";
import type { MyLibraryItemRef } from "@/lib/api";
import { MY_LIBRARY_COPY } from "@/components/library/mine/copy";

interface SaveToggleButtonProps {
  /** The item this toggle pins/unpins (never named `ref` — React reserves it). */
  target: MyLibraryItemRef;
  /** `source === 'manual'` upstream — the row is deliberately pinned. */
  isSaved: boolean;
  /** Compact icon-only variant for nested مواد rows. */
  compact?: boolean;
  className?: string;
}

/**
 * «حفظ» / «إزالة الحفظ» — the explicit pin.
 *
 * FREE AT EVERY TIER AND GRANTS NO ACCESS (§5B.2): it stores a pointer, never
 * content, so it is enabled on frozen and gated rows too — pinning something
 * you cannot yet read is a legitimate intent signal.
 *
 * Optimistic + rollback lives in the mutation hooks, mirroring the
 * conversation-star interaction (`useStarConversation`).
 */
export function SaveToggleButton({
  target,
  isSaved,
  compact = false,
  className,
}: SaveToggleButtonProps) {
  const save = useSaveLibraryItem();
  const unsave = useUnsaveLibraryItem();
  const isPending = save.isPending || unsave.isPending;

  const label = isSaved ? MY_LIBRARY_COPY.unsave : MY_LIBRARY_COPY.save;

  const handleClick = () => {
    if (isPending) return;
    if (isSaved) unsave.mutate(target);
    else save.mutate(target);
  };

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={isPending}
      aria-pressed={isSaved}
      aria-label={label}
      title={label}
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60",
        isSaved
          ? "border-primary/40 bg-primary/5 text-primary hover:bg-primary/10"
          : "border-border bg-card text-muted-foreground hover:border-primary/40 hover:text-primary",
        className,
      )}
    >
      {isPending ? (
        <Loader2 aria-hidden="true" className="h-3 w-3 animate-spin" />
      ) : isSaved ? (
        <BookmarkCheck aria-hidden="true" className="h-3 w-3" />
      ) : (
        <Bookmark aria-hidden="true" className="h-3 w-3" />
      )}
      {!compact && <span>{isSaved ? MY_LIBRARY_COPY.saved : MY_LIBRARY_COPY.save}</span>}
    </button>
  );
}
