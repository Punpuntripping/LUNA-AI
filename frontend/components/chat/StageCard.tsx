"use client";

import { useEffect, useLayoutEffect, useState, type ReactNode } from "react";
import { Check, Info } from "lucide-react";

import { cn } from "@/lib/utils";
import type { StepStatus } from "@/lib/progress-stages";
import { PushReadyChip } from "@/components/chat/PushReadyChip";

// ─────────────────────────────────────────────────────────────────────────────
// The live stage card shared by DeepSearchProgress («بحث عميق» / «بحث شامل»)
// and FilesProgress («تجهيز المستندات»): one header line — «title › detail▍ …
// extras · mm:ss» — then the stages on a 6-column grid (a cell spans 2 columns
// → three per row, or 3 → two per row). Each cell is its own progress bar; the
// caller decides status + fill, this file only draws.
// ─────────────────────────────────────────────────────────────────────────────

/** Bar/timer tick. 250ms reads as continuous motion without churning renders. */
export const TICK_MS = 250;

/** How long each rotating header phrase stays before the next (ms). */
export const PHRASE_ROTATION_MS = 7000;

/** Room kept beside a narrow cell's label for the ✓ a finished stage shows. */
const CHECK_RESERVE_PX = 18;

export interface StageCell {
  key: string;
  /** Full name — shown whenever it fits; always the screen-reader name. */
  label: string;
  /** One-word fallback when the three-per-row cells are too narrow (phones). */
  short: string;
  /** Grid columns out of 6: 2 = three cells per row, 3 = two per row. */
  span: 2 | 3;
  /** Draw a dashed seam (not a solid divider) before this cell: the two
   *  stages run together (search + evaluation). */
  seamBefore?: boolean;
  status: StepStatus;
  /** 0–100. */
  fill: number;
}

/**
 * Local clock: `Date.now()` every TICK_MS while `running`. Drives the timer and
 * the bars' creep without a store write per tick (which would re-render every
 * chat-store consumer for the whole run).
 */
export function useNow(running: boolean): number {
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    setNowMs(Date.now());
    const id = window.setInterval(() => setNowMs(Date.now()), TICK_MS);
    return () => window.clearInterval(id);
  }, [running]);
  return nowMs;
}

/**
 * Index into a rotating phrase list, reset to 0 whenever `resetKey` changes so
 * a fresh stage starts on its first phrase.
 */
export function usePhraseIndex(resetKey: string | null, count: number): number {
  const [index, setIndex] = useState(0);
  useEffect(() => {
    setIndex(0);
    if (resetKey === null || count < 2) return;
    const id = window.setInterval(() => setIndex((i) => (i + 1) % count), PHRASE_ROTATION_MS);
    return () => window.clearInterval(id);
  }, [resetKey, count]);
  return index;
}

/**
 * Narrow cells (three per row) show their full names when every one fits,
 * otherwise one word each (phones). Measured against a hidden full-name copy,
 * so the decision never depends on which label is currently showing — no
 * flip-flop. Re-checked on resize and when the web font finishes loading.
 *
 * Takes the grid ELEMENT (from a callback ref), not a ref object: the grid may
 * mount after the card, and the effect must re-run then to attach its observer.
 */
function useTightCells(grid: HTMLOListElement | null): boolean {
  const [tight, setTight] = useState(false);

  useLayoutEffect(() => {
    if (!grid) return;

    const check = () => {
      let next = false;
      grid.querySelectorAll<HTMLElement>("[data-narrow]").forEach((cell) => {
        const probe = cell.querySelector<HTMLElement>("[data-probe]");
        if (!probe) return;
        const style = window.getComputedStyle(cell);
        const room =
          cell.clientWidth -
          parseFloat(style.paddingLeft) -
          parseFloat(style.paddingRight) -
          CHECK_RESERVE_PX;
        if (probe.offsetWidth > room) next = true;
      });
      setTight(next);
    };

    check();
    const observer =
      typeof ResizeObserver !== "undefined" ? new ResizeObserver(check) : null;
    observer?.observe(grid);
    const fonts = document.fonts;
    let alive = true;
    void fonts?.ready.then(() => {
      if (alive) check();
    });
    fonts?.addEventListener?.("loadingdone", check);
    return () => {
      alive = false;
      observer?.disconnect();
      fonts?.removeEventListener?.("loadingdone", check);
    };
  }, [grid]);

  return tight;
}

