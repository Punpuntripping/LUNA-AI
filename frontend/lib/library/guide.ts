// The service-guide rendering contract, client-side half.
//
// A service guide (`service_guides.guide_md`) is OUR OWN authored rewrite of the
// issuing entity's official PDF user-guide, with our own screenshot pipeline. It
// is Arabic markdown with HOLES in it: a line whose entire content is a bare
// `{guide_ref}_{n}` token. Every hole has exactly one `service_guide_images` row
// carrying that screenshot's Arabic description and its public URL. Rendering a
// guide = walk the markdown, and every time a line is ONLY a token, swap that
// line for the image + its caption. Nothing else in the markdown changes.
//
// Source of truth for this contract:
// `C:\Programming\agentic_for_ministry\ingestion\service_guides\REFERENCE.md`
// §3–§4 (and the Python reference renderer in §3.2, which this mirrors).
//
// ⚠ TWO ANCHORS LOOK NATURAL AND ARE BOTH WRONG (REFERENCE.md §4):
//
//   1. «الصورة {n}» — 2,804 occurrences of that phrase sit INSIDE ordinary
//      sentences («…كما هو موضح في الصورة 3»), against only 197 standalone
//      caption lines. A renderer anchored there rewrites 2,804 normal sentences
//      into image tags. Only the bare-token LINE is unambiguous by construction.
//   2. POSITION / ORDER — `image_index` is complete `1..N` per guide, but 28% of
//      guides place their holes OUT of numeric order because the rewrite
//      reorders steps. "the 3rd hole in the document" is NOT `image_index = 3`.
//      Resolve strictly by `image_ref`, never by the order segments come out of
//      `splitGuideMarkdown`.
//
// And the rule both traps exist to protect: an UNRESOLVED hole emits NOTHING. A
// raw `223719_1` visible on a user-facing page is the single failure mode this
// whole split-storage design exists to prevent.
//
// Pure functions — no React, no fetching, no DOM. Safe in the server and the
// browser graph alike, and directly testable.

/**
 * A whole line that is ONLY a `{digits}_{digits}` token — THE hole marker.
 *
 * Kept as a source string, not a shared `RegExp`: a `g`-flagged regex carries
 * `lastIndex` between calls, so one shared instance would resume mid-string on
 * the second render of a page and silently drop that guide's first holes.
 * `splitGuideMarkdown` compiles a fresh one per call.
 *
 * Byte-identical to the Python side (`re.compile(r"^[ \t]*(\d+_\d+)[ \t]*$",
 * re.M)`). `guide_md` is stored with newlines normalised to `\n` (REFERENCE.md
 * §8), which is why no `\r` tolerance is needed here — and why adding any input
 * transform would put the two renderers out of step for no gain.
 */
const PLACEHOLDER_SOURCE = String.raw`^[ \t]*(\d+_\d+)[ \t]*$`;

/**
 * One piece of a split guide body, in DOCUMENT ORDER.
 *
 * `image` carries only the `ref` — resolution against the payload's images is
 * the caller's job precisely so it cannot be done positionally (trap 2).
 */
export type GuideSegment =
  | { kind: "text"; value: string }
  | { kind: "image"; ref: string };

/**
 * Split a guide body into ordered text / image-hole segments.
 *
 * Text segments are handed back VERBATIM (minus the hole lines themselves) so a
 * markdown renderer sees exactly the author's markdown. Whitespace-only runs
 * between two adjacent holes are dropped — rendering them would emit empty
 * paragraphs between consecutive screenshots.
 *
 * A hole segment is emitted for EVERY token line, including ones the caller
 * cannot resolve; dropping the unresolvable ones is the renderer's decision
 * (`GuideBody`), which keeps this function a faithful description of the body.
 */
export function splitGuideMarkdown(md: string): GuideSegment[] {
  const source = md ?? "";
  const segments: GuideSegment[] = [];
  const placeholder = new RegExp(PLACEHOLDER_SOURCE, "gm");

  let cursor = 0;
  let match: RegExpExecArray | null;
  while ((match = placeholder.exec(source)) !== null) {
    const text = source.slice(cursor, match.index);
    if (text.trim().length > 0) segments.push({ kind: "text", value: text });
    segments.push({ kind: "image", ref: match[1] });
    // The match covers the token line only (not its newline), and the pattern
    // cannot match empty, so `lastIndex` always advances — no infinite loop.
    cursor = match.index + match[0].length;
  }

  const tail = source.slice(cursor);
  if (tail.trim().length > 0) segments.push({ kind: "text", value: tail });

  return segments;
}

/**
 * The corpus prefix every guide title carries, and the «بالصور» form of it.
 *
 * All 169 titles begin with `GUIDE_PREFIX` (verified live against prod
 * 2026-08-19), so the «بالصور» treatment is a PREFIX REWRITE. Appending would
 * read «الدليل الشامل: إصدار تأشيرة عمل — الدليل الشامل بالصور».
 */
const GUIDE_PREFIX = "الدليل الشامل:";
const GUIDE_PREFIX_IMAGES = "الدليل الشامل بالصور:";

/**
 * The title a reader sees — H1, `<title>`, OG/Twitter, the `/og` card, the
 * JSON-LD headline, and the hub cards. One helper so all of them agree.
 *
 * Two carve-outs, both deliberate:
 *
 *   - `imageCount === 0` keeps «الدليل الشامل:». Ten guides are legitimately
 *     text-only (REFERENCE.md §8), and promising «بالصور» on a guide with no
 *     صور is a lie the reader catches immediately.
 *   - A title that does NOT start with the prefix is returned untouched.
 *     Inventing a prefix for an unknown title shape is worse than leaving it
 *     alone — it would prepend «الدليل الشامل بالصور» to a sentence that never
 *     asked for it.
 */
export function guideDisplayTitle(title: string, imageCount: number): string {
  const trimmed = (title ?? "").trim();
  if (imageCount <= 0 || !trimmed.startsWith(GUIDE_PREFIX)) return trimmed;
  return GUIDE_PREFIX_IMAGES + trimmed.slice(GUIDE_PREFIX.length);
}
