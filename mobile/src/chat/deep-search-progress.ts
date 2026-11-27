import type { DeepSearchStage, SSEAgentProgress } from "@contracts";

import { formatCount, type ArabicPlural } from "@/lib/format";

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
  /** «بحث شامل» only (`data.searches`): the run's parallel searches — how
   *  many in all and how many have finished. `null` on an ordinary deep
   *  search; kept when a later event omits it. */
  searches: { total: number; done: number } | null;
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

/**
 * «بحث شامل» `data.searches` → progress state (web chat-store
 * `mergeSearches`): N parallel searches, M finished. `done` is monotonic and
 * never above `total`; an event without a valid block keeps the previous value.
 */
export function mergeSearches(
  prev: DeepSearchProgressState["searches"],
  raw: unknown,
): DeepSearchProgressState["searches"] {
  if (typeof raw !== "object" || raw === null) return prev;
  const { total, done } = raw as { total?: unknown; done?: unknown };
  if (
    typeof total !== "number" ||
    typeof done !== "number" ||
    !Number.isFinite(total) ||
    !Number.isFinite(done) ||
    total < 1
  ) {
    return prev;
  }
  const t = Math.round(total);
  return { total: t, done: Math.min(t, Math.max(prev?.done ?? 0, Math.round(done), 0)) };
}

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
    searches: null,
    startedAt: nowMs,
    log: [],
  };
  const sources = typeof event.data?.sources === "number" ? Math.max(base.sources, event.data.sources) : base.sources;
  const queries = typeof event.data?.queries === "number" ? Math.max(base.queries, event.data.queries) : base.queries;
  const searches = mergeSearches(base.searches, event.data?.searches);
  const nextStage =
    stage && DEEP_SEARCH_STAGE_ORDER[stage] >= DEEP_SEARCH_STAGE_ORDER[base.stage] ? stage : base.stage;
  const stale = stage === null || nextStage !== stage;
  const text = stale ? base.text : (detail ?? (stage === base.stage ? base.text : null));
  const log = detail && base.log[base.log.length - 1] !== detail ? [...base.log, detail].slice(-MAX_LOG) : base.log;
  return { ...base, stage: nextStage, text, sources, queries, searches, log };
}

/** «بحث شامل» parallel searches in the tracker header (web DeepSearchProgress
 *  `SEARCH_FORMS`, same Arabic agreement). */
export const SEARCH_FORMS: ArabicPlural = {
  one: "بحث واحد",
  two: "بحثان متوازيان",
  few: "بحوث متوازية",
  many: "بحثًا متوازيًا",
};

/** «4 بحوث متوازية · اكتمل 1» — Latin digits (web `formatSearchesAr`). */
export function formatSearches(searches: { total: number; done: number }): string {
  return `${formatCount(searches.total, SEARCH_FORMS)} · اكتمل ${searches.done}`;
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

/** A run that never left "planning" searched nothing (the planner handed the
 * request back to the router, or the turn went to the prior-report check), so
 * its reply gets no chip. Same rule as the web store's `sealDeepSearchSummary`. */
export function isSealable(run: DeepSearchProgressState | null | undefined): run is DeepSearchProgressState {
  return !!run && run.stage !== "planning";
}

export function sealSummary(run: DeepSearchProgressState, nowMs: number): DeepSearchSummary {
  return { sources: run.sources, elapsedMs: Math.max(0, nowMs - run.startedAt), log: run.log };
}
