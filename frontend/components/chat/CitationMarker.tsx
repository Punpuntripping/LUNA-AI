"use client";

import { cn } from "@/lib/utils";

interface CitationMarkerProps {
  n: number;
  onClick?: (n: number) => void;
}

/**
 * Inline clickable [n] citation marker rendered inside assistant markdown.
 *
 * The marker is forced LTR with isolated bidi so an RTL paragraph keeps the
 * brackets attached to the number even when surrounded by Arabic glyphs.
 * Clicking opens the associated agent_search artifact in the workspace pane
 * and scrolls the matching reference card into view (handled by the parent
 * via ``onClick``). When ``onClick`` is omitted (e.g. message has no
 * ``artifact_ids``) the marker renders muted and non-interactive.
 */
export function CitationMarker({ n, onClick }: CitationMarkerProps) {
  return (
    <button
      type="button"
      dir="ltr"
      style={{ unicodeBidi: "isolate" }}
      onClick={() => onClick?.(n)}
      aria-label={`فتح المرجع رقم ${n}`}
      disabled={!onClick}
      className={cn(
        "inline-flex items-center justify-center align-baseline",
        "mx-0.5 px-1 min-w-[1.4rem] h-[1.25rem] rounded",
        "text-[11px] font-semibold tabular-nums",
        "bg-primary/10 text-primary hover:bg-primary/20 transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50",
        !onClick && "cursor-default opacity-70 hover:bg-primary/10"
      )}
    >
      [{n}]
    </button>
  );
}
