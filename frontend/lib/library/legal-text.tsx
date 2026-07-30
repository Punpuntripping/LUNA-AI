import type { ReactNode } from "react";

// Shared presentational formatter for pre-formatted legal / summary text
// (نص المادة، الملخص، متن التعميم…). Deliberately NOT a full markdown parser:
// it only lifts markdown heading lines («## النطاق») into styled sub-headers and
// renders inline `**bold**` term definitions. Numbered clauses («1- …»),
// line breaks and everything else stay verbatim — matching the intentional
// "plain legal text" contract while killing raw `##`/`**` artifacts.

export type LegalBlock =
  | { type: "heading"; text: string }
  | { type: "para"; text: string };

/** Split text into heading + paragraph blocks (blank lines break paragraphs). */
export function toLegalBlocks(text: string): LegalBlock[] {
  const blocks: LegalBlock[] = [];
  let buffer: string[] = [];
  const flush = (): void => {
    const joined = buffer.join("\n").trim();
    if (joined) blocks.push({ type: "para", text: joined });
    buffer = [];
  };
  for (const line of text.split("\n")) {
    const heading = line.match(/^\s{0,3}#{1,6}\s+(.+?)\s*$/);
    if (heading) {
      flush();
      blocks.push({ type: "heading", text: heading[1] });
    } else if (line.trim() === "") {
      flush();
    } else {
      buffer.push(line);
    }
  }
  flush();
  return blocks;
}

/** Render inline `**bold**` spans; everything else stays verbatim. */
export function renderInline(text: string): ReactNode[] {
  // Split on paired `**bold**` spans: odd indices are the bold content, even
  // indices the surrounding plain text. Server-side gate truncation can cut
  // INSIDE a `**bold**` span, leaving an unmatched `**` behind. Any `**` still
  // present in a plain (even-index) segment is therefore an UNMATCHED marker —
  // strip it instead of printing it literally («7- **المناطق» → «7- المناطق»).
  return text.split(/\*\*(.+?)\*\*/g).map((part, index) =>
    index % 2 === 1 ? (
      <strong key={index} className="font-bold text-foreground">
        {part}
      </strong>
    ) : (
      <span key={index}>{part.replace(/\*\*/g, "")}</span>
    ),
  );
}

/**
 * Normalize a heading for duplicate comparison: strip leading markdown `#`s,
 * collapse all whitespace, normalize spacing AROUND colons (so
 * «الباب الأول : الفصل» == «الباب الأول: الفصل»), and ignore trailing colon(s).
 * Handles both the ASCII «:» and the full-width «：».
 */
export function normalizeHeadingText(text: string): string {
  return (text || "")
    .replace(/^\s*#{1,6}\s*/, "") // leading markdown hashes
    .replace(/\s+/g, " ") // collapse whitespace
    .replace(/\s*([:：])\s*/g, "$1") // normalize spacing around colons
    .trim()
    .replace(/[:：]+$/, "") // drop trailing colon(s)
    .trim();
}

/**
 * Drop the FIRST block when it is a heading that duplicates `dedupeHeading`
 * (compared via {@link normalizeHeadingText}). Kills the common case where a
 * styled section `<h2>` wraps a formatted body whose first line repeats that
 * exact heading. Returns a shortened copy on a match; a no-op (same array)
 * otherwise or when `dedupeHeading` is empty.
 */
export function dropDuplicateLeadingHeading(
  blocks: LegalBlock[],
  dedupeHeading?: string,
): LegalBlock[] {
  if (!dedupeHeading || blocks.length === 0) return blocks;
  const first = blocks[0];
  if (
    first.type === "heading" &&
    normalizeHeadingText(first.text) === normalizeHeadingText(dedupeHeading)
  ) {
    return blocks.slice(1);
  }
  return blocks;
}
