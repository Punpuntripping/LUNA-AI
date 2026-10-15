// «المراجع» copy block — moved verbatim from
// components/workspace/ReferencePanel.tsx (which re-exports it).

import type { Reference } from "./references";
import { referenceCopyLabel } from "./reference-labels";

/**
 * The copy payload of a Rayhan document: ``body`` plus one ``{n}-{label}`` line
 * per reference under a «المراجع» heading — no snippets, domains or links.
 *
 * The references render in a sibling panel, not in the markdown body, so
 * without this a copied answer carries ``[n]`` markers with nothing to resolve
 * them. Labels come from ``referenceCopyLabel`` (the pasted list has no panel
 * around it). No references → ``body`` unchanged; blank body → the block alone.
 * Shared by AgentSearchViewer and the writer editor (MarkdownDocEditor).
 */
export function appendReferencesForCopy(
  body: string,
  references: Reference[],
): string {
  if (references.length === 0) return body;
  const refLines = [...references]
    .sort((a, b) => a.n - b.n)
    .map((ref) => `${ref.n}-${referenceCopyLabel(ref)}`)
    .join("\n");
  return body.trim().length > 0
    ? `${body}\n\nالمراجع\n${refLines}`
    : `المراجع\n${refLines}`;
}
