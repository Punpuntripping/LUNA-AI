// Citation markers in agent markdown — `[4]`, `[4,17]`, `[4، 17]`, `[١١]`.
//
// Same grammar as the web's remark plugin (`lib/markdown/remark-citations.ts`,
// which mirrors agents/deep_search_v4/aggregator/postvalidator.py::_CITATION_RE):
// numbers separated by an ASCII comma or the Arabic comma «،» (U+060C), digits
// ASCII, Arabic-Indic (U+0660–U+0669) or Extended/Persian (U+06F0–U+06F9). Each
// number becomes its own citation; numbers ≤ 0 are dropped — and a group whose
// numbers are ALL dropped (e.g. `[0]`) renders as nothing, exactly as on the web.

// Digit classes accepted inside a citation tag.
const CITATION_DIGITS = "\\d\\u0660-\\u0669\\u06F0-\\u06F9";

/**
 * The citation-group pattern. ⚠ Global (`g`) and therefore stateful via
 * `lastIndex` — the helpers below never use this instance directly; copy it
 * (`new RegExp(CITATION_RE.source, "g")`) if you need to iterate.
 */
export const CITATION_RE = new RegExp(
  `\\[([${CITATION_DIGITS}]+(?:\\s*[,،]\\s*[${CITATION_DIGITS}]+)*)\\]`,
  "g",
);

/**
 * Arabic-Indic and Extended Arabic-Indic digits → ASCII, so `Number.parseInt`
 * can read them. Everything else passes through untouched.
 */
export function toAsciiDigits(value: string): string {
  let out = "";
  for (const ch of value) {
    const code = ch.codePointAt(0) ?? 0;
    if (code >= 0x0660 && code <= 0x0669) out += String(code - 0x0660);
    else if (code >= 0x06f0 && code <= 0x06f9) out += String(code - 0x06f0);
    else out += ch;
  }
  return out;
}

/** The reference numbers inside one matched group's inner text (`4، 17`). */
function groupNumbers(inner: string): number[] {
  const out: number[] = [];
  for (const raw of inner.split(/[,،]/)) {
    const n = Number.parseInt(toAsciiDigits(raw.trim()), 10);
    if (Number.isFinite(n) && n > 0) out.push(n);
  }
  return out;
}

export type CitationSegment =
  | { type: "text"; value: string }
  | { type: "cite"; n: number };

/**
 * Split plain text into text runs and one `cite` segment per cited number —
 * the same output the web's remark plugin produces for a text node. Text with
 * no citation comes back as a single text segment (or none when empty).
 */
export function splitCitations(text: string): CitationSegment[] {
  const re = new RegExp(CITATION_RE.source, "g");
  const out: CitationSegment[] = [];
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = re.exec(text)) !== null) {
    if (match.index > last) out.push({ type: "text", value: text.slice(last, match.index) });
    for (const n of groupNumbers(match[1])) out.push({ type: "cite", n });
    last = match.index + match[0].length;
  }
  if (last < text.length) out.push({ type: "text", value: text.slice(last) });
  return out;
}

/** Code fence opener/closer: ≥3 backticks or tildes (same rule as markdown/images.ts). */
const FENCE = /^\s*(`{3,}|~{3,})/;

/** Inline code spans — same pattern as markdown/plain-text.ts. */
const CODE_SPAN = /(`+)([^`]|[^`][\s\S]*?[^`])\1(?!`)/g;

/** Private-use placeholder for a parked code span (never in real text). */
const PH = /(\d+)/g;

/** Does a citation group (`[6]`, `[6،7]`) start exactly at `index`? */
function startsWithCitation(str: string, index: number): boolean {
  const re = new RegExp(CITATION_RE.source, "y");
  re.lastIndex = index;
  return re.test(str);
}

function linkifyLine(line: string): string {
  const kept: string[] = [];
  let s = line.replace(CODE_SPAN, (m) => {
    kept.push(m);
    return `${kept.length - 1}`;
  });

  const re = new RegExp(CITATION_RE.source, "g");
  s = s.replace(re, (whole: string, inner: string, offset: number, str: string) => {
    const before = offset > 0 ? str[offset - 1] : "";
    const after = str[offset + whole.length] ?? "";
    // Leave markdown syntax alone: a link/image text `[4](…)`, a reference
    // link `[4][a]`, a definition `[4]: url` (line start only), an escaped
    // `\[4]`, and a marker that is already linkified (`[[4]](cite:4)`).
    // NOT syntax (the web's remark plugin pills these, so must we): adjacent
    // markers `[1][6]` and a marker before a colon mid-line «كالآتي [1]:».
    if (after === "(") return whole;
    if (after === "[" && !startsWithCitation(str, offset + whole.length)) return whole;
    if (after === ":" && /^\s{0,3}$/.test(str.slice(0, offset))) return whole;
    if (before === "\\" || before === "!" || before === "[") return whole;
    return groupNumbers(inner)
      .map((n) => `[[${n}]](cite:${n})`)
      .join("");
  });

  return s.replace(PH, (_m, idx: string) => kept[Number(idx)] ?? "");
}

/**
 * Turn every citation group in a markdown document into one markdown link per
 * number: `[4،17]` → `[[4]](cite:4)[[17]](cite:17)` (digits always ASCII), so a
 * plain markdown renderer can route `cite:` links to the source reader.
 *
 * Untouched: fenced code blocks (``` / ~~~), inline code spans, and citation-
 * shaped text that is already markdown syntax (see `linkifyLine`). Table cells
 * are ordinary inline text — the output contains no `|`, so rows stay intact.
 * Idempotent: running it twice changes nothing.
 */
export function linkifyCitations(markdown: string): string {
  if (!markdown.includes("[")) return markdown;
  const lines = markdown.split("\n");
  let inFence = false;
  let fenceChar = "";
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const fence = FENCE.exec(line);
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
    if (inFence || !line.includes("[")) continue;
    lines[i] = linkifyLine(line);
  }
  return lines.join("\n");
}
