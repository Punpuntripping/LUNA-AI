/**
 * «اقرأ الحكم كاملًا» — links from a reply to a page in our library
 * (`message.metadata.library_links`, artifact_analyzer_scope_full_text plan D14).
 *
 * A reply that answers from a ruling's own text carries a link to that ruling's
 * page. The backend writes the list; this is the one boundary parser for it. A
 * malformed item is DROPPED rather than allowed to break the bubble, and only an
 * in-app judgments path survives: the value comes from stored metadata, so a
 * stray absolute or script URL must never become a clickable link.
 */

import { isZodLikeObject, trimmedNonEmpty } from "./next-steps";

export interface LibraryLink {
  /** Button text, e.g. «اقرأ الحكم كاملًا». */
  label: string;
  /** In-app path, e.g. `/judgments/<slug>`. */
  url: string;
  /** The ruling's name. Shown when one reply carries more than one link. */
  title: string | null;
}

/** The most links one reply shows (the backend sends at most this many). */
export const MAX_LIBRARY_LINKS = 3;

const JUDGMENT_PATH = /^\/judgments\/[^\s/?#\\]+$/;

/** True for an in-app ruling page path and nothing else. */
export function isLibraryPath(value: string): boolean {
  return JUDGMENT_PATH.test(value);
}

/**
 * Validate a raw `library_links` value. `[]` when it is absent or not a list;
 * invalid items are skipped and a repeated url keeps its first occurrence.
 */
export function parseLibraryLinks(raw: unknown): LibraryLink[] {
  if (!Array.isArray(raw)) return [];
  const out: LibraryLink[] = [];
  const seen = new Set<string>();
  for (const item of raw) {
    if (!isZodLikeObject(item)) continue;
    const label = trimmedNonEmpty(item.label);
    const url = trimmedNonEmpty(item.url);
    if (label === null || url === null || !isLibraryPath(url) || seen.has(url)) continue;
    seen.add(url);
    out.push({ label, url, title: trimmedNonEmpty(item.title) });
    if (out.length === MAX_LIBRARY_LINKS) break;
  }
  return out;
}
