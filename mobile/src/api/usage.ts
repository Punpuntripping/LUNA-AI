import { apiFetch } from "./client";

// GET /usage — the composer reads ONE field: the plan's cap on conversations
// answering at the same time (parallel_conversations plan). The full
// UsageReport (bars, plan) stays web-only until the app shows usage
// (Phase 5); then it moves to @contracts instead of growing here.

export interface UsageParallelSnapshot {
  max_parallel_runs?: number;
}

export function getUsage(): Promise<UsageParallelSnapshot> {
  return apiFetch<UsageParallelSnapshot>("/usage");
}

/** Defaults to 1 when the field is missing or not a positive integer (web `maxParallelRuns`). */
export function maxParallelRuns(report: UsageParallelSnapshot | undefined): number {
  const raw = report?.max_parallel_runs;
  return typeof raw === "number" && Number.isInteger(raw) && raw >= 1 ? raw : 1;
}
