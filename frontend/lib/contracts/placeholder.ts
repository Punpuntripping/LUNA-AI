// The empty assistant placeholder («ريحان يفكّر») — how long a client keeps
// polling for it, and when it is declared dead. Pure ports of the web's
// `hooks/use-messages.ts` (`refetchInterval`) and `components/chat/MessageList.tsx`
// (`isDead`), which keep their own copies; change both together.

import type { Message } from "./messages";

/**
 * Background-recovery window for an empty assistant placeholder.
 *
 * While the newest message is a blank assistant row the run is either still
 * streaming or finishing in the background (the backend detaches a dropped
 * run, it never cancels it), so the client keeps refetching until it fills.
 * Past this age the run is considered dead — the poll stops AND the UI flips
 * the "thinking" spinner to a failed bubble.
 */
export const PLACEHOLDER_MAX_AGE_MS = 7 * 60 * 1000;

/** Poll ceiling for a placeholder whose run the server reports as live. */
export const PLACEHOLDER_LIVE_CAP_MS = 30 * 60 * 1000;

/** Poll cadence while a run is (or may be) live. */
export const PLACEHOLDER_POLL_MS = 4000;

/** Slow cadence when the server says nothing is running (deploy overlap). */
export const PLACEHOLDER_SLOW_POLL_MS = 10000;

/**
 * Refetch interval for a conversation's messages, from its NEWEST message and
 * the page's `run_active`. `false` = stop polling.
 *
 * - newest is not an empty assistant row → `false`;
 * - `run_active: true`  → 4 s until 30 min old (a slow run is not a dead one);
 * - `run_active: false` → 10 s through the 7-min window (an old instance can
 *   still finish the run during a deploy overlap);
 * - unknown (older backend) → 4 s through the 7-min window.
 */
export function placeholderPollMs(
  newest: Pick<Message, "role" | "content" | "created_at"> | undefined,
  runActive: boolean | undefined,
  nowMs: number,
): number | false {
  if (!newest || newest.role !== "assistant") return false;
  if ((newest.content ?? "").trim() !== "") return false;
  const ageMs = nowMs - new Date(newest.created_at).getTime();
  if (runActive === true) return ageMs < PLACEHOLDER_LIVE_CAP_MS ? PLACEHOLDER_POLL_MS : false;
  if (runActive === false) {
    return ageMs < PLACEHOLDER_MAX_AGE_MS ? PLACEHOLDER_SLOW_POLL_MS : false;
  }
  return ageMs < PLACEHOLDER_MAX_AGE_MS ? PLACEHOLDER_POLL_MS : false;
}

export interface DeadPlaceholderInput {
  /** A live stream is feeding this row right now. */
  isStreaming: boolean;
  /** `run_active` of the newest messages page (server truth, may be absent). */
  runActive: boolean | undefined;
  /** This row is the newest message of the conversation. */
  isNewest: boolean;
  /** `now - created_at` in ms. */
  ageMs: number;
}

/**
 * Whether an empty assistant placeholder should render as FAILED (with retry)
 * instead of «ريحان يفكّر». A row being streamed is never dead.
 * `run_active: false` = nothing is running → dead now. Otherwise the age decides
 * — except for the newest row while the server says its run is live.
 */
export function isDeadPlaceholder({
  isStreaming,
  runActive,
  isNewest,
  ageMs,
}: DeadPlaceholderInput): boolean {
  return (
    !isStreaming &&
    (runActive === false ||
      ((!isNewest || runActive === undefined) && ageMs >= PLACEHOLDER_MAX_AGE_MS))
  );
}
