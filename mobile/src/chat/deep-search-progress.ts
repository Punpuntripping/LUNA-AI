import type { DeepSearchStage, SSEAgentProgress } from "@contracts";

// Live deep_search tracker state — pure port of the web's chat-store
// `setDeepSearchProgress` / `appendDeepSearchLog` / `sealDeepSearchSummary`
// (frontend/stores/chat-store.ts). The UI (M6) reads it; the engine feeds it.

export interface DeepSearchProgressState {
  stage: DeepSearchStage;
  /** Latest Arabic detail line for the ACTIVE stage. */
  text: string | null;
  /** Monotonic — an event without counts never zeroes them. */
  sources: number;
  queries: number;
  /** «بحث في …» topic lines seen so far (live query counter). */
  topicsSeen: number;
  /** Client clock at the first progress event. */
  startedAt: number;
  /** Stage lines + `status` lines, capped. */
  log: string[];
}

/** Sealed totals of a finished run, keyed by assistant message id. Session only. */
export interface DeepSearchSummary {
  sources: number;
  elapsedMs: number;
  log: string[];
}

const MAX_LOG = 200;

/** Prefix of a live sub-query status line (trailing space intended). */
export const DEEP_SEARCH_TOPIC_PREFIX = "بحث في ";

/** The two executors run in parallel, so stages can arrive out of order —
 *  the tracker never walks backwards. */
export const DEEP_SEARCH_STAGE_ORDER: Record<DeepSearchStage, number> = {
  planning: 0,
  searching: 1,
  evaluating: 2,
  aggregating: 3,
  writing: 4,
  done: 5,
};

export function applyProgress(
  prev: DeepSearchProgressState | null,
  event: SSEAgentProgress,
  nowMs: number,
): DeepSearchProgressState {
  // `stage`/`text` may be "" — an empty stage is treated as "unchanged".
  const stage: DeepSearchStage | null = event.stage && event.stage in DEEP_SEARCH_STAGE_ORDER ? event.stage : null;
  const detail = (event.text ?? "").trim() || null;
  const base: DeepSearchProgressState = prev ?? {
    stage: stage ?? "planning",
    text: null,
    sources: 0,
    queries: 0,
    topicsSeen: 0,
    startedAt: nowMs,
    log: [],
  };
  const sources = typeof event.data?.sources === "number" ? Math.max(base.sources, event.data.sources) : base.sources;
  const queries = typeof event.data?.queries === "number" ? Math.max(base.queries, event.data.queries) : base.queries;
  const nextStage =
    stage && DEEP_SEARCH_STAGE_ORDER[stage] >= DEEP_SEARCH_STAGE_ORDER[base.stage] ? stage : base.stage;
  const stale = stage === null || nextStage !== stage;
  const text = stale ? base.text : (detail ?? (stage === base.stage ? base.text : null));
  const log = detail && base.log[base.log.length - 1] !== detail ? [...base.log, detail].slice(-MAX_LOG) : base.log;
  return { ...base, stage: nextStage, text, sources, queries, log };
}

/** A `status` line. Returns `prev` unchanged when there is no live run (other
 *  families emit status lines too) or on an exact repeat. */
export function applyStatus(prev: DeepSearchProgressState | null, text: string): DeepSearchProgressState | null {
  const line = text.trim();
  if (!prev || !line || prev.log[prev.log.length - 1] === line) return prev;
  const log = [...prev.log, line].slice(-MAX_LOG);
  if (line.startsWith(DEEP_SEARCH_TOPIC_PREFIX)) {
    return { ...prev, text: line, topicsSeen: prev.topicsSeen + 1, log };
  }
  return { ...prev, log };
}

export function sealSummary(run: DeepSearchProgressState, nowMs: number): DeepSearchSummary {
  return { sources: run.sources, elapsedMs: Math.max(0, nowMs - run.startedAt), log: run.log };
}
