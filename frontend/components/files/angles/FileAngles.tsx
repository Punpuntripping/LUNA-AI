"use client";

import { useCallback, useId, useMemo, useRef, useState } from "react";
import { AlertTriangle, ChevronDown, ChevronLeft } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  ANGLE_CONFIDENCE_LABELS,
  ANGLE_GAP_LABELS,
  angleClientKnown,
  angleDocumentLabel,
  formatFileDate,
  pickAngleMap,
  type AngleClientConfidence,
  type AngleGapKind,
  type AngleMap,
} from "@/lib/contracts";
import { ANGLE_GAP_STYLE } from "./angle-style";
import { AngleMapDiagram } from "./AngleMapDiagram";
import { AngleCard, SectionList } from "./AngleCard";
import { AngleSectionDialog } from "./AngleSectionDialog";
import { ANGLES_COPY } from "./copy";

interface FileAnglesProps {
  /** Every map the file is in (one per conversation it was sent in). */
  maps: AngleMap[];
  /** The viewed file — its column is marked «هذا الملف». */
  itemId: string;
  /** Shown first when one of the maps comes from it (the pane's conversation / the row's own). */
  conversationId?: string | null;
}

/**
 * The «الأوجه» tab of an opened file (owner, 2026-11-23): which conversation's
 * map (only when there are 2+), the client's side, the drawn map (a row per
 * angle), every angle as a card, then the gaps and the files left out.
 */
