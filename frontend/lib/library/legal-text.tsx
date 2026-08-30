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
  /**
   * A figure swapped back in for its `IMG_{n}` placeholder line. `ref` is the
   * token it resolved from (the key and the debug handle); everything else is
   * what `<ChunkFigure>` renders. `n` is the RENDER-ORDER number the caption
   * prints — minted upstream, never `chunk_images.meta->>'n'` (see ChunkFigure).
   */
  | {
      type: "image";
      ref: string;
      n: number;
      title: string;
      description: string;
      url: string;
      width: number;
      height: number;
    }
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
 * Rendered figures keyed by the `IMG_{n}` token that stands in for each one
 * inside the body text. Same discipline as {@link LegalTableMap}: the lifter
 * asks for the least it needs, so the richer wire type (which also carries
 * `image_ref`) stays assignable to it.
 */
export type LegalImageMap = Record<
  string,
  {
    n: number;
    title: string;
    description: string;
    url: string;
    width: number;
    height: number;
  }
>;

/**
 * A whole line that is nothing but a `TBL_…` token — the placeholder the corpus
 * writes where a table was lifted out of the prose. Whole line, nothing else on
 * it. `[A-Za-z0-9_]+` and not something looser is what makes BOTH token shapes
 * match (plain, and hash-suffixed when sanitizing the chunk ref changed it) —
 * do not relax it.
 */
const TABLE_PLACEHOLDER = /^[ \t]*(TBL_[A-Za-z0-9_]+)[ \t]*$/;

/**
 * A whole line that is nothing but an `IMG_{n}` token — OUR placeholder, minted
 * by the server for each figure it resolved, never anything the corpus wrote.
 *
 * ⚠ `IMG_\d+`, and deliberately NOT the `[A-Za-z0-9_]+` shape `TBL_` uses. The
 * token is not built from `image_ref`: four regulations carry ARABIC in their
 * ref (`17645_reg_الانظمة_002_chunk_001`), so a ref-derived token could not use
 * an ASCII anchor at all. `IMG_{n}` is ASCII by construction, is unique inside
 * the payload the server just built, and IS the caption number — so the token
 * and the label can never disagree. Verified corpus-wide: 0 chunks contain a
 * whole-line `IMG_\d+` (8 contain one inline, which this anchor never matches).
 */
const IMAGE_PLACEHOLDER = /^[ \t]*(IMG_\d+)[ \t]*$/;

/**
 * The CORPUS's own image markup — `![img-1.jpeg](images/page_005_img_001.jpeg)`
 * — matched ANYWHERE on a line, not just on one of its own.
 *
 * This is a DIFFERENT regex from {@link IMAGE_PLACEHOLDER} doing a different
 * job, and the two must never be merged: that one is the stand-in we project
 * onto the wire, this one is the raw markup we are deleting. 3,630 of the 3,677
 * live spans are whole-line, but 47 sit INSIDE a prose sentence, so a whole-line
 * rule would silently drop those 47 sentences' worth of context and leave the
 * prose looking finished.
 *
 * Global — used ONLY with `String.replace`, which resets `lastIndex` itself.
 * Never call `.test()` on it.
 */
const IMAGE_SPAN = /!\[[^\]]*\]\(images\/[^)]+\)/g;

/**
 * Delete every raw corpus image span from one line, leaving the sentence around
 * it intact.
 *
 * ⚠ THIS RUNS UNCONDITIONALLY — it is not gated on an image map, and it must
 * never become gated. Read {@link toLegalBlocks}'s docstring for why.
 *
 * The `includes` guard is not just speed: a line with no span is returned
 * BYTE-IDENTICAL, so every non-regulation caller (circulars, forms, judgments,
 * guides, summaries) is provably unchanged by this feature. Only a line that
 * actually lost a span gets its whitespace tidied — collapsing the hole the span
 * left («النص ![x](images/a.jpeg) يكمل» → «النص يكمل», not «النص  يكمل») and
 * dropping what is left over on a line that was nothing BUT a span, so the
 * caller can flush it exactly like a blank line.
 */
