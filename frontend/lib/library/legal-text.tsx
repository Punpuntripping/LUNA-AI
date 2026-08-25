import type { ReactNode } from "react";

// Shared presentational formatter for pre-formatted legal / summary text
// (نص المادة، الملخص، متن التعميم…). Deliberately NOT a full markdown parser:
// it lifts the STRUCTURE a statute already carries — chapter lines (الباب /
// الفصل), clause numbers («٣/٧٦», «المادة الخامسة»), lettered / numbered
// sub-clauses («أ- …», «1- …») and repealed stubs («ملغاة») — into typed
// blocks, and renders inline `**bold**` term definitions. Everything else stays
// verbatim, matching the intentional "plain legal text" contract while killing
// raw `##`/`**` artifacts.
//
// Why structure matters: the corpus stores a لائحة as blank-line-separated
// fragments — the clause number on its own line, each sub-clause its own
// paragraph. Rendered as uniform paragraphs with uniform gaps, a clause
// number floats as an isolated line and the sub-clauses scatter. Typed blocks
// let the renderer attach the number to its clause and tighten the list.

export type LegalBlock =
  /** Chapter / section title. `level` 1 = باب, 2 = فصل / `##`, 3 = deeper. */
  | { type: "heading"; text: string; level: 1 | 2 | 3 }
  /** A clause number standing alone («٣/٧٦», «المادة الخامسة»). */
  | { type: "label"; text: string }
  /** Consecutive sub-clauses («أ- …», «1- …»). `marker` is the verbatim prefix. */
  | { type: "list"; items: { marker: string; text: string }[] }
  /** A repealed stub («ملغاة»). */
  | { type: "repealed"; text: string }
  /**
   * A table swapped back in for its `TBL_…` placeholder line. `html` is
   * sanitized server-side and rendered as-is by `<ChunkTable>`; `ref` is the
   * token it resolved from, kept for debugging and for keys.
   */
  | { type: "table"; ref: string; html: string }
  | { type: "para"; text: string };

/**
 * Sanitized tables keyed by the `TBL_…` token that stands in for each one
 * inside the body text. The wire shape carries `md` too (the prose the table
 * replaced — the copy string and the gate weight); the lifter only ever reads
 * `html`, so it asks for the least it needs and the richer wire type is
 * assignable to it.
 */
export type LegalTableMap = Record<string, { html: string }>;

/**
 * A whole line that is nothing but a `TBL_…` token — the placeholder the corpus
 * writes where a table was lifted out of the prose. Whole line, nothing else on
 * it. `[A-Za-z0-9_]+` and not something looser is what makes BOTH token shapes
 * match (plain, and hash-suffixed when sanitizing the chunk ref changed it) —
 * do not relax it.
 */
const TABLE_PLACEHOLDER = /^[ \t]*(TBL_[A-Za-z0-9_]+)[ \t]*$/;

const ARABIC_ORDINALS = new Set([
  "الأولى", "الاولى", "الثانية", "الثالثة", "الرابعة", "الخامسة", "السادسة",
  "السابعة", "الثامنة", "التاسعة", "العاشرة", "الحادية", "الحاديه", "عشرة",
  "عشر", "العشرون", "الثلاثون", "الأربعون", "الاربعون", "الخمسون", "الستون",
  "السبعون", "الثمانون", "التسعون", "المائة", "المئة", "المائتان", "المئتان",
  "الثلاثمائة", "الأربعمائة", "بعد", "مكرر", "مكررا", "مكررًا", "مكرراً",
  "الأخيرة", "الاخيرة", "التمهيدية",
]);

const CHAPTER_WORDS = /^(الباب|الفصل|القسم|الفرع|الجزء|الكتاب)\s+(\S+)/;
const CHAPTER_ORDINAL =
  /^(ال(?:أول|اول|ثاني|ثالث|رابع|خامس|سادس|سابع|ثامن|تاسع|عاشر|حادي|عشرون|ثلاثون|أربعون|اربعون|خمسون|تمهيدي|أخير|اخير)|[0-9٠-٩]+)[:：]?$/;

