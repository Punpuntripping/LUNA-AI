// «المراجع» copy block — moved verbatim from
// components/workspace/ReferencePanel.tsx (which re-exports it).

import type { Reference } from "./references";
import { referenceCopyLabel } from "./reference-labels";
import { fileRefsToText, type FileRefRow } from "./file-refs";

/**
 * The copy payload of a Rayhan document: ``body`` plus one ``{n}-{label}`` line
 * per reference under a «المراجع» heading — no snippets, domains or links.
 *
 * The references render in a sibling panel, not in the markdown body, so
 * without this a copied answer carries ``[n]`` markers with nothing to resolve
 * them. Labels come from ``referenceCopyLabel`` (the pasted list has no panel
 * around it). No references → ``body`` unchanged; blank body → the block alone.
 * Shared by AgentSearchViewer and the writer editor (MarkdownDocEditor).
 *
 * ``fileRefs`` (aggregator_file_refs.md §4.7): the report's «من مستنداتك» rows.
 * When given, each ``[F1·S7]`` tag in the body becomes «(القسم 7 من …)» and a
 * tag with no row is removed. Omitted → the body is copied as it is.
 */
export function appendReferencesForCopy(
  body: string,
  references: Reference[],
  fileRefs?: readonly FileRefRow[],
): string {
  const text = fileRefsToText(body, fileRefs);
  if (references.length === 0) return text;
  const refLines = [...references]
    .sort((a, b) => a.n - b.n)
    .map((ref) => `${ref.n}-${referenceCopyLabel(ref)}`)
    .join("\n");
  return text.trim().length > 0
    ? `${text}\n\nالمراجع\n${refLines}`
    : `المراجع\n${refLines}`;
}
