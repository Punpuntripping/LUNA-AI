/**
 * Product analytics — the beacon client
 * (`.claude/plans/product_analytics.md` §5.1).
 *
 * `track(name, props)` is the ONLY entry point. It buffers, batches (≤20 per
 * request, the endpoint's cap) and flushes to `POST /api/v1/public/events` via
 * `navigator.sendBeacon`, falling back to `fetch(…, { keepalive: true })`.
 *
 * ⚠ FIRE AND FORGET, permanently (T9). `track()` never throws, never returns a
 * promise a caller has to handle, and is a silent no-op when the session key is
 * unavailable. A failed flush is DROPPED — never retried, never queued to disk,
 * never surfaced. A reader must never see a degraded page because a tracker
 * failed, and a retry loop against a 503 is exactly how a tracker takes a page
 * down with it.
 *
 * ⚠ T2 — PURE CLIENT, PERMANENTLY. The library wings run ISR with a SHARED
 * cache: anything emitted during a server render is baked into the page every
 * subsequent visitor receives, so one bake would report thousands of identical
 * events. Every function here is a no-op when `window` is undefined, and the
 * module must never be imported into a server component's render path.
 *
 * ⚠ T5 — the unconditional flush hangs off `visibilitychange → hidden`, not
 * `unload`. `unload` does not fire reliably on mobile Safari, which is exactly
 * the population question 1 is about; `pagehide` is registered alongside it as
 * the iOS belt-and-braces, and it only FLUSHES — it emits nothing, so nothing
 * is double-counted.
 */

import type { AnalyticsEventName, AnalyticsEventPayload } from "@/lib/analytics/events";
import { getAnalyticsSessionKey } from "@/lib/analytics/session";
import { getAccessToken } from "@/lib/api";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const API_PREFIX = "/api/v1";
const ENDPOINT = `${API_BASE}${API_PREFIX}/public/events`;

/** The endpoint's batch cap (§5.5). Anything larger is split across requests. */
const MAX_BATCH = 20;
/**
 * Short enough that a reader who lands and leaves within a few seconds is still
 * counted, long enough that a burst of route changes rides one request.
 */
const FLUSH_DEBOUNCE_MS = 2_000;
/**
 * Hard ceiling on the buffer. If flushing is impossible (offline, blocked
 * beacon) events must not accumulate without bound in a long chat session —
 * the OLDEST are dropped, because the recent ones are the ones still capable of
 * completing a funnel.
 */
const MAX_BUFFER = 100;

let buffer: AnalyticsEventPayload[] = [];
let flushTimer: ReturnType<typeof setTimeout> | null = null;
let listenersBound = false;

/** `window` exists AND storage gave us a session — the two preconditions. */
function isBrowser(): boolean {
  return typeof window !== "undefined" && typeof document !== "undefined";
}

/**
 * The current pathname — NEVER the query string (T4). `?q=` on the navigation
 * search surfaces is user-typed legal text; sending it would put case
 * descriptions in an analytics table.
 */
function currentPath(): string | undefined {
  try {
    const path = window.location?.pathname;
    return typeof path === "string" && path.length > 0 ? path : undefined;
  } catch {
    return undefined;
  }
}

/**
 * Bind the departure flush once, lazily, on the first tracked event — so
 * importing this module costs nothing and a page that never tracks never
 * touches the document.
 */
function bindDepartureFlush(): void {
  if (listenersBound || !isBrowser()) return;
  listenersBound = true;
  try {
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") flushAnalytics();
    });
    // iOS Safari can skip `visibilitychange` on a tab close; `pagehide` is the
    // one that survives there. Flush only — no event is emitted here.
    window.addEventListener("pagehide", () => flushAnalytics());
  } catch {
    // A document that refuses listeners simply loses the departure flush; the
    // debounce still ships everything that was buffered before it.
  }
}

function scheduleFlush(): void {
  if (flushTimer !== null) return;
  try {
    flushTimer = setTimeout(() => {
      flushTimer = null;
      flushAnalytics();
    }, FLUSH_DEBOUNCE_MS);
  } catch {
    flushTimer = null;
  }
}

