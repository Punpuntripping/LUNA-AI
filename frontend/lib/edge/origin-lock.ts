// The frontend origin lock's decision (`library_guardrails_hardening.md` Phase
// 1b). Railway publishes this service on a `*.up.railway.app` hostname that
// answers to anyone, so every Cloudflare rule on `rayhanai.com` is one hostname
// away from being bypassed. Cloudflare injects `X-Edge-Secret` on every proxied
// request, and a request without it did not come through the edge.
//
// Pure: no Next imports, no env reads, no logging. The caller passes strings in
// and acts on the answer, so the whole truth table can be tested without a server.
//
// ⚠ NEVER IMPORT THIS FROM CLIENT CODE (see the note in `secret-match.ts`).
//
// ⚠ INERT UNTIL ARMED, AND THE ORDER BELOW IS WHAT MAKES THAT TRUE.
//   - `EDGE_SECRET` unset or blank passes everything, in every mode.
//   - Only the mode `enforce` can refuse a request: trimmed, as on the backend,
//     but case-exact. Unset, a typo, `ENFORCE`, `off` and `observe` all land in
//     observe, so a bad value can never start enforcing. The backend follows
//     the same rule.
//
// A mismatch is a host-based choice, and D3 sets it: Railway hostnames are old
// shared links, so they get a redirect. Anything else without the secret is
// someone reaching the origin with a spoofed Host, so it gets a refusal.

import { anyValueMatches, splitValues } from "@/lib/edge/secret-match";

/** Set by Cloudflare's Transform Rule on every proxied request. */
export const EDGE_SECRET_HEADER = "x-edge-secret";

const RAILWAY_HOST_SUFFIX = ".up.railway.app";
const ENFORCE_MODE = "enforce";

export type OriginLockAction = "pass" | "redirect" | "block";

/**
 * Closed vocabularies, on purpose. The raw Host and header values are
 * attacker-controlled, so they never enter a record: that keeps the logs clean
 * and keeps `hostClass × outcome` a small fixed set for the caller to throttle on.
 */
export type OriginLockHostClass = "railway" | "rayhanai" | "other" | "missing";
export type OriginLockOutcome = "missing" | "mismatch";
export type OriginLockModeLabel = "unset" | "observe" | "enforce" | "unknown";

/**
 * A request without the secret, in any mode: `action` says whether the lock
 * refused it or, in observe, only would have. Never holds a secret.
 */
export interface OriginLockRecord {
  hostClass: OriginLockHostClass;
  outcome: OriginLockOutcome;
  valueCount: number;
  mode: OriginLockModeLabel;
  action: OriginLockAction;
}

export interface OriginLockDecision {
  action: OriginLockAction;
  record: OriginLockRecord | null;
}

export interface OriginLockInput {
  /** The raw `Host` header. Never `request.nextUrl.host`: that is `localhost:PORT` in standalone. */
  host: string | null;
  /** The raw `x-edge-secret` header value, comma-joined if it arrived more than once. */
  edgeSecretHeader: string | null;
  mode: string | undefined;
  edgeSecret: string | undefined;
}

const PASS: OriginLockDecision = { action: "pass", record: null };

/** Lowercase and drop the port, leaving a bracketed IPv6 literal's own colons alone. */
function normalizeHost(host: string | null): string {
  const lowered = (host ?? "").trim().toLowerCase();
  if (lowered.startsWith("[")) {
    const closing = lowered.indexOf("]");
    return closing === -1 ? lowered : lowered.slice(0, closing + 1);
  }
  const colon = lowered.lastIndexOf(":");
  return colon === -1 ? lowered : lowered.slice(0, colon);
}

function classifyHost(host: string): OriginLockHostClass {
  if (!host) return "missing";
  if (host.endsWith(RAILWAY_HOST_SUFFIX)) return "railway";
  if (host === "rayhanai.com" || host.endsWith(".rayhanai.com")) return "rayhanai";
  return "other";
}

function modeLabel(mode: string | undefined): OriginLockModeLabel {
  if (!mode) return "unset";
  if (mode === ENFORCE_MODE) return "enforce";
  return mode === "observe" ? "observe" : "unknown";
}

function refusalFor(hostClass: OriginLockHostClass): OriginLockAction {
  return hostClass === "railway" ? "redirect" : "block";
}

export function decideOriginLock({
  host,
  edgeSecretHeader,
  mode: rawMode,
  edgeSecret,
}: OriginLockInput): OriginLockDecision {
  const expected = edgeSecret?.trim();
  if (!expected) return PASS;

  // The backend's `folded` cutover gate does not apply here: `splitValues` matches comma-joined copies, so this lock cannot refuse our own traffic that way.
  const values = splitValues(edgeSecretHeader);
  if (anyValueMatches(values, expected)) return PASS;

  const mode = rawMode?.trim();
  const hostClass = classifyHost(normalizeHost(host));
  const action = mode === ENFORCE_MODE ? refusalFor(hostClass) : "pass";

  return {
    action,
    record: {
      hostClass,
      outcome: values.length === 0 ? "missing" : "mismatch",
      valueCount: values.length,
      mode: modeLabel(mode),
      action,
    },
  };
}
