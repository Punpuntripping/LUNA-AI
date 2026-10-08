/**
 * «القوالب المستخدمة في الكتابة» — which templates a writer draft was built on.
 *
 * ONE boundary parser for the two places the list enters the client: the
 * `templates_used` SSE event and the persisted `message.metadata.templates_used`.
 * A malformed item is DROPPED rather than allowed to break the bubble.
 *
 * Contract: `undefined` = the turn drafted nothing (key absent / not a list);
 * `[]` = a draft was produced WITHOUT a template.
 */

import { z } from "zod";
import type { TemplateUsed } from "@/types";

function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

const templateUsedSchema = z.object({
  template_id: z.string().trim().min(1),
  title: z.string().trim().min(1),
  scope: z.enum(["user", "system"]),
  subtype: z.string().nullable().optional(),
  court: z.string().nullable().optional(),
  sources: z.array(z.unknown()).optional(),
});

/**
 * Validate + normalise a raw `templates_used` value. Returns `undefined` when
 * `raw` is not an array; invalid items are skipped, duplicate ids keep the
 * first occurrence, and only http(s) source URLs survive.
 */
export function parseTemplatesUsed(raw: unknown): TemplateUsed[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  const out: TemplateUsed[] = [];
  const seen = new Set<string>();
  for (const item of raw) {
    const parsed = templateUsedSchema.safeParse(item);
    if (!parsed.success) continue;
    const { template_id, title, scope, subtype, court, sources } = parsed.data;
    if (seen.has(template_id)) continue;
    seen.add(template_id);
    out.push({
      template_id,
      title,
      scope,
      subtype: subtype ?? null,
      court: court ?? null,
      sources: (sources ?? []).filter(
        (s): s is string => typeof s === "string" && isHttpUrl(s.trim()),
      ).map((s) => s.trim()),
    });
  }
  return out;
}
