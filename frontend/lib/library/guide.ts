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

import { normalizeHeadingText } from "@/lib/library/legal-text";
import { extractHeadings, type TocHeading } from "@/lib/markdown/headings";

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

/**
 * A readable label for a URL that a guide body printed as its own link text.
 *
 * ⚠ WHY THIS EXISTS. 155 of 169 guide bodies carry a `**الرابط الرسمي:**
 * [<url>](<url>)` line, and 13 of those URLs are percent-encoded Arabic. Rendered
 * literally, the label is a 200-character `%D8%A5%D8%B5%D8%AF%D8%A7%D8%B1…`
 * wall that wraps across five lines and is unreadable in either script. The
 * `href` is fine — it is only the visible TEXT that is wrong.
 *
 * Returns the DECODED last path segment (hyphens → spaces, so an Arabic slug
 * reads as the sentence it is), falling back to the host when there is no
 * meaningful segment. Host-plus-path was rejected: mixing a Latin host and an
 * Arabic path in one RTL line puts the punctuation on the wrong side.
 *
 * Never throws — a malformed URL or a bad percent-escape returns the input
 * untouched, because a slightly ugly link beats a page that fails to render.
 */
export function prettyUrlLabel(url: string): string {
  const raw = (url ?? "").trim();
  if (!raw) return raw;

  try {
    const parsed = new URL(raw);
    const segments = parsed.pathname.split("/").filter(Boolean);
    const last = segments.length ? segments[segments.length - 1] : "";

    // `decodeURIComponent` throws on a malformed escape ("%D8%") — that is the
    // exact input this helper exists for, so it must not take the page down.
    let label = "";
    if (last) {
      try {
        label = decodeURIComponent(last);
      } catch {
        label = last;
      }
      label = label.replace(/[-_]+/g, " ").trim();
    }

    if (!label) return parsed.host;
    // A decoded segment can still be a long file name; keep it to one line.
    return label.length > 80 ? `${label.slice(0, 79)}…` : label;
  } catch {
    return raw;
  }
}