/** Title of the standing note above a long-running card. */
const NOTE_TITLE = "شكرًا لتجربتك ريحان";

/** Second sentence of every note: leaving is safe, the run lives on the server. */
export const NOTE_LEAVE_AR =
  "يمكنك تركه يعمل في الخلفية والانشغال بشيء آخر — ستجد الإجابة بانتظارك هنا.";

/**
 * The expectation-setting note above a long-running card («بحث عميق»,
 * «بحث شامل», «تجهيز المستندات»): this wait is long *by design*, and leaving
 * the tab is safe. Static copy — it never reacts to progress, so it stays out
 * of the card's render path and live region.
 *
 * While the run is in flight it also carries the web-push opt-in
 * (PushReadyChip, pwa_step1.md §1D): the note already says «you can leave»,
 * the chip is how they get called back. It owns its own state and renders
 * nothing when push can't help.
 */
export function StageNote({ running, body }: { running: boolean; body: string }) {
  return (
    <div
      dir="rtl"
      lang="ar"
      className="w-full rounded-2xl border border-primary/30 bg-primary/5 px-4 py-3"
    >
      <div className="flex items-start gap-2">
        <Info className="mt-[2px] h-3.5 w-3.5 shrink-0 text-primary" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold text-foreground">{NOTE_TITLE}</p>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">{body}</p>
          {running && <PushReadyChip className="mt-2" />}
        </div>
      </div>
    </div>
  );
}

interface StageCardProps {
  /** First word of the header line («بحث عميق»…). */
  title: string;
  /** Live detail after the title (a rotating phrase, a backend label…). */
  detail: string;
  /** False once the run is over: no blinking caret, `aria-busy` off. */
  running: boolean;
  /** Counts before the timer («3 بحوث متوازية · اكتمل 1», «42 مصدرًا»…). */
  extras?: ReactNode;
  elapsedMs: number;
  /** The ONLY live-region text: the active stage's name (or the done line). */
  liveLabel: string;
  cells: readonly StageCell[];
}

