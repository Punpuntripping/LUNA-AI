import { markdownToPlainText } from "@contracts";

/**
 * A one-paragraph plain-text preview of a markdown excerpt (reference-card
 * snippets). Every markdown marker goes (headings, emphasis, list markers,
 * links → their text, images, table pipes — the same stripper as «النسخ
 * لناجز»), then all line breaks fold into single spaces so a clamped
 * `numberOfLines` preview is not eaten by blank heading lines.
 */
export function plainSnippet(markdown: string | null | undefined): string {
  if (!markdown) return "";
  return markdownToPlainText(markdown).replace(/\s+/g, " ").trim();
}