export function FileAngles({ maps, itemId, conversationId }: FileAnglesProps) {
  const first = useMemo(() => pickAngleMap(maps, conversationId), [maps, conversationId]);
  const [mapId, setMapId] = useState<string | null>(first?.map_id ?? null);
  const map = maps.find((m) => m.map_id === mapId) ?? first;
  const [selected, setSelected] = useState<number | null>(null);
  const [openRef, setOpenRef] = useState<string | null>(null);
  const mapRef = useRef<HTMLDivElement>(null);

  const switchMap = useCallback((id: string) => {
    setMapId(id);
    setSelected(null);
    setOpenRef(null);
  }, []);

  // From a card: highlight its row and bring the map back into view.
  const showOnMap = useCallback((seq: number) => {
    setSelected(seq);
    const el = mapRef.current;
    if (el && el.getBoundingClientRect().top < 0) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  // From the map: highlight the row and go to the angle's card.
  const showCard = useCallback(
    (seq: number) => {
      setSelected(seq);
      if (!map) return;
      document.getElementById(`angle-${map.map_id}-${seq}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
    },
    [map],
  );

  if (!map) return null;
  const multiDocument = map.documents.length > 1;

  return (
    <div className="space-y-4">
      <p className="text-xs leading-6 text-muted-foreground">{ANGLES_COPY.intro}</p>

      {maps.length > 1 && <MapPicker maps={maps} value={map.map_id} onChange={switchMap} />}

      <ClientBox map={map} />

      <div ref={mapRef} className="scroll-mt-4">
        <AngleMapDiagram
          map={map}
          selected={selected}
          onSelect={showCard}
          onOpenSection={setOpenRef}
          currentItemId={itemId}
        />
      </div>

      <section aria-label={ANGLES_COPY.angles} className="space-y-3">
        <h3 className="text-sm font-semibold text-foreground">
          {ANGLES_COPY.angles}
          <span className="ms-1.5 text-xs font-normal tabular-nums text-muted-foreground">{map.angles.length}</span>
        </h3>
        {map.angles.map((angle) => (
          <AngleCard
            key={`${map.map_id}-${angle.seq}`}
            map={map}
            angle={angle}
            selected={selected === angle.seq}
            multiDocument={multiDocument}
            onShowOnMap={showOnMap}
            onOpenSection={setOpenRef}
          />
        ))}
      </section>

      {map.gaps.length > 0 && <Gaps map={map} multiDocument={multiDocument} onOpenSection={setOpenRef} />}

      {map.left_out.length > 0 && (
        <div className="rounded-md border border-dashed border-border px-3 py-2 text-xs leading-6 text-muted-foreground">
          <p className="font-semibold">{ANGLES_COPY.leftOut}</p>
          <ul className="list-inside list-disc">
            {map.left_out.map((f) => (
              <li key={f.item_id}>{f.title}</li>
            ))}
          </ul>
        </div>
      )}

      <AngleSectionDialog
        map={map}
        sectionRef={openRef}
        onClose={() => setOpenRef(null)}
        onSelectAngle={showOnMap}
      />
    </div>
  );
}

/** «من محادثة: …» — only when the file is in 2+ maps. */
function MapPicker({
  maps,
  value,
  onChange,
}: {
  maps: AngleMap[];
  value: string;
  onChange: (id: string) => void;
}) {
  const id = useId();
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <label htmlFor={id} className="text-muted-foreground">
        {ANGLES_COPY.fromConversation}:
      </label>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-8 max-w-full rounded-md border border-border bg-card px-2 text-xs text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring max-md:h-9"
      >
        {maps.map((m) => (
          <option key={m.map_id} value={m.map_id}>
            {(m.conversation_title || "محادثة") + " — " + formatFileDate(m.created_at)}
          </option>
        ))}
      </select>
    </div>
  );
}

const CONFIDENCE_STYLE: Record<AngleClientConfidence, string> = {
  clear: "border-success-fg/50 text-success-fg",
  likely: "border-warning-fg/50 text-warning-fg",
  unclear: "border-error-fg/50 text-error-fg",
};

/** «الموكّل: …» with its confidence; how it was told (folded); its name in other documents. */
function ClientBox({ map }: { map: AngleMap }) {
  const [open, setOpen] = useState(false);
  const { client } = map;
  const known = angleClientKnown(client);
  const confidence: AngleClientConfidence =
    client.confidence === "clear" || client.confidence === "likely" ? client.confidence : "unclear";
  const others = client.by_document.filter((d) => map.documents.some((doc) => doc.index === d.document));

  return (
    <div className="rounded-lg border border-border bg-card px-3 py-2 text-sm leading-7">
      {known ? (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="font-semibold">{ANGLES_COPY.client}:</span>
          <span>{client.party || client.side}</span>
          <span className={cn("rounded border px-1.5 text-xs leading-5", CONFIDENCE_STYLE[confidence])}>
            {ANGLE_CONFIDENCE_LABELS[confidence]}
          </span>
        </div>
      ) : (
        <p className="flex items-start gap-1.5 text-warning-fg">
          <AlertTriangle className="mt-1.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {ANGLES_COPY.clientUnknown}
        </p>
      )}

      {others.length > 0 && map.documents.length > 1 && (
        <div className="mt-1 text-xs leading-6 text-muted-foreground">
          <span className="font-semibold">{ANGLES_COPY.inOtherDocuments}: </span>
          {others.map((d, i) => (
            <span key={d.document}>
              {i > 0 && "، "}
              {angleDocumentLabel(d.document)}: {d.party}
            </span>
          ))}
        </div>
      )}

      {client.basis && (
        <div className="mt-1">
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
            className="inline-flex items-center gap-1 rounded text-xs text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronLeft className="h-3.5 w-3.5" />}
            {ANGLES_COPY.clientBasis}
          </button>
          {open && <p className="mt-1 text-xs leading-6 text-muted-foreground">{client.basis}</p>}
        </div>
      )}
    </div>
  );
}

function Gaps({
  map,
  multiDocument,
  onOpenSection,
}: {
  map: AngleMap;
  multiDocument: boolean;
  onOpenSection: (ref: string) => void;
}) {
  const [open, setOpen] = useState(true);
  const id = useId();
  return (
    <section aria-labelledby={id} className="rounded-lg border border-border bg-card px-3 py-2">
      <button
        type="button"
        id={id}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 text-start text-sm font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {open ? <ChevronDown className="h-4 w-4 text-muted-foreground" /> : <ChevronLeft className="h-4 w-4 text-muted-foreground" />}
        {ANGLES_COPY.gaps}
        <span className="text-xs font-normal tabular-nums text-muted-foreground">{map.gaps.length}</span>
        <span className="ms-1 truncate text-xs font-normal text-muted-foreground max-sm:hidden">— {ANGLES_COPY.gapsHint}</span>
      </button>
      {open && (
        <ul className="mt-2 space-y-3">
          {map.gaps.map((gap, i) => {
            const kind: AngleGapKind =
              gap.kind === "contradiction" || gap.kind === "unanswered" ? gap.kind : "missing";
            return (
              <li key={i} className="text-sm leading-7">
                <span className={cn("me-1.5 text-xs font-bold", ANGLE_GAP_STYLE[kind])}>{ANGLE_GAP_LABELS[kind]}</span>
                {gap.issue}
                {gap.ocr_suspect && (
                  <span
                    className="ms-1.5 inline-flex items-center gap-1 rounded-full bg-warning px-2 text-xs font-medium leading-5 text-warning-fg"
                    title={ANGLES_COPY.ocrSuspectHint}
                  >
                    <AlertTriangle className="h-3 w-3" aria-hidden="true" />
                    {ANGLES_COPY.ocrSuspect}
                  </span>
                )}
                <SectionList
                  map={map}
                  refs={gap.sections}
                  multiDocument={multiDocument}
                  onOpenSection={onOpenSection}
                />
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
