import Link from "next/link";
import { cn } from "@/lib/utils";
import { subjectPath } from "@/lib/blog/slug";
import type { BlogSubjectRef } from "@/types";

/**
 * The three types a public blog can carry (`public_blogs.type`, a DB CHECK).
 * Labels COPIED from the plan's own vocabulary (`blog_subjects.md` §1) — never
 * retyped, never shortened to fit a badge.
 *
 * ⚠ A TYPE IS A BADGE, NEVER A URL (plan D3). The browse axis is subjects; type
 * is carried by the blog and is filterable, not addressable.
 */
export const BLOG_TYPE_LABEL: Record<string, string> = {
  laws_explanation: "أنظمة — شروحات وتعديلات",
  judicial_research: "أبحاث قضائية",
  compliance: "امتثال",
};

/**
 * Arabic label for a blog type, falling back to the raw value. A fourth type
 * added server-side then renders as itself instead of vanishing — the same
 * discipline `SUBTYPE_LABEL` uses on the legacy surfaces.
 */
export function blogTypeLabel(type: string | null | undefined): string | null {
  if (!type) return null;
  return BLOG_TYPE_LABEL[type] ?? type;
}

/**
 * The type badge + subject chips that sit under a public blog's byline.
 *
 * This is the internal-linking spine of the wing (plan §4): how a reader who
 * landed on one article from Google discovers the subject it belongs to, and
 * how link equity reaches the subject listing pages. Server component — real
 * `<Link>`s in the SSR HTML, for readers and crawlers alike.
 *
 * Renders nothing when there is neither a type nor a subject, which is exactly
 * the legacy `blog_posts` case: those rows have no `type` and no subjects, and
 * the same reading surface serves them.
 */
export function SubjectChips({
  type,
  subjects = [],
  className,
}: {
  type?: string | null;
  subjects?: BlogSubjectRef[];
  className?: string;
}) {
  const typeLabel = blogTypeLabel(type);
  if (!typeLabel && subjects.length === 0) return null;

  return (
    <div
      dir="rtl"
      className={cn("flex flex-wrap items-center justify-center gap-2", className)}
    >
      {typeLabel && (
        <span className="inline-flex items-center rounded-full bg-primary/10 px-3 py-1 text-xs font-medium text-primary">
          {typeLabel}
        </span>
      )}
      {subjects.map((subject) => (
        <Link
          key={subject.slug}
          href={subjectPath(subject.slug)}
          className="inline-flex items-center rounded-full border border-border bg-card px-3 py-1 text-xs font-medium text-text-secondary transition-colors hover:border-primary/40 hover:text-primary"
        >
          {subject.label_ar}
        </Link>
      ))}
    </div>
  );
}
