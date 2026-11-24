"use client";

import { memo, useState } from "react";
import { AlertTriangle, MapPinned } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  ANGLE_KIND_LABELS,
  ANGLE_SIDE_LABELS,
  ANGLE_STRENGTH_LABELS,
  angleDocumentLabel,
  coerceAngleKind,
  coerceAngleStrength,
  isCrossDocumentAngle,
  type AngleMap,
  type AngleMapAngle,
  type AngleSectionRef,
} from "@/lib/contracts";
import { ANGLE_KIND_STYLE, ANGLE_SIDE_TAG } from "./angle-style";
import { AngleNumber } from "./AngleMapDiagram";
import { ANGLES_COPY } from "./copy";

/** Sections listed before «عرض الكل». */
const SECTIONS_SHOWN = 5;

interface AngleCardProps {
  map: AngleMap;
  angle: AngleMapAngle;
  selected: boolean;
  /** The map has 2+ documents: section rows name their document. */
  multiDocument: boolean;
  onShowOnMap: (seq: number) => void;
  onOpenSection: (ref: string) => void;
}

/**
 * One angle, as the preview page shows it (angle_map_preview.html): number,
 * kind, strength, «يربط بين المستندات», «قد يكون خطأ قراءة», the party it
 * serves; title, argument, «سؤال البحث», «ما يضعفه», and the sections it rests
 * on — each opens its text. The card of the angle picked on the map is ringed.
 */
export const AngleCard = memo(function AngleCard({
  map,
  angle,
  selected,
  multiDocument,
  onShowOnMap,
  onOpenSection,
}: AngleCardProps) {
  const kind = coerceAngleKind(angle.kind);
  const strength = coerceAngleStrength(angle.strength);
  const style = ANGLE_KIND_STYLE[kind];

  return (
    <article
      id={`angle-${map.map_id}-${angle.seq}`}
      aria-label={angle.title}
      className={cn(
        "scroll-mt-4 rounded-lg border border-s-4 bg-card px-3 py-2.5 transition-shadow",
        style.edge,
        selected && cn("ring-2", style.ring),
      )}
    >
      <div className="flex flex-wrap items-center gap-1.5 text-xs leading-5">
        <AngleNumber seq={angle.seq} kind={kind} />
        <span className={cn("rounded-full px-2 font-semibold", style.soft)}>{ANGLE_KIND_LABELS[kind]}</span>
        <span
          className={cn(
            "rounded-full border border-border px-2 text-muted-foreground",
            strength === "strong" && "font-semibold text-foreground",
          )}
          title={ANGLES_COPY.strength}
        >
          {ANGLE_STRENGTH_LABELS[strength]}
        </span>
        {isCrossDocumentAngle(angle) && (
          <span className="rounded-full border border-info-fg/50 px-2 font-medium text-info-fg">
            {ANGLES_COPY.crossDocument}
          </span>
        )}
        {angle.ocr_suspect && (
          <span
            className="inline-flex items-center gap-1 rounded-full bg-warning px-2 font-medium text-warning-fg"
            title={ANGLES_COPY.ocrSuspectHint}
          >
            <AlertTriangle className="h-3 w-3" aria-hidden="true" />
            {ANGLES_COPY.ocrSuspect}
          </span>
        )}
        {angle.for_party && (
          <span className="text-muted-foreground">
            {ANGLES_COPY.forParty}: {angle.for_party}
          </span>
        )}
        <button
          type="button"
          onClick={() => onShowOnMap(angle.seq)}
          aria-pressed={selected}
          className={cn(
            "ms-auto inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-muted-foreground transition-colors max-md:py-1.5",
            "hover:bg-accent/50 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          )}
        >
          <MapPinned className="h-3.5 w-3.5" aria-hidden="true" />
          {ANGLES_COPY.showOnMap}
        </button>
      </div>

      <h4 className="mt-2 text-sm font-semibold leading-6 text-foreground">{angle.title}</h4>
      {angle.argument && <p className="mt-1 text-sm leading-7 text-foreground/90">{angle.argument}</p>}

      {angle.legal_question && (
        <div className="mt-2 rounded-md bg-muted/60 px-2.5 py-1.5 text-sm leading-7">
          <span className="font-semibold">{ANGLES_COPY.legalQuestion}: </span>
          {angle.legal_question}
        </div>
      )}

      {angle.weakness && (
        <p className="mt-2 text-sm leading-7 text-muted-foreground">
          <span className="font-semibold">{ANGLES_COPY.weakness}: </span>
          {angle.weakness}
        </p>
      )}

      <SectionList
        map={map}
        refs={angle.sections}
        multiDocument={multiDocument}
        onOpenSection={onOpenSection}
        title={ANGLES_COPY.sections}
      />
    </article>
  );
});

/** The sections an angle or a gap rests on — one row each, opening its text. */
export function SectionList({
  map,
  refs,
  multiDocument,
  onOpenSection,
  title,
}: {
  map: AngleMap;
  refs: AngleSectionRef[];
  multiDocument: boolean;
  onOpenSection: (ref: string) => void;
  title?: string;
}) {
  const [all, setAll] = useState(false);
  const known = refs.filter((r) => map.sections.some((s) => s.ref === r.ref));
  if (known.length === 0) return null;
  const shown = all ? known : known.slice(0, SECTIONS_SHOWN);

  return (
    <div className="mt-2.5">
      {title && <p className="mb-1 text-xs font-semibold text-muted-foreground">{title}</p>}
      <ul className="divide-y divide-border/60 overflow-hidden rounded-md border border-border/60">
        {shown.map((r) => (
          <li key={r.ref}>
            <SectionRow map={map} sectionRef={r} multiDocument={multiDocument} onOpen={onOpenSection} />
          </li>
        ))}
      </ul>
      {known.length > SECTIONS_SHOWN && (
        <button
          type="button"
          onClick={() => setAll((v) => !v)}
          className="mt-1 rounded px-1 text-xs text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {all ? "عرض أقل" : `عرض الكل (${known.length})`}
        </button>
      )}
    </div>
  );
}

function SectionRow({
  map,
  sectionRef,
  multiDocument,
  onOpen,
}: {
  map: AngleMap;
  sectionRef: AngleSectionRef;
  multiDocument: boolean;
  onOpen: (ref: string) => void;
}) {
  const section = map.sections.find((s) => s.ref === sectionRef.ref);
  if (!section) return null;
  const side = sectionRef.side;
  return (
    <button
      type="button"
      onClick={() => onOpen(section.ref)}
      className="flex w-full items-start gap-2 px-2.5 py-1.5 text-start text-xs leading-5 transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring max-md:py-2"
    >
      {side ? (
        <span className={cn("mt-0.5 shrink-0 rounded-full px-1.5 font-medium", ANGLE_SIDE_TAG[side])}>
          {ANGLE_SIDE_LABELS[side]}
        </span>
      ) : null}
      <span className="min-w-0 flex-1">
        <span className="tabular-nums text-muted-foreground">
          {multiDocument && `${angleDocumentLabel(section.document)} · `}
          {ANGLES_COPY.sectionLabel(section.seq)}
        </span>
        <span className="text-muted-foreground"> — </span>
        <span className="text-foreground">{section.what}</span>
        {section.owner && <span className="text-muted-foreground"> · {section.owner}</span>}
      </span>
    </button>
  );
}
