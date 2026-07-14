"use client";

import { memo, useEffect, useMemo, useState } from "react";
import { Check, Search } from "lucide-react";

import { cn } from "@/lib/utils";
import { DEEP_SEARCH_TOPIC_PREFIX, useChatStore } from "@/stores/chat-store";
import { TypingIndicator } from "@/components/chat/TypingIndicator";
import type { DeepSearchStage } from "@/types";

// ─────────────────────────────────────────────────────────────────────────────
// EDITABLE CONFIG
//
// Every Arabic string the tracker can render lives here (there is no i18n
// framework — same convention as AGENT_PHRASES in TypingIndicator.tsx).
// To rename a stage, edit STEPS below; the keys must keep matching the
// `stage` values on the `agent_progress` SSE event.
// ─────────────────────────────────────────────────────────────────────────────

/** Card title. */
const TITLE = "بحث معمّق";

/** The four stages, in pipeline order. `done` is not a step — it fills all four. */
type RunningStage = Exclude<DeepSearchStage, "done">;

// Each step names what the pipeline is ACTUALLY doing at that point — the
// labels are not interchangeable decorations:
//   evaluating  = the per-sub-query rerankers scoring/keeping/dropping results
//                 (this is the real "تقييم وترجيح" — it lives inside the search
//                 loops, NOT after them)
//   aggregating = the AGGREGATOR, which writes the answer itself (synthesis_md
//                 with [n] citations) — the longest stage of the run
//   writing     = the responder turning that answer into the chat summary
const STEPS: ReadonlyArray<{ key: RunningStage; label: string }> = [
  { key: "planning", label: "تخطيط البحث" },
  { key: "searching", label: "البحث في الأنظمة والأحكام" },
  { key: "evaluating", label: "تقييم النتائج وترجيحها" },
  { key: "aggregating", label: "كتابة الإجابة" },
  { key: "writing", label: "عرض الإجابة" },
];

/** Arabic plural forms — [1] / [2] / [3–10] / [11+]. */
export interface ArabicPlural {
  one: string;
  two: string;
  few: string;
  many: string;
}

/** Retrieved results shown under the active step ("١٢ نتيجة"). */
const SOURCE_FORMS: ArabicPlural = {
  one: "نتيجة واحدة",
  two: "نتيجتان",
  few: "نتائج",
  many: "نتيجة",
};

/** Sub-queries shown under the active step ("٦ استعلامات"). */
const QUERY_FORMS: ArabicPlural = {
  one: "استعلام واحد",
  two: "استعلامان",
  few: "استعلامات",
  many: "استعلامًا",
};

/**
 * Live query counter shown during `searching` before the authoritative
 * phase-end `queries` count arrives: «الاستعلام ٣». Driven by `topicsSeen`
 * (one bump per streamed "بحث في …" line).
 */
const QUERY_COUNTER_PREFIX = "الاستعلام ";

/**
 * Cosmetic labels rotated in the detail line during the two stages that stream
 * NO topics — `planning` (~50s) and `aggregating` (~90s) — so the card never
 * looks frozen. These name a real running stage; they do NOT fake counts or
 * move the bar. Same rotation cadence/convention as AGENT_PHRASES in
 * TypingIndicator.tsx.
 */
const QUIET_PHRASES: Partial<Record<RunningStage, readonly string[]>> = {
  planning: ["تحليل السؤال…", "تحديد نطاق البحث…", "اختيار المصادر…"],
  // The rerankers: judging each retrieved result against its sub-query.
  evaluating: ["مطابقة النتائج بالأسئلة…", "استبعاد غير المتصل…", "ترجيح الأقوى…"],
  // The aggregator writing the answer + its citations — the longest stage.
  aggregating: ["ربط الأحكام بالمصادر…", "صياغة الإجابة…", "توثيق المراجع…"],
};

/** How long each quiet-stage phrase stays before crossfading to the next (ms). */
const QUIET_ROTATION_MS = 7000;

