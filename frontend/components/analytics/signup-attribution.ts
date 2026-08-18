/**
 * Product analytics — which gate sent this tab to `/login`
 * (`.claude/plans/product_analytics.md` §5.4, the funnel's missing joint).
 *
 * `gate_cta_click` knows the gate but not whether an account followed;
 * `signup_completed` knows the account but not which gate earned it. Neither
 * can be joined to the other in SQL, because a signup fires on a DIFFERENT path
 * (`/login`, then wherever `?next=` lands) minutes after the click, and
 * `session_key` alone cannot say which of the two gates a reader saw was the
 * one they acted on. So the click leaves a note here and the signup reads it.
 *
 * ⚠ sessionStorage, never localStorage — §2. This is the same contract
 * `lib/analytics/session.ts` documents at length: a VISIT is tracked, a PERSON
 * is not. A durable key here would quietly turn gate attribution into
 * cross-visit visitor tracking and break the PDPL posture the whole feature was
 * built around. Dying with the tab is correct, and it costs nothing real: a
 * signup that happens in a NEW tab is a signup we simply cannot attribute, and
 * an unattributed signup is still counted.
 *
 * ⚠ FAILS CLOSED, like every other module in this folder: unusable storage
 * (privacy mode, SSR, a blocked third-party context) yields `null` and the
 * signup is recorded without attribution. Never throws (T9).
 */

import type { GateKind } from "@/lib/analytics/events";

/**
 * `v1` versions the VALUE shape. The key dies with the tab, so there is nothing
 * to migrate and no stale second key to clean up.
 */
export const GATE_ATTRIBUTION_STORAGE_KEY = "rayhan_analytics_gate_v1";

/** The gate a reader acted on, and the page they were reading when they did. */
export interface GateAttribution {
  gate_kind: GateKind;
  /** The PATHNAME the gate was shown on — never a URL, never a query (T4). */
  gate_path: string;
}

/**
 * Remember the gate that just sent this tab toward an account.
 *
 * LAST WRITE WINS on purpose. A reader who clicks the CTA on a regulation,
 * comes back, reads another and clicks again should be attributed to the gate
 * they actually converted from — the most recent one — not the first they ever
 * touched.
 */
export function stashGateAttribution(gateKind: GateKind, gatePath: string): void {
  try {
    window.sessionStorage.setItem(
      GATE_ATTRIBUTION_STORAGE_KEY,
      JSON.stringify({ gate_kind: gateKind, gate_path: gatePath }),
    );
  } catch {
    // T9 — attribution is a nice-to-have; the funnel still counts without it.
  }
}

/**
 * The stashed gate, or `null` when this tab has no click to attribute (someone
 * who opened `/login` from the header, or a brand-new tab).
 *
 * Deliberately NOT cleared on read. `signup_completed` fires at most once per
 * tab on its own latch, and leaving the note in place means a reload during the
 * email-confirmation round trip does not lose the attribution.
 */
export function readGateAttribution(): GateAttribution | null {
  try {
    const raw = window.sessionStorage.getItem(GATE_ATTRIBUTION_STORAGE_KEY);
    if (!raw) return null;

    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;

    const { gate_kind, gate_path } = parsed as Record<string, unknown>;
    if (typeof gate_kind !== "string" || typeof gate_path !== "string") {
      return null;
    }
    return { gate_kind: gate_kind as GateKind, gate_path };
  } catch {
    // Unusable storage, or a value some other tab left malformed. Either way
    // the signup is recorded unattributed rather than lost.
    return null;
  }
}
