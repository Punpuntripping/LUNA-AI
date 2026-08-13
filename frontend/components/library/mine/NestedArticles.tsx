"use client";

import Link from "next/link";
import { Lock } from "lucide-react";
import type { MyLibraryArticle } from "@/lib/api";
import { SaveToggleButton } from "@/components/library/mine/SaveToggleButton";
import { MY_LIBRARY_COPY, usageLabel } from "@/components/library/mine/copy";

/**
 * The مواد a user shelved from one نظام, nested under it (§5B.1 — مواد are
 * NEVER a top-level tab: "a مادة without its statute reads as an orphan").
 *
 * Rendered as a right-indented list (RTL: `border-s` is the right edge) so the
 * hierarchy is readable at a glance. Each مادة keeps its own lock badge, usage
 * count and «حفظ» toggle — the parent's state never stands in for a child's.
 */
export function NestedArticles({ articles }: { articles: MyLibraryArticle[] }) {
  if (articles.length === 0) return null;

  return (
    <ul dir="rtl" className="mt-2 space-y-1 border-s-2 border-border/70 ps-3">
      {articles.map((article) => {
        const label =
          article.article_label || article.title || MY_LIBRARY_COPY.untitled;
        const linkable = article.is_available && !!article.url;

        return (
          <li
            key={article.content_id}
            className="flex items-center gap-2 rounded-md px-1 py-0.5 text-xs transition-colors hover:bg-surface-2/60"
          >
            {linkable ? (
              <Link
                href={article.url as string}
                className="truncate font-medium text-text-secondary transition-colors hover:text-primary hover:underline"
              >
                {label}
              </Link>
            ) : (
              <span
                className="truncate font-medium text-muted-foreground"
                title={MY_LIBRARY_COPY.unavailableNote}
              >
                {label}
                <span className="ms-1 text-xs">
                  ({MY_LIBRARY_COPY.unavailableBadge})
                </span>
              </span>
            )}

            {article.is_frozen && (
              <Lock
                aria-label={MY_LIBRARY_COPY.frozenBadge}
                className="h-3 w-3 shrink-0 text-primary"
              />
            )}

            <span className="shrink-0 text-xs text-text-muted">
              {usageLabel(article.use_count)}
            </span>

            <SaveToggleButton
              compact
              target={{
                content_type: "article",
                content_id: article.content_id,
              }}
              isSaved={article.source === "manual"}
              className="ms-auto"
            />
          </li>
        );
      })}
    </ul>
  );
}
