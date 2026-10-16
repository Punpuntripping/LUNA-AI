/**
 * Next-step chips (.claude/plans/next_step_suggestions.md §3.6–§3.7).
 *
 * ONE boundary parser for the two places the items enter a client: the
 * `next_steps` SSE event and the persisted `message.metadata.next_steps`. Both
 * come from an LLM via the backend, so a malformed item is DROPPED rather than
 * allowed to break the bubble — chips are never worth failing a render over.
 *
 * Zod-free port of the web's original zod schema (contracts take no third-party
 * packages), extended with the `answer` kind (unified_responder plan §6.3).
 * Equivalence with the zod reference (updated to the same rule) is pinned by
 * `lib/__tests__/contracts-parsers-equivalence.test.ts`.
 */

import type { NextStep, NextStepKind } from "./messages";

/**
 * D3: at most three chips. Action kinds are one-per-kind; ``answer`` chips
 * (unified_responder plan §6.3) are exempt from that dedup but capped at two.
 */
const MAX_NEXT_STEPS = 3;
const MAX_ANSWER_STEPS = 2;

const NEXT_STEP_KINDS: readonly string[] = ["narrow_search", "draft", "apply", "open", "answer"];

/**
 * What zod's `z.object` accepts as an object: non-null, not an array, not a
 * thenable, Map, Set or Date. (Plain JSON never yields the last four; the
 * checks keep the port exact.)
 */
export function isZodLikeObject(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const v = value as { then?: unknown; catch?: unknown };
  if (typeof v.then === "function" && typeof v.catch === "function") return false;
  if (value instanceof Map || value instanceof Set || value instanceof Date) return false;
  return true;
}

/** `z.string().trim().min(1)` — the trimmed string, or `null` when it fails. */
export function trimmedNonEmpty(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length >= 1 ? trimmed : null;
}

function parseNextStep(item: unknown): NextStep | null {
  if (!isZodLikeObject(item)) return null;
  const kind = item.kind;
  if (typeof kind !== "string" || !NEXT_STEP_KINDS.includes(kind)) return null;
  const label = trimmedNonEmpty(item.label);
  if (label === null) return null;
  const prompt = trimmedNonEmpty(item.prompt);
  if (prompt === null) return null;
  return { kind: kind as NextStepKind, label, prompt };
}

/**
 * Validate + normalise a raw `next_steps` value. Invalid items (incl. unknown
 * kinds) are skipped; duplicate action kinds keep the first occurrence;
 * `answer` items are exempt from that dedup but only the first two are kept;
 * the list is capped at 3 overall. Anything that is not an array yields `[]`.
 */
export function parseNextSteps(raw: unknown): NextStep[] {
  if (!Array.isArray(raw)) return [];
  const out: NextStep[] = [];
  const seenKinds = new Set<string>();
  let answers = 0;
  for (const item of raw) {
    const parsed = parseNextStep(item);
    if (parsed === null) continue;
    if (parsed.kind === "answer") {
      if (answers >= MAX_ANSWER_STEPS) continue;
      answers += 1;
    } else {
      if (seenKinds.has(parsed.kind)) continue;
      seenKinds.add(parsed.kind);
    }
    out.push(parsed);
    if (out.length >= MAX_NEXT_STEPS) break;
  }
  return out;
}
