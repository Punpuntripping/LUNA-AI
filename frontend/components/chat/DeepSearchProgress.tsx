"use client";

import { memo, useEffect, useState } from "react";

import { cn } from "@/lib/utils";
import {
  DEEP_STEPS,
  EXTENDED_STEPS,
  activeSearchStep,
  fillPercent,
  isMerging,
  searchStepStatus,
  type SearchStep,
} from "@/lib/progress-stages";
import { useChatStore } from "@/stores/chat-store";
import { TypingIndicator } from "@/components/chat/TypingIndicator";
import {
  NOTE_LEAVE_AR,
  StageCard,
  StageNote,
  formatCountAr,
  useNow,
  usePhraseIndex,
  type ArabicPlural,
  type StageCell,
} from "@/components/chat/StageCard";

// ─────────────────────────────────────────────────────────────────────────────
// EDITABLE CONFIG
//
// Every Arabic string the tracker can render lives here (there is no i18n
// framework — same convention as AGENT_PHRASES in TypingIndicator.tsx).
// Two runs share this card:
//   • «بحث عميق» — one search (5 stages, 3 + 2);
//   • «بحث شامل» — parallel searches (`data.searches` on the events) and the
//     merger: 6 stages, 3 + 3, «دمج المصادر» before «عرض الإجابة».
// ─────────────────────────────────────────────────────────────────────────────

/** Card title — the first word of the header line. */
const DEEP_TITLE = "بحث عميق";
const EXTENDED_TITLE = "بحث شامل";

/** Header detail once the run has finished (before the card collapses). */
const DONE_LINE = "اكتملت الإجابة";

/**
 * Standing note ABOVE the card («شكرًا لتجربتك ريحان», StageNote). Its whole
 * job is to set the expectation before the wait starts: this run is long *by
 * design*, and leaving the tab is safe — the answer is here on return.
 */
const DEEP_NOTE_BODY =
  "البحث العميق مصمَّم ليمرّ على كل المصادر ذات الصلة بسؤالك، وقد يستغرق من 3 إلى 5 دقائق. " +
  NOTE_LEAVE_AR;
const EXTENDED_NOTE_BODY =
  "البحث الشامل يبحث في خيوط ملفك ببحوث متوازية ثم يدمجها في جواب واحد، وقد يستغرق من 5 إلى 10 دقائق. " +
  NOTE_LEAVE_AR;

interface StepCopy {
  /** Full name — shown whenever it fits; always the screen-reader name. */
  label: string;
  /** One-word fallback when three cells share a row and don't fit (phones). */
  short: string;
}

// Each step names what the pipeline is ACTUALLY doing at that point:
//   searching   = the executors' expanders + vector search
//   evaluating  = the per-sub-query rerankers. They live INSIDE the search
//                 loops, so this stage overlaps the tail of `searching` —
//                 hence the dashed seam between the two cells.
//   aggregating = the AGGREGATOR writing the answer (one search) — or, on
//                 «بحث شامل», every search's aggregator writing its report
//   merging     = «بحث شامل» only: the merger turning the reports into ONE
//                 answer (the backend stays at `aggregating`; every search done)
//   writing     = the responder turning that answer into the chat summary
const DEEP_COPY: Record<SearchStep, StepCopy> = {
  planning: { label: "تخطيط البحث", short: "التخطيط" },
  searching: { label: "البحث في الأنظمة والأحكام", short: "البحث" },
  evaluating: { label: "تقييم النتائج وترتيبها", short: "التقييم" },
  aggregating: { label: "كتابة الإجابة", short: "كتابة الإجابة" },
  merging: { label: "دمج المصادر", short: "الدمج" },
  writing: { label: "عرض الإجابة", short: "عرض الإجابة" },
};

const EXTENDED_COPY: Record<SearchStep, StepCopy> = {
  ...DEEP_COPY,
  aggregating: { label: "كتابة التقارير", short: "التقارير" },
  merging: { label: "دمج المصادر", short: "الدمج" },
  writing: { label: "عرض الإجابة", short: "العرض" },
};

