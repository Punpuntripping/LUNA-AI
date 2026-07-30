// Query-string helpers for FILTERED public library hubs (today: /judgments).
// Pure string/URL work — no data fetching, no client state. The hubs are server
// components: a filter is a LINK that changes the URL, not React state, so these
// helpers are what turn "the active filters" into hrefs and back.

/** Next 15 hands a page `searchParams` value as string | string[] | undefined. */
export type RawSearchParams = Record<string, string | string[] | undefined>;

/**
 * Read one search param as a trimmed string. A repeated param (`?q=a&q=b`)
 * arrives as an array — take the first, never render `"a,b"`. Missing/blank → "".
 */
export function readParam(params: RawSearchParams, key: string): string {
  const raw = params[key];
  const value = Array.isArray(raw) ? raw[0] : raw;
  return (value ?? "").trim();
}

/**
 * Encode a filter set as a query string (no leading "?"), dropping empty
 * values. Key order is the object's insertion order, so callers get stable,
 * cache-friendly URLs. Values are percent-encoded — Arabic filter values
 * (domains, free text) are safe to pass straight in.
 */
export function toFilterQuery(
  filters: Record<string, string | undefined>,
): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    const trimmed = value?.trim();
    if (trimmed) search.set(key, trimmed);
  }
  return search.toString();
}

/** `basePath` + the encoded filter query (or the bare path when unfiltered). */
export function hrefWithFilters(
  basePath: string,
  filters: Record<string, string | undefined>,
): string {
  const query = toFilterQuery(filters);
  return query ? `${basePath}?${query}` : basePath;
}
