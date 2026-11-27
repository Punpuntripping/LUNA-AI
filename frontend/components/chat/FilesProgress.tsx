"use client";

import { memo } from "react";

import { cn } from "@/lib/utils";
import { FILE_STEPS, fileStepStatus, fillPercent } from "@/lib/progress-stages";
import { useChatStore } from "@/stores/chat-store";
import { StageCard, useNow, type StageCell } from "@/components/chat/StageCard";
import type { OcrStage } from "@/types";

// ─────────────────────────────────────────────────────────────────────────────
// EDITABLE CONFIG — every Arabic string of the card.
//
// «تجهيز المستندات»: the steps on this turn's files before the answer
// (`ocr_progress`, extended_search.md D34) — OCR → summary → the segmenter →
// the citation pins → the angle map. Same card as the deep search one: each
// step is its own bar, a step is ✓ only when a later one really starts.
// ─────────────────────────────────────────────────────────────────────────────

const TITLE = "تجهيز المستندات";

/** The header line while a step runs — owner wording 2026-11-27. */
const STEP_LINE: Record<OcrStage, string> = {
  extracting: "ريحان يستخرج المستندات",
  summarizing: "ريحان يلخص المستندات",
  arranging: "ريحان يرتب المستندات",
  organizing: "ريحان ينظم المستندات",
  threads: "ريحان يستخرج الخيوط",
};

/** Cell names (full / one word when three share a row on a phone). */
const STEP_COPY: Record<OcrStage, { label: string; short: string; span: 2 | 3 }> = {
  extracting: { label: "استخراج المستندات", short: "الاستخراج", span: 2 },
  summarizing: { label: "تلخيص المستندات", short: "التلخيص", span: 2 },
  arranging: { label: "ترتيب المستندات", short: "الترتيب", span: 2 },
  organizing: { label: "تنظيم المستندات", short: "التنظيم", span: 3 },
  threads: { label: "استخراج الخيوط", short: "الخيوط", span: 3 },
};

/**
 * Each step's creep time constant (≈ prod p50, not a promise). Prod 11-20…27:
 * segmenter 37 s median; segmenter start → stored angle map 2.8 min median.
 * OCR depends on the page count; the summary and the pins take seconds.
 */
const EXPECTED_MS: Record<OcrStage, number> = {
  extracting: 20_000,
  summarizing: 15_000,
  arranging: 45_000,
  organizing: 10_000,
  threads: 150_000,
};

// ─────────────────────────────────────────────────────────────────────────────

interface FilesProgressProps {
  conversationId: string;
  className?: string;
}

/**
 * Live card for the steps on the user's files, shown in place of the typing
 * indicator while `ocr_progress` says a step is running; it gives way to the
 * next indicator (the deep search card, or the typing line) when they end.
 * Only subscriber of the stream's `extracting*` fields.
 */
export const FilesProgress = memo(function FilesProgress({
  conversationId,
  className,
}: FilesProgressProps) {
  const stage = useChatStore((s) => s.streams[conversationId]?.extractingStage ?? null);
  const startedAt = useChatStore(
    (s) => s.streams[conversationId]?.extractingStartedAt ?? null,
  );
  const starts = useChatStore((s) => s.streams[conversationId]?.extractingStageStarts);

  const nowMs = useNow(startedAt !== null);
  const current: OcrStage = stage ?? "extracting";
  const elapsedMs = startedAt !== null ? Math.max(0, nowMs - startedAt) : 0;

  const cells: StageCell[] = FILE_STEPS.map((step) => {
    const status = fileStepStatus(step, current);
    const begin = starts?.[step] ?? nowMs;
    return {
      key: step,
      ...STEP_COPY[step],
      status,
      fill: fillPercent(status, Math.max(0, nowMs - begin), EXPECTED_MS[step]),
    };
  });

  return (
    <div className={cn("w-full", className)}>
      <StageCard
        title={TITLE}
        detail={STEP_LINE[current]}
        running
        elapsedMs={elapsedMs}
        liveLabel={STEP_COPY[current].label}
        cells={cells}
      />
    </div>
  );
});