/**
 * Ship one batch. Returns nothing and awaits nothing: whether the request
 * lands is not this module's business.
 *
 * `sendBeacon` cannot set headers, so an AUTHED actor is sent with
 * `fetch(…, { keepalive: true })` carrying the bearer token — the endpoint
 * reads it opportunistically to fill `user_id` / `user_type` (§4, §5.5), and
 * without it every authed event would classify as anonymous and question 6
 * ("authed or anonymous?") would answer `anon` for everyone. `keepalive`
 * fetches, like beacons, outlive the document.
 */
function send(batch: AnalyticsEventPayload[]): void {
  let body: string;
  try {
    body = JSON.stringify({ events: batch });
  } catch {
    return; // Unserialisable props — drop the batch, never the page.
  }

  let token: string | null = null;
  try {
    token = getAccessToken();
  } catch {
    token = null;
  }

  if (!token) {
    try {
      if (typeof navigator !== "undefined" && typeof navigator.sendBeacon === "function") {
        const blob = new Blob([body], { type: "application/json" });
        if (navigator.sendBeacon(ENDPOINT, blob)) return;
      }
    } catch {
      // Beacon refused (quota, blocked context) → fall through to fetch.
    }
  }

  try {
    void fetch(ENDPOINT, {
      method: "POST",
      keepalive: true,
      credentials: "omit",
      headers: token
        ? { "Content-Type": "application/json", Authorization: `Bearer ${token}` }
        : { "Content-Type": "application/json" },
      body,
    }).catch(() => {
      // Dropped. Never retried (T9).
    });
  } catch {
    // Dropped.
  }
}

/**
 * Ship everything buffered, now. Safe to call at any time and from any number
 * of places — an empty buffer is a no-op, and the buffer is cleared before the
 * request so a re-entrant call cannot send the same event twice.
 *
 * Called unconditionally on `visibilitychange → hidden`; also exported so the
 * tracker can flush immediately after emitting `page_exit`, which must not wait
 * on a debounce timer in a tab that is already hidden (T13: hidden-tab timers
 * are throttled and may never run again).
 */
export function flushAnalytics(): void {
  try {
    if (flushTimer !== null) {
      clearTimeout(flushTimer);
      flushTimer = null;
    }
    if (buffer.length === 0) return;

    const pending = buffer;
    buffer = [];
    for (let i = 0; i < pending.length; i += MAX_BATCH) {
      send(pending.slice(i, i + MAX_BATCH));
    }
  } catch {
    // Never throws (T9).
  }
}

/**
 * Record one event. Fire-and-forget: no return value, no promise, no throw.
 *
 * A `path` inside `props` is lifted to the payload's own `path` column so call
 * sites can keep passing it the way the §3 tables describe without producing a
 * duplicate. Everything else travels in `props`.
 */
export function track(
  name: AnalyticsEventName,
  props?: Record<string, unknown>,
): void {
  try {
    if (!isBrowser()) return;

    // Fails closed: no key ⇒ tracking is off for this tab (§2 / T3). An
    // unkeyed event cannot be grouped into a session, so it would inflate
    // every denominator in §6 while answering nothing.
    const sessionKey = getAnalyticsSessionKey();
    if (!sessionKey) return;

    let path = currentPath();
    let rest: Record<string, unknown> | undefined;
    if (props) {
      const { path: propPath, ...others } = props;
      if (typeof propPath === "string" && propPath.length > 0) path = propPath;
      rest = Object.keys(others).length > 0 ? others : undefined;
    }

    const payload: AnalyticsEventPayload = { event_name: name, session_key: sessionKey };
    if (path) payload.path = path;
    if (rest) payload.props = rest;

    buffer.push(payload);
    if (buffer.length > MAX_BUFFER) buffer = buffer.slice(-MAX_BUFFER);

    bindDepartureFlush();
    if (buffer.length >= MAX_BATCH) flushAnalytics();
    else scheduleFlush();
  } catch {
    // Never throws (T9). Analytics must never break a page.
  }
}
