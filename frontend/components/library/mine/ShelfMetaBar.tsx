"use client";

import { EyeOff, Lock } from "lucide-react";
import { cn } from "@/lib/utils";
import type { MyLibraryContentType, MyLibraryItemRef } from "@/lib/api";
import { SaveToggleButton } from "@/components/library/mine/SaveToggleButton";
import {
  MY_LIBRARY_COPY,
  lastUsedLabel,
  usageLabel,
} from "@/components/library/mine/copy";

interface ShelfMetaBarProps {
  contentType: MyLibraryContentType;
  contentId: string;
  /** `use_count` for a plain row, `group_use_count` for a نظام + its مواد. */
  useCount: number;
  lastUsedAt: string | null;
  /** `source === 'manual'` — the row is deliberately pinned. */
  isSaved: boolean;
  /** A `library_unlocks` row exists but the §1.2 predicate now fails. */
  isFrozen: boolean;
  /** No public URL resolved — the card is listed but not linkable. */
  isAvailable: boolean;
  /** Extra leading content (e.g. the nested-مواد counter on a نظام group). */
  leading?: React.ReactNode;
  className?: string;
}

/**
 * The caption strip under every shelf card: lock / unavailable badges, the
 * usage line, and the «حفظ» toggle.
 *
 * It sits BESIDE the hub card rather than on top of it. The hub cards are a
 * single `<Link>` wrapping the whole card, and interactive content inside an
 * anchor is invalid HTML — so the shelf actions live in a sibling row, which
 * also keeps them keyboard-reachable and never overlaps the cards' badges.
 */
export function ShelfMetaBar({
  contentType,
  contentId,
  useCount,
  lastUsedAt,
  isSaved,
  isFrozen,
  isAvailable,
  leading,
  className,
}: ShelfMetaBarProps) {
  const target: MyLibraryItemRef = {
    content_type: contentType,
    content_id: contentId,
  };
  const lastUsed = lastUsedLabel(lastUsedAt);

  return (
    <div
      dir="rtl"
      className={cn(
        "mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 px-1 text-xs text-text-muted",
        className,
      )}
    >
      {isFrozen && (
        <span className="inline-flex items-center gap-1 rounded-full bg-accent-soft px-2 py-0.5 font-medium text-primary">
          <Lock aria-hidden="true" className="h-3 w-3" />
          {MY_LIBRARY_COPY.frozenBadge}
        </span>
      )}

      {!isAvailable && (
        <span className="inline-flex items-center gap-1 rounded-full bg-surface-2 px-2 py-0.5 font-medium text-muted-foreground">
          <EyeOff aria-hidden="true" className="h-3 w-3" />
          {MY_LIBRARY_COPY.unavailableBadge}
        </span>
      )}

      {leading}

      <span>{usageLabel(useCount)}</span>
      {lastUsed && (
        <>
          <span aria-hidden="true">·</span>
          <span>{lastUsed}</span>
        </>
      )}

      <SaveToggleButton
        target={target}
        isSaved={isSaved}
        className="ms-auto"
      />
    </div>
  );
}
