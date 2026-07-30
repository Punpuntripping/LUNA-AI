// Presentational helpers for the «محتويات النظام» table-of-contents rail.
// Pure string parsing — no data fetching, no side effects.

/**
 * Split a TOC label into an optional gutter chip (the مادة number) and the row
 * text. For the common article-index label «المادة 80» this returns
 * `{ chip: "80", text: "المادة" }` so the rail can render the word on the RTL
 * start side and the number as a page-number-style chip on the end side —
 * without duplicating the number. Digits may be Western (0-9) or Arabic-Indic
 * (٠-٩). Any label that is not a bare «(ال)مادة N» keeps its full text and gets
 * no chip (chapter/باب titles, unnumbered sections).
 */
export function parseTocLabel(label: string): {
  chip: string | null;
  text: string;
} {
  const match = label.match(/^\s*((?:ال)?مادة)\s+([0-9٠-٩]+)\s*$/);
  if (match) return { chip: match[2], text: match[1] };
  return { chip: null, text: label.trim() };
}
