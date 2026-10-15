// ==========================================
// Markdown → plain text (النسخ لناجز)
// ==========================================
// Najiz (منصة ناجز) fields accept NO formatting: whatever a lawyer pastes lands
// verbatim, so a Rayhan document copied as markdown arrives littered with
// ``#``, ``**``, ``- ``, ``>`` and table pipes. The «النسخ لناجز» copy option
// runs the document through this function instead — every piece of markdown
// syntax goes, the STRUCTURE survives as line breaks, numbering and indentation.
// Copy path only; nothing rendered on screen goes through here.

import { stripMarkdownImages } from "./images";

/** Indent per nesting level of a list — matches the width of ``"1. "``. */
const LIST_INDENT = "   ";

/**
 * A list item: bullet (``-`` ``*`` ``+``) or ordered (``N.`` / ``N)``) marker
 * followed by whitespace. The whitespace is what keeps the «المراجع» block out:
 * ``1-نظام العمل — المادة 77`` has a digit, a hyphen and NO space, so it never
 * matches and passes through byte-for-byte.
 */
const LIST_ITEM = /^(\s*)([-*+]|\d{1,9}[.)])(?:\s+(.*))?$/;

/** GFM task-list checkbox at the head of an item: ``[ ]`` / ``[x]``. */
const TASK_BOX = /^\[[ xX]\]\s+/;

/** ATX heading, optional closing ``#`` run. */
const ATX_HEADING = /^\s{0,3}#{1,6}(?:\s+(.*?))?(?:\s+#+)?\s*$/;

/** Thematic break: three or more ``-`` / ``*`` / ``_``, spaces allowed between. */
const HORIZONTAL_RULE = /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/;

/** Setext H1 underline. (The H2 ``---`` form is caught by HORIZONTAL_RULE.) */
const SETEXT_H1 = /^\s{0,3}=+\s*$/;

/** Table separator row: ``|---|:--:|`` — at least one pipe required. */
const TABLE_SEPARATOR = /^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)+\|?\s*$|^\s*\|\s*:?-+:?\s*\|\s*$/;

/** Leading blockquote markers, nested ``>>`` / ``> >`` included. */
const BLOCKQUOTE = /^\s{0,3}(?:>\s?)+/;

