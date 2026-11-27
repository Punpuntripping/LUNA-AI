"use client";

import { useCallback, useId, useMemo, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/stores/chat-store";
import { useConversationWorkspace } from "@/hooks/use-workspace";
import {
  ANGLE_KIND_LABELS,
  ANGLE_STRENGTH_LABELS,
  EXTENDED_ANGLE_STATES,
  EXTENDED_ANGLE_STATE_LABELS,
  EXTENDED_SYNTHESIS_AR,
  extendedAngleTitle,
  extendedBoard,
  extendedConfidenceAr,
  extendedMetaPartsAr,
  extendedOpponentTagAr,
  extendedReportByNumber,
  extendedReportLabelAr,
  extendedReportSideAr,
  extendedReportTitle,
  isOpponentAngle,
  type ExtendedAngleState,
  type ExtendedBoardRow,
  type ExtendedReport,
  type ExtendedSynthesis,
} from "@/lib/contracts";

/**
 * «بحث موسّع» — the merged answer's own sections, around the ordinary
 * `agent_search` body (AgentSearchViewer). The design is the owner-accepted
 * preview (merger_inputs_2026-11-24/build_merged_wi_page.py), built as drawn:
 *
 * - `ExtendedSynthesisLead` — above the answer: the «بحث موسّع» chip and the
 *   meta line, «نقاط القوة والضعف» (tally bar, legend, the two groups of
 *   collapsible angle cards), then the «الجواب» heading. The markdown body
 *   itself (citations, reference focus) stays ArtifactPreview's.
 * - `ExtendedSynthesisTrail` — below it: «ما حُسم بين البحوث», «ما لم يُحسم
 *   بعد», «أوجه لم تُبحث», «البحوث التي بُني عليها». A report card (and a
 *   «بحث n» chip) opens that child item in the pane through the chat store's
 *   `openWorkspaceItem`, like a workspace card.
 *
 * The references keep the viewer's ReferencePanel. Colours: the theme's status
 * families only, so light and dark come for free.
 */

/** Pill + tally/legend colour per scoreboard state. */
const STATE_STYLE: Record<ExtendedAngleState, { pill: string; bar: string }> = {
  supported: { pill: "bg-success text-success-fg", bar: "bg-success-fg" },
  answered: { pill: "bg-info text-info-fg", bar: "bg-info-fg" },
  open: { pill: "bg-warning text-warning-fg", bar: "bg-warning-fg" },
  against: { pill: "bg-error text-error-fg", bar: "bg-error-fg" },
  not_researched: { pill: "bg-surface-3 text-text-muted", bar: "bg-text-subtle" },
};

const PILL = "shrink-0 whitespace-nowrap rounded-full px-2.5 text-xs font-semibold leading-6";
const CHIP = "inline-flex items-center whitespace-nowrap rounded-full bg-pill px-2.5 text-xs leading-6 text-pill-fg";
const TAG = "rounded-full border border-border px-2 text-xs leading-5 text-muted-foreground";

// ---------------------------------------------------------------------------
// Above the answer
// ---------------------------------------------------------------------------

export function ExtendedSynthesisLead({
  data,
  refCount,
}: {
  data: ExtendedSynthesis;
  /** The item's reference count (the loaded list, else `metadata.ref_count`). */
  refCount: number | null;
}) {
  const meta = extendedMetaPartsAr(data, refCount);
  return (
    <div dir="rtl">
      <header className="grid gap-2 border-b border-border pb-4">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className={cn(CHIP, "bg-primary font-semibold text-primary-fg")}>
            {EXTENDED_SYNTHESIS_AR.chip}
          </span>
        </div>
        {meta.length > 0 && (
          <div className="flex flex-wrap gap-x-3.5 gap-y-1 text-sm tabular-nums text-muted-foreground">
            {meta.map((part) => (
              <span key={part}>{part}</span>
            ))}
          </div>
        )}
      </header>
      <Scoreboard data={data} />
      <h2 className="pb-3 pt-5 text-lg font-bold text-foreground">
        {EXTENDED_SYNTHESIS_AR.answer}
      </h2>
    </div>
  );
}

function Scoreboard({ data }: { data: ExtendedSynthesis }) {
  const board = useMemo(() => extendedBoard(data), [data]);
  if (board.rows.length === 0) return null;
  const present = EXTENDED_ANGLE_STATES.filter((k) => board.counts[k] > 0);
  return (
    <Block title={EXTENDED_SYNTHESIS_AR.board} count={`${board.rows.length} أوجه`} className="border-b">
      <div
        role="img"
        aria-label={EXTENDED_SYNTHESIS_AR.boardAria}
        className="flex h-2 overflow-hidden rounded-full bg-surface-3"
      >
        {present.map((k) => (
          <span key={k} className={cn("block", STATE_STYLE[k].bar)} style={{ flexGrow: board.counts[k] }} />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1.5 text-xs text-text-secondary">
        {present.map((k) => (
          <span key={k} className="inline-flex items-center gap-1 tabular-nums">
            <span className={cn("inline-block h-2.5 w-2.5 rounded-[3px]", STATE_STYLE[k].bar)} aria-hidden="true" />
            {EXTENDED_ANGLE_STATE_LABELS[k]} {board.counts[k]}
          </span>
        ))}
      </div>
      {/* Side by side only when the pane itself is wide (container query —
          the workspace pane is usually narrower than the viewport). */}
      <div className="[container-type:inline-size]">
        <div className="grid gap-3.5 [@container_(min-width:40rem)]:grid-cols-2">
          <AngleGroup title={EXTENDED_SYNTHESIS_AR.mine} rows={board.mine} clientSide={data.client_side} />
          <AngleGroup title={EXTENDED_SYNTHESIS_AR.theirs} rows={board.theirs} clientSide={data.client_side} />
        </div>
      </div>
    </Block>
  );
}

function AngleGroup({
  title,
  rows,
  clientSide,
}: {
  title: string;
  rows: ExtendedBoardRow[];
  clientSide: string;
}) {
  return (
    <div className="grid min-w-0 content-start gap-2">
      <h3 className="text-sm font-semibold text-muted-foreground">{title}</h3>
      {rows.length > 0 ? (
        rows.map((row) => <AngleCard key={row.angle.id} row={row} clientSide={clientSide} />)
      ) : (
        <p className="text-xs text-muted-foreground">{EXTENDED_SYNTHESIS_AR.none}</p>
      )}
    </div>
  );
}

function AngleCard({ row, clientSide }: { row: ExtendedBoardRow; clientSide: string }) {
  const { angle, status, note } = row;
  const tags: { text: string; strong?: boolean }[] = [];
  if (angle.kind) tags.push({ text: ANGLE_KIND_LABELS[angle.kind] });
  if (angle.strength) tags.push({ text: `${EXTENDED_SYNTHESIS_AR.strength}: ${ANGLE_STRENGTH_LABELS[angle.strength]}` });
  if (isOpponentAngle(angle, clientSide)) tags.push({ text: extendedOpponentTagAr(angle), strong: true });
  if (angle.ocr_suspect) tags.push({ text: EXTENDED_SYNTHESIS_AR.ocrSuspect });

  const details: [string, string][] = [];
  if (angle.argument) details.push([EXTENDED_SYNTHESIS_AR.argument, angle.argument]);
  if (angle.legal_question) details.push([EXTENDED_SYNTHESIS_AR.legalQuestion, angle.legal_question]);
  if (angle.weakness) details.push([EXTENDED_SYNTHESIS_AR.weakness, angle.weakness]);
  if (angle.sections.length) details.push([EXTENDED_SYNTHESIS_AR.sections, angle.sections.join("، ")]);

  const summary = (
    <>
      <div className="flex items-start gap-2">
        <span className={cn(PILL, STATE_STYLE[status].pill)}>{EXTENDED_ANGLE_STATE_LABELS[status]}</span>
        <span className="min-w-0 flex-1 text-base font-semibold leading-7 text-foreground">{angle.title}</span>
      </div>
      {note && <p className="text-sm leading-7 text-text-secondary">{note}</p>}
      {tags.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {tags.map((t) => (
            <span key={t.text} className={cn(TAG, t.strong && "border-border-strong text-text-secondary")}>
              {t.text}
            </span>
          ))}
        </div>
      )}
    </>
  );

  // An angle the metadata does not describe has nothing to unfold.
  if (details.length === 0) {
    return <div className="grid gap-1 rounded-lg border border-border bg-card px-3 py-2.5">{summary}</div>;
  }
  return (
    <details className="rounded-lg border border-border bg-card">
      <summary
        className={cn(
          "grid cursor-pointer list-none gap-1 rounded-lg px-3 py-2.5 [&::-webkit-details-marker]:hidden",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        )}
      >
        {summary}
      </summary>
      <div className="grid gap-1.5 border-t border-dashed border-border px-3 pb-3 pt-2.5 text-sm leading-7 text-text-secondary">
        <p className="text-xs text-text-subtle">{EXTENDED_SYNTHESIS_AR.analysis}</p>
        {details.map(([label, text]) => (
          <p key={label}>
            <span className="font-semibold text-foreground">{label}:</span> {text}
          </p>
        ))}
      </div>
    </details>
  );
}

// ---------------------------------------------------------------------------
// Below the answer
// ---------------------------------------------------------------------------

/**
 * Opens a child report in the pane — the same store action a workspace card
 * uses. A report whose item is known to be gone (the conversation's full item
 * list is loaded and lacks it) is not offered; while the list is loading, or
 * when it is truncated, the id is trusted.
 */
function useChildReportOpener(conversationId: string | null) {
  const openWorkspaceItem = useChatStore((s) => s.openWorkspaceItem);
  const { data: list } = useConversationWorkspace(conversationId ?? undefined);
  const known = useMemo(() => {
    if (!list || list.items.length < list.total) return null;
    return new Set(list.items.map((i) => i.item_id));
  }, [list]);
  const canOpen = useCallback(
    (itemId: string | null) => !!conversationId && !!itemId && (known === null || known.has(itemId)),
    [conversationId, known],
  );
  const open = useCallback(
    (itemId: string) => {
      if (conversationId) openWorkspaceItem(conversationId, itemId);
    },
    [conversationId, openWorkspaceItem],
  );
  return { canOpen, open };
}

export function ExtendedSynthesisTrail({
  data,
  conversationId,
}: {
  data: ExtendedSynthesis;
  conversationId: string | null;
}) {
  const { canOpen, open } = useChildReportOpener(conversationId);
  const opener = (report: ExtendedReport | undefined) =>
    report && canOpen(report.item_id) ? () => open(report.item_id as string) : undefined;

  if (!data.resolved.length && !data.gaps.length && !data.left_out.length && !data.reports.length) {
    return null;
  }
  return (
    <div dir="rtl" className="mt-6">
      {data.resolved.length > 0 && (
        <Block title={EXTENDED_SYNTHESIS_AR.resolved} count={data.resolved.length} className="border-t">
          <ul className="grid gap-2">
            {data.resolved.map((r, i) => (
              <li key={`${i}-${r.topic}`} className="grid gap-1 rounded-lg border border-border px-3 py-2.5">
                <div className="flex flex-wrap items-start gap-2">
                  <span className="min-w-[12ch] flex-1 text-base font-bold leading-7 text-foreground">{r.topic}</span>
                  <span className={cn(PILL, r.settled ? STATE_STYLE.supported.pill : STATE_STYLE.open.pill)}>
                    {r.settled ? EXTENDED_SYNTHESIS_AR.settled : EXTENDED_SYNTHESIS_AR.unsettled}
                  </span>
                </div>
                {r.decision && <p className="text-sm leading-7 text-text-secondary">{r.decision}</p>}
                {r.reports.length > 0 && (
                  <div className="flex flex-wrap items-center gap-1.5">
                    {r.reports.map((n) => {
                      const report = extendedReportByNumber(data, n);
                      return (
                        <ReportLink
                          key={n}
                          label={extendedReportLabelAr(n)}
                          title={report ? extendedReportTitle(report) : undefined}
                          onOpen={opener(report)}
                        />
                      );
                    })}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </Block>
      )}

      {data.gaps.length > 0 && (
        <Block title={EXTENDED_SYNTHESIS_AR.gaps} count={data.gaps.length} className="border-t">
          <ul className="grid gap-2">
            {data.gaps.map((g, i) => (
              <li
                key={`${i}-${g}`}
                className="relative ps-3.5 text-sm leading-7 text-text-secondary before:absolute before:start-0 before:top-[0.8em] before:h-1.5 before:w-1.5 before:rounded-full before:bg-text-subtle before:content-['']"
              >
                {g}
              </li>
            ))}
          </ul>
        </Block>
      )}

      {data.left_out.length > 0 && (
        <Block title={EXTENDED_SYNTHESIS_AR.leftOut} count={data.left_out.length} className="border-t">
          <ul className="grid gap-2">
            {data.left_out.map((x, i) => (
              <li key={`${i}-${x.angle}`} className="grid gap-1 rounded-lg border border-border px-3 py-2.5">
                <span className="text-base font-bold leading-7 text-foreground">{x.title || x.angle}</span>
                {x.why && <p className="text-sm leading-7 text-text-secondary">{x.why}</p>}
              </li>
            ))}
          </ul>
        </Block>
      )}

      {data.reports.length > 0 && (
        <Block title={EXTENDED_SYNTHESIS_AR.reports} count={data.reports.length} className="border-t">
          <div className="grid grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-2.5">
            {data.reports.map((r) => (
              <ReportCard key={r.n} data={data} report={r} onOpen={opener(r)} />
            ))}
          </div>
        </Block>
      )}
    </div>
  );
}

function ReportLink({ label, title, onOpen }: { label: string; title?: string; onOpen?: () => void }) {
  const cls = "rounded-full border border-border bg-surface-2 px-2 text-xs leading-[22px] tabular-nums text-foreground";
  if (!onOpen) {
    return (
      <span className={cls} title={title}>
        {label}
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={onOpen}
      title={title}
      className={cn(
        cls,
        "transition-colors hover:border-border-strong hover:bg-surface-3",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
      )}
    >
      {label}
    </button>
  );
}

function ReportCard({
  data,
  report,
  onOpen,
}: {
  data: ExtendedSynthesis;
  report: ExtendedReport;
  onOpen?: () => void;
}) {
  const side = extendedReportSideAr(report.mode);
  const confidence = extendedConfidenceAr(report.confidence);
  const body = (
    <>
      <span className="flex flex-wrap items-center gap-1.5">
        {side && <span className={CHIP}>{side}</span>}
        {report.ok ? (
          confidence && <span className={CHIP}>{confidence}</span>
        ) : (
          <span className={cn(PILL, STATE_STYLE.against.pill)}>{EXTENDED_SYNTHESIS_AR.incomplete}</span>
        )}
      </span>
      <span className="text-base font-semibold leading-7 text-foreground">{extendedReportTitle(report)}</span>
      {report.angles.length > 0 && (
        <span className="flex flex-wrap gap-1.5">
          {report.angles.map((id) => (
            <span key={id} className={TAG}>
              {extendedAngleTitle(data, id)}
            </span>
          ))}
        </span>
      )}
      {onOpen && <span className="text-xs font-semibold text-primary">{EXTENDED_SYNTHESIS_AR.openReport}</span>}
    </>
  );
  const cls = "grid content-start gap-2 rounded-xl border border-border bg-surface-2 p-3 text-start";
  if (!onOpen) return <div className={cls}>{body}</div>;
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        cls,
        "transition-colors hover:border-border-strong",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
      )}
    >
      {body}
    </button>
  );
}

// ---------------------------------------------------------------------------

function Block({
  title,
  count,
  className,
  children,
}: {
  title: string;
  count?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <section aria-labelledby={id} className={cn("grid min-w-0 gap-3 border-border py-5", className)}>
      <h2 id={id} className="flex items-center gap-2 text-lg font-bold text-foreground">
        {title}
        {count !== undefined && <span className="text-sm font-normal tabular-nums text-muted-foreground">{count}</span>}
      </h2>
      {children}
    </section>
  );
}
