"use client";

import { memo, useEffect, useLayoutEffect, useState } from "react";
import { Check, Info } from "lucide-react";

import { cn } from "@/lib/utils";
import { DEEP_SEARCH_STAGE_ORDER, useChatStore } from "@/stores/chat-store";
import { TypingIndicator } from "@/components/chat/TypingIndicator";
import { PushReadyChip } from "@/components/chat/PushReadyChip";
import type { DeepSearchStage } from "@/types";

// ─────────────────────────────────────────────────────────────────────────────
// EDITABLE CONFIG
//
// Every Arabic string the tracker can render lives here (there is no i18n
// framework — same convention as AGENT_PHRASES in TypingIndicator.tsx).
// To rename a stage, edit STEPS below; the keys must keep matching the
// `stage` values on the `agent_progress` SSE event.
// ─────────────────────────────────────────────────────────────────────────────

/** Card title — the first word of the header line. */
const TITLE = "بحث معمّق";

/** Header detail once the run has finished (before the card collapses). */
const DONE_LINE = "اكتملت الإجابة";

/**
 * Standing note ABOVE the card. Its whole job is to set the expectation before
 * the wait starts: this run is long *by design*, and leaving the tab is safe —
 * the run lives on the server, so the answer is here on return.
 */
const NOTE_TITLE = "شكرًا لتجربتك ريحان";
const NOTE_BODY =
  "البحث المعمّق مصمَّم ليمرّ على كل المصادر ذات الصلة بسؤالك، وقد يستغرق من 3 إلى 5 دقائق. " +
  "يمكنك تركه يعمل في الخلفية والانشغال بشيء آخر — ستجد الإجابة بانتظارك هنا.";

/** The five stages, in pipeline order. `done` is not a step — it fills all five. */
type RunningStage = Exclude<DeepSearchStage, "done">;

type StepStatus = "pending" | "active" | "done";

interface StepDef {
  key: RunningStage;
  /** Full name — shown whenever it fits; always the screen-reader name. */
  label: string;
  /** One-word fallback for row 1 when the three cells are too narrow (phones). */
  short: string;
  /** Row 1 holds three cells (2 of 6 columns each), row 2 holds two (3 each). */
  row: 1 | 2;
}

// Each step names what the pipeline is ACTUALLY doing at that point — the
// labels are not interchangeable decorations:
//   searching   = the executors' expanders + vector search
//   evaluating  = the per-sub-query rerankers scoring/keeping/dropping results.
//                 They live INSIDE the search loops, so this stage overlaps the
//                 tail of `searching` — hence the two share row 1, joined by a
//                 dashed seam, and both complete when `aggregating` starts.
//   aggregating = the AGGREGATOR, which writes the answer itself (synthesis_md
//                 with [n] citations) — the longest stage of the run
//   writing     = the responder turning that answer into the chat summary
const STEPS: ReadonlyArray<StepDef> = [
  { key: "planning", label: "تخطيط البحث", short: "التخطيط", row: 1 },
  { key: "searching", label: "البحث في الأنظمة والأحكام", short: "البحث", row: 1 },
  { key: "evaluating", label: "تقييم النتائج وترتيبها", short: "التقييم", row: 1 },
  { key: "aggregating", label: "كتابة الإجابة", short: "كتابة الإجابة", row: 2 },
  { key: "writing", label: "عرض الإجابة", short: "عرض الإجابة", row: 2 },
];

/** Arabic plural forms — [1] / [2] / [3–10] / [11+]. */
export interface ArabicPlural {
  one: string;
  two: string;
  few: string;
  many: string;
}

/** Retrieved sources in the header ("24 مصدرًا") — same wording as the chip. */
const SOURCE_FORMS: ArabicPlural = {
  one: "مصدر واحد",
  two: "مصدران",
  few: "مصادر",
  many: "مصدرًا",
};

