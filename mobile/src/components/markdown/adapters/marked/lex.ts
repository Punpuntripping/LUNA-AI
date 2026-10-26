import { Lexer, type Token } from "marked";

// Pure (no RN imports) so it is unit-tested under node.

/**
 * Markdown → top-level block tokens (GFM). `breaks`: a single newline inside a
 * paragraph becomes a `br` token (a visible line break) — the web's
 * `white-space: pre-line`, used for «رسائل ريحان» bodies only. Without it the
 * newline stays inside the text token (chat / reader, unchanged).
 */
export function lexMarkdown(markdown: string, breaks = false): Token[] {
  return markdown ? Lexer.lex(markdown, { gfm: true, breaks }).filter((t) => t.type !== "space") : [];
}
