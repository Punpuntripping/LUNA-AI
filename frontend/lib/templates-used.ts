/**
 * «القوالب المستخدمة في الكتابة» — the parser lives in the shared contracts
 * folder (web + mobile) as a zod-free port, proven equivalent to the zod
 * original by `lib/__tests__/contracts-parsers-equivalence.test.ts`.
 *
 * The web keeps its WHATWG `URL` source check (the contracts default is a pure
 * prefix test for React Native, whose `URL` is not a WHATWG parser).
 *
 * Contract: `undefined` = the turn drafted nothing (key absent / not a list);
 * `[]` = a draft was produced WITHOUT a template.
 */

import { parseTemplatesUsed as parseShared } from "@/lib/contracts/templates-used";
import type { TemplateUsed } from "@/types";

function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

export function parseTemplatesUsed(raw: unknown): TemplateUsed[] | undefined {
  return parseShared(raw, isHttpUrl);
}
