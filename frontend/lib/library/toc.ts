// Presentational helpers for the «محتويات النظام» table-of-contents rail.
// Pure string parsing — no data fetching, no side effects.

/**
 * Split a TOC label into an optional gutter chip (the مادة number) and the row
 * text. For the common article-index label «المادة 80» this returns
 * `{ chip: "80", text: "المادة" }` so the rail can render the word on the RTL
 * start side and the number as a page-number-style chip on the end side —
 * without duplicating the number. Digits may be Western (0-9) or Arabic-Indic
 * (٠-٩). A continuation section — a chunk-fallback title suffixed «(تابع)» by
 * the page because it repeats the previous title — returns the suffix as the
 * chip and the bare title as text: the row truncates at its END, so a suffix
 * left inline is exactly the part a long title loses. Any other label keeps its
 * full text and gets no chip (chapter/باب titles, unnumbered sections).
 */
export function parseTocLabel(label: string): {
  chip: string | null;
  text: string;
} {
  const match = label.match(/^\s*((?:ال)?مادة)\s+([0-9٠-٩]+)\s*$/);
  if (match) return { chip: match[2], text: match[1] };
  const continuation = label.match(/^(.*\S)\s*\(تابع\)\s*$/);
  if (continuation) return { chip: "تابع", text: continuation[1] };
  return { chip: null, text: label.trim() };
}
