import { cn } from "@/lib/utils";
import { ChunkTable } from "@/components/library/blocks/ChunkTable";
import { renderInline, type LegalBlock } from "@/lib/library/legal-text";

/**
 * Renders the typed blocks {@link toLegalBlocks} lifts out of pre-formatted
 * legal text, on the app's long-form reading scale (`text-read`, 18px/1.7 on
 * desktop, 17px on phones — the 16px UI tier read as chrome, not text).
 *
 * The vertical rhythm is the point. A statute is not a stack of equal
 * paragraphs: a clause number belongs to the clause under it, sub-clauses
 * belong together, and chapters open wider than clauses. So spacing is decided
 * per block from its own type AND the block before it — a block that follows a
 * clause label sits tight under it (`mt-1.5`), regardless of its type.
 *
 *   heading L1 (الباب)   mt-10  centred, xl bold
 *   heading L2 (الفصل)   mt-8   start-side accent, xl bold
 *   heading L3           mt-6   lg bold
 *   label («٣/٧٦»)       mt-7   lg bold primary, tabular numerals
 *   list (أ- / 1-)       mt-3   marker column + hanging text, items 8px apart
 *   para                 mt-3.5
 *   repealed («ملغاة»)   mt-2   muted italic
 *   table (TBL_…)        mt-4   ChunkTable — its own scroll box, never the page
 *
 * `tone="lead"` is the AI summary lead: same structure, secondary text colour.
 * Server component.
 */
export function LegalBlocks({
  blocks,
  tone = "body",
  className,
}: {
  blocks: LegalBlock[];
  tone?: "body" | "lead";
  className?: string;
}) {
  return (
    <div
      className={cn(
        "break-words text-read",
        tone === "lead" ? "text-text-secondary" : "text-foreground",
        className,
      )}
    >
      {blocks.map((block, index) => {
        const prev = index > 0 ? blocks[index - 1] : null;
        const gap =
          index === 0
            ? "mt-0"
            : prev?.type === "label"
              ? "mt-1.5"
              : block.type === "heading"
                ? block.level === 1
                  ? "mt-10"
                  : block.level === 2
                    ? "mt-8"
                    : "mt-6"
                : GAP[block.type];

        switch (block.type) {
          case "heading":
            // Styled visual sub-header (not a semantic <h#>) so the body keeps
            // the page's heading outline unchanged — presentation only.
            return (
              <p
                key={index}
                className={cn(
                  gap,
                  "font-bold leading-snug text-foreground",
                  block.level === 1 && "text-center text-xl",
                  block.level === 2 &&
                    "border-s-[3px] border-primary/40 ps-3 text-xl",
                  block.level === 3 && "text-lg",
                )}
              >
                {renderInline(block.text)}
              </p>
            );
          case "label":
            return (
              <p
                key={index}
                className={cn(
                  gap,
                  "text-lg font-bold leading-snug tabular-nums text-primary",
                )}
              >
                {renderInline(block.text)}
              </p>
            );
          case "list":
            return (
              <ul key={index} role="list" className={cn(gap, "list-none space-y-2")}>
                {block.items.map((item, itemIndex) => (
                  <li key={itemIndex} className="flex gap-3">
                    <span className="min-w-[1.5em] shrink-0 font-semibold tabular-nums text-text-secondary">
                      {item.marker}
                    </span>
                    <span className="min-w-0 flex-1 whitespace-pre-line">
                      {renderInline(item.text)}
                    </span>
                  </li>
                ))}
              </ul>
            );
          case "repealed":
            return (
              <p key={index} className={cn(gap, "text-base italic text-text-muted")}>
                {block.text}
              </p>
            );
          case "table":
            // The gap rides a wrapper so `ChunkTable` stays spacing-agnostic —
            // the مراجع popup renders the same component in a different rhythm.
            return (
              <div key={index} className={gap}>
                <ChunkTable html={block.html} />
              </div>
            );
          default:
            return (
              <p key={index} className={cn(gap, "whitespace-pre-line")}>
                {renderInline(block.text)}
              </p>
            );
        }
      })}
    </div>
  );
}

const GAP: Record<Exclude<LegalBlock["type"], "heading">, string> = {
  label: "mt-7",
  list: "mt-3",
  para: "mt-3.5",
  repealed: "mt-2",
  table: "mt-4",
};
