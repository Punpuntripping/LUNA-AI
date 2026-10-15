/**
 * «القوالب المستخدمة في الكتابة» — which templates a writer draft was built on.
 *
 * ONE boundary parser for the two places the list enters a client: the
 * `templates_used` SSE event and the persisted `message.metadata.templates_used`.
 * A malformed item is DROPPED rather than allowed to break the bubble.
 *
 * Contract: `undefined` = the turn drafted nothing (key absent / not a list);
 * `[]` = a draft was produced WITHOUT a template.
 *
 * Zod-free port of the web's original zod schema. Equivalence with that schema
 * (given the same URL check) is pinned by
 * `lib/__tests__/contracts-parsers-equivalence.test.ts`.
 *
 * The http(s) source check is a PARAMETER: the web passes its WHATWG `URL`
 * check, while the default here is a pure prefix test, because React Native's
 * `URL` is not a WHATWG parser (no host validation, scheme case kept).
 */

import type { TemplateScope, TemplateUsed } from "./messages";
import { isZodLikeObject, trimmedNonEmpty } from "./next-steps";

/**
 * Pure "is this an absolute http(s) URL" test: scheme `http`/`https` (any
 * case) + `://` + a non-empty host with no whitespace. Stricter than WHATWG on
 * scheme-relative oddities (`http:example.com` is rejected) and on userinfo
 * (`https://user@host` is rejected), looser on host
 * syntax (an invalid IPv4/IPv6 literal is accepted). Sources are our own blog
 * URLs written by the backend, so neither edge occurs in practice.
 */
export function isHttpUrlLike(value: string): boolean {
  return /^https?:\/\/[^\s/?#@]+(?:[/?#]\S*)?$/i.test(value);
}

/** `z.string().nullable().optional()` → value, or `false` when it fails. */
function nullableString(value: unknown): string | null | undefined | false {
  if (value === undefined || value === null || typeof value === "string") return value;
  return false;
}

/**
 * Validate + normalise a raw `templates_used` value. Returns `undefined` when
 * `raw` is not an array; invalid items are skipped, duplicate ids keep the
 * first occurrence, and only http(s) source URLs survive.
 */
export function parseTemplatesUsed(
  raw: unknown,
  isHttpUrl: (value: string) => boolean = isHttpUrlLike,
): TemplateUsed[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  const out: TemplateUsed[] = [];
  const seen = new Set<string>();
  for (const item of raw) {
    if (!isZodLikeObject(item)) continue;
    const template_id = trimmedNonEmpty(item.template_id);
    if (template_id === null) continue;
    const title = trimmedNonEmpty(item.title);
    if (title === null) continue;
    const scope = item.scope;
    if (scope !== "user" && scope !== "system") continue;
    const subtype = nullableString(item.subtype);
    if (subtype === false) continue;
    const court = nullableString(item.court);
    if (court === false) continue;
    const sources = item.sources;
    if (sources !== undefined && !Array.isArray(sources)) continue;

    if (seen.has(template_id)) continue;
    seen.add(template_id);
    out.push({
      template_id,
      title,
      scope: scope as TemplateScope,
      subtype: subtype ?? null,
      court: court ?? null,
      sources: ((sources as unknown[] | undefined) ?? []).filter(
        (s): s is string => typeof s === "string" && isHttpUrl(s.trim()),
      ).map((s) => s.trim()),
    });
  }
  return out;
}
