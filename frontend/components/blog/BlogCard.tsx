import Link from "next/link";
import { Eye } from "lucide-react";
import { AR_DATE_LOCALE } from "@/lib/format/numerals";
import { blogPath } from "@/lib/blog/slug";
import { blogTypeLabel } from "@/components/blog/SubjectChips";
import type { PublicBlogCard } from "@/types";

// Arabic long-form Gregorian date («12 يونيو 2026»), Latin digits. Module-level
// so the Intl formatter is built once, not per card render.
const DATE_FORMATTER = new Intl.DateTimeFormat(AR_DATE_LOCALE, {
  day: "numeric",
  month: "long",
  year: "numeric",
});

function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return DATE_FORMATTER.format(date);
}

/**
 * One card in the `/blog` gallery or a `/blog/{subject}` listing.
 *
 * Addressed by `slug`, never a token: a public blog is open by default (plan
 * D17), so the slug IS the address. The href carries the raw Arabic — `Link`
 * does the one encode (`lib/blog/slug.ts`).
 *
 * Server component. The type renders as a kicker badge and is never a link
 * (D3); the subject chips that ARE links live on the article surface, where
 * there is room for them.
 */
export function BlogCard({ blog }: { blog: PublicBlogCard }) {
  const typeLabel = blogTypeLabel(blog.type);
  const date = formatDate(blog.created_at);

  return (
    <Link
      href={blogPath(blog.slug)}
      className="flex flex-col rounded-xl border bg-card p-4 shadow-sm transition hover:border-primary/30 hover:shadow-md"
    >
      {typeLabel && (
        <span className="mb-2 inline-flex w-fit items-center rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
          {typeLabel}
        </span>
      )}

      <h2 className="line-clamp-2 text-base font-bold leading-snug text-foreground">
        {blog.title}
      </h2>

      {blog.snippet && (
        <p className="mt-2 line-clamp-3 text-sm leading-relaxed text-muted-foreground">
          {blog.snippet}
        </p>
      )}

      <div className="mt-4 flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>{date}</span>
        <span className="inline-flex items-center gap-1 tabular-nums">
          <Eye className="h-3.5 w-3.5" />
          {blog.view_count}
        </span>
      </div>
    </Link>
  );
}
