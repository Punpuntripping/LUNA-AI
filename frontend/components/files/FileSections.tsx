"use client";

import {
  memo,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  ChevronDown,
  ChevronLeft,
  ChevronsDownUp,
  ChevronsUpDown,
  Files,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
import {
  filterSectionsByOwner,
  formatSectionPages,
  groupSectionsByDocument,
  sectionBroughtBy,
  sectionOwnerGroups,
  sectionOwnerLabel,
  sectionOwnerSide,
  stripMarkdownImages,
  type FileSection,
  type SectionOwnerSide,
} from "@/lib/contracts";
import { FILES_COPY } from "./copy";

interface FileSectionsProps {
  sections: FileSection[];
  documents: { name: string }[];
}

/**
 * A file read as an outline (plan §3.3): one row per section in document
 * order, titled by `what`, with «ص 3–5» when the section has page numbers.
 * Rows start collapsed; a row opens to the section's ORIGINAL text, exactly as
 * stored (never re-sliced from `content_md` — joined sections are not a slice).
 *
 * Sized for the worst file on record — 318 sections, one of them 31,886
 * characters. A collapsed row renders no text at all. A row the reader opens
 * renders its text at once; rows opened together by «توسيع الكل» render their
 * markdown only as they come near the screen (`SectionBody`), so a 318-row
 * file mounts what is visible and fills the rest in as you scroll. Rows are
 * memoised: opening one re-renders that row only.
 */
export function FileSections({ sections, documents }: FileSectionsProps) {
  const idBase = useId();
  const [expanded, setExpanded] = useState<ReadonlySet<number>>(() => new Set());
  // Rows opened one by one render their text immediately; the rest of an
  // «توسيع الكل» waits for the screen.
  const [openedByHand, setOpenedByHand] = useState<ReadonlySet<number>>(() => new Set());

  // seg-v5.2: whose words each section carries. The filter appears only when
  // the file has sections from at least two owner classes.
  const ownerGroups = useMemo(() => sectionOwnerGroups(sections), [sections]);
  const [ownerKey, setOwnerKey] = useState<string | null>(null);
  const activeOwner =
    ownerKey && ownerGroups.some((g) => g.key === ownerKey) ? ownerKey : null;
  const visible = useMemo(
    () => filterSectionsByOwner(sections, activeOwner),
    [sections, activeOwner],
  );

  const runs = useMemo(
    () => groupSectionsByDocument(visible, documents),
    [visible, documents],
  );
  const allExpanded = visible.length > 0 && visible.every((s) => expanded.has(s.seq));

  const toggle = useCallback((seq: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(seq)) next.delete(seq);
      else next.add(seq);
      return next;
    });
    setOpenedByHand((prev) => {
      if (prev.has(seq)) return prev;
      const next = new Set(prev);
      next.add(seq);
      return next;
    });
  }, []);

  const toggleAll = () => {
    if (allExpanded) {
      setExpanded(new Set());
      setOpenedByHand(new Set());
    } else {
      setExpanded(new Set(visible.map((s) => s.seq)));
    }
  };

  return (
    <section aria-labelledby={`${idBase}-title`} className="mt-6">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 id={`${idBase}-title`} className="text-sm font-semibold text-foreground">
          {FILES_COPY.sections}
          <span className="ms-1.5 text-xs font-normal tabular-nums text-muted-foreground">
            {visible.length}
          </span>
        </h2>
        {visible.length > 1 && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={toggleAll}
            className="h-7 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground max-md:h-9"
          >
            {allExpanded ? (
              <ChevronsDownUp className="h-3.5 w-3.5" aria-hidden="true" />
            ) : (
              <ChevronsUpDown className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            {allExpanded ? FILES_COPY.collapseAll : FILES_COPY.expandAll}
          </Button>
        )}
      </div>

      {ownerGroups.length > 0 && (
        <div
          role="group"
          aria-label={FILES_COPY.ownerFilterLabel}
          className="-mx-1 mb-2.5 flex gap-1 overflow-x-auto px-1 pb-1"
        >
          <OwnerChip
            label={FILES_COPY.allChip}
            count={sections.length}
            active={activeOwner === null}
            onClick={() => setOwnerKey(null)}
          />
          {ownerGroups.map((group) => (
            <OwnerChip
              key={group.key}
              label={group.label}
              count={group.count}
              active={activeOwner === group.key}
              onClick={() => setOwnerKey(group.key)}
            />
          ))}
        </div>
      )}

      <div className="space-y-4">
        {runs.map((run, runIndex) => (
          <div key={`${run.documentIndex ?? "x"}-${runIndex}`}>
            {run.heading && (
              <h3 className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
                <Files className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                <span className="min-w-0 truncate" title={run.heading}>
                  {run.heading}
                </span>
              </h3>
            )}
            <ul className="divide-y divide-border/60 overflow-hidden rounded-lg border border-border/60 bg-card">
              {run.sections.map((section) => (
                <SectionRow
                  key={section.seq}
                  section={section}
                  expanded={expanded.has(section.seq)}
                  eager={openedByHand.has(section.seq)}
                  onToggle={toggle}
                  idBase={idBase}
                />
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}

const SectionRow = memo(function SectionRow({
  section,
  expanded,
  eager,
  onToggle,
  idBase,
}: {
  section: FileSection;
  expanded: boolean;
  /** Opened by the reader (render now) rather than by «توسيع الكل». */
  eager: boolean;
  onToggle: (seq: number) => void;
  idBase: string;
}) {
  const headerId = `${idBase}-h${section.seq}`;
  const panelId = `${idBase}-p${section.seq}`;
  const pages = formatSectionPages(section.page_from, section.page_to);
  const title = section.what.trim() || `القسم ${section.seq}`;
  const owner = sectionOwnerLabel(section);
  const broughtBy = sectionBroughtBy(section);

  return (
    <li>
      <button
        type="button"
        id={headerId}
        aria-expanded={expanded}
        aria-controls={panelId}
        onClick={() => onToggle(section.seq)}
        className={cn(
          "flex w-full items-start gap-2 px-3 py-2.5 text-start transition-colors max-md:py-3",
          "hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
          expanded && "bg-accent/30",
        )}
      >
        {expanded ? (
          <ChevronDown className="mt-1 h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        ) : (
          <ChevronLeft className="mt-1 h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        )}
        <span className="min-w-0 flex-1">
          <span className="block text-sm leading-6 text-foreground">{title}</span>
          {(owner || broughtBy) && (
            <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs leading-5">
              {owner && (
                <span
                  className={cn(
                    "inline-flex max-w-full items-center rounded-full px-2 py-0.5 font-medium",
                    OWNER_SIDE_TAG[sectionOwnerSide(section.owner_class)],
                  )}
                >
                  <span className="min-w-0 truncate">{owner}</span>
                </span>
              )}
              {broughtBy && (
                <span className="text-muted-foreground">
                  {FILES_COPY.broughtBy}: {broughtBy}
                </span>
              )}
            </span>
          )}
        </span>
        {section.is_metadata && (
          <span className="mt-0.5 shrink-0 rounded-full border border-border/70 bg-muted/50 px-2 py-0.5 text-xs leading-4 text-muted-foreground">
            {FILES_COPY.metadataTag}
          </span>
        )}
        {pages && (
          <span className="mt-0.5 shrink-0 whitespace-nowrap text-xs leading-5 tabular-nums text-muted-foreground">
            {pages}
          </span>
        )}
      </button>
      {expanded && (
        <div
          id={panelId}
          role="region"
          aria-labelledby={headerId}
          className="border-t border-border/60 bg-muted/20 px-4 py-3"
        >
          <SectionBody content={section.content_md} eager={eager} />
        </div>
      )}
    </li>
  );
});

/**
 * The owner tag's tint — the theme's existing families only (the ones the
 * file-type tiles use): the claimant's side, the respondent's side, whoever
 * decides or issues, and an outline for everyone else.
 */
const OWNER_SIDE_TAG: Record<SectionOwnerSide, string> = {
  claimant: "bg-primary/10 text-primary",
  respondent: "bg-info text-info-fg",
  official: "bg-muted text-text-muted",
  other: "border border-border bg-card text-muted-foreground",
};

function OwnerChip({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  // The same chip as the type filter on /files.
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors max-md:py-1.5",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active
          ? "border-primary/60 bg-primary/5 text-foreground"
          : "border-transparent text-muted-foreground hover:bg-accent/50 hover:text-foreground",
      )}
    >
      <span>{label}</span>
      <span className={cn("tabular-nums", active ? "text-primary" : "text-muted-foreground/80")}>
        {count}
      </span>
    </button>
  );
}

/** Approximate rendered height of `chars` of Arabic text, for the placeholder. */
function estimatedHeight(chars: number): number {
  return Math.max(28, Math.ceil(chars / 70) * 28);
}

/** How far outside the screen a section's text is already rendered. */
const PRERENDER_MARGIN_PX = 800;

/**
 * The section's original text through the same `MarkdownRenderer` the
 * extracted text has always used, with OCR image placeholders
 * (`![img-3.jpeg](img-3.jpeg)`) dropped — they point at nothing.
 *
 * `eager` (a row the reader opened): rendered in the same frame. Otherwise (a
 * row opened by «توسيع الكل») a placeholder of about the right height stands
 * in until an IntersectionObserver reports it near the screen — no layout is
 * read synchronously, so opening 318 rows does not force 318 layouts.
 */
function SectionBody({ content, eager }: { content: string; eager: boolean }) {
  const text = useMemo(() => stripMarkdownImages(content ?? "").trim(), [content]);
  const ref = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(eager);

  useEffect(() => {
    if (visible) return;
    const node = ref.current;
    if (!node) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: `${PRERENDER_MARGIN_PX}px 0px` },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [visible]);

  if (!text) {
    return <p className="text-sm text-muted-foreground">{FILES_COPY.emptySection}</p>;
  }

  return (
    <div
      ref={ref}
      style={visible ? undefined : { minHeight: estimatedHeight(text.length) }}
    >
      {visible ? <MarkdownRenderer content={text} /> : null}
    </div>
  );
}
