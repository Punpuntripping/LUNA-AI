// ==========================================
// Markdown → clipboard HTML («نسخ» into Word / Google Docs)
// ==========================================
// Word and Google Docs build formatting from the clipboard's ``text/html``
// flavor and ignore markdown, so a ``text/plain``-only copy pastes literal
// ``#`` / ``**``. This renders the markdown to HTML for that flavor. Every
// block gets ``dir="rtl"`` — Word does not inherit direction from a wrapper,
// and without it Arabic paragraphs paste LTR (punctuation jumps to the left).
// Copy path only; nothing rendered on screen goes through here.

import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import remarkRehype from "remark-rehype";
import rehypeStringify from "rehype-stringify";
import { stripMarkdownImages } from "./images";

const RTL_BLOCKS = new Set([
  "p", "h1", "h2", "h3", "h4", "h5", "h6",
  "ul", "ol", "li", "blockquote", "table", "th", "td",
]);

/** Inline style per tag — Word drops stylesheet rules, keeps ``style=``. */
const TAG_STYLE: Record<string, string> = {
  table: "border-collapse:collapse",
  th: "border:1px solid #999;padding:4px 8px",
  td: "border:1px solid #999;padding:4px 8px",
};

interface HastNode {
  type: string;
  tagName?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
}

/** Stamp ``dir``/``lang`` (and table borders) on every block element. */
function rehypeRtlBlocks() {
  const walk = (node: HastNode) => {
    if (node.type === "element" && node.tagName) {
      const props = (node.properties ??= {});
      if (RTL_BLOCKS.has(node.tagName)) {
        props.dir = "rtl";
        props.lang = "ar";
      }
      const style = TAG_STYLE[node.tagName];
      if (style) props.style = style;
    }
    node.children?.forEach(walk);
  };
  return (tree: HastNode) => walk(tree);
}

// Raw HTML in the markdown is dropped (remark-rehype default), so the output
// carries only what the markdown itself describes.
const processor = unified()
  .use(remarkParse)
  .use(remarkGfm)
  .use(remarkRehype)
  .use(rehypeRtlBlocks)
  .use(rehypeStringify);

/**
 * Markdown → an RTL HTML fragment for the clipboard. Images are stripped
 * (relative card URLs would paste as broken boxes), same as the plain pass.
 */
export function markdownToClipboardHtml(markdown: string): string {
  const body = String(processor.processSync(stripMarkdownImages(markdown)));
  return `<div dir="rtl" lang="ar">${body}</div>`;
}
