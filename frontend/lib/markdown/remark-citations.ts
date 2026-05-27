/**
 * remark plugin: turn ``[n]`` and ``[n,m,...]`` citations inside text nodes
 * into individual ``<cite data-n="n" />`` hast elements.
 *
 * Why a remark plugin (not a component override):
 *   react-markdown v10 dispatches ``components`` by HTML element name. There's
 *   no ``text`` element, so an override keyed on ``text`` is dead code — text
 *   nodes are rendered as raw strings inside whatever container element holds
 *   them (``p``, ``li``, ``td``, ``blockquote``, ``em``, …). Intercepting at
 *   the AST layer gives us real React components in every nesting context
 *   without enumerating every container override.
 *
 * Citation grammar (mirrors agents/deep_search_v4/aggregator/postvalidator.py::_CITATION_RE):
 *   - Single:        ``[4]``
 *   - Multi (ASCII): ``[4,17]`` / ``[4, 17, 23]``
 *   - Multi (Arabic comma, U+060C): ``[4،17]``
 *
 * Each number in a group becomes its own ``<cite data-n="n" />`` — so
 * ``[4,17]`` renders as two adjacent ``CitationMarker`` buttons (``[4][17]``).
 * Each is independently clickable; we don't preserve the "they're in the same
 * bracket group" visual because the per-ref click is what the user actually
 * needs. If you want literal ``[4, 17]`` grouping later, that's a follow-up
 * pass on the renderer (the plugin output stays the same).
 *
 * Edge cases handled by the regex itself:
 *   - ``[abc]``, ``[4abc]``, unclosed ``[4`` → no match, stays as text.
 *   - Inside a fenced code block: code-block contents live on the ``code``
 *     mdast node's ``value``, not as ``text`` children — our visitor doesn't
 *     touch them. ``[4]`` written inside a code block stays as text.
 */

import type { Plugin } from "unified";
import type { Root, RootContent } from "mdast";
import { visit, SKIP } from "unist-util-visit";

// Anchored on the opening ``[`` and closing ``]``. Numbers separated by ASCII
// comma OR Arabic comma (U+060C), with optional surrounding whitespace. The
// outer capture group holds the comma-separated number list; we split on the
// comma class below.
const CITATION_RE = /\[(\d+(?:\s*[,،]\s*\d+)*)\]/g;

export const remarkCitations: Plugin<[], Root> = () => (tree) => {
  visit(tree, "text", (node, index, parent) => {
    if (!parent || index == null) return;
    const value = node.value;
    if (!value || !value.includes("[")) return;

    CITATION_RE.lastIndex = 0;
    const next: RootContent[] = [];
    let lastIndex = 0;
    let foundAny = false;
    let match: RegExpExecArray | null;

    while ((match = CITATION_RE.exec(value)) !== null) {
      foundAny = true;
      if (match.index > lastIndex) {
        next.push({ type: "text", value: value.slice(lastIndex, match.index) });
      }
      // Split on ASCII or Arabic comma; one cite node per number.
      for (const raw of match[1].split(/[,،]/)) {
        const n = Number.parseInt(raw.trim(), 10);
        if (Number.isFinite(n) && n > 0) {
          next.push({
            // Custom mdast node — mdast-util-to-hast honours data.hName /
            // data.hProperties to emit a hast element of our choosing.
            type: "citation",
            // Leaf node — CitationMarker draws the "[n]" itself; no inner text.
            data: {
              hName: "cite",
              hProperties: { "data-n": String(n) },
            },
          } as unknown as RootContent);
        }
      }
      lastIndex = match.index + match[0].length;
    }

    if (!foundAny) return;
    if (lastIndex < value.length) {
      next.push({ type: "text", value: value.slice(lastIndex) });
    }

    parent.children.splice(index, 1, ...next);
    // Skip the inserted siblings so visit doesn't re-process them.
    return [SKIP, index + next.length];
  });
};
