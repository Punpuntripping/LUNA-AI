/**
 * Streaming-markdown block splitter.
 *
 * Re-parsing the whole accumulated markdown on every reveal frame is O(n²)
 * over a stream. ``splitStreamingContent`` cuts the text at the last "safe"
 * block boundary: everything before it (``prefix``) is a complete markdown
 * document whose string value only changes when a new block finishes — so the
 * memoized renderer skips its re-parse — and only the short ``tail`` is
 * re-parsed as tokens land.
 *
 * A safe boundary is the start of a line that
 *   1. follows a blank line,
 *   2. sits outside a fenced code block (a fence may legally contain blank
 *      lines — splitting inside one would shatter it), and
 *   3. starts with a non-whitespace character (an indented line can be a
 *      list-item continuation or indented code; splitting there changes how
 *      it parses).
 *
 * Splitting a loose list at a blank line between items is deliberately
 * allowed: bullets render identically across the seam, and ordered lists keep
 * their numbering because CommonMark carries the resume number on the first
 * item of each fragment (``MarkdownRenderer`` forwards the ``start`` attr).
 */
export interface StreamingSplit {
  /** Complete leading blocks — safe to parse once and memoize. */
  prefix: string;
  /** The still-growing final block — re-parsed on every reveal frame. */
  tail: string;
}

const FENCE_RE = /^ {0,3}(?:`{3,}|~{3,})/;

export function splitStreamingContent(content: string): StreamingSplit {
  let boundary = 0;
  let inFence = false;
  let prevBlank = false;
  let pos = 0;

  for (const line of content.split("\n")) {
    const blank = line.trim() === "";
    if (!inFence && prevBlank && !blank && !/^[ \t]/.test(line)) {
      boundary = pos;
    }
    if (FENCE_RE.test(line)) {
      inFence = !inFence;
    }
    prevBlank = !inFence && blank;
    pos += line.length + 1; // +1 for the "\n" consumed by split()
  }

  return {
    prefix: content.slice(0, boundary),
    tail: content.slice(boundary),
  };
}
