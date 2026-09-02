// ==========================================
// Markdown heading utilities (مدونة TOC)
// ==========================================
// Shared between the table-of-contents builders (the library `TocList` /
// `TocRail` / `TocFloating` trio, which `BlogArticleView` projects these
// headings into) and the MarkdownRenderer's opt-in ``headingAnchors`` mode.
// Both surfaces MUST derive the same anchor id for a given heading, so the slug
// is a DETERMINISTIC, pure function of the heading text — no randomness, no
// document-order suffixing. Two different headings may collide on the same
// slug; that's an accepted trade-off in exchange for TOC links always matching
// rendered ids.

/** One heading extracted from a markdown document for the TOC. */
export interface TocHeading {
  /** Heading level, 1..6 (``#`` → 1, ``######`` → 6). */
  depth: number;
  /** Display text — citation markers + inline emphasis/code/link syntax stripped. */
  text: string;
  /** Anchor id; matches ``slugifyHeading(text)`` used at render time. */
  slug: string;
}

/**
 * Deterministic, Arabic-safe HTML id for a heading.
 *
 * Lowercases, trims, collapses whitespace runs into single hyphens, and strips
 * characters that aren't letters / numbers / whitespace / hyphen. Arabic (and
 * any other non-ASCII) letters are KEPT — only punctuation and symbols are
 * removed. No randomness is added, so the same text always yields the same id
 * (required: the TOC builder and the renderer must agree).
 */
export function slugifyHeading(text: string): string {
  return text
    .trim()
    .toLowerCase()
    // Drop everything that isn't a Unicode letter, number, whitespace or hyphen.
    // ``\p{L}`` keeps Arabic letters; ``\p{N}`` keeps digits. Requires the ``u`` flag.
    .replace(/[^\p{L}\p{N}\s-]/gu, "")
    // Whitespace runs → single hyphen.
    .replace(/\s+/g, "-")
    // Collapse repeated hyphens.
    .replace(/-+/g, "-")
    // Trim leading / trailing hyphens.
    .replace(/^-+|-+$/g, "");
}

/**
 * Strip inline citation markers (``[3]`` / ``[3,4]``), basic markdown emphasis
 * (``**``/``__``/``*``/``_``), inline code backticks, and link syntax
 * (``[text](url)`` → ``text``) from a raw heading line, leaving clean display
 * text. Whitespace is collapsed and trimmed.
 */
function cleanHeadingText(raw: string): string {
  return raw
    // Link syntax → link text (before citation strip so ``[x](y)`` isn't mangled).
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    // Citation markers: ``[3]``, ``[3,4]``, ``[3, 4, 5]``.
    .replace(/\[\s*\d+(?:\s*,\s*\d+)*\s*\]/g, "")
    // Bold (``**text**`` / ``__text__``).
    .replace(/(\*\*|__)(.*?)\1/g, "$2")
    // Italic (``*text*`` / ``_text_``).
    .replace(/(\*|_)(.*?)\1/g, "$2")
    // Inline code.
    .replace(/`([^`]+)`/g, "$1")
    // Collapse whitespace.
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Scan a markdown document for ATX headings (``# …`` through ``###### …``) and
 * return them in document order. Headings inside fenced code blocks
 * (```` ``` ```` or ``~~~``) are skipped so commented-out ``#`` lines and shell
 * prompts don't pollute the TOC.
 */
export function extractHeadings(markdown: string): TocHeading[] {
  const lines = markdown.split(/\r?\n/);
  const headings: TocHeading[] = [];

  let inFence = false;
  let fenceChar = "";

  for (const line of lines) {
    // Fence toggle: a line that starts (after optional indent) with >=3
    // backticks or tildes. Only a matching marker char closes an open fence.
    const fence = /^\s*(`{3,}|~{3,})/.exec(line);
    if (fence) {
      const marker = fence[1][0];
      if (!inFence) {
        inFence = true;
        fenceChar = marker;
      } else if (marker === fenceChar) {
        inFence = false;
        fenceChar = "";
      }
      continue;
    }
    if (inFence) continue;

    // ATX heading: 1–6 ``#``, a required space, the text, optional trailing
    // ``#`` run. Non-greedy text so the closing hashes are trimmed.
    const m = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
    if (!m) continue;

    const text = cleanHeadingText(m[2]);
    if (!text) continue;

    headings.push({
      depth: m[1].length,
      text,
      slug: slugifyHeading(text),
    });
  }

  return headings;
}