/**
 * Paced reveal of streamed topics. The pipeline launches every sub-query in
 * parallel, so all "بحث في …" lines arrive in ONE burst — then the searching
 * stage is quiet for ~90s. Revealing them one-by-one spreads that real
 * evidence across the quiet window (the rAF token-reveal principle:
 * presentation pacing of real data, never fabrication — the full list is in
 * the log/chip from the moment it arrives). First topic shows instantly.
 */
const TOPIC_REVEAL_MS = 5000;

// ── Bar model: anchored milestones + time creep between them ────────────────
//
// The bar is a HYBRID, not a step ladder and not a fake ramp:
//
//   • Each stage OWNS a fixed anchor. Entering the stage snaps the bar there.
//   • Inside a stage the bar creeps with elapsed time toward the NEXT anchor,
//     so it is always in motion during the long quiet stretches.
//   • The creep decays exponentially and is capped short of the next anchor
//     (CREEP_CAP) — so the bar can never *cross* a milestone the pipeline has
//     not actually reached. A slow aggregator asymptotes just under 95%; it
//     hits 95% only when the writer really starts, and 100% only on `done`.
//
// The creep is calibrated (STAGE_EXPECTED_MS ≈ our observed p50 timings) so a
// typical 3–5 min run reads as one continuous sweep rather than four jumps.
// Budget shape: searching + evaluating are the stages the user actually waits
// through, so they OWN the middle of the bar — searching 10→35, evaluating
// 35→80. Planning creeps up to 10 before the search starts; the tail (aggregator
// writing, responder presenting) rides 80→95→100.
const STAGE_ANCHORS: Record<RunningStage, number> = {
  planning: 5,
  searching: 10,
  evaluating: 35,
  aggregating: 80,
  writing: 98,
};

/** Ceiling each stage creeps toward — the next stage's anchor (`done` = 100). */
const STAGE_TARGETS: Record<RunningStage, number> = {
  planning: STAGE_ANCHORS.searching,
  searching: STAGE_ANCHORS.evaluating,
  evaluating: STAGE_ANCHORS.aggregating,
  aggregating: STAGE_ANCHORS.writing,
  writing: 100,
};

/**
 * Typical wall-clock per stage — the creep's time constant, not a promise.
 * Calibrated to observed p50s: planning ~30s, searching ~25s (the vector search
 * is fast; it's the rerankers that grind), evaluating ~70s, aggregating ~90s
 * (long, but it only has 80→95 to cover), writing ~15s.
 */
const STAGE_EXPECTED_MS: Record<RunningStage, number> = {
  planning: 30_000,
  searching: 25_000,
  evaluating: 70_000,
  aggregating: 90_000,
  writing: 15_000,
};

/** Creep shape: 1 − e^(−k·t/expected) → ~92% of the gap at the expected time. */
const CREEP_K = 2.5;

/** Hard ceiling on the creep, as a fraction of the gap to the next anchor. */
const CREEP_CAP = 0.94;

/** Bar/timer tick. 250ms reads as continuous motion without churning renders. */
const TICK_MS = 250;

/** Drop the redundant "بحث في " prefix — the leading Search icon says it. */
function stripTopicPrefix(line: string): string {
  return line.startsWith(DEEP_SEARCH_TOPIC_PREFIX)
    ? line.slice(DEEP_SEARCH_TOPIC_PREFIX.length)
    : line;
}

/** Words of the sub-query kept in the tracker — enough to read at a glance. */
const TOPIC_WORD_LIMIT = 5;

/**
 * A topic line as the tracker shows it: «الأنظمة واللوائح: فصل الموظف بسبب التغيب…»
 *
 * The corpus label (everything up to the colon) is KEPT — it says which body of
 * law was searched — and only the sub-query itself is clipped to the first
 * TOPIC_WORD_LIMIT words. The backend already hard-truncates the query at 80
 * chars and appends "...", so that tail is stripped before counting words to
 * avoid an ellipsis-on-ellipsis. The full line survives untouched in the log,
 * which is what the summary chip renders.
 */