/** «بحث موسّع» parallel searches in the header («4 بحوث متوازية»). */
const SEARCH_FORMS: ArabicPlural = {
  one: "بحث واحد",
  two: "بحثان متوازيان",
  few: "بحوث متوازية",
  many: "بحثًا متوازيًا",
};

/** «4 بحوث متوازية · اكتمل 1» — Latin digits. */
export function formatSearchesAr(searches: { total: number; done: number }): string {
  return `${formatCountAr(searches.total, SEARCH_FORMS)} · اكتمل ${searches.done}`;
}

/**
 * Cosmetic labels rotated in the header detail so the card never looks frozen.
 * The backend never relays sub-query lines (they expose the search logic —
 * owner, 2026-10-26), so these name a real running stage — never a query; they
 * do NOT fake counts or move a bar. `writing` has none: it shows the backend's
 * own label (`progress.text`). Same cadence as AGENT_PHRASES in
 * TypingIndicator.tsx.
 */
const QUIET_PHRASES: Partial<Record<RunningStage, readonly string[]>> = {
  planning: ["تحليل السؤال…", "تحديد نطاق البحث…", "اختيار المصادر…"],
  searching: ["صياغة استعلامات البحث…", "البحث في المصادر…", "جمع النتائج المرشّحة…"],
  // The rerankers: judging each retrieved result against its sub-query.
  evaluating: ["مطابقة النتائج بالأسئلة…", "استبعاد غير المتصل…", "ترجيح الأقوى…"],
  // The aggregator writing the answer + its citations — the longest stage.
  aggregating: ["ربط الأحكام بالمصادر…", "صياغة الإجابة…", "توثيق المراجع…"],
};

/** How long each quiet-stage phrase stays before crossfading to the next (ms). */
const QUIET_ROTATION_MS = 7000;

// ── Per-stage bars ──────────────────────────────────────────────────────────
//
// Every stage cell IS its own progress bar (fill grows from the right in RTL):
//
//   • pending → empty.
//   • active  → creeps with elapsed time since the stage began, decaying
//               exponentially and capped at CREEP_CAP — it can never claim the
//               stage is finished while the pipeline is still in it.
//   • done    → full. A stage is done only when a LATER stage really starts
//               (searching: when `aggregating` starts — it runs alongside
//               `evaluating`), so 100% always means a real pipeline milestone.
//
// STAGE_EXPECTED_MS is each creep's time constant (≈ observed p50), not a
// promise. Logfire p50s over 109 turns (12–26 Oct 2026): planning ~19s,
// aggregator 41–84s, responder ~57s; search ~30s. `evaluating` spans from the
// first reranker to the last executor finishing, hence ~20s rather than the
// few seconds one decider call takes.
const STAGE_EXPECTED_MS: Record<RunningStage, number> = {
  planning: 20_000,
  searching: 35_000,
  evaluating: 20_000,
  aggregating: 60_000,
  writing: 55_000,
};

/** Creep shape: 1 − e^(−k·t/expected) → ~92% of the bar at the expected time. */
const CREEP_K = 2.5;

/** Hard ceiling on the creep while a stage is still running. */
const CREEP_CAP = 0.94;

/** Bar/timer tick. 250ms reads as continuous motion without churning renders. */
const TICK_MS = 250;

/**
 * Status of one step given the run's (monotonic) current stage. `searching`
 * stays active through `evaluating` — the rerankers run inside the search
 * loops — and completes with it when the aggregator starts.
 */
function stepStatus(step: RunningStage, current: DeepSearchStage): StepStatus {
  const cur = DEEP_SEARCH_STAGE_ORDER[current];
  const start = DEEP_SEARCH_STAGE_ORDER[step];
  const end =
    step === "searching" ? DEEP_SEARCH_STAGE_ORDER.aggregating : start + 1;
  if (cur < start) return "pending";
  if (cur >= end) return "done";
  return "active";
}

function fillPercent(status: StepStatus, stageMs: number, expectedMs: number): number {
  if (status === "done") return 100;
  if (status === "pending") return 0;
  const creep = 1 - Math.exp((-CREEP_K * stageMs) / expectedMs);
  return Math.min(creep, CREEP_CAP) * 100;
}