function stripImageSpans(line: string): string {
  if (!line.includes("](images/")) return line;
  return line
    .replace(IMAGE_SPAN, "")
    .replace(/[ \t]{2,}/g, " ")
    .replace(/[ \t]+$/, "");
}

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
 *
 * `images` (optional) turns each whole-line `IMG_{n}` placeholder into an
 * `image` block, under the same "an unresolved token emits NOTHING" rule. Here
 * that rule is not merely defensive: 656 chunks carry image markup with no row
 * behind it at all — the figure was judged decorative, or sat in front matter,
 * or could not be attached — so 298 spans on published pages point at an image
 * that does not exist and never will. Deleting them IS the fix, not a fallback.
 *
 * ⚠⚠ TWO THINGS ARE MATCHED UNCONDITIONALLY HERE, GATED ON NOTHING, and this is
 * the load-bearing part of the whole feature:
 *
 *   1. a whole-line `IMG_{n}` token — dropped when it does not resolve, exactly
 *      like `TBL_`; and
 *   2. **a raw `![…](images/…)` span, anywhere on a line — ALWAYS removed**,
 *      map or no map, caller or no caller.
 *
 * Rule 2 is why this ships BEFORE any backend change and fixes something on its
 * own: 168 published أنظمة print 1,956 of those spans as literal body text
 * TODAY, plus 52 `seo_articles` rows in their `article_text`. The moment this
 * deploys they stop, everywhere, including on every 24h ISR payload baked before
 * the wire grew an `images` key and on every caller that never passes one.
 *
 * ⚠ DO NOT "SIMPLIFY" EITHER MATCH BY GATING IT ON `images`. That is the exact
 * mistake that once shipped `TBL_…` reference ids to readers: `FullContentGate`
 * rendered the paid reveal without passing the table map, every token fell
 * through to the paragraph buffer, and a نظام that is mostly tables displayed
 * almost nothing but its own ids. Gating rule 2 is strictly worse than that —
 * it would not degrade a forgetful caller to a MISSING figure, it would restore
 * today's bug and print `page_005_img_001.jpeg` inside a statute.
 *
 * The one thing a caller can still get wrong is passing `text` without its
 * `images`: the figures vanish. That is why `FullSection`/`FullArticle` carry
 * the map too — a reader who PAID for the reveal must never see fewer figures
 * than the anonymous preview of the same page.
 */
export function toLegalBlocks(
  text: string,
  tables?: LegalTableMap,
  images?: LegalImageMap,
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

  for (const rawLine of text.split("\n")) {
    // ⚠ FIRST, AND UNCONDITIONALLY. Raw corpus image markup is deleted from
    // EVERY line before anything else looks at it — see the docstring's rule 2.
    // A line that was nothing but a span now trims to empty and flushes like a
    // blank line; an inline span leaves its sentence behind and that sentence
    // goes on to be parsed exactly as before. A line with no span is untouched,
    // byte for byte.
    const line = stripImageSpans(rawLine);

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

    // ⚠ MATCHED UNCONDITIONALLY — do NOT gate this on `tables`. A whole-line
    // `TBL_…` is a placeholder, never legal text, so it must vanish whether or
    // not a map was supplied. Gating it on `tables` is what shipped reference
    // ids to readers: `FullContentGate` rendered the reveal without passing the
    // map, every token fell through to the paragraph buffer, and a نظام that is
    // mostly tables displayed almost nothing but its own ids. Dropping instead
    // degrades a forgetful caller to a MISSING table — bad, but not a leak.
    const placeholder = line.match(TABLE_PLACEHOLDER);
    // ⚠ MATCHED UNCONDITIONALLY TOO, and for the same reason — do NOT gate this
    // on `images`. A whole-line `IMG_{n}` is a placeholder, never legal text, so
    // it must vanish whether or not a map was supplied. The failure this
    // prevents is a bare `IMG_3` on a statute page, which is what a payload
    // whose `text` came from the new projector but whose `images` went missing
    // (a half-formed 24h ISR bake) would otherwise render.
    const figure = line.match(IMAGE_PLACEHOLDER);
    if (placeholder) {
      flush();
      const ref = placeholder[1];
      const html = tables?.[ref]?.html;
      // Resolved ⇒ the grid. Unresolved, empty markup, or no map at all ⇒
      // nothing at all (the corpus contract's rule 2).
      if (html) blocks.push({ type: "table", ref, html });
    } else if (figure) {
      flush();
      const ref = figure[1];
      const image = images?.[ref];
      // Resolved ⇒ the figure. Unresolved, no bytes behind it (`url` empty is
      // how an `uploaded_at IS NULL` row would arrive), or no map at all ⇒
      // nothing at all. A URL for absent bytes is a broken-image icon inside a
      // statute, which is the exact thing this feature exists to stop.
      if (image?.url) {
        blocks.push({
          type: "image",
          ref,
          n: image.n,
          title: image.title,
          description: image.description,
          url: image.url,
          width: image.width,
          height: image.height,
        });
      }
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