const MD_LINK_WITH_URL_LABEL = /\[\s*(https?:\/\/[^\]\s]+)\s*\]\(([^)\s]+)\)/g;
// A bare URL is one that is NOT already a markdown href. The lookbehind targets
// `](` exactly — anything looser also skips a URL sitting in ordinary
// parentheses, «(https://…)», which is prose and SHOULD be prettified.
const BARE_URL = /(?<!\]\()\bhttps?:\/\/[^\s<>()[\]]+/g;
// Sentence punctuation that a bare URL at the end of a sentence swallows.
// «زر https://a.gov.sa/foo.» must link to `/foo`, not to `/foo.` — absorbing the
// full stop into the href is a 404 the reader gets blamed for.
const TRAILING_PUNCT = /[.,;:!?،؛»"')\]]+$/;

/**
 * Rewrite every URL a guide body would render as raw link text into a readable
 * label, keeping the destination untouched.
 *
 * Two forms, because the corpus uses both: 81 bodies write `[<url>](<url>)` and
 * 83 leave the URL bare for the markdown renderer to autolink. Both end up as
 * `[<pretty>](<url>)`.
 *
 * ⚠ ORDER IS LOAD-BEARING: the markdown-link pass must run FIRST so that the
 * bare-URL pass cannot see a link label and wrap it a second time.
 *
 * This is DISPLAY ONLY. `href` values are passed through byte-for-byte — a
 * percent-encoded Arabic path is a perfectly valid URL and decoding it into the
 * href could break the destination.
 */
export function prettifyGuideUrls(md: string): string {
  const source = md ?? "";
  if (!source) return source;

  return source
    .replace(MD_LINK_WITH_URL_LABEL, (_m, label: string, href: string) =>
      `[${prettyUrlLabel(label)}](${href})`)
    .replace(BARE_URL, (url: string) => {
      // Give the sentence its punctuation back — it is not part of the link.
      const trailing = TRAILING_PUNCT.exec(url)?.[0] ?? "";
      const clean = trailing ? url.slice(0, -trailing.length) : url;
      if (!clean) return url;
      return `[${prettyUrlLabel(clean)}](${clean})${trailing}`;
    });
}

/* ---------------------------------------------------------------- *
 * «العنصر» — the service-facts card, in ONE shape for all 169 guides
 * ---------------------------------------------------------------- */

/**
 * The canonical rows, in the order every guide prints them.
 *
 * ⚠ MEASURED AGAINST THE WHOLE CORPUS, not invented. Only 21 of 169 bodies open
 * with a facts TABLE; 130 write the very same facts as a run of bold label
 * lines (`**الخدمة:** …`) and 18 carry no facts block at all. Worse, the shapes
 * disagree on the label wording — الخدمة / اسم الخدمة, المستفيد / المستفيدون /
 * الفئة المستفيدة, القناة / قناة التقديم — and the tables disagree on the header
 * too (العنصر|الوصف, العنصر|التفاصيل, معلومات الخدمة|التفاصيل). The reader met a
 * differently-shaped page per guide.
 *
 * `aliases` are the labels the corpus actually uses (counted over every bold
 * line in every body); `canon` is the wording the guides that already ship a
 * table agree on. Folding one onto the other is what makes the card identical
 * everywhere.
 */
const SERVICE_FACT_ROWS: ReadonlyArray<{
  canon: string;
  aliases: readonly string[];
}> = [
  { canon: "اسم الخدمة", aliases: ["اسم الخدمة", "الخدمة", "عنوان الخدمة"] },
  {
    canon: "عن الخدمة",
    aliases: ["ما هي الخدمة", "وصف الخدمة", "تعريف الخدمة", "نبذة عن الخدمة"],
  },
  {
    canon: "الجهة المقدمة",
    aliases: [
      "الجهة المقدمة",
      "الجهة",
      "الجهة المسؤولة",
      "جهة تقديم الخدمة",
      "مقدم الخدمة",
    ],
  },
  {
    canon: "الفئة المستفيدة",
    aliases: [
      "الفئة المستفيدة",
      "الفئات المستفيدة",
      "المستفيد",
      "المستفيدون",
      "المستفيدين",
      "المستفيد من الخدمة",
      "الفئة المستهدفة",
    ],
  },
  {
    canon: "قناة التقديم",
    aliases: ["قناة التقديم", "قنوات التقديم", "القناة", "قناة الخدمة"],
  },
  {
    canon: "الرابط الرسمي",
    aliases: ["الرابط الرسمي", "الرابط", "رابط الخدمة", "الموقع الرسمي"],
  },
];

/** First-column header wordings that mark a table as THE facts table. */
const FACTS_TABLE_HEADS: readonly string[] = [
  "العنصر",
  "العناصر",
  "معلومات الخدمة",
  "معلومات أساسية",
  "بطاقة الخدمة",
  "البند",
  "البيان",
  "المعلومة",
];

/**
 * A `**label:** value` / `- **label**: value` line — both colon placements, and
 * the value is OPTIONAL: 4 guides put the label on its own line and the value
 * on the lines below it. The line must OPEN with the bold run (after an
 * optional bullet), which is what keeps `1. …اضغط على **"ابدأ الخدمة"**` — a
 * step with bold inside it — from ever being read as a label.
 */
const BOLD_LABEL_LINE =
  /^\s*(?:[-*+]\s+)?\*\*\s*([^*\n]+?)\s*\*\*\s*[:：]?\s*(.*?)\s*$/;

/**
 * A line that ends a label's value block. Bullets are deliberately absent —
 * `- الأشخاص ذوو الإعاقة` IS the value of «المستفيدون» in 4 guides.
 */
function isStructuralLine(line: string): boolean {
  const trimmed = line.trim();
  return (
    trimmed.startsWith("#") ||
    trimmed.startsWith("|") ||
    /^-{3,}$/.test(trimmed) ||
    /^\d+[._)]/.test(trimmed)
  );
}
/** «**بطاقة تعريفية بالخدمة**» — a caption the table's own header replaces. */
const FACTS_CAPTION_LINE = /^\s*\*{1,2}\s*بطاقة[^*\n]*\*{1,2}\s*$/;
/** Any `| … |` row. A table needs this PLUS a separator on the next line. */
const TABLE_ROW = /^\s*\|(.*)\|\s*$/;
const TABLE_SEPARATOR = /^\s*\|[\s:|-]+\|\s*$/;

/**
 * Strip the decoration a label can carry so two spellings compare equal: bold
 * stars, a trailing colon (inside OR outside the stars) and the question mark
 * that «ما هي الخدمة؟» ends with.
 *
 * DISPLAY-SAFE — the result is what an unrecognised row prints, so it must not
 * alter the Arabic itself. Folding for LOOKUP is `factLookupKey`'s job.
 */
function factLabelKey(raw: string): string {
  return (raw ?? "")
    .replace(/\*/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/[:：؛]+$/, "")
    .replace(/[؟?]+$/, "")
    .trim();
}

