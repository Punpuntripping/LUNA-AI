/**
 * Next-step chips (.claude/plans/next_step_suggestions.md §3.6–§3.7).
 *
 * ONE boundary parser for the two places the items enter the client: the
 * `next_steps` SSE event and the persisted `message.metadata.next_steps`. Both
 * come from an LLM via the backend, so a malformed item is DROPPED rather than
 * allowed to break the bubble — chips are never worth failing a render over.
 */

import { z } from "zod";
import type { NextStep } from "@/types";

/** D3: at most three chips, each a different kind. */
const MAX_NEXT_STEPS = 3;

const nextStepSchema = z.object({
  kind: z.enum(["narrow_search", "draft", "apply", "open"]),
  label: z.string().trim().min(1),
  prompt: z.string().trim().min(1),
});

/**
 * Validate + normalise a raw `next_steps` value. Invalid items are skipped,
 * duplicate kinds keep the first occurrence, and the list is capped at 3.
 * Anything that is not an array yields `[]`.
 */
export function parseNextSteps(raw: unknown): NextStep[] {
  if (!Array.isArray(raw)) return [];
  const out: NextStep[] = [];
  const seenKinds = new Set<string>();
  for (const item of raw) {
    const parsed = nextStepSchema.safeParse(item);
    if (!parsed.success) continue;
    if (seenKinds.has(parsed.data.kind)) continue;
    seenKinds.add(parsed.data.kind);
    out.push(parsed.data);
    if (out.length >= MAX_NEXT_STEPS) break;
  }
  return out;
}
