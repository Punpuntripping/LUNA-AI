import { cn } from "@/lib/utils";

/**
 * One figure lifted back out of a corpus chunk — the diagram a نظام printed,
 * where the reading surface has been printing its FILENAME.
 *
 * A regulation chunk is Arabic markdown, and 1,839 chunks carry image markup
 * pointing at a file no app can reach:
 *
 *     ![img-1.jpeg](images/page_005_img_001.jpeg)
 *
 * The library body runs the `plain` path (`toLegalBlocks` → `LegalBlocks`),
 * which parses no markdown, so that line fell into the paragraph buffer and was
 * printed VERBATIM on 168 published أنظمة. The bytes were always there —
 * `chunk_images` (5,347 rows) plus a public `regulation-images` bucket. This
 * component is the pixels; `toLegalBlocks` decides where they land.
 *
 * Five things it has to get right, four of them already paid for by
 * `GuideBody.tsx` (the same problem, solved once for 3,180 guide screenshots):
 *
 * 1. `width`/`height` ARE REQUIRED, NOT DECORATIVE. They reserve the box before
 *    the bytes land. One chunk carries 31 figures and the widest object in the
 *    corpus is 12,250px — without intrinsic dimensions the section reflows once
 *    per image, and a reader loses their place mid-statute.
 * 2. A PLAIN `<img>`, ON PURPOSE — not `next/image`. The bucket is public and
 *    the payload already carries the dimensions, so the optimizer would only add
 *    a remote pattern, a transform per image and a bill against 5,347 figures
 *    served off ISR-cached ANONYMOUS pages. `GuideBody` made this call for 3,180
 *    screenshots; it holds here for more.
 * 3. `alt` IS THE `description`, NEVER THE FILENAME. It is what a screen reader
 *    and a crawler get, and this wing is published for SEO. `description` is
 *    98–2,008 chars of machine-facing Arabic analysis — right for `alt`, wrong
 *    for the page (see 4).
 * 4. A `<figcaption>`, UNLIKE `GuideBody` — and the difference is `title`.
 *    `service_guide_images` had no short label, so printing the 400-char
 *    description under every screenshot read as a wall of generated prose and
 *    the caption was dropped. `chunk_images.title` is 4–77 chars (mean 31).
 *    That IS a caption. `transcribed_text` is NOT printed to the reader in v1 —
 *    it is a gate-exposure decision (up to 4,854 chars of a photographed
 *    penalty schedule as selectable text), not a rendering one.
 * 5. «الصورة {n}» IS A RENDER-ORDER COUNTER, minted upstream, and its digits are
 *    LATIN. The number is app chrome, so `project_latin_numerals_policy` applies
 *    to it. It is NEVER `meta->>'n'` (120 of 418 regulations have gaps in that
 *    index — worst case a jump of 383, so a reader would conclude 401 figures
 *    are missing) and never `n_in_chunk` (it restarts every chunk). The `title`
 *    beside it is CORPUS TEXT: whatever digits the statute printed are what
 *    render, which is the same carve-out `ChunkTable` documents.
 *
 * `loading`: everything is `lazy` except the one figure a page opens on — the
 * heaviest single object in the corpus is 4.67 MB, and lazying the whole page is
 * what makes that survivable. Callers set `eager` for the first figure only.
 *
 * Server component: props in, JSX out. Rendered by BOTH the library body
 * (`LegalBlocks`) and the مراجع popup (`ReferencePanel`), so the two surfaces
 * cannot drift.
 */
export function ChunkFigure({
  url,
  width,
  height,
  title,
  description,
  n,
  eager,
  className,
}: ChunkFigureProps) {
  return (
    <figure className={cn("my-5", className)} dir="rtl">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={url}
        // ⚠ OMITTED, never emitted as 0. `shared/library/chunk_images.py`
        // defaults a missing `meta.width`/`meta.height` to 0 rather than drop
        // the figure over it, and `width="0"` collapses the image to nothing —
        // a worse outcome than the reflow the dimensions exist to prevent.
        // Unreachable on today's corpus (0 of 5,347 rows; min 56×38) and
        // guarded anyway, because the fallback has to fail toward showing the
        // figure.
        width={width > 0 ? width : undefined}
        height={height > 0 ? height : undefined}
        alt={description}
        loading={eager ? "eager" : "lazy"}
        decoding="async"
        className="mx-auto h-auto max-w-full rounded-lg border border-border bg-muted/30"
      />
      <figcaption className="mt-2 text-sm text-text-secondary">
        الصورة {n}: {title}
      </figcaption>
    </figure>
  );
}

interface ChunkFigureProps {
  /**
   * Public Storage URL, built SERVER-SIDE from `storage_path` — never from
   * `image_ref + ".jpeg"`, because 575 of 5,347 objects are PNG.
   */
  url: string;
  /** Intrinsic width in px. Required — see rule 1 in the docstring. */
  width: number;
  /** Intrinsic height in px. Required — see rule 1 in the docstring. */
  height: number;
  /** The caption text (`chunk_images.title`, 4–77 chars). Rendered verbatim. */
  title: string;
  /** The `alt` text (`chunk_images.description`). Never the filename. */
  description: string;
  /**
   * The render-order number the caption prints — «الصورة {n}». Minted by the
   * renderer that placed this figure (document-wide on `/regulations/{slug}`,
   * page-wide on a مادة page, chunk-wide in مراجع), never read off the corpus.
   */
  n: number;
  /**
   * Load this one eagerly. Set for the FIRST figure a page shows and nothing
   * else: the rest sit below the fold and the corpus holds a 4.67 MB object.
   */
  eager?: boolean;
  /** Spacing override for a surface with its own rhythm (the مراجع popup). */
  className?: string;
}