/**
 * ⚠ TASHKEEL IS WHY THIS EXISTS. The corpus writes «الجهة المقدّمة» with a
 * shadda and «الُمستفيدون» with a damma — visually the alias, but a different
 * string, so a plain comparison misses them and the row falls through as
 * unrecognised and sorts to the bottom of the card. Folds the harakat, the
 * tatweel and the alef hamza forms. LOOKUP ONLY: never printed.
 */
function factLookupKey(label: string): string {
  return label
    .replace(/[ً-ْٰـ]/g, "")
    .replace(/[أإآٱ]/g, "ا")
    .replace(/\s+/g, " ")
    .trim();
}

/** `rank` orders the canonical rows; -1 keeps an unrecognised row as authored. */
function canonicalFact(raw: string): { label: string; rank: number } {
  const label = factLabelKey(raw);
  const key = factLookupKey(label);
  for (let index = 0; index < SERVICE_FACT_ROWS.length; index += 1) {
    const aliases = SERVICE_FACT_ROWS[index].aliases;
    if (aliases.some((alias) => factLookupKey(alias) === key)) {
      return { label: SERVICE_FACT_ROWS[index].canon, rank: index };
    }
  }
  return { label, rank: -1 };
}

/**
 * ⚠ THE ONE LINE SHAPE THAT MUST NEVER BECOME A TABLE ROW. `**الخطوة 3:** …` is
 * how the corpus writes its STEPS — ~250 of them across the wing, far more than
 * there are facts. They match `BOLD_FACT_LINE` perfectly, so without this guard
 * the normaliser would eat a guide's entire procedure into a table.
 */
function isStepLabel(raw: string): boolean {
  return /^الخطوة\b/.test(factLookupKey(factLabelKey(raw)));
}

interface FactRow {
  label: string;
  value: string;
  rank: number;
}

/**
 * Canonical rows first, in canonical order; anything unrecognised keeps its
 * authored position AFTER them — dropping it would lose real content («الرسوم»,
 * «مدة التنفيذ» appear in a handful of tables).
 */
function orderFactRows(rows: readonly FactRow[]): FactRow[] {
  const seen = new Set<string>();
  const unique = rows.filter((row) => {
    if (!row.label || !row.value) return false;
    if (seen.has(row.label)) return false;
    seen.add(row.label);
    return true;
  });
  const known = unique
    .filter((row) => row.rank >= 0)
    .sort((a, b) => a.rank - b.rank);
  const rest = unique.filter((row) => row.rank < 0);
  return [...known, ...rest];
}

/** A cell may not contain a raw `|`, and a hard break inside one breaks GFM. */
function factCell(value: string): string {
  return value.replace(/\s+/g, " ").replace(/\|/g, "\\|").trim();
}

function renderFactsTable(rows: readonly FactRow[]): string {
  return [
    "| العنصر | الوصف |",
    "| --- | --- |",
    ...rows.map((row) => `| ${factCell(row.label)} | ${factCell(row.value)} |`),
  ].join("\n");
}

/**
 * Split a line that carries the WHOLE card on it into one line per fact.
 *
 * Two guides (18905, 245508) write «**ما هي الخدمة؟** … **المستفيد:** …
 * **قناة التقديم:** …» as a single paragraph. Line-oriented scanning sees one
 * label whose value swallows the rest of the card.
 *
 * Returns null unless ≥2 RECOGNISED fact labels are on the line — the same
 * guard the block scanners use, and the reason an ordinary sentence with two
 * bold phrases in it is never touched.
 */
function explodeInlineFactLine(line: string): string[] | null {
  const marks = [...line.matchAll(/\*\*\s*([^*\n]+?)\s*\*\*/g)];
  if (marks.filter((m) => canonicalFact(m[1]).rank >= 0).length < 2) return null;

  const pieces: string[] = [];
  const first = marks[0].index ?? 0;
  // Any prose before the first label stays a line of its own — it is the
  // author's sentence, not a cell.
  if (first > 0) pieces.push(line.slice(0, first).trim());
  for (let k = 0; k < marks.length; k += 1) {
    const start = marks[k].index ?? 0;
    const end = k + 1 < marks.length ? (marks[k + 1].index ?? line.length) : line.length;
    pieces.push(line.slice(start, end).trim());
  }
  return pieces.filter(Boolean);
}

function splitTableCells(line: string): string[] {
  const inner = TABLE_ROW.exec(line)?.[1] ?? "";
  return inner.split("|").map((cell) => cell.trim());
}