/** Retrieved sources in the header ("24 مصدرًا") — same wording as the chip. */
const SOURCE_FORMS: ArabicPlural = {
  one: "مصدر واحد",
  two: "مصدران",
  few: "مصادر",
  many: "مصدرًا",
};

/** «بحث شامل» parallel searches in the header («4 بحوث متوازية»). */
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

/** Phones: «اكتمل 1 من 4» — the long form pushes the timer off the card. */
export function formatSearchesShortAr(searches: { total: number; done: number }): string {
  return `اكتمل ${searches.done} من ${searches.total}`;
}

/**
 * Cosmetic labels rotated in the header detail so the card never looks frozen.
 * The backend never relays sub-query lines (they expose the search logic —
 * owner, 2026-10-26), so these name a real running stage — never a query; they
 * do NOT fake counts or move a bar. `writing` has none: it shows the backend's
 * own label (`progress.text`).
 */
const DEEP_PHRASES: Partial<Record<SearchStep, readonly string[]>> = {
  planning: ["تحليل السؤال…", "تحديد نطاق البحث…", "اختيار المصادر…"],
  searching: ["صياغة استعلامات البحث…", "البحث في المصادر…", "جمع النتائج المرشّحة…"],
  evaluating: ["مطابقة النتائج بالأسئلة…", "استبعاد غير المتصل…", "ترجيح الأقوى…"],
  aggregating: ["ربط الأحكام بالمصادر…", "صياغة الإجابة…", "توثيق المراجع…"],
};

const EXTENDED_PHRASES: Partial<Record<SearchStep, readonly string[]>> = {
  planning: ["تجهيز البحوث المتوازية…"],
  searching: DEEP_PHRASES.searching,
  evaluating: DEEP_PHRASES.evaluating,
  aggregating: ["كتابة تقارير البحوث…", "ربط الأحكام بالمصادر…", "توثيق المراجع…"],
  merging: ["دمج التقارير في جواب واحد…", "التوفيق بين التقارير…", "توثيق المراجع…"],
};

/**
 * Each stage's creep time constant (≈ observed p50), not a promise.
 * «بحث عميق» — Logfire p50s over 109 turns (12–26 Oct 2026): planning ~19s,
 * aggregator 41–84s, responder ~57s; search ~30s.
 * «بحث شامل» — offline e2e 2026-11-24 + prod 11-27: searches with their reports
 * 4–6 min, the merger ~3 min (longer under load).
 */
const DEEP_EXPECTED_MS: Record<SearchStep, number> = {
  planning: 20_000,
  searching: 35_000,
  evaluating: 20_000,
  aggregating: 60_000,
  merging: 60_000,
  writing: 55_000,
};

const EXTENDED_EXPECTED_MS: Record<SearchStep, number> = {
  planning: 5_000,
  searching: 120_000,
  evaluating: 60_000,
  aggregating: 120_000,
  merging: 200_000,
  writing: 55_000,
};

// ─────────────────────────────────────────────────────────────────────────────

interface DeepSearchProgressProps {
  /** The conversation whose run this tracker shows (each has its own slot). */
  conversationId: string;
  className?: string;
}

/**
 * Live step-tracker for a running `deep_search` turn, shown in place of the
 * generic TypingIndicator. «بحث شامل» when the events carry `data.searches`.
 *
 * Driven exclusively by real pipeline events: a cell only reaches 100% when
 * the pipeline has actually moved past it.
 *
 * Render isolation: this is the ONLY subscriber of
 * `chat-store.streams[cid].deepSearchProgress`. Keep it that way — the message
 * list must not re-render on progress events or the fluid-streaming reveal gets
 * choppy. The elapsed timer likewise ticks on local state, never the store.
 */
