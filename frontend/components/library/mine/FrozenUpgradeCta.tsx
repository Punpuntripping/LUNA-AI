"use client";

import Link from "next/link";
import { Lock } from "lucide-react";
import { cn } from "@/lib/utils";
import { buttonVariants } from "@/components/ui/button";
import {
  MY_LIBRARY_COPY,
  frozenCtaText,
} from "@/components/library/mine/copy";

/**
 * The §5B.4 conversion surface: a downgraded user's shelf is never hidden or
 * emptied — every item still lists with a lock badge — and this banner names
 * how much of it is currently out of reach.
 *
 * "A frozen library rendered as an empty page is a worse product AND a worse
 * conversion surface." Listing leaks nothing: titles, metadata, entity, dates
 * and topic chips are all in the never-gated class (§1.3).
 *
 * Renders nothing when nothing is frozen (always the case for a paid caller).
 */
export function FrozenUpgradeCta({ frozenCount }: { frozenCount: number }) {
  if (frozenCount <= 0) return null;

  return (
    <div
      dir="rtl"
      className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-primary/30 bg-accent-soft px-4 py-3"
    >
      <p className="flex items-center gap-2 text-sm font-medium text-foreground">
        <Lock aria-hidden="true" className="h-4 w-4 shrink-0 text-primary" />
        {frozenCtaText(frozenCount)}
      </p>
      <Link
        href="/pricing"
        className={cn(
          buttonVariants({ size: "sm" }),
          "text-sm font-semibold",
        )}
      >
        {MY_LIBRARY_COPY.frozenCtaAction}
      </Link>
    </div>
  );
}