/**
 * Rewrite a guide's service-facts block into the canonical «العنصر» table.
 *
 * Handles the two shapes the corpus uses — an existing (differently-headed)
 * table, and a run of bold label lines — and converts only the FIRST block it
 * finds, because a guide has exactly one. A body with no recognisable block is
 * returned BYTE-FOR-BYTE UNCHANGED: 18 guides have no facts to tabulate, and
 * inventing an empty card for them would be worse than the inconsistency.
 *
 * ⚠ TWO GUARDS CARRY THE WHOLE FUNCTION:
 *   1. `isStepLabel` — see its note. Steps outnumber facts 2:1.
 *   2. ≥2 RECOGNISED labels per block. One stray `**ملاحظة:** …` in prose is not
 *      a facts card, and promoting it to a table would be a visible lie.
 *
 * Runs BEFORE `splitGuideMarkdown` (a facts block never contains an image hole)
 * and before `prettifyGuideUrls`, so a URL moved into the الرابط الرسمي cell
 * still gets its readable label.
 */
export function normalizeServiceFactsTable(md: string): string {
  const source = md ?? "";
  if (!source) return source;

  const lines = source.split("\n");

  for (let i = 0; i < lines.length; i += 1) {
    // ---- Shape A: a table already there, headed some other way ----
    if (
      TABLE_ROW.test(lines[i]) &&
      i + 1 < lines.length &&
      TABLE_SEPARATOR.test(lines[i + 1])
    ) {
      const head = splitTableCells(lines[i]).filter(Boolean);
      const headKey = factLookupKey(factLabelKey(head[0]));
      const headIsColumnName = FACTS_TABLE_HEADS.some(
        (name) => factLookupKey(name) === headKey,
      );
      // ⚠ SOME TABLES HAVE NO HEADER ROW. A handful open straight on
      // `| **الخدمة** | … |` with the separator under it, so GFM treats the
      // first FACT as the header. Detect that and keep the row as data —
      // otherwise the service's own name is silently eaten as a column title.
      const headIsFactRow = head.length >= 2 && canonicalFact(head[0]).rank >= 0;
      if (head.length >= 2 && (headIsColumnName || headIsFactRow)) {
        let end = i + 2;
        const rows: FactRow[] = [];
        if (headIsFactRow && !headIsColumnName) {
          const { label, rank } = canonicalFact(head[0]);
          rows.push({ label, value: head.slice(1).join(" — ").trim(), rank });
        }
        while (end < lines.length && TABLE_ROW.test(lines[end])) {
          const cells = splitTableCells(lines[end]).filter(
            (cell, index, all) =>
              !(cell === "" && (index === 0 || index === all.length - 1)),
          );
          if (cells.length >= 2) {
            const { label, rank } = canonicalFact(cells[0]);
            rows.push({ label, value: cells.slice(1).join(" — ").trim(), rank });
          }
          end += 1;
        }
        if (rows.filter((row) => row.rank >= 0).length >= 2) {
          lines.splice(i, end - i, renderFactsTable(orderFactRows(rows)));
          return lines.join("\n");
        }
      }
    }

    // ---- Shape B: a run of bold label lines ----
    //
    // The corpus writes this run FOUR ways and all four have to land in the
    // same table:
    //   `**الخدمة:** قيمة`                    — value inline, lines adjacent
    //   `- **الخدمة**: قيمة`                  — the same as a bullet list
    //   `**المستفيد:** قيمة` + BLANK + next   — entries split by blank lines
    //   `**المستفيدون:**` \n `- قيمة` \n `- قيمة` — value on the FOLLOWING lines
    // A whole card on one line becomes one line per fact, then falls through to
    // the scanner below. Safe to mutate: every failure path returns `source`,
    // the untouched original, so a rejected block leaves nothing behind.
    const exploded = explodeInlineFactLine(lines[i]);
    if (exploded) lines.splice(i, 1, ...exploded);

    if (BOLD_LABEL_LINE.test(lines[i])) {
      const rows: FactRow[] = [];
      let cursor = i;
      let consumedTo = i; // exclusive; never includes a trailing blank

      while (cursor < lines.length) {
        // Tolerate ONE blank line between entries, but only between them: a
        // wider gap stops being a card and starts being the document.
        let probe = cursor;
        if (rows.length > 0 && lines[probe]?.trim() === "") probe += 1;

        const match = BOLD_LABEL_LINE.exec(lines[probe] ?? "");
        if (!match || isStepLabel(match[1])) break;

        const { label, rank } = canonicalFact(match[1]);
        const inline = (match[2] ?? "").trim();
        let value = inline;
        let last = probe;

        if (!value) {
          // Label alone on its line ⇒ the value is the lines below it, up to
          // the blank line that ends the entry. A bullet list collapses into
          // one cell; that is the only faithful way to put it in a table.
          const parts: string[] = [];
          let scan = probe + 1;
          while (scan < lines.length) {
            const line = lines[scan];
            if (line.trim() === "" || isStructuralLine(line)) break;
            if (BOLD_LABEL_LINE.test(line)) break;
            parts.push(line.replace(/^\s*[-*+]\s*/, "").trim());
            scan += 1;
          }
          if (!parts.length) break;
          value = parts.join(" • ");
          last = scan - 1;
        }

        rows.push({ label, value, rank });
        consumedTo = last + 1;
        cursor = last + 1;
      }

      if (rows.filter((row) => row.rank >= 0).length >= 2) {
        // Swallow «**بطاقة تعريفية بالخدمة**» sitting directly above the run —
        // the table's own header now says what it said.
        let start = i;
        if (start > 0 && FACTS_CAPTION_LINE.test(lines[start - 1])) start -= 1;
        lines.splice(
          start,
          consumedTo - start,
          renderFactsTable(orderFactRows(rows)),
        );
        return lines.join("\n");
      }
      i = consumedTo > i ? consumedTo - 1 : i;
    }
  }

  return source;
}