export function StageCard({
  title,
  detail,
  running,
  extras,
  elapsedMs,
  liveLabel,
  cells,
}: StageCardProps) {
  const [grid, setGrid] = useState<HTMLOListElement | null>(null);
  const tight = useTightCells(grid);

  return (
    <div
      dir="rtl"
      lang="ar"
      aria-busy={running}
      className="w-full overflow-hidden rounded-xl border border-border bg-card font-code text-xs shadow-sm"
    >
      {/* Header line: title › live detail + caret … extras · timer. The timer
          is deliberately OUTSIDE the live region below — announcing it would
          speak once per second. */}
      <div className="flex items-center gap-2 whitespace-nowrap px-3 py-1 leading-5 text-muted-foreground">
        <span className="shrink-0 font-bold text-foreground">{title}</span>
        <span className="shrink-0 text-text-subtle" aria-hidden="true">
          ›
        </span>
        <span className="flex min-w-0 flex-1 items-center gap-0.5" aria-hidden="true">
          {/* Keyed by the text: each new phrase remounts and fades in. The
              span is exactly as wide as its own text, so the caret always sits
              right after it. */}
          <span
            key={detail}
            className="min-w-0 truncate animate-ds-fade-in motion-reduce:animate-none"
          >
            {detail}
          </span>
          {running && (
            <span className="h-[1.05em] w-[0.5em] shrink-0 animate-blink bg-primary motion-reduce:animate-none" />
          )}
        </span>
        {/* The counts may shrink (each truncates); the timer never does. */}
        <span className="flex min-w-0 items-center gap-2 tabular-nums">
          {extras}
          <span className="shrink-0">{formatElapsedAr(elapsedMs)}</span>
        </span>
      </div>

      {/* The only live region: it holds the active stage label and nothing
          else, so screen readers announce stage TRANSITIONS only. */}
      <span role="status" aria-live="polite" className="sr-only">
        {liveLabel}
      </span>

      {/* Stages: 6-column grid. The 1px gaps show the border colour through,
          drawing the dividers. */}
      <ol ref={setGrid} className="grid grid-cols-6 gap-px border-t border-border bg-border">
        {cells.map((cell) => {
          const narrow = cell.span === 2;
          const showShort = narrow && tight;
          return (
            <li
              key={cell.key}
              title={cell.label}
              data-narrow={narrow ? "" : undefined}
              className={cn(
                "relative flex min-h-7 min-w-0 items-center gap-1.5 overflow-hidden bg-card px-2.5 py-0.5 leading-5",
                narrow ? "col-span-2" : "col-span-3",
                // Two stages that run together: cover the solid 1px gap and
                // draw a dashed seam instead.
                cell.seamBefore && "-ms-px",
              )}
            >
              {/* The bar — the cell itself. The card is dir="rtl", so the fill
                  grows from the right edge. LINEAR transition matched to the
                  tick so creep updates read as one smooth sweep. */}
              <span
                aria-hidden="true"
                className="absolute inset-y-0 start-0 overflow-hidden bg-primary/15 transition-[width] duration-300 ease-linear motion-reduce:transition-none"
                style={{ width: `${cell.fill}%` }}
              >
                {cell.status === "active" && (
                  <>
                    <span className="absolute inset-0 animate-ds-bar-sweep bg-gradient-to-r from-transparent via-primary-fg to-transparent opacity-30 motion-reduce:hidden" />
                    <span className="absolute inset-y-0 end-0 w-0.5 bg-primary" />
                  </>
                )}
              </span>

              {cell.seamBefore && (
                <span
                  aria-hidden="true"
                  className="absolute inset-y-1.5 start-0 border-s border-dashed border-border-strong"
                />
              )}

              {/* Hidden full-name copy that useTightCells measures. */}
              {narrow && (
                <span
                  data-probe=""
                  aria-hidden="true"
                  className="invisible absolute whitespace-nowrap font-bold"
                >
                  {cell.label}
                </span>
              )}

              <span
                className={cn(
                  "relative min-w-0 truncate",
                  cell.status === "active"
                    ? "font-bold text-foreground"
                    : cell.status === "done"
                      ? "text-text-secondary"
                      : "text-text-subtle",
                )}
              >
                {showShort ? (
                  <>
                    <span aria-hidden="true">{cell.short}</span>
                    <span className="sr-only">{cell.label}</span>
                  </>
                ) : (
                  cell.label
                )}
              </span>

              {cell.status === "done" && (
                <Check className="relative h-3 w-3 shrink-0 text-primary" aria-hidden="true" />
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Formatters (shared with DeepSearchSummaryChip)
// ─────────────────────────────────────────────────────────────────────────────

/** Arabic plural forms — [1] / [2] / [3–10] / [11+]. */
export interface ArabicPlural {
  one: string;
  two: string;
  few: string;
  many: string;
}

/**
 * Elapsed wall-clock as mm:ss in Latin digits ("01:50").
 *
 * Built by hand rather than through `toLocaleString`: the tracker renders
 * during SSR-hydrated client updates and must never disagree with itself
 * across environments.
 */
export function formatElapsedAr(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

/** Count + Arabic plural agreement ("نتيجة واحدة" / "6 نتائج" / "12 نتيجة"). */
export function formatCountAr(n: number, forms: ArabicPlural): string {
  if (n === 1) return forms.one;
  if (n === 2) return forms.two;
  if (n >= 3 && n <= 10) return `${n} ${forms.few}`;
  return `${n} ${forms.many}`;
}
