"use client";

import Link from "next/link";
import { FileQuestion } from "lucide-react";
import { cn } from "@/lib/utils";
import { RegulationCard } from "@/components/library/hub/RegulationCard";
import { JudgmentCard } from "@/components/library/hub/JudgmentCard";
import { CircularCard } from "@/components/library/hub/CircularCard";
import { ComplianceCard } from "@/components/library/hub/ComplianceCard";
import { FormCard } from "@/components/library/hub/FormCard";
import type { ApiDocStatus } from "@/lib/library/api";
import type { MyLibraryRow } from "@/lib/api";
import { MY_LIBRARY_COPY } from "@/components/library/mine/copy";

/**
 * Renders ONE shelf row with the existing public-hub card for its wing —
 * «مكتبتي» is a filtered hub, not a new design system (§5B.1). The row already
 * carries the hub item fields under the hub's own names, so each card takes it
 * essentially as-is; this switch only fills the non-null defaults the card prop
 * types require.
 *
 * LINKABLE IS NOT THE SAME AS AVAILABLE. An item unlocked from a chat citation
 * very often has no public library page yet — only 100 of 3,373 regulations
 * carry a slug while the library is in sample mode — so "unlocked but
 * unpublished" is the COMMON case here, not an edge case. Those rows render as
 * normal cards (the reader owns them) and are simply not links; `CardShell`
 * takes `href={null}` and drops the anchor. Falling back to a muted "غير متاح"
 * box for them, as an earlier pass did, made the shelf look broken for most of
 * its contents and told the reader something false — they had unlocked the item
 * and read it in chat.
 *
 * Only two cases still fall through to the plain card:
 *   * `is_available === false` — nothing hydrated at all (the corpus row is
 *     gone). There is genuinely nothing to show but a title.
 *   * wings with no hub card — `calculator`, and a مادة that could not be
 *     nested under its نظام.
 */
export function ShelfCard({ row }: { row: MyLibraryRow }) {
  const title = row.title?.trim() || fallbackTitle(row);

  if (!row.is_available) {
    return <PlainShelfCard title={title} note={MY_LIBRARY_COPY.unavailableNote} />;
  }

  // Null href ⇒ rendered as a card, not a link (no public page yet).
  const href = row.url ?? null;

  switch (row.content_type) {
    case "regulation":
      return (
        <RegulationCard
          href={href}
          item={{
            slug: row.slug ?? "",
            title,
            entity_name: row.entity_name ?? "",
            status: asDocStatus(row.status),
            doc_type: row.doc_type ?? "",
            summary_snippet: row.summary_snippet ?? "",
            sectors: row.sectors ?? [],
          }}
        />
      );
    case "judgment":
      return (
        <JudgmentCard
          href={href}
          item={{
            slug: row.slug ?? "",
            title,
            court: row.court ?? "",
            court_level: row.court_level ?? "",
            court_level_label: row.court_level_label ?? "",
            city: row.city ?? null,
            date_hijri: row.date_hijri ?? null,
            date_gregorian: row.date_gregorian ?? null,
            domains: row.domains ?? [],
            snippet: row.snippet ?? "",
          }}
        />
      );
    case "circular":
      return (
        <CircularCard
          href={href}
          item={{
            slug: row.slug ?? "",
            title,
            entity_name: row.entity_name ?? null,
            source_label: row.source_label ?? null,
            body_snippet: row.body_snippet ?? "",
            body_length: row.body_length ?? 0,
          }}
        />
      );
    case "service":
      return (
        <ComplianceCard
          href={href}
          item={{
            slug: row.slug ?? "",
            title,
            provider_name: row.provider_name ?? "",
            is_most_used: row.is_most_used ?? false,
            sectors: row.sectors ?? [],
            intro_snippet: row.intro_snippet ?? "",
          }}
        />
      );
    case "form":
      return (
        <FormCard
          href={href}
          item={{
            slug: row.slug ?? "",
            title,
            category: row.category ?? null,
            use_case_snippet: row.use_case_snippet ?? "",
          }}
        />
      );
    default:
      // calculator · a top-level (un-nestable) مادة — no hub card exists.
      return <PlainShelfCard title={title} href={row.url} />;
  }
}

/** A title for a row whose corpus record no longer hydrates. */
function fallbackTitle(row: MyLibraryRow): string {
  if (row.content_type === "calculator") {
    return MY_LIBRARY_COPY.calculatorFallbackTitle;
  }
  if (row.content_type === "article") {
    return row.article_label || MY_LIBRARY_COPY.untitled;
  }
  return MY_LIBRARY_COPY.untitled;
}

/**
 * Unknown / missing status → no badge. `toDocStatus` (used inside
 * RegulationCard) maps anything outside active|amended|repealed to `null`, so
 * `'draft'` is the fail-closed value: a repealed law must never render as
 * current, and a badge is never guessed.
 */
function asDocStatus(status: string | null | undefined): ApiDocStatus {
  return status === "active" || status === "amended" || status === "repealed"
    ? status
    : "draft";
}

/**
 * The muted, optionally non-linkable card. Same footprint as a hub card so the
 * grid stays even.
 */
export function PlainShelfCard({
  title,
  href,
  note,
}: {
  title: string;
  href?: string | null;
  note?: string;
}) {
  const body = (
    <>
      <FileQuestion
        aria-hidden="true"
        className="mb-2 h-4 w-4 text-muted-foreground"
      />
      <h2 className="line-clamp-2 text-base font-bold leading-snug text-foreground">
        {title}
      </h2>
      {note && (
        <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
          {note}
        </p>
      )}
    </>
  );

  const shell = cn(
    "flex h-full flex-col rounded-xl border border-dashed border-border bg-card/60 p-4 sm:p-5",
  );

  if (href) {
    return (
      <Link
        href={href}
        dir="rtl"
        className={cn(
          shell,
          "group border-solid shadow-xs transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md",
        )}
      >
        {body}
      </Link>
    );
  }

  return (
    <div dir="rtl" className={shell} aria-disabled="true">
      {body}
    </div>
  );
}
