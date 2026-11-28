/**
 * remark plugin: turn the report's file-ref tags (``[F1·S7]`` = section 7 of
 * the conversation's file WI-1, ``[F1]`` = WI-1 as a whole) into
 * ``<data value="F1.S7">``
 * hast elements, one per ref (plan ``.claude/plans/aggregator_file_refs.md``
 * §4.6). ``MarkdownRenderer`` dispatches them to its ``renderFileRef`` prop.
 *
 * Same approach as ``remark-citations.ts`` (an AST pass reaches every container
 * — paragraphs, lists, table cells, emphasis — without per-container overrides;
 * code blocks are never touched). The grammar is ``lib/contracts/file-refs.ts``
 * (mirrors ``shared/workspace/file_refs.py``); every tag carries letters, so a library
 * ``[4]`` / ``[4,17]`` / ``[٤]`` never matches and ``remarkCitations`` never
 * matches a file tag — the two plugins can run in any order.
 *
 * ``<data>`` because react-markdown dispatches by HTML element name and nothing
 * else in a markdown body can produce one (raw HTML is not rendered). Its text
 * child is the tag as written: without a ``renderFileRef`` (chat, streaming…)
 * the renderer prints that text, so the body reads exactly as before. A bracket
 * holding several refs (``[F1·S7، F1·S9]``) gives one element per ref; the
 * first carries the whole bracket's text, the rest none.
 */

import type { Plugin } from "unified";
import type { Root, RootContent } from "mdast";
import { visit, SKIP } from "unist-util-visit";
import { findFileRefTags } from "../contracts/file-refs";

/** The hast tag name the plugin emits (and ``MarkdownRenderer`` overrides). */
export const FILE_REF_ELEMENT = "data";

export const remarkFileRefs: Plugin<[], Root> = () => (tree) => {
  visit(tree, "text", (node, index, parent) => {
    if (!parent || index == null) return;
    const value = node.value;
    if (!value || !value.includes("[")) return;

    const tags = findFileRefTags(value);
    if (tags.length === 0) return;

    const next: RootContent[] = [];
    let last = 0;
    for (const tag of tags) {
      if (tag.start > last) {
        next.push({ type: "text", value: value.slice(last, tag.start) });
      }
      tag.refs.forEach((ref, i) => {
        next.push({
          // Custom mdast node — mdast-util-to-hast honours data.hName /
          // data.hProperties and keeps the text child.
          type: "fileRef",
          data: { hName: FILE_REF_ELEMENT, hProperties: { value: ref } },
          children: i === 0 ? [{ type: "text", value: tag.raw }] : [],
        } as unknown as RootContent);
      });
      last = tag.end;
    }
    if (last < value.length) {
      next.push({ type: "text", value: value.slice(last) });
    }

    parent.children.splice(index, 1, ...next);
    // Skip the inserted siblings so visit doesn't re-process them.
    return [SKIP, index + next.length];
  });
};