/** Numeric clause label: «٣/٧٦», «76/3», «3». */
const NUMERIC_LABEL = /^[0-9٠-٩]+(?:\s*\/\s*[0-9٠-٩]+)?[:：]?$/;
/** «المادة 5» / «مادة (5)» / «المادة الخامسة عشرة:» / «المادة 5 مكرر». */
const MADDA_LABEL =
  /^(?:\(?\s*)?(?:ال)?مادة\s*(?::|：)?\s*(.+?)\s*[:：]?\s*\)?$/;

const SUBCLAUSE_MARKER =
  /^(\(?\s*(?:[0-9٠-٩]{1,3}|[أ-يء]{1,2})\s*[)\-–—.:]|[-–—•▪])\s+(?=\S)/;

const REPEALED = /^[(«"']?\s*(ملغاة|ملغى|ملغية|ملغي|ألغيت|الغيت)\s*[)»"']?\s*[.]?$/;

function stripHashes(line: string): { level: 1 | 2 | 3; text: string } | null {
  const heading = line.match(/^\s{0,3}(#{1,6})\s+(.+?)\s*$/);
  if (!heading) return null;
  const hashes = heading[1].length;
  return { level: hashes <= 1 ? 1 : hashes === 2 ? 2 : 3, text: heading[2] };
}

/** Is this trimmed single line a clause number standing alone? */
export function isClauseLabel(text: string): boolean {
  const t = text.trim();
  if (!t || t.length > 60) return false;
  if (NUMERIC_LABEL.test(t)) return true;
  const madda = t.match(MADDA_LABEL);
  if (!madda) return false;
  const rest = madda[1].trim();
  if (/^\(?\s*[0-9٠-٩]+\s*\)?(?:\s*مكرر[اًا]*)?$/.test(rest)) return true;
  // Spelled-out ordinal: every word must be an ordinal token («الخامسة عشرة
  // بعد المائة»); a sentence («المادة الأولى من النظام») fails on «من».
  const words = rest.split(/\s+/);
  return (
    words.length > 0 &&
    words.length <= 6 &&
    words.every((w) => ARABIC_ORDINALS.has(w.replace(/^و/, "")))
  );
}

/** Bare chapter line («الباب الخامس», «الفصل الأول الإعسار») → heading level. */
function bareChapterLevel(text: string): 1 | 2 | 3 | null {
  const t = text.trim();
  if (t.length > 90 || /[.؛;]$/.test(t)) return null;
  const m = t.match(CHAPTER_WORDS);
  if (!m || !CHAPTER_ORDINAL.test(m[2])) return null;
  if (m[1] === "الكتاب" || m[1] === "الباب" || m[1] === "الجزء") return 1;
  if (m[1] === "الفصل") return 2;
  return 3;
}

/**
 * Split text into typed blocks. Blank lines break paragraphs; `#` lines and
 * bare chapter lines become headings; a lone clause number becomes a label;
 * runs of «أ- …» / «1- …» lines become one list (consecutive lists merge, so a
 * corpus that blank-line-separates every sub-clause still yields ONE list).
 *
 * `tables` (optional) turns each whole-line `TBL_…` placeholder into a `table`
 * block. Two rules, both load-bearing:
 *
 *   - AN UNRESOLVED TOKEN EMITS NOTHING. A token whose ref is not in `tables`
 *     (or whose entry carries empty markup, which is what the server's
 *     sanitizer returns when nothing survived the allowlist) is DROPPED, not
 *     printed. The server must never send an unresolvable token — but `text`
 *     and `tables` are baked together into a 24h ISR payload, and if that pair
 *     ever arrives half-formed, a raw `TBL_17261_reg_501_chunk_003_1` on a
 *     statute page is the exact failure this feature exists to prevent.
 *   - A token line FLUSHES the paragraph buffer, exactly like a blank line, so
 *     the prose above a table never absorbs the table's position.
 *
 * WITHOUT `tables` the placeholder branch does not run at all, and the result
 * is byte-identical to the pre-tables behaviour for every input — which is what
 * lets every other caller (circulars, forms, judgments, guides, summaries) keep
 * calling this with one argument and change nothing.
 */
export function toLegalBlocks(
  text: string,
  tables?: LegalTableMap,
): LegalBlock[] {
  const blocks: LegalBlock[] = [];
  let buffer: string[] = [];

  const pushList = (items: { marker: string; text: string }[]): void => {
    const last = blocks[blocks.length - 1];
    if (last && last.type === "list") last.items.push(...items);
    else blocks.push({ type: "list", items });
  };

  const flush = (): void => {
    const lines = buffer;
    buffer = [];
    const joined = lines.join("\n").trim();
    if (!joined) return;

    if (lines.length === 1 || !joined.includes("\n")) {
      if (REPEALED.test(joined)) {
        blocks.push({ type: "repealed", text: joined });
        return;
      }
      if (isClauseLabel(joined)) {
        blocks.push({ type: "label", text: joined });
        return;
      }
      const level = bareChapterLevel(joined);
      if (level) {
        blocks.push({ type: "heading", text: joined, level });
        return;
      }
    }

    // Sub-clause lines inside the paragraph: each marker line opens an item and
    // unmarked lines continue the open item. Unmarked lines BEFORE the first
    // marker are the clause's intro («…ما يأتي:») and stay a paragraph of
    // their own, so «intro\n1- …\n2- …» renders as intro + list.
    const items: { marker: string; text: string }[] = [];
    const intro: string[] = [];
    for (const raw of joined.split("\n")) {
      const line = raw.trim();
      const m = line.match(SUBCLAUSE_MARKER);
      if (m) {
        items.push({ marker: m[1].trim(), text: line.slice(m[0].length) });
      } else if (items.length > 0) {
        items[items.length - 1].text += `\n${line}`;
      } else {
        intro.push(line);
      }
    }
    if (intro.length > 0) blocks.push({ type: "para", text: intro.join("\n") });
    if (items.length > 0) pushList(items);
  };

  for (const line of text.split("\n")) {
    const heading = stripHashes(line);
    if (heading) {
      flush();
      // «## ٤/٧٤» / «## المادة 5»: a heading whose text is a clause number is a
      // clause label, not a chapter — the corpus marks both the same way.
      if (isClauseLabel(heading.text)) {
        blocks.push({ type: "label", text: heading.text });
      } else {
        blocks.push({ type: "heading", text: heading.text, level: heading.level });
      }
      continue;
    }

    const placeholder = tables ? line.match(TABLE_PLACEHOLDER) : null;
    if (placeholder) {
      flush();
      const ref = placeholder[1];
      const html = tables?.[ref]?.html;
      // Resolved ⇒ the grid. Unresolved (or empty markup) ⇒ nothing at all.
      if (html) blocks.push({ type: "table", ref, html });
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
 * Drop the FIRST block when it is a heading OR clause label that duplicates
 * `dedupeHeading` (compared via {@link normalizeHeadingText}). Kills the common
 * case where a styled section `<h2>` wraps a formatted body whose first line
 * repeats that exact heading («## المادة 5» under a «المادة 5» section).
 * Returns a shortened copy on a match; a no-op (same array) otherwise or when
 * `dedupeHeading` is empty.
 */
export function dropDuplicateLeadingHeading(
  blocks: LegalBlock[],
  dedupeHeading?: string,
): LegalBlock[] {
  if (!dedupeHeading || blocks.length === 0) return blocks;
  const first = blocks[0];
  if (
    (first.type === "heading" || first.type === "label") &&
    normalizeHeadingText(first.text) === normalizeHeadingText(dedupeHeading)
  ) {
    return blocks.slice(1);
  }
  return blocks;
}
