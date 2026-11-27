// Stage logic shared by the live progress cards (no React, no store):
//   • DeepSearchProgress — «بحث عميق» (one search) and «بحث شامل» (parallel
//     searches + the merger), fed by `agent_progress`;
//   • FilesProgress — «تجهيز المستندات», the steps on the user's files before
//     the answer, fed by `ocr_progress`.
//
// Every card cell IS its own progress bar: pending = empty; active = creeps with
// the time since the stage began (exponential, capped below full); done = full.
// A stage is done only when a LATER stage really starts, so 100% always means a
// real pipeline milestone.

import type { DeepSearchStage, OcrStage } from "./contracts/sse";

export type StepStatus = "pending" | "active" | "done";

// ── Bars ────────────────────────────────────────────────────────────────────

/** Creep shape: 1 − e^(−k·t/expected) → ~92% of the bar at the expected time. */
const CREEP_K = 2.5;

/** Hard ceiling on the creep while a stage is still running. */
const CREEP_CAP = 0.94;

/** Fill of one cell, 0–100. `expectedMs` is the creep's time constant (≈ p50). */
export function fillPercent(status: StepStatus, stageMs: number, expectedMs: number): number {
  if (status === "done") return 100;
  if (status === "pending") return 0;
  const creep = 1 - Math.exp((-CREEP_K * Math.max(0, stageMs)) / expectedMs);
  return Math.min(creep, CREEP_CAP) * 100;
}

// ── Search cards: «بحث عميق» / «بحث شامل» ──────────────────────────────────

/** A card step. `merging` exists only on «بحث شامل» and has no backend stage. */
export type SearchStep = Exclude<DeepSearchStage, "done"> | "merging";

/**
 * Position of each step on the run's timeline. `merging` sits between the
 * reports (`aggregating`) and the chat summary (`writing`): the backend keeps
 * the stage at `aggregating` while the merger runs.
 */
const SEARCH_POSITION: Record<SearchStep | "done", number> = {
  planning: 0,
  searching: 1,
  evaluating: 2,
  aggregating: 3,
  merging: 3.5,
  writing: 4,
  done: 5,
};

/** Steps of an ordinary deep search, in order (3 + 2 on the card). */
export const DEEP_STEPS: readonly SearchStep[] = [
  "planning",
  "searching",
  "evaluating",
  "aggregating",
  "writing",
];

/** Steps of «بحث شامل» (3 + 3): the merger is added before «عرض الإجابة». */
export const EXTENDED_STEPS: readonly SearchStep[] = [
  "planning",
  "searching",
  "evaluating",
  "aggregating",
  "merging",
  "writing",
];

/**
 * The backend's line when every search ended but only one report came out — no
 * merge follows (extended/runner.py `_ALL_DONE_TEXT_AR`). Any other line at that
 * point means the merger is running.
 */
export const NO_MERGE_TEXT_AR = "اكتملت البحوث — إعداد الجواب";

/**
 * «بحث شامل»: every parallel search has finished and the run is still at
 * `aggregating` → the merger is writing the one answer («دمج المصادر»).
 */
export function isMerging(
  stage: DeepSearchStage,
  searches: { total: number; done: number } | null,
  text: string | null,
): boolean {
  return (
    searches !== null &&
    stage === "aggregating" &&
    searches.done >= searches.total &&
    text !== NO_MERGE_TEXT_AR
  );
}

/**
 * Status of one step of a search card given the run's (monotonic) stage.
 * `searching` stays active through `evaluating` — the rerankers run inside the
 * search loops — and completes with it when the reports start. Every other step
 * ends when the NEXT step of the same card starts.
 */
export function searchStepStatus(
  step: SearchStep,
  steps: readonly SearchStep[],
  stage: DeepSearchStage,
  merging: boolean,
): StepStatus {
  const cur = merging ? SEARCH_POSITION.merging : SEARCH_POSITION[stage];
  const start = SEARCH_POSITION[step];
  const i = steps.indexOf(step);
  const next = steps[i + 1];
  const end =
    step === "searching"
      ? SEARCH_POSITION.aggregating
      : next !== undefined
        ? SEARCH_POSITION[next]
        : SEARCH_POSITION.done;
  if (cur < start) return "pending";
  if (cur >= end) return "done";
  return "active";
}

/** The step a search card names as running now (the header's live region). */
export function activeSearchStep(
  steps: readonly SearchStep[],
  stage: DeepSearchStage,
  merging: boolean,
): SearchStep {
  if (merging && steps.includes("merging")) return "merging";
  const found = steps.find((s) => s === stage);
  return found ?? steps[steps.length - 1];
}

// ── Files card: «تجهيز المستندات» ───────────────────────────────────────────

/** The steps on the user's files, in the order they run (orchestrator OCR_STAGES). */
export const FILE_STEPS: readonly OcrStage[] = [
  "extracting",
  "summarizing",
  "arranging",
  "organizing",
  "threads",
];

/**
 * Status of one file step. A step the turn skips (no OCR needed, no map for this
 * plan…) is never active: it fills when a later step starts — the same rule as
 * the search cards.
 */
export function fileStepStatus(step: OcrStage, current: OcrStage): StepStatus {
  const cur = FILE_STEPS.indexOf(current);
  const at = FILE_STEPS.indexOf(step);
  if (at > cur) return "pending";
  if (at < cur) return "done";
  return "active";
}
