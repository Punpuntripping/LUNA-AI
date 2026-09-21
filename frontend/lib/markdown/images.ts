// ==========================================
// Markdown image utilities (نسخ المقال)
// ==========================================
// A published blog body carries its marketing cards inline — the cover, the
// article-card illustrations, the «أبرز النقاط» panels — as plain markdown
// images pointing at the public ``blog-cards`` bucket. On the page they ARE the
// article; in the clipboard they are noise: a reader who pastes the post into a
// memo or a case file gets a wall of Supabase URLs between the paragraphs.
// So the copy path strips them, and only the copy path — the rendered surface
// is untouched.

/**
 * Inline markdown image: ``![alt](url)``, optional ``"title"`` included.
 *
 * ``[^)]*`` for the target is deliberate — a parenthesis inside an image URL
 * would have to be percent-encoded to survive a markdown parser anyway, so a
 * bare ``)`` always closes the image here exactly as it does at render time.
 * The leading ``!`` is what separates an image from a link: a ``[نص](url)``
 * link is NOT touched.
 */
const MARKDOWN_IMAGE = /!\[[^\]]*\]\([^)]*\)/g;

/**
 * Remove every markdown image from a document, leaving the prose intact.
 *
 * A line that held nothing but images disappears entirely (rather than leaving
 * a blank behind), and the blank-line runs that produces are collapsed to a
 * single separator so the pasted text keeps its paragraph rhythm. An image sat
 * mid-sentence is dropped in place, the sentence survives.
 *
 * Fenced code blocks are skipped whole: an ``![…](…)`` inside a fence is sample
 * code a reader is copying ON PURPOSE. Same fence tracking as
 * ``extractHeadings``.
 *
 * A document with no images is returned byte-for-byte unchanged — no blank-line
 * normalisation is applied to bodies this function has nothing to do with.
 */
export function stripMarkdownImages(markdown: string): string {
  if (!markdown.includes("![")) return markdown;

  const lines = markdown.split(/\r?\n/);
  const out: string[] = [];

  let inFence = false;
  let fenceChar = "";
  let dropped = false;

  // Blank-line collapse, applied OUTSIDE fences only: the gaps a removed card
  // leaves behind are indistinguishable from the ones the author typed, so the
  // whole prose stream gets one blank line between blocks. Inside a fence every
  // line is pushed verbatim.
  const push = (line: string) => {
    if (line.trim().length === 0 && out[out.length - 1]?.trim().length === 0) {
      return;
    }
    out.push(line);
  };

  for (const line of lines) {
    // Fence toggle: >=3 backticks or tildes after optional indent. Only a
    // matching marker char closes an open fence.
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
      out.push(line);
      continue;
    }
    if (inFence) {
      out.push(line);
      continue;
    }
    if (!line.includes("![")) {
      push(line);
      continue;
    }

    const stripped = line.replace(MARKDOWN_IMAGE, "");
    dropped = true;
    // Image-only line → drop the line itself, not just its content.
    if (stripped.trim().length === 0) continue;
    // Mixed line: close the gap the image left («خلاصة  داخل الجملة»), without
    // touching the INDENT — a nested list item keeps its level.
    const indent = /^[^\S\n]*/.exec(stripped)![0];
    push(
      indent +
        stripped
          .slice(indent.length)
          .replace(/[^\S\n]{2,}/g, " ")
          .trimEnd(),
    );
  }

  if (!dropped) return markdown;

  return out.join("\n").trim();
}
