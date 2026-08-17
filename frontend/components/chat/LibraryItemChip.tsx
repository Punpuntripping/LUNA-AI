"use client";

import type { LucideIcon } from "lucide-react";
import { BookText, Gavel, Loader2, Scale, ScrollText, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { LibraryItemPageType, PendingLibraryItem } from "@/types";

interface LibraryItemChipsProps {
  items: PendingLibraryItem[];
  onRemove: (id: string) => void;
  className?: string;
}

/**
 * Icon + generic Arabic label per carried page type. The label is a FALLBACK
 * only — a library page always has a heading, and the heading is what the
 * reader recognises. It exists for the window between «attach» and the POST
 * returning a title, and for the rare row with a blank one.
 */
const PAGE_TYPE_META: Record<
  LibraryItemPageType,
  { Icon: LucideIcon; label: string }
> = {
  regulation: { Icon: Scale, label: "نظام" },
  article: { Icon: ScrollText, label: "مادة" },
  judgment: { Icon: Gavel, label: "حكم" },
  blog: { Icon: BookText, label: "مدونة" },
};

/**
 * Composer chips for library pages carried into the chat by «تحدّث مع ريحان عن
 * هذه الصفحة» (`.claude/plans/simple_search_family.md` §8) — the library twin
 * of `BlogChips`. One pill per page: spinner while the
 * `POST /conversations/{id}/library-items` is in flight, the page title once
 * it lands, an Arabic error state when it fails. Failed chips never block send
 * (`ChatInput` counts only `ready` ones), so a page that cannot be carried
 * degrades to "the message still sends" rather than a dead composer.
 */
export function LibraryItemChips({
  items,
  onRemove,
  className,
}: LibraryItemChipsProps) {
  if (items.length === 0) return null;

  return (
    <div dir="rtl" className={cn("flex flex-wrap gap-2", className)}>
      {items.map((item) => {
        const meta = PAGE_TYPE_META[item.pageType];
        const Icon = meta.Icon;
        return (
          <div
            key={item.id}
            className={cn(
              "flex max-w-64 items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs",
              item.status === "failed"
                ? "border-destructive/30 bg-destructive/10 text-destructive"
                : "border-border bg-muted/50 text-foreground",
            )}
          >
            {item.status === "loading" ? (
              <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" />
            ) : (
              <Icon
                aria-hidden="true"
                className={cn(
                  "h-3.5 w-3.5 shrink-0",
                  item.status === "failed"
                    ? "text-destructive"
                    : "text-primary",
                )}
              />
            )}

            <span className="truncate">
              {item.status === "failed"
                ? item.errorMessage || "تعذّر إضافة الصفحة"
                : (item.title ?? "").trim() || meta.label}
            </span>

            <button
              type="button"
              onClick={() => onRemove(item.id)}
              aria-label={`إزالة ${meta.label}`}
              className="shrink-0 rounded-full p-0.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              <X aria-hidden="true" className="h-3 w-3" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
