// TODO(K1): switch to `linkifyCitations` from `@contracts` once
// frontend/lib/contracts/citations.ts lands (the contracts agent is building
// it). This is a LOCAL STUB for the K0 spike only — same grammar as the web's
// remark plugin (frontend/lib/markdown/remark-citations.ts:47-70).

const CITATION_DIGITS = "\\d\\u0660-\\u0669\\u06F0-\\u06F9";
const CITATION_RE = new RegExp(
  `\\[([${CITATION_DIGITS}]+(?:\\s*[,\\u060C]\\s*[${CITATION_DIGITS}]+)*)\\]`,
  "g",
);
const FENCE_RE = /^ {0,3}(`{3,}|~{3,})/;
const CODE_SPAN_RE = /(`+)[^`]*?\1/g;

/** Arabic-Indic (U+0660–0669) / Extended (U+06F0–06F9) → ASCII digits. */
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

function linkifyText(text: string): string {
  return text.replace(CITATION_RE, (match: string, list: string, offset: number, whole: string) => {
    const prev = whole[offset - 1];
    const next = whole[offset + match.length];
    // `[[4]](cite:4)` (already linked), `[4](url)` link text, `[4]: url` definition.
    if (prev === "[" || next === "(" || next === "]" || next === ":") return match;
    const numbers = list
      .split(/[,،]/)
      .map((raw) => Number.parseInt(toAsciiDigits(raw.trim()), 10))
      .filter((n) => Number.isFinite(n) && n > 0);
    return numbers.length ? numbers.map((n) => `[[${n}]](cite:${n})`).join("") : match;
  });
}

/** `[4]` / `[4،17]` / `[١١]` → `[[4]](cite:4)` links, skipping fenced and inline code. */
export function linkifyCitations(markdown: string): string {
  let fence: string | null = null;
  return markdown
    .split("\n")
    .map((line) => {
      const opener = FENCE_RE.exec(line);
      if (fence) {
        if (opener && opener[1][0] === fence[0] && opener[1].length >= fence.length) fence = null;
        return line;
      }
      if (opener) {
        fence = opener[1];
        return line;
      }
      if (!line.includes("[")) return line;
      // Keep inline code spans verbatim; linkify only the text between them.
      let out = "";
      let last = 0;
      for (const m of line.matchAll(CODE_SPAN_RE)) {
        const at = m.index ?? 0;
        out += linkifyText(line.slice(last, at)) + m[0];
        last = at + m[0].length;
      }
      return out + linkifyText(line.slice(last));
    })
    .join("\n");
}
