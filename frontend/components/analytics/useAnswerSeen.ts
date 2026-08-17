"use client";

import { useEffect, useRef, type RefObject } from "react";
import { trackAnswerSeen } from "@/components/analytics/run-tracker";

/**
 * `answer_seen` — `.claude/plans/product_analytics.md` §3b / T15.
 *
 * This is the metric the whole chat-depth section exists to produce ("answers
 * generated but never read", joined to `llm_calls` for what those runs cost),
 * so "rendered" must not be allowed to mean "seen". The bubble may mount far
 * below the fold, or stream into a backgrounded tab. All of the following are
 * required before the event fires:
 *
 * 1. an `IntersectionObserver` reporting the bubble ≥50% visible,
 * 2. `document.visibilityState === "visible"`,
 * 3. held for ≥1s,
 * 4. and only AFTER `done` for that message (enforced by the tracker, which
 *    also needs the `done` stamp to compute `ms_since_done`).
 *
 * A bubble that streamed into a backgrounded tab was never read; counting it
 * as read would quietly invert the number.
 *
 * On T13: the 1s hold uses a `setTimeout`, and that is deliberate — it is a
 * dwell THRESHOLD, not an elapsed-time measurement, and it only ever runs
 * while the tab is visible (hiding the tab cancels it), so timer throttling
 * cannot distort it. Every `ms_*` prop is still a `Date.now()` difference
 * taken at the events themselves.
 */

const ANSWER_SEEN_RATIO = 0.5;
const ANSWER_SEEN_HOLD_MS = 1000;
/**
 * Floor for the tall-answer case. A deep_search answer is routinely several
 * viewports tall, and such a bubble can NEVER be 50% intersected — it would be
 * permanently excluded from `answer_seen`, which is precisely the population
 * this metric is about, and the exclusion would push the "never read" number
 * the wrong way. When the bubble is taller than the viewport we instead ask
 * that it fill ~half the viewport, expressed as the equivalent ratio.
 */
const MIN_RATIO = 0.05;

interface UseAnswerSeenParams {
  messageId: string;
  conversationId: string;
  /**
   * `Date.now()` of this message's `done`, or `null` when the run did not
   * complete in this tab (history, a still-streaming bubble). `null` arms
   * nothing — no observer is created, so scrolling through a long history
   * costs no observers at all.
   */
  doneAt: number | null;
}

export function useAnswerSeen({
  messageId,
  conversationId,
  doneAt,
}: UseAnswerSeenParams): RefObject<HTMLDivElement | null> {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (doneAt === null) return;
    if (typeof window === "undefined") return;
    if (typeof IntersectionObserver === "undefined") return;
    const node = ref.current;
    if (!node) return;

    let holdTimer: number | null = null;
    let isVisibleEnough = false;
    let fired = false;

    const clearHold = (): void => {
      if (holdTimer !== null) {
        window.clearTimeout(holdTimer);
        holdTimer = null;
      }
    };

    const evaluate = (): void => {
      if (fired) return;
      const ok = isVisibleEnough && document.visibilityState === "visible";
      if (!ok) {
        clearHold();
        return;
      }
      if (holdTimer !== null) return;
      holdTimer = window.setTimeout(() => {
        holdTimer = null;
        if (fired) return;
        // Re-check at fire time: the user may have scrolled away or
        // backgrounded the tab during the hold.
        if (!isVisibleEnough || document.visibilityState !== "visible") return;
        fired = true;
        trackAnswerSeen(messageId, conversationId);
        cleanup();
      }, ANSWER_SEEN_HOLD_MS);
    };

    // NOT a fudge factor, and deliberately not a flat 0.5.
    //
    // `IntersectionObserver` ratios are fractions of THE ELEMENT, so a bubble
    // taller than the viewport can never reach 0.5 no matter how attentively
    // it is read — a 3-viewport deep_search answer tops out around 0.33. A
    // hard 0.5 would therefore make `answer_seen` structurally impossible for
    // the longest answers, which are also the slowest and most expensive ones,
    // and "answers generated but never read" would be at its worst precisely
    // where the metric was blind. So: keep ≥50% OF THE BUBBLE wherever that is
    // achievable, and fall back to "the bubble fills ~half the viewport",
    // expressed as the equivalent element-fraction, when it is not.
    const height = node.getBoundingClientRect().height;
    const viewport = window.innerHeight || 0;
    const threshold =
      height > 0 && viewport > 0 && height > viewport
        ? Math.max(MIN_RATIO, Math.min(ANSWER_SEEN_RATIO, (viewport * ANSWER_SEEN_RATIO) / height))
        : ANSWER_SEEN_RATIO;

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          isVisibleEnough =
            entry.isIntersecting && entry.intersectionRatio >= threshold;
        }
        evaluate();
      },
      { threshold: [0, threshold] },
    );

    function cleanup(): void {
      clearHold();
      observer.disconnect();
      document.removeEventListener("visibilitychange", evaluate);
    }

    observer.observe(node);
    document.addEventListener("visibilitychange", evaluate);

    return cleanup;
  }, [messageId, conversationId, doneAt]);

  return ref;
}
