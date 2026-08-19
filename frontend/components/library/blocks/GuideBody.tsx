import { cn } from "@/lib/utils";
import { ArticleBody } from "@/components/library/blocks/ArticleBody";
import {
  prettifyGuideUrls,
  splitGuideMarkdown,
  stripDuplicatedLead,
} from "@/lib/library/guide";
import type { ComplianceGuideImage } from "@/lib/library/api";

/**
 * The body of a service guide: our own authored rewrite of the issuing entity's
 * official PDF user-guide, published in full, with its screenshots dropped back
 * into the exact places the prose refers to them.
 *
 * `guide_md` arrives with HOLES — lines containing nothing but a `{ref}` token —
 * and `images` carries one row per hole. This component owns the join. The
 * contract it implements (and the two traps it exists to avoid) is documented on
 * `lib/library/guide.ts`; the short version:
 *
 *   - Resolve every hole by `image_ref` through the map below. NEVER by
 *     position: 28% of guides place their holes out of numeric order.
 *   - A hole with NO entry in the map renders NOTHING AT ALL. A raw `223719_1`
 *     on a user-facing page is the one failure mode this design prevents, and
 *     the backend already blanks unresolvable holes — this is the second layer.
 *
 * Server component: markdown in, JSX out, no data fetching, no state. The text
 * segments go through `ArticleBody` — the same library body block every other
 * document page renders through — so a guide's headings, lists and tables look
 * identical to the rest of the library.
 *
 * `dedupeHeading` / `dedupeLead` take the PLAIN corpus title and the summary the
 * page rendered above the body; see `stripDuplicatedLead` for why every guide
 * needs them.
 */
export function GuideBody({
  guideMd,
  images,
  dedupeHeading,
  dedupeLead,
  className,
}: {
  guideMd: string;
  images: ComplianceGuideImage[];
  dedupeHeading?: string;
  dedupeLead?: string;
  className?: string;
}) {
  const segments = splitGuideMarkdown(guideMd);
  const byRef = new Map(
    (images ?? []).map((image) => [image.image_ref, image] as const),
  );

  // The first RESOLVED screenshot loads eagerly — on a guide it is usually the
  // largest element above the fold, so lazying it would delay LCP. Identified by
  // segment identity rather than by index so an unresolvable leading hole cannot
  // hand the flag to nothing.
  const firstImageSegment = segments.find(
    (segment) => segment.kind === "image" && byRef.has(segment.ref),
  );

  const firstTextSegment = segments.find((segment) => segment.kind === "text");

  return (
    <div dir="rtl" className={cn("space-y-4", className)}>
      {segments.map((segment, index) => {
        if (segment.kind === "text") {
          // Only the FIRST text segment can carry the duplicated title/abstract.
          // `prettifyGuideUrls` LAST: 155 of 169 bodies print «الرابط الرسمي:»
          // followed by the URL as its own link text, and 13 of those are
          // percent-encoded Arabic that renders as an unreadable five-line wall.
          // Display only — every href is passed through untouched.
          const value = prettifyGuideUrls(
            segment === firstTextSegment
              ? stripDuplicatedLead(segment.value, dedupeHeading, dedupeLead)
              : segment.value,
          );
          // A body that was nothing BUT its own title + abstract strips to
          // empty — render nothing rather than an empty paragraph.
          if (!value.trim()) return null;
          return (
            <ArticleBody key={index} visibleText={value} headingAnchors />
          );
        }

        const image = byRef.get(segment.ref);
        if (!image) return null;

        return (
          <figure key={index} className="my-6">
            {/* ⚠ NO `<figcaption>`, DELIBERATELY — do not "restore" it.
                `image_ref.description` is a 400–1,031 char analysis written for
                MACHINE consumers (the RAG/agent layer that answers «أين أضغط؟»
                from it), NOT copy for a reader. Printed under each screenshot it
                read as a wall of generated prose — 47 paragraphs on the حساب
                المواطن guide alone — restating what the image already shows and
                what the prose above it already said.

                ⚠ THE INGESTION CONTRACT DISAGREES AND IS OVERRIDDEN HERE.
                REFERENCE.md §3.2 rule 3 calls the description "alt text and/or
                caption" and §3.3 has a text-only channel print it as a
                parenthetical. That is right for an agent rendering a guide into
                a chat reply; it is wrong for this page. The product decision
                (2026-08-19) is: descriptions serve agents, the page shows the
                screenshot.

                It stays as `alt` — invisible to sighted readers, and dropping it
                would blind screen readers and crawlers on a wing whose entire
                purpose is SEO. */}
            {/* `width`/`height` are REQUIRED here, not decorative: they reserve
                the box before the bytes land, and one guide carries 69
                screenshots — without them the page reflows 69 times.

                A plain `<img>` on purpose, not `next/image`: the bucket is
                public and the payload already carries intrinsic dimensions, so
                the optimizer would only add a remote pattern, a transform per
                image and a bill to 3,180 screenshots served off ISR-cached
                anonymous pages. */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={image.url}
              width={image.width}
              height={image.height}
              alt={image.description}
              loading={segment === firstImageSegment ? "eager" : "lazy"}
              decoding="async"
              className="mx-auto h-auto max-w-full rounded-lg border border-border bg-muted/30"
            />
          </figure>
        );
      })}
    </div>
  );
}