function formatTopic(line: string): string {
  const stripped = stripTopicPrefix(line);
  const colon = stripped.indexOf(":");
  const label = colon >= 0 ? stripped.slice(0, colon + 1) : "";
  const query = (colon >= 0 ? stripped.slice(colon + 1) : stripped)
    .replace(/[.…]+\s*$/, "")
    .trim();

  const words = query.split(/\s+/).filter(Boolean);
  const clipped = words.slice(0, TOPIC_WORD_LIMIT).join(" ");
  const tail = words.length > TOPIC_WORD_LIMIT ? "…" : "";
  const short = `${clipped}${tail}`;

  return label ? `${label} ${short}` : short;
}

// ─────────────────────────────────────────────────────────────────────────────
// Formatters (shared with DeepSearchSummaryChip)
// ─────────────────────────────────────────────────────────────────────────────

const AR_DIGITS = ["٠", "١", "٢", "٣", "٤", "٥", "٦", "٧", "٨", "٩"] as const;

/**
 * Latin → Arabic-Indic digits. Deterministic on purpose (no `toLocaleString`):
 * the tracker renders during SSR-hydrated client updates and must never
 * disagree with itself across environments.
 */
export function toArabicDigits(value: string | number): string {
  return String(value).replace(/\d/g, (d) => AR_DIGITS[Number(d)]);
}

/** Elapsed wall-clock as m:ss in Arabic-Indic digits ("١:٥٠"). */
export function formatElapsedAr(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return toArabicDigits(`${minutes}:${String(seconds).padStart(2, "0")}`);
}

/** Count + Arabic plural agreement ("نتيجة واحدة" / "٦ نتائج" / "١٢ نتيجة"). */
export function formatCountAr(n: number, forms: ArabicPlural): string {
  if (n === 1) return forms.one;
  if (n === 2) return forms.two;
  if (n >= 3 && n <= 10) return `${toArabicDigits(n)} ${forms.few}`;
  return `${toArabicDigits(n)} ${forms.many}`;
}

// ─────────────────────────────────────────────────────────────────────────────

/**
 * One-line label that CROSSFADES whenever its `text` changes instead of
 * swapping abruptly. Both the outgoing and incoming line are grid-stacked in a
 * single cell (the technique from TypingIndicator.tsx) so the box reserves the
 * taller line's height and there is no reflow. Truncated to one line, so the
 * host can rely on a fixed row height.
 *
 * `motion-reduce:` drops the transition for users who ask for less motion.
 */
function CrossfadeLine({
  text,
  className,
}: {
  text: string;
  className?: string;
}) {
  // Two slots so both lines exist at once during the fade: the incoming line is
  // written into the hidden slot, then `show` flips — the pair crossfade.
  const [slots, setSlots] = useState<{ a: string; b: string; show: "a" | "b" }>(
    { a: text, b: "", show: "a" },
  );

  useEffect(() => {
    setSlots((prev) => {
      const visible = prev.show === "a" ? prev.a : prev.b;
      if (text === visible) return prev;
      return prev.show === "a"
        ? { a: prev.a, b: text, show: "b" }
        : { a: text, b: prev.b, show: "a" };
    });
  }, [text]);

  return (
    <span className="grid min-w-0 flex-1">
      <span
        className={cn(
          "[grid-area:1/1] truncate transition-opacity duration-300 motion-reduce:transition-none",
          slots.show === "a" ? "opacity-100" : "opacity-0",
          className,
        )}
      >
        {slots.a}
      </span>
      <span
        className={cn(
          "[grid-area:1/1] truncate transition-opacity duration-300 motion-reduce:transition-none",
          slots.show === "b" ? "opacity-100" : "opacity-0",
          className,
        )}
      >
        {slots.b}
      </span>
    </span>
  );
}

// ─────────────────────────────────────────────────────────────────────────────

interface DeepSearchProgressProps {
  className?: string;
}

/**
 * Live step-tracker for a running `deep_search` turn (1–4 min), shown in place
 * of the generic TypingIndicator.
 *
 * Driven exclusively by real pipeline events — the bar's fill is `n of 4`
 * completed steps, never a synthetic percentage ramp.
 *
 * Render isolation: this is the ONLY subscriber of `chat-store.deepSearchProgress`.
 * Keep it that way — the message list must not re-render on progress events or
 * the fluid-streaming reveal gets choppy. The elapsed timer likewise ticks on
 * local state, never through the store.
 */