// ─────────────────────────────────────────────────────────────────────────────
// Formatters (shared with DeepSearchSummaryChip)
// ─────────────────────────────────────────────────────────────────────────────

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

// ─────────────────────────────────────────────────────────────────────────────

/**
 * The expectation-setting note that sits above the tracker for the whole run.
 * Static copy — it never reacts to progress, so it stays out of the tracker's
 * render path and out of the card's live region.
 *
 * While the run is in flight it also carries the web-push opt-in
 * (PushReadyChip, pwa_step1.md §1D): the note already says «you can leave»,
 * the chip is how they get called back. It owns its own state and renders
 * nothing when push can't help, so the note is unchanged for most users.
 */
function DeepSearchNote({ running }: { running: boolean }) {
  return (
    <div
      dir="rtl"
      lang="ar"
      className="w-full rounded-2xl border border-primary/30 bg-primary/5 px-4 py-3"
    >
      <div className="flex items-start gap-2">
        <Info
          className="mt-[2px] h-3.5 w-3.5 shrink-0 text-primary"
          aria-hidden="true"
        />
        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold text-foreground">{NOTE_TITLE}</p>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            {NOTE_BODY}
          </p>
          {running && <PushReadyChip className="mt-2" />}
        </div>
      </div>
    </div>
  );
}

/**
 * Row 1 shows the full stage names when all three fit, otherwise one word each
 * (phones). Measured against a hidden full-name copy, so the decision never
 * depends on which label is currently showing — no flip-flop. Re-checked on
 * resize and when the web font finishes loading (it changes the widths).
 *
 * Takes the grid ELEMENT (from a callback ref), not a ref object: the grid
 * only mounts once the first progress event lands, and the effect must re-run
 * then to attach its observer.
 */