/**
 * Drop the guide's own title + abstract when the PAGE already rendered them.
 *
 * ⚠ MEASURED AGAINST THE WHOLE CORPUS, not guessed: 168 of 169 guide bodies open
 * with `# {title}` — the exact corpus title — and all 169 carry the `summary`
 * text. Rendered raw under a page that already has an `<h1>` and the summary,
 * every guide page would ship TWO `<h1>`s (the second missing the «بالصور»
 * treatment, so the two disagree) and print its abstract twice.
 *
 * Both strips are EQUALITY-GATED, never positional: a body whose first heading
 * is not the title, or whose first paragraph is not the summary, is left
 * completely alone. Comparison goes through `normalizeHeadingText` — the same
 * whitespace/colon-insensitive rule `ArticleBody`'s `dedupeHeading` uses.
 *
 * ⚠ LIVES HERE, NOT IN `GuideBody`, because the TABLE OF CONTENTS has to see the
 * same text the body renders. Extracting headings from the raw `guide_md` would
 * list a title heading that the body no longer emits, and its anchor would jump
 * nowhere. One implementation, two consumers.
 */
export function stripDuplicatedLead(
  text: string,
  heading?: string,
  lead?: string,
): string {
  let out = (text ?? "").replace(/^\s+/, "");

  if (heading) {
    const h1 = /^#{1,6}[ \t]+([^\n]*)(?:\n|$)/.exec(out);
    if (h1 && normalizeHeadingText(h1[1]) === normalizeHeadingText(heading)) {
      out = out.slice(h1[0].length).replace(/^\s+/, "");
    }
  }

  if (lead) {
    // The first paragraph = everything up to the first blank line. `split` with
    // a limit returns a true prefix of `out`, so slicing by its length is exact.
    const paragraph = out.split(/\n\s*\n/, 1)[0] ?? "";
    if (
      paragraph &&
      normalizeHeadingText(paragraph) === normalizeHeadingText(lead)
    ) {
      out = out.slice(paragraph.length).replace(/^\s+/, "");
    }
  }

  return out;
}

/**
 * The guide's headings, in document order — the TOC's data source.
 *
 * Runs over EXACTLY the text `GuideBody` renders: hole lines removed, and the
 * duplicated title/abstract stripped from the first text segment. That equality
 * is the whole contract — the ids come from `slugifyHeading` inside
 * `MarkdownRenderer`'s `headingAnchors` mode, and the hrefs come from here, so
 * the two must be derived from the same string or every anchor dead-links.
 *
 * ⚠ DE-DUPED BY SLUG, keeping the first. `slugifyHeading` is deterministic and
 * NOT collision-suffixed by design, so two identical heading texts in one guide
 * produce one id. Rendering both TOC rows would give two links to one anchor;
 * the ids themselves cannot be made unique from here, because the renderer
 * derives them independently.
 */
export function guideTocHeadings(
  guideMd: string,
  dedupeHeading?: string,
  dedupeLead?: string,
): TocHeading[] {
  const segments = splitGuideMarkdown(guideMd);
  const firstText = segments.find((segment) => segment.kind === "text");

  const body = segments
    .filter(
      (segment): segment is { kind: "text"; value: string } =>
        segment.kind === "text",
    )
    .map((segment) =>
      segment === firstText
        ? stripDuplicatedLead(segment.value, dedupeHeading, dedupeLead)
        : segment.value,
    )
    .join("\n\n");

  const seen = new Set<string>();
  return extractHeadings(body).filter((heading) => {
    if (!heading.slug || seen.has(heading.slug)) return false;
    seen.add(heading.slug);
    return true;
  });
}
