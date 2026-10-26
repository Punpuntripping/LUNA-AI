"use client";

import Link from "next/link";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
import { buttonVariants } from "@/components/ui/button";
import { AR_DATE_LOCALE } from "@/lib/format/numerals";
import { cn } from "@/lib/utils";
import type { Announcement } from "@/lib/contracts";

const DATE_FORMAT = new Intl.DateTimeFormat(AR_DATE_LOCALE, {
  calendar: "gregory",
  year: "numeric",
  month: "long",
  day: "numeric",
});

export function formatAnnouncementDate(iso: string): string {
  const at = Date.parse(iso);
  return Number.isNaN(at) ? "" : DATE_FORMAT.format(new Date(at));
}

/**
 * Markdown body of one «رسائل ريحان» item — shared by the popup and /messages.
 * Messages are written line by line and must show as written, so a single
 * newline inside a paragraph is a visible line break (`pre-line` on <p> only —
 * on the container it would also render the "\n" text nodes react-markdown
 * leaves between blocks).
 */
export function AnnouncementMarkdown({ item }: { item: Announcement }) {
  return (
    <MarkdownRenderer
      content={item.body_md}
      className="text-sm leading-relaxed text-foreground [&_p]:whitespace-pre-line"
    />
  );
}

/**
 * The optional call-to-action. An in-site path stays in the app (client
 * navigation); an absolute https URL opens in a new tab.
 */
export function AnnouncementLink({
  item,
  onNavigate,
  className,
  variant = "default",
  size = "default",
}: {
  item: Announcement;
  onNavigate?: () => void;
  className?: string;
  variant?: "default" | "outline";
  size?: "default" | "sm";
}) {
  if (!item.link_url || !item.link_label) return null;
  const classes = cn(buttonVariants({ variant, size }), "font-semibold", className);

  if (item.link_url.startsWith("/")) {
    return (
      <Link href={item.link_url} className={classes} onClick={onNavigate}>
        {item.link_label}
      </Link>
    );
  }
  return (
    <a
      href={item.link_url}
      target="_blank"
      rel="noopener noreferrer"
      className={classes}
      onClick={onNavigate}
    >
      {item.link_label}
    </a>
  );
}