function useTightRow(grid: HTMLOListElement | null): boolean {
  const [tight, setTight] = useState(false);

  useLayoutEffect(() => {
    if (!grid) return;

    const check = () => {
      let next = false;
      grid.querySelectorAll<HTMLElement>("[data-row1]").forEach((cell) => {
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

/** Room kept beside a row-1 label for the ✓ a finished stage shows (icon + gap). */
const CHECK_RESERVE_PX = 18;

// ─────────────────────────────────────────────────────────────────────────────

interface DeepSearchProgressProps {
  /** The conversation whose run this tracker shows (each has its own slot). */
  conversationId: string;
  className?: string;
}

/**
 * Live step-tracker for a running `deep_search` turn (3–5 min), shown in place
 * of the generic TypingIndicator.
 *
 * Layout (Kawkab Mono, `font-code`): one header line — «بحث معمّق › phrase▍ …
 * [«بحث موسّع»: N بحوث متوازية · اكتمل M] sources · mm:ss» — then the stages
 * on two rows: planning · search ·
 * evaluation, then writing · showing. Each stage cell is its own progress bar.
 *
 * Driven exclusively by real pipeline events: a cell only reaches 100% when
 * the pipeline has actually moved past it.
 *
 * Render isolation: this is the ONLY subscriber of `chat-store.streams[cid].deepSearchProgress`.
 * Keep it that way — the message list must not re-render on progress events or
 * the fluid-streaming reveal gets choppy. The elapsed timer likewise ticks on
 * local state, never through the store.
 */
export const DeepSearchProgress = memo(function DeepSearchProgress({
  conversationId,
  className,
}: DeepSearchProgressProps) {
  const progress = useChatStore(
    (s) => s.streams[conversationId]?.deepSearchProgress ?? null,
  );
  const startedAt = progress?.startedAt ?? null;
  const stage = progress?.stage ?? null;

  // Local clock tick — drives BOTH the elapsed readout and the bars' creep. A
  // store write per tick would re-render every consumer of the chat store for
  // the whole run; this stays inside the (memoized) tracker.
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    if (startedAt === null) return;
    setNowMs(Date.now());
    const id = window.setInterval(() => setNowMs(Date.now()), TICK_MS);
    return () => window.clearInterval(id);
  }, [startedAt]);
  const elapsedMs = startedAt !== null ? Math.max(0, nowMs - startedAt) : 0;

  // When each stage was first seen — the origin of that stage's creep. Kept per
  // stage (not just the current one) because `searching` keeps creeping while
  // `evaluating` runs beside it. Reset on a fresh run.
  const [stageStarts, setStageStarts] = useState<
    Partial<Record<RunningStage, number>>
  >({});
  useEffect(() => {
    setStageStarts({});
  }, [startedAt]);
  useEffect(() => {
    if (stage === null || stage === "done") return;
    setStageStarts((prev) =>
      prev[stage] !== undefined ? prev : { ...prev, [stage]: Date.now() },
    );
  }, [stage, startedAt]);

  // Rotate the quiet-stage phrases. Reset to the first phrase whenever the
  // stage changes so a fresh stage starts clean. Local state — never touches
  // the store, so it can't re-render the list.
  const [phraseIndex, setPhraseIndex] = useState(0);
  useEffect(() => {
    setPhraseIndex(0);
    if (stage === null || stage === "done") return;
    const phrases = QUIET_PHRASES[stage];
    if (!phrases || phrases.length < 2) return;
    const id = window.setInterval(() => {
      setPhraseIndex((i) => (i + 1) % phrases.length);
    }, QUIET_ROTATION_MS);
    return () => window.clearInterval(id);
  }, [stage]);

  const [grid, setGrid] = useState<HTMLOListElement | null>(null);
  const tight = useTightRow(grid);

  // A deep_search run whose backend never emitted `agent_progress` (deploy
  // skew, resume leg without the hook) degrades to the generic indicator
  // rather than rendering an empty shell.
  // The note still belongs here: that degraded run is just as long, so the
  // expectation it sets is the same one.
  if (!progress) {
    return (
      <div className={cn("w-full space-y-2", className)}>
        <DeepSearchNote running />
        <TypingIndicator conversationId={conversationId} />
      </div>
    );
  }

  const isDone = progress.stage === "done";
  const activeStep =
    STEPS.find((s) => s.key === progress.stage) ?? STEPS[STEPS.length - 1];

  // Header detail: the current stage's rotating phrase; `writing` (no phrases)
  // shows the backend's own label; a finished run says so.
  const quietPhrases = isDone ? undefined : QUIET_PHRASES[activeStep.key];
  const detailText = isDone
    ? DONE_LINE
    : quietPhrases
      ? quietPhrases[phraseIndex % quietPhrases.length]
      : (progress.text ?? "");

  return (
    <div className={cn("w-full space-y-2", className)}>
      <DeepSearchNote running={!isDone} />

      <div
        dir="rtl"
        lang="ar"
        aria-busy={!isDone}
        className="w-full overflow-hidden rounded-xl border border-border bg-card font-code text-xs shadow-sm"
      >
        {/* Header line: title › live detail + caret … sources · timer. The
            timer is deliberately OUTSIDE the live region below — announcing it
            would speak once per second. */}
        <div className="flex items-center gap-2 whitespace-nowrap px-3 py-1 leading-5 text-muted-foreground">
          <span className="shrink-0 font-bold text-foreground">{TITLE}</span>
          <span className="shrink-0 text-text-subtle" aria-hidden="true">
            ›
          </span>
          <span className="flex min-w-0 flex-1 items-center gap-0.5" aria-hidden="true">
            {/* Keyed by the text: each new phrase remounts and fades in. The
                span is exactly as wide as its own text, so the caret always
                sits right after it (a two-slot crossfade would hold the old,
                longer phrase's width and strand the caret). */}
            <span
              key={detailText}
              className="min-w-0 truncate animate-ds-fade-in motion-reduce:animate-none"
            >
              {detailText}
            </span>
            {!isDone && (
              <span className="h-[1.05em] w-[0.5em] shrink-0 animate-blink bg-primary motion-reduce:animate-none" />
            )}
          </span>
          <span className="flex shrink-0 items-center gap-2 tabular-nums">
            {progress.searches && (
              <span className="text-foreground">{formatSearchesAr(progress.searches)}</span>
            )}
            {progress.sources > 0 && (
              <span className="hidden min-[380px]:inline">
                {formatCountAr(progress.sources, SOURCE_FORMS)}
              </span>
            )}
            <span>{formatElapsedAr(elapsedMs)}</span>
          </span>
        </div>

        {/* The only live region: it holds the active stage label and nothing
            else, so screen readers announce stage TRANSITIONS only. */}
        <span role="status" aria-live="polite" className="sr-only">
          {isDone ? DONE_LINE : activeStep.label}
        </span>

        {/* Stages: 6-column grid — row 1 = three cells × 2 columns, row 2 =
            two cells × 3 columns. The 1px gaps show the border colour through,
            drawing the dividers. */}
        <ol
          ref={setGrid}
          className="grid grid-cols-6 gap-px border-t border-border bg-border"
        >
          {STEPS.map((step) => {
            const status = stepStatus(step.key, progress.stage);
            const begin =
              stageStarts[step.key] ??
              (step.key === "searching" ? stageStarts.evaluating : undefined) ??
              nowMs;
            const fill = fillPercent(
              status,
              Math.max(0, nowMs - begin),
              STAGE_EXPECTED_MS[step.key],
            );
            const showShort = step.row === 1 && tight;

            return (
              <li
                key={step.key}
                title={step.label}
                data-row1={step.row === 1 ? "" : undefined}
                className={cn(
                  "relative flex min-h-7 min-w-0 items-center gap-1.5 overflow-hidden bg-card px-2.5 py-0.5 leading-5",
                  step.row === 1 ? "col-span-2" : "col-span-3",
                  // Search + evaluation run together: cover the solid 1px gap
                  // and draw a dashed seam instead.
                  step.key === "evaluating" && "-ms-px",
                )}
              >
                {/* The bar — the cell itself. The card is dir="rtl", so the
                    fill grows from the right edge. LINEAR transition matched
                    to the tick so creep updates read as one smooth sweep. */}
                <span
                  aria-hidden="true"
                  className="absolute inset-y-0 start-0 overflow-hidden bg-primary/15 transition-[width] duration-300 ease-linear motion-reduce:transition-none"
                  style={{ width: `${fill}%` }}
                >
                  {status === "active" && (
                    <>
                      <span className="absolute inset-0 animate-ds-bar-sweep bg-gradient-to-r from-transparent via-primary-fg to-transparent opacity-30 motion-reduce:hidden" />
                      <span className="absolute inset-y-0 end-0 w-0.5 bg-primary" />
                    </>
                  )}
                </span>

                {step.key === "evaluating" && (
                  <span
                    aria-hidden="true"
                    className="absolute inset-y-1.5 start-0 border-s border-dashed border-border-strong"
                  />
                )}

                {/* Hidden full-name copy that useTightRow measures. */}
                {step.row === 1 && (
                  <span
                    data-probe=""
                    aria-hidden="true"
                    className="invisible absolute whitespace-nowrap font-bold"
                  >
                    {step.label}
                  </span>
                )}

                <span
                  className={cn(
                    "relative min-w-0 truncate",
                    status === "active"
                      ? "font-bold text-foreground"
                      : status === "done"
                        ? "text-text-secondary"
                        : "text-text-subtle",
                  )}
                >
                  {showShort ? (
                    <>
                      <span aria-hidden="true">{step.short}</span>
                      <span className="sr-only">{step.label}</span>
                    </>
                  ) : (
                    step.label
                  )}
                </span>

                {status === "done" && (
                  <Check
                    className="relative h-3 w-3 shrink-0 text-primary"
                    aria-hidden="true"
                  />
                )}
              </li>
            );
          })}
        </ol>
      </div>
    </div>
  );
});
