"use client";

import { BookText, Loader2, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { PendingBlog } from "@/types";

interface BlogChipsProps {
  blogs: PendingBlog[];
  onRemove: (id: string) => void;
  className?: string;
}

/**
 * Composer chips for pasted blog share-links (.claude/plans/blog_import.md
 * §D4) — the blog twin of ``FilePreview``. One pill per pasted token:
 * spinner while the import is in flight, the blog title once ready, an
 * Arabic error state on a bad/revoked link. Failed chips never block send.
 */
export function BlogChips({ blogs, onRemove, className }: BlogChipsProps) {
  if (blogs.length === 0) return null;

  return (
    <div dir="rtl" className={cn("flex flex-wrap gap-2", className)}>
      {blogs.map((blog) => (
        <div
          key={blog.id}
          className={cn(
            "flex max-w-64 items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs",
            blog.status === "failed"
              ? "border-destructive/30 bg-destructive/10 text-destructive"
              : "border-border bg-muted/50 text-foreground",
          )}
        >
          {blog.status === "loading" ? (
            <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" />
          ) : (
            <BookText
              className={cn(
                "h-3.5 w-3.5 shrink-0",
                blog.status === "failed" ? "text-destructive" : "text-primary",
              )}
            />
          )}

          <span className="truncate">
            {blog.status === "failed"
              ? blog.errorMessage || "رابط مدونة غير صالح"
              : (blog.title ?? "").trim() || "مدونة"}
          </span>

          <button
            type="button"
            onClick={() => onRemove(blog.id)}
            aria-label="إزالة المدونة"
            className="shrink-0 rounded-full p-0.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            <X className="h-3 w-3" />
          </button>
        </div>
      ))}
    </div>
  );
}