export const DeepSearchProgress = memo(function DeepSearchProgress({
  conversationId,
  className,
}: DeepSearchProgressProps) {
  const progress = useChatStore(
    (s) => s.streams[conversationId]?.deepSearchProgress ?? null,
  );
  const startedAt = progress?.startedAt ?? null;
  const isDone = progress?.stage === "done";
  const extended = (progress?.searches ?? null) !== null;
  const steps = extended ? EXTENDED_STEPS : DEEP_STEPS;
  const merging = progress
    ? isMerging(progress.stage, progress.searches, progress.text)
    : false;
  const activeKey: SearchStep | null =
    progress && !isDone ? activeSearchStep(steps, progress.stage, merging) : null;

  const nowMs = useNow(startedAt !== null && !isDone);
  const elapsedMs = startedAt !== null ? Math.max(0, nowMs - startedAt) : 0;

  // When each step was first seen — the origin of that step's creep. Kept per
  // step (not just the current one) because `searching` keeps creeping while
  // `evaluating` runs beside it. Reset on a fresh run.
  const [stepStarts, setStepStarts] = useState<Partial<Record<SearchStep, number>>>({});
  useEffect(() => {
    setStepStarts({});
  }, [startedAt]);
  useEffect(() => {
    if (activeKey === null) return;
    setStepStarts((prev) =>
      prev[activeKey] !== undefined ? prev : { ...prev, [activeKey]: Date.now() },
    );
  }, [activeKey, startedAt]);

  const phrasesByStep = extended ? EXTENDED_PHRASES : DEEP_PHRASES;
  const phrases = activeKey ? phrasesByStep[activeKey] : undefined;
  const phraseIndex = usePhraseIndex(activeKey, phrases?.length ?? 0);

  // A deep_search run whose backend never emitted `agent_progress` (deploy
  // skew, resume leg without the hook) degrades to the generic indicator
  // rather than rendering an empty shell. The note still belongs here: that
  // degraded run is just as long.
  if (!progress) {
    return (
      <div className={cn("w-full space-y-2", className)}>
        <StageNote running body={DEEP_NOTE_BODY} />
        <TypingIndicator conversationId={conversationId} />
      </div>
    );
  }

  const copy = extended ? EXTENDED_COPY : DEEP_COPY;
  const expected = extended ? EXTENDED_EXPECTED_MS : DEEP_EXPECTED_MS;

  // Header detail: the active step's rotating phrase; `writing` (no phrases)
  // shows the backend's own label; a finished run says so.
  const detail = isDone
    ? DONE_LINE
    : phrases
      ? phrases[phraseIndex % phrases.length]
      : (progress.text ?? "");

  const cells: StageCell[] = steps.map((step, i) => {
    const status = searchStepStatus(step, steps, progress.stage, merging);
    const begin =
      stepStarts[step] ??
      (step === "searching" ? stepStarts.evaluating : undefined) ??
      nowMs;
    // Deep: three cells on row 1, two on row 2. Shamil: three and three.
    const span: 2 | 3 = extended || i < 3 ? 2 : 3;
    return {
      key: step,
      label: copy[step].label,
      short: copy[step].short,
      span,
      seamBefore: step === "evaluating",
      status,
      fill: fillPercent(status, Math.max(0, nowMs - begin), expected[step]),
    };
  });

  return (
    <div className={cn("w-full space-y-2", className)}>
      <StageNote
        running={!isDone}
        body={extended ? EXTENDED_NOTE_BODY : DEEP_NOTE_BODY}
      />
      <StageCard
        title={extended ? EXTENDED_TITLE : DEEP_TITLE}
        detail={detail}
        running={!isDone}
        elapsedMs={elapsedMs}
        liveLabel={isDone || activeKey === null ? DONE_LINE : copy[activeKey].label}
        cells={cells}
        extras={
          <>
            {progress.searches && (
              <span className="min-w-0 truncate text-foreground">
                <span className="hidden sm:inline">{formatSearchesAr(progress.searches)}</span>
                <span className="sm:hidden">{formatSearchesShortAr(progress.searches)}</span>
              </span>
            )}
            {progress.sources > 0 && (
              // «بحث شامل» on a phone: the searches count wins the room.
              <span
                className={cn(
                  "hidden shrink-0",
                  progress.searches ? "sm:inline" : "min-[380px]:inline",
                )}
              >
                {formatCountAr(progress.sources, SOURCE_FORMS)}
              </span>
            )}
          </>
        }
      />
    </div>
  );
});