export const DeepSearchProgress = memo(function DeepSearchProgress({
  className,
}: DeepSearchProgressProps) {
  const progress = useChatStore((s) => s.deepSearchProgress);
  const startedAt = progress?.startedAt ?? null;
  const stage = progress?.stage ?? null;

  // Local clock tick — drives BOTH the elapsed readout and the bar's creep. A
  // store write per tick would re-render every consumer of the chat store for
  // the whole 4-minute run; this stays inside the (memoized) tracker.
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    if (startedAt === null) return;
    setNowMs(Date.now());
    const id = window.setInterval(() => setNowMs(Date.now()), TICK_MS);
    return () => window.clearInterval(id);
  }, [startedAt]);
  const elapsedMs = startedAt !== null ? Math.max(0, nowMs - startedAt) : 0;

  // When the current stage began — the origin of the bar's creep. Re-stamped on
  // every stage change (and on a fresh run, via `startedAt`).
  const [stageStartedAt, setStageStartedAt] = useState<number | null>(null);
  useEffect(() => {
    setStageStartedAt(stage === null ? null : Date.now());
  }, [stage, startedAt]);

  // Rotate the quiet-stage phrases (planning / aggregating only). Reset to the
  // first phrase whenever the stage changes so a fresh stage starts clean.
  // Local state — never touches the store, so it can't re-render the list.
  const [phraseIndex, setPhraseIndex] = useState(0);
  useEffect(() => {
    setPhraseIndex(0);
    if (stage !== "planning" && stage !== "aggregating") return;
    const phrases = QUIET_PHRASES[stage];
    if (!phrases || phrases.length < 2) return;
    const id = window.setInterval(() => {
      setPhraseIndex((i) => (i + 1) % phrases.length);
    }, QUIET_ROTATION_MS);
    return () => window.clearInterval(id);
  }, [stage]);

  // Paced topic reveal (see TOPIC_REVEAL_MS). `log` is replaced immutably on
  // every append, so it's a safe memo dependency. Local state only.
  const progressLog = progress?.log;
  const allTopics = useMemo(
    () =>
      progressLog
        ? progressLog.filter((l) => l.startsWith(DEEP_SEARCH_TOPIC_PREFIX))
        : [],
    [progressLog],
  );
  const [revealedCount, setRevealedCount] = useState(0);
  useEffect(() => {
    // New run → start the reveal from scratch.
    setRevealedCount(0);
  }, [startedAt]);
  useEffect(() => {
    // The reveal spans searching AND evaluating: the sub-queries all fire in
    // parallel, so their topics land in one burst early in `searching`, while
    // the rerankers (evaluating) are still judging the very results those
    // queries returned. Cutting the reveal at the stage boundary would drop
    // real evidence on the floor mid-list.
    if (stage !== "searching" && stage !== "evaluating") return;
    if (revealedCount >= allTopics.length) return;
    if (revealedCount === 0) {
      setRevealedCount(1); // first topic instantly — no artificial dead air
      return;
    }
    const id = window.setTimeout(
      () => setRevealedCount((r) => Math.min(r + 1, allTopics.length)),
      TOPIC_REVEAL_MS,
    );
    return () => window.clearTimeout(id);
  }, [stage, revealedCount, allTopics.length]);

  // A deep_search run whose backend never emitted `agent_progress` (deploy
  // skew, resume leg without the hook) degrades to the generic indicator
  // rather than rendering an empty shell.
  if (!progress) return <TypingIndicator className={className} />;

  const isDone = progress.stage === "done";
  const stageIndex = STEPS.findIndex((s) => s.key === progress.stage);
  const activeIndex = isDone
    ? STEPS.length - 1
    : stageIndex >= 0
      ? stageIndex
      : 0;

  const runningStage = STEPS[activeIndex].key;
  // The topic evidence belongs to BOTH search stages: `searching` runs the
  // sub-queries, `evaluating` reranks what those same sub-queries returned.
  // Keeping the feed alive across the boundary is what makes the two stages
  // read as one continuous act of searching rather than a hard cut.
  const showsTopics =
    progress.stage === "searching" || progress.stage === "evaluating";

  // Bar fill: the stage's anchor + a time-decayed creep toward the next anchor,
  // capped short of it (see STAGE_ANCHORS). Always moving, never crossing a
  // milestone the pipeline hasn't actually reached. Monotonic by construction —
  // the capped creep always stays below the next stage's anchor.
  let barPercent: number;
  if (isDone) {
    barPercent = 100;
  } else {
    const anchor = STAGE_ANCHORS[runningStage];
    const target = STAGE_TARGETS[runningStage];
    const stageMs =
      stageStartedAt !== null ? Math.max(0, nowMs - stageStartedAt) : 0;
    const creep = Math.min(
      1 - Math.exp((-CREEP_K * stageMs) / STAGE_EXPECTED_MS[runningStage]),
      CREEP_CAP,
    );
    barPercent = anchor + (target - anchor) * creep;
  }

  // The detail line. While topics are still being revealed (paced — see
  // TOPIC_REVEAL_MS) it shows the newest one, and that reveal carries on into
  // `evaluating`. Once the topics run out — and for the stages that stream none
  // (planning / aggregating) — it rotates that stage's phrases instead, so the
  // line is never empty. CrossfadeLine handles every transition between values.
  const revealedTopics = allTopics.slice(0, revealedCount);
  const stillRevealing = revealedCount < allTopics.length;
  const showTopicAsDetail =
    showsTopics &&
    revealedTopics.length > 0 &&
    (progress.stage === "searching" || stillRevealing);

  const quietPhrases = QUIET_PHRASES[runningStage];
  const detailText = showTopicAsDetail
    ? formatTopic(revealedTopics[revealedTopics.length - 1])
    : quietPhrases
      ? quietPhrases[phraseIndex % quietPhrases.length]
      : progress.text
        ? stripTopicPrefix(progress.text)
        : "";

  // Recent-topics mini feed: the last 1–2 revealed topics, newest first. When
  // the detail line is showing a topic, that topic is excluded here (it's
  // already above); when the detail has moved on to phrases, the feed keeps the
  // most recent topics visible as evidence of what was actually searched.
  const feedTopics = showTopicAsDetail
    ? revealedTopics.slice(0, -1)
    : revealedTopics;
  const recentTopics = showsTopics ? feedTopics.slice(-2).reverse() : [];

  // Meta line under the detail: prefer the authoritative phase-end counts;
  // before the `queries` count arrives, fall back to the live revealed-topic
  // counter («الاستعلام ٣») so the search stages show progress from the first
  // sub-query and the number advances with the paced reveal.
  const metaParts: string[] = [];
  if (progress.sources > 0) {
    metaParts.push(formatCountAr(progress.sources, SOURCE_FORMS));
  }
  if (progress.queries > 0) {
    metaParts.push(formatCountAr(progress.queries, QUERY_FORMS));
  } else if (showsTopics && revealedCount > 0) {
    metaParts.push(`${QUERY_COUNTER_PREFIX}${toArabicDigits(revealedCount)}`);
  }
  const metaLine = metaParts.length > 0 ? metaParts.join(" · ") : null;

  return (
    <div
      dir="rtl"
      lang="ar"
      aria-busy={!isDone}
      className={cn(
        "w-full rounded-2xl border bg-card px-4 py-3 shadow-sm",
        className,
      )}
    >
      {/* Header: title + elapsed. The timer is deliberately OUTSIDE the live
          region below — announcing it would speak once per second. */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <Search
            className="h-3.5 w-3.5 shrink-0 text-primary"
            aria-hidden="true"
          />
          <span className="text-[13px] font-semibold text-foreground">
            {TITLE}
          </span>
        </div>
        <span className="text-[11px] tabular-nums text-muted-foreground">
          {formatElapsedAr(elapsedMs)}
        </span>
      </div>

      {/* The only live region: it holds the active stage label and nothing
          else, so screen readers announce stage TRANSITIONS only — not every
          status line, count bump, or token. */}
      <span role="status" aria-live="polite" className="sr-only">
        {STEPS[activeIndex].label}
      </span>

      {/* Bar — canonical Luna markup (AttachmentUploadCard). The card is
          dir="rtl", so the fill grows from the right edge. Width = stage anchor
          + capped time creep (see STAGE_ANCHORS): continuously moving, but it
          can only cross a milestone when the pipeline actually reaches it. The
          transition is LINEAR and matched to the tick so consecutive creep
          updates read as one smooth sweep instead of 4/sec ease-out stutters; a
          glossy highlight sweeps the filled portion for motion even when the
          creep has flattened out. Sweep is disabled under reduced-motion. */}
      <div
        className="mt-2.5 h-1 w-full overflow-hidden rounded-full bg-muted"
        aria-hidden="true"
      >
        <div
          className="relative h-full overflow-hidden rounded-full bg-primary transition-[width] duration-300 ease-linear motion-reduce:transition-none"
          style={{ width: `${barPercent}%` }}
        >
          <span className="absolute inset-0 animate-ds-bar-sweep bg-gradient-to-r from-transparent via-primary-fg to-transparent opacity-30 motion-reduce:hidden" />
        </div>
      </div>

      {/* Steps */}
      <ol className="mt-3 space-y-1.5">
        {STEPS.map((step, i) => {
          const state =
            isDone || i < activeIndex
              ? "done"
              : i === activeIndex
                ? "active"
                : "pending";
          return (
            <li key={step.key} className="flex items-start gap-2">
              <span
                className="mt-[3px] flex h-3.5 w-3.5 shrink-0 items-center justify-center"
                aria-hidden="true"
              >
                {state === "done" ? (
                  <Check className="h-3.5 w-3.5 text-primary" />
                ) : state === "active" ? (
                  <span className="h-2 w-2 animate-pulse rounded-full bg-primary" />
                ) : (
                  <span className="h-2 w-2 rounded-full border border-muted-foreground/40" />
                )}
              </span>

              <div className="min-w-0 flex-1">
                <p
                  className={cn(
                    "text-[13px] leading-5",
                    state === "active"
                      ? "font-medium text-foreground"
                      : state === "done"
                        ? "text-muted-foreground"
                        : "text-muted-foreground/50",
                  )}
                >
                  {step.label}
                </p>

                {/* Live evidence under the ACTIVE step only. Every sub-block
                    keeps a FIXED reserved height (detail 1 line + feed 2 lines +
                    meta 1 line) so the card never changes height for the whole
                    run — the active step just moves down the list. Decorative,
                    so the whole block is aria-hidden (the sr-only live region
                    above announces stage transitions instead). */}
                {state === "active" && (
                  <div className="mt-1 space-y-0.5" aria-hidden="true">
                    {/* Detail line — crossfades on every new topic (search
                        stages) or rotated phrase (the stages that stream none).
                        The magnifier marks a real sub-query; the dot marks a
                        phrase, so the two are never mistaken for each other. */}
                    <div className="flex items-start gap-1.5">
                      {showTopicAsDetail ? (
                        <Search className="mt-[3px] h-3 w-3 shrink-0 text-primary" />
                      ) : (
                        <span className="mt-[6px] h-1 w-1 shrink-0 rounded-full bg-primary" />
                      )}
                      <CrossfadeLine
                        text={detailText}
                        className="text-[11px] leading-4 text-muted-foreground"
                      />
                    </div>

                    {/* Recent-topics feed (searching only). Fixed 2-line box,
                        reserved even when empty, so the card height is constant
                        across every stage. Each new line eases in. */}
                    <div className="h-9 space-y-0.5 overflow-hidden">
                      {recentTopics.map((topic) => (
                        <div
                          key={topic}
                          className="flex items-start gap-1.5 animate-ds-fade-in motion-reduce:animate-none"
                        >
                          <span className="mt-[6px] h-1 w-1 shrink-0 rounded-full bg-current text-text-subtle" />
                          <span className="truncate text-[11px] leading-4 text-text-subtle">
                            {formatTopic(topic)}
                          </span>
                        </div>
                      ))}
                    </div>

                    {/* Counts / live query counter — one reserved line. */}
                    <div className="min-h-[1rem]">
                      {metaLine && (
                        <p className="text-[11px] leading-4 tabular-nums text-muted-foreground">
                          {metaLine}
                        </p>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
});
