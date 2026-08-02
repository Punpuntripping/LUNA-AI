"use client";

import { SEARCH_COPY } from "@/lib/search/copy";
import type { SearchErrorKind } from "@/hooks/use-search";

/**
 * A search that produced no cards for a reason that is not «no matches» — the
 * sibling of `SearchEmptyState`, and shared by every search surface for the same
 * reason: a reader who searches in two places must be told the same thing the
 * same way.
 *
 * Lifted out of `HubSearchPanel` when the cross-wing `/library` page (Wave E)
 * became the second caller. It was private there while there was one caller; a
 * second copy of the rate-limit wording is exactly how two surfaces start
 * disagreeing about what a 429 means.
 *
 * ⚠ THE TWO BRANCHES ARE DIFFERENT ANSWERS AND MUST STAY DIFFERENT. A full reach
 * budget is a true statement ABOUT THE READER and carries no retry — at an hour
 * of `Retry-After` a button could only fail again — while a fault is «try again»
 * and carries one. Collapsing them into a single «something went wrong» is the
 * exact bug the access-tiers work fixed one layer down.
 *
 * `"use client"` because of `onRetry`: unlike `SearchEmptyState` this is not a
 * pure presentational subtree, and a server component importing it would build
 * and then fail at the event handler.
 */
export function SearchFailure({
  kind,
  onRetry,
}: {
  kind: SearchErrorKind;
  onRetry: () => void;
}) {
  if (kind === "rate_limited") {
    return (
      <div
        dir="rtl"
        className="flex flex-col items-center justify-center gap-2 py-16 text-center"
      >
        <p className="text-sm font-medium text-foreground">
          {SEARCH_COPY.rateLimitedTitle}
        </p>
        <p className="text-xs text-muted-foreground">
          {SEARCH_COPY.rateLimitedBody}
        </p>
      </div>
    );
  }

  return (
    <div
      dir="rtl"
      className="flex flex-col items-center justify-center gap-3 py-16 text-center"
    >
      <p className="text-sm font-medium text-muted-foreground">
        {SEARCH_COPY.errorTitle}
      </p>
      <button
        type="button"
        onClick={onRetry}
        className="rounded-full border border-border bg-card px-4 py-1.5 text-xs font-medium text-foreground transition-colors hover:border-primary/40 hover:text-primary"
      >
        {SEARCH_COPY.retry}
      </button>
    </div>
  );
}
