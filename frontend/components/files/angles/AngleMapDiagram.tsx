"use client";

import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import {
  ANGLE_KIND_LABELS,
  ANGLE_KINDS,
  angleMapRows,
  coerceAngleKind,
  type AngleKind,
  type AngleMap,
} from "@/lib/contracts";
import { ANGLE_KIND_STYLE } from "./angle-style";
import { ANGLES_COPY } from "./copy";

interface AngleMapDiagramProps {
  map: AngleMap;
  /** The angle whose row is highlighted, or null. */
  selected: number | null;
  /** A row's title was tapped. */
  onSelect: (seq: number) => void;
  /** A section number was tapped. */
  onOpenSection: (ref: string) => void;
  /** The file being viewed — its column is marked «هذا الملف». */
  currentItemId?: string;
}

const MIN_COLUMN = 84; // px; below this the documents scroll sideways
/** Section numbers shown per cell; the rest are «+n» (tap → the angle's card lists them all). */
const CELL_MAX = 6;

/**
 * The angle map drawn as the owner's mock-up (2026-11-23: "the other option,
 * simplify it"): the message's documents are the columns, each angle is a row.
 * A row's cells hold the numbers of the angle's sections in each document, and
 * one straight line in the angle's colour runs along the row from the first
 * document it reaches to the last — so an angle that ties documents together is
 * one line across them. Tap a number for the section's text, a row's title for
 * its card.
 */
export function AngleMapDiagram({ map, selected, onSelect, onOpenSection, currentItemId }: AngleMapDiagramProps) {
  const rows = useMemo(() => angleMapRows(map), [map]);
  const docs = useMemo(() => [...map.documents].sort((a, b) => a.index - b.index), [map.documents]);
  const single = docs.length <= 1;

  const scrollerRef = useRef<HTMLDivElement>(null);
  const [available, setAvailable] = useState(0);
  useLayoutEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    setAvailable(el.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => setAvailable(el.clientWidth));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const labelWidth = available > 0 && available < 420 ? 118 : 156;
  const columnWidth = single
    ? Math.max(available - labelWidth, MIN_COLUMN)
    : Math.max(MIN_COLUMN, Math.floor((Math.max(available, 1) - labelWidth) / docs.length));
  const width = labelWidth + columnWidth * Math.max(docs.length, 1);
  const overflowing = available > 0 && width > available + 1;

  return (
    <section aria-label={ANGLES_COPY.map}>
      {overflowing && <p className="mb-1 text-xs text-muted-foreground">{ANGLES_COPY.scrollHint} ←</p>}
      <div ref={scrollerRef} className="overflow-x-auto rounded-lg border border-border bg-card [scrollbar-width:thin]">
        <div style={{ width: available > 0 ? width : "100%" }}>
          {!single && (
            <div className="flex items-end border-b border-border bg-muted/30">
              <div style={{ width: labelWidth }} className="shrink-0" />
              {docs.map((d) => {
                const current = d.item_id === currentItemId;
                return (
                  <div key={d.index} style={{ width: columnWidth }} className="shrink-0 px-1.5 py-2 text-center">
                    <span
                      className={cn(
                        "mx-auto mb-1 flex h-5 w-5 items-center justify-center rounded text-xs font-semibold tabular-nums",
                        current ? "bg-primary text-primary-foreground" : "bg-muted text-foreground",
                      )}
                    >
                      {d.index}
                    </span>
                    <span className="line-clamp-2 text-xs leading-5 text-foreground" title={d.title}>
                      {d.title}
                    </span>
                    {current && <span className="text-xs leading-5 text-primary">{ANGLES_COPY.thisFile}</span>}
                  </div>
                );
              })}
            </div>
          )}

          {rows.map((row, i) => {
            const kind = coerceAngleKind(row.angle.kind);
            const style = ANGLE_KIND_STYLE[kind];
            const active = selected === row.angle.seq;
            const spans = !single && row.first != null && row.last != null && row.last > row.first;
            return (
              <div
                key={row.angle.seq}
                className={cn(
                  "relative flex items-stretch transition-colors",
                  i > 0 && "border-t border-border/60",
                  active && "bg-accent/50",
                )}
              >
                {spans && (
                  <span
                    aria-hidden="true"
                    className={cn("pointer-events-none absolute top-1/2 h-0.5 -translate-y-1/2 rounded-full", style.line)}
                    style={{
                      insetInlineStart: labelWidth + row.first! * columnWidth + columnWidth / 2,
                      width: (row.last! - row.first!) * columnWidth,
                    }}
                  />
                )}
                <button
                  type="button"
                  onClick={() => onSelect(row.angle.seq)}
                  aria-pressed={active}
                  style={{ width: labelWidth }}
                  className="flex shrink-0 items-start gap-1.5 px-2 py-2 text-start hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                >
                  <AngleNumber seq={row.angle.seq} kind={kind} className="mt-0.5" />
                  <span className="line-clamp-2 text-xs leading-5 text-foreground">{row.angle.title}</span>
                </button>
                {row.cells.map((cell) => (
                  <div
                    key={cell.document}
                    style={{ width: columnWidth }}
                    className={cn(
                      "relative z-10 flex shrink-0 flex-wrap content-center items-center gap-1 px-1.5 py-2",
                      single ? "justify-start" : "justify-center",
                    )}
                  >
                    {cell.sections.length > CELL_MAX + 1 && (
                      <button
                        type="button"
                        onClick={() => onSelect(row.angle.seq)}
                        dir="ltr"
                        className="order-last rounded-md px-1 text-xs tabular-nums leading-6 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        +{cell.sections.length - CELL_MAX}
                      </button>
                    )}
                    {(cell.sections.length > CELL_MAX + 1 ? cell.sections.slice(0, CELL_MAX) : cell.sections).map((s) => (
                      <button
                        key={s.ref}
                        type="button"
                        title={s.what}
                        aria-label={`${ANGLES_COPY.sectionLabel(s.seq)} — ${s.what}`}
                        onClick={() => onOpenSection(s.ref)}
                        className={cn(
                          "min-w-7 rounded-md border bg-card px-1.5 text-xs font-semibold tabular-nums leading-6 transition-colors hover:bg-accent",
                          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                          style.chip,
                        )}
                      >
                        {s.seq}
                      </button>
                    ))}
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs leading-5 text-muted-foreground">
        {ANGLE_KINDS.map((k) => (
          <span key={k} className="inline-flex items-center gap-1">
            <span className={cn("h-0.5 w-4 rounded-full", ANGLE_KIND_STYLE[k].line)} aria-hidden="true" />
            {ANGLE_KIND_LABELS[k]}
          </span>
        ))}
        <span>{single ? ANGLES_COPY.mapHintSingle : ANGLES_COPY.mapHint}</span>
      </div>
    </section>
  );
}

/** The angle's number in a filled circle of its kind's colour. */
export function AngleNumber({ seq, kind, className }: { seq: number; kind: AngleKind; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex h-5 min-w-5 shrink-0 items-center justify-center rounded-full px-1 text-xs font-bold tabular-nums leading-none",
        ANGLE_KIND_STYLE[kind].solid,
        className,
      )}
    >
      {seq}
    </span>
  );
}