/** Code fence opener/closer: >=3 backticks or tildes. Same rule as images.ts. */
const FENCE = /^\s*(`{3,}|~{3,})/;

/**
 * Characters that count as "inside a word" for ``_`` emphasis. CommonMark
 * never treats an intraword underscore as emphasis, so ``snake_case`` and an
 * underscore wedged between Arabic letters stay as typed. Explicit ranges
 * (Latin + the Arabic blocks) rather than ``\p{L}`` — the project targets
 * ES2017, where Unicode property escapes are not available.
 */
const WORD = "A-Za-z0-9_\\u0600-\\u06FF\\u0750-\\u077F\\u08A0-\\u08FF\\uFB50-\\uFDFF\\uFE70-\\uFEFF";
const UNDERSCORE_STRONG = new RegExp(`(^|[^${WORD}])__(\\S(?:.*?\\S)?)__(?=$|[^${WORD}])`, "g");
const UNDERSCORE_EM = new RegExp(`(^|[^${WORD}])_(\\S(?:.*?\\S)?)_(?=$|[^${WORD}])`, "g");

/**
 * Placeholders for text that must survive the inline pass literally (code-span
 * contents, backslash-escaped characters). Private-use code points — they cannot
 * occur in a real document and none of the inline patterns match them.
 */
const PH_OPEN = "";
const PH_CLOSE = "";
const PH_PATTERN = /(\d+)/g;

/**
 * Strip inline markdown from ONE line: emphasis, strikethrough, inline code,
 * links, autolinks, HTML. Only PAIRED emphasis markers are removed — a lone
 * ``*`` or ``_`` is prose and stays. Citation markers ``[3]`` / ``[1، 2]`` are
 * untouched: they are not followed by ``(`` so the link rule never sees them.
 *
 * May return a string containing ``\n`` (a ``<br>`` becomes a real break).
 */
function stripInline(line: string): string {
  const kept: string[] = [];
  const keep = (value: string) => {
    kept.push(value);
    return `${PH_OPEN}${kept.length - 1}${PH_CLOSE}`;
  };

  let s = line;

  // Inline code first: its contents are literal — no emphasis, no escapes.
  // One surrounding space on each side is padding, not content (CommonMark).
  s = s.replace(/(`+)([^`]|[^`][\s\S]*?[^`])\1(?!`)/g, (_m, _ticks: string, code: string) =>
    keep(/^ .* $/.test(code) && code.trim().length > 0 ? code.slice(1, -1) : code),
  );

  // Backslash escapes: ``\*`` is a literal star — park it so it can't pair up.
  s = s.replace(/\\([!-/:-@[-`{-~])/g, (_m, ch: string) => keep(ch));
  // Trailing backslash = hard line break marker; the line break itself stays.
  s = s.replace(/\\$/, "");

  // HTML: comments, <br> → newline, autolinks → the bare URL, other tags gone.
  s = s.replace(/<!--[\s\S]*?-->/g, "");
  s = s.replace(/<br\s*\/?>/gi, "\n");
  s = s.replace(/<((?:https?|mailto):[^<>\s]+)>/gi, "$1");
  s = s.replace(/<\/?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?\/?>/g, "");

  // Links: [text](url "title") → text. Images were stripped before this pass.
  s = s.replace(/\[([^\]]*)\]\([^)]*\)/g, "$1");

  // Emphasis — longest markers first so ***x*** unwinds to x in two steps.
  s = s.replace(/~~(\S(?:.*?\S)?)~~/g, "$1");
  s = s.replace(/\*\*(\S(?:.*?\S)?)\*\*/g, "$1");
  s = s.replace(UNDERSCORE_STRONG, "$1$2");
  s = s.replace(/\*(\S(?:.*?\S)?)\*/g, "$1");
  s = s.replace(UNDERSCORE_EM, "$1$2");

  // Restore parked literals. A kept value never contains a placeholder itself.
  return s.replace(PH_PATTERN, (_m, idx: string) => kept[Number(idx)] ?? "");
}

/** Leading-whitespace width, tabs counted as 4 columns. */
function indentWidth(line: string): number {
  const lead = /^[ \t]*/.exec(line)![0];
  let width = 0;
  for (const ch of lead) width += ch === "\t" ? 4 : 1;
  return width;
}

/** Split a table row into trimmed cell texts, honouring ``\|`` escapes. */
function tableCells(row: string): string[] {
  let body = row.trim();
  if (body.startsWith("|")) body = body.slice(1);
  if (body.endsWith("|") && !body.endsWith("\\|")) body = body.slice(0, -1);

  const cells: string[] = [];
  let current = "";
  for (let i = 0; i < body.length; i++) {
    const ch = body[i];
    if (ch === "\\" && body[i + 1] === "|") {
      current += "|";
      i++;
    } else if (ch === "|") {
      cells.push(current);
      current = "";
    } else {
      current += ch;
    }
  }
  cells.push(current);
  return cells.map((c) => stripInline(c.trim()).replace(/\s*\n\s*/g, " ").trim());
}

/**
 * Mark which source lines belong to a GFM table: a separator row, the header
 * row above it, and every contiguous piped row below it. A stray ``|`` in prose
 * is left alone — only a real table (separator present) is flattened.
 */
function findTableLines(lines: string[], inFenceAt: boolean[]): Set<number> {
  const table = new Set<number>();
  for (let i = 1; i < lines.length; i++) {
    if (inFenceAt[i] || !TABLE_SEPARATOR.test(lines[i])) continue;
    const header = lines[i - 1];
    if (inFenceAt[i - 1] || !header.includes("|")) continue;
    table.add(i - 1);
    table.add(i);
    for (let j = i + 1; j < lines.length; j++) {
      if (inFenceAt[j] || lines[j].trim().length === 0 || !lines[j].includes("|")) break;
      table.add(j);
    }
  }
  return table;
}

/**
 * Convert a markdown document to plain, paste-anywhere text.
 *
 * - Images removed (``stripMarkdownImages``), code fences removed with their
 *   inner text kept verbatim.
 * - Headings → their text on its own line, blank line before and after.
 *   Blockquote markers, horizontal rules and setext underlines → removed.
 * - EVERY list (bullet or ordered) → ``N. text``. The counter restarts per
 *   list block; a nested list gets its own counter, indented 3 spaces a level.
 * - Tables → separator row dropped, each row's cells joined with « — ».
 * - Inline syntax stripped (see ``stripInline``); ``[n]`` citation markers and
 *   the «المراجع» ``1-label`` lines pass through unchanged.
 * - Trailing whitespace trimmed per line, 3+ newlines collapsed to 2, ends trimmed.
 *
 * Counters are JS numbers, so output digits are always Latin (project rule).
 */
export function markdownToPlainText(markdown: string): string {
  const lines = stripMarkdownImages(markdown).split(/\r?\n/);

  // Pre-pass: which lines sit inside a fence (fence lines themselves included),
  // so table detection never reaches into code.
  const inFenceAt: boolean[] = [];
  {
    let open = false;
    let char = "";
    for (const line of lines) {
      const fence = FENCE.exec(line);
      if (fence) {
        const marker = fence[1][0];
        if (!open) {
          open = true;
          char = marker;
          inFenceAt.push(true);
          continue;
        }
        if (marker === char) {
          open = false;
          char = "";
          inFenceAt.push(true);
          continue;
        }
      }
      inFenceAt.push(open);
    }
  }
  const tableLines = findTableLines(lines, inFenceAt);

  const out: string[] = [];
  // Open list levels, outermost first: source indent, list kind, running
  // counter. A bullet list and an ordered list back to back at the same indent
  // are TWO lists (CommonMark) — the kind change restarts the counter.
  let stack: { indent: number; ordered: boolean; count: number }[] = [];
  let prevBlank = true;
  let inFence = false;
  let fenceChar = "";

  const pushText = (text: string, indent = "") => {
    for (const part of text.split("\n")) out.push(indent + part);
  };
  const pushBlank = () => out.push("");

  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i];

    // --- Code fences: drop the fence lines, keep the body verbatim. ---------
    const fence = FENCE.exec(raw);
    if (fence) {
      const marker = fence[1][0];
      if (!inFence) {
        inFence = true;
        fenceChar = marker;
        if (indentWidth(raw) < 2) stack = [];
        prevBlank = false;
        continue;
      }
      if (marker === fenceChar) {
        inFence = false;
        fenceChar = "";
        continue;
      }
    }
    if (inFence) {
      out.push(raw);
      continue;
    }

    // --- Tables. -------------------------------------------------------------
    if (tableLines.has(i)) {
      stack = [];
      prevBlank = false;
      if (TABLE_SEPARATOR.test(raw)) continue;
      const cells = tableCells(raw).filter((c) => c.length > 0);
      if (cells.length > 0) out.push(cells.join(" — "));
      continue;
    }

    // Blockquote markers go first; what's left is handled like any line.
    const line = raw.replace(BLOCKQUOTE, "");

    if (line.trim().length === 0) {
      pushBlank();
      prevBlank = true;
      continue;
    }

    // --- Setext underline / horizontal rule. ---------------------------------
    if (SETEXT_H1.test(line) || HORIZONTAL_RULE.test(line)) {
      // Underline directly under text → that text was a heading: give it the
      // same blank-line frame an ATX heading gets.
      if (!prevBlank && out.length > 0 && out[out.length - 1].trim().length > 0) {
        if (out.length > 1 && out[out.length - 2].trim().length > 0) {
          out.splice(out.length - 1, 0, "");
        }
      }
      stack = [];
      pushBlank();
      prevBlank = true;
      continue;
    }

    // --- ATX heading. --------------------------------------------------------
    const heading = ATX_HEADING.exec(line);
    if (heading) {
      stack = [];
      const text = stripInline((heading[1] ?? "").trim()).trim();
      if (text.length > 0) {
        pushBlank();
        pushText(text);
        pushBlank();
      }
      prevBlank = true;
      continue;
    }

    // --- List item. ----------------------------------------------------------
    const item = LIST_ITEM.exec(line);
    if (item) {
      const indent = indentWidth(item[1]);
      const ordered = /\d/.test(item[2]);
      while (stack.length > 0 && stack[stack.length - 1].indent > indent) stack.pop();
      const top = stack[stack.length - 1];
      if (top && top.indent === indent) {
        top.count = top.ordered === ordered ? top.count + 1 : 1;
        top.ordered = ordered;
      } else {
        stack.push({ indent, ordered, count: 1 });
      }
      const level = stack.length - 1;
      const text = stripInline((item[3] ?? "").replace(TASK_BOX, "").trim()).trim();
      const n = stack[level].count;
      const lead = LIST_INDENT.repeat(level);
      const [first, ...rest] = text.split("\n");
      out.push(`${lead}${n}. ${first}`);
      for (const part of rest) out.push(lead + LIST_INDENT + part.trim());
      prevBlank = false;
      continue;
    }

    // --- Plain paragraph line. -----------------------------------------------
    // Inside an open list, an indented line (or one with no blank before it)
    // continues the current item; otherwise the list block is over.
    if (stack.length > 0 && (indentWidth(line) > 0 || !prevBlank)) {
      pushText(stripInline(line.trim()).trim(), LIST_INDENT.repeat(stack.length));
    } else {
      stack = [];
      pushText(stripInline(line.trim()).trim());
    }
    prevBlank = false;
  }

  return out
    .map((l) => l.replace(/\s+$/, ""))
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}
