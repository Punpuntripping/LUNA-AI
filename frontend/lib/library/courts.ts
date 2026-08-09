// The 12 court buckets of the /judgments wing — «الجهة القضائية».
//
// ⚠ `shared/library/courts.py` IS THE SOURCE OF TRUTH. This file is a MIRROR of
// its `_COURTS` dict: the slug keys, the Arabic labels, and — critically — the
// INSERTION ORDER, which is corpus-volume order and IS the browse order.
// Alphabetical would bury المحكمة التجارية (20,335 rows) under المحكمة العامة
// (69). The two files must be edited in LOCKSTEP, in the same commit; the
// Python module additionally owns the raw `cases.court` → bucket variant map
// (the query predicate), which has no reason to exist in the browser and is
// deliberately NOT mirrored here.
//
// This is the same relationship `lib/library/court-levels.ts` has with
// `agents/deep_search_v4/shared/court_levels.py`: pure data + pure string
// helpers, no fetching, no client state, safe in both graphs.
//
// ⚠ THE SLUGS ARE ARABIC, and that is a deliberate departure from the
// «structural path segments are Latin» rule in `.claude/plans/library_sectors.md`
// (D4). That rule's justification was SEO neutrality and the whole /judgments
// wing is `noindex, nofollow` behind the PDPL gate, so there is no SEO to be
// neutral about — the user asked for Arabic explicitly. Two consequences:
//
//   1. A Next dynamic-route param arrives PERCENT-ENCODED for non-ASCII
//      (`%D8%A7%D9%84…`). Every entry point runs it through
//      `normalizeCourtSlug()` BEFORE comparing it to the vocabulary — a raw
//      `params.court` never matches `COURT_LABELS` on its own.
//   2. Anything sent onward is sent DECODED exactly once: the backend takes the
//      court as a QUERY param (`?court=…`), where `URLSearchParams` does the one
//      encode; hrefs carry the raw Arabic (the established pattern for Arabic
//      document slugs — `JudgmentCard` links `/judgments/${item.slug}`).
//      Encoding a param that arrived encoded produces `%25D8…`, which 404s.
//
// Counts, feeds and the raw-variant lists live server-side. Nothing here reads
// the corpus.

/**
 * `[slug, label]` for the 12 buckets, in the Python module's insertion order.
 *
 * One tuple per bucket rather than two parallel literals ON PURPOSE: the slug
 * and its label are the same Arabic text differing only by the hyphens, and
 * keeping them apart in an RTL editor is how a mismatched pair gets shipped.
 */
const COURTS = [
  // 20,335 rows
  ["المحكمة-التجارية", "المحكمة التجارية"],
  // 2,625
  ["ديوان-المظالم-تجارية", "ديوان المظالم — الدائرة التجارية"],
  // 2,281 — the untyped ZATCA residual (the LARGEST tax bucket; see the Python
  // module's docstring: the tax-type split only reaches 54% of that corpus).
  ["اللجان-الضريبية-عام", "اللجان الضريبية والزكوية — عام"],
  // 1,879
  ["ديوان-المظالم-إدارية", "ديوان المظالم — الدائرة الإدارية"],
  // 1,622
  ["لجان-ضريبة-القيمة-المضافة", "لجان ضريبة القيمة المضافة"],
  // 1,063
  ["لجان-ضريبة-الدخل-والزكاة", "لجان ضريبة الدخل والزكاة"],
  // 225
  ["لجان-التأمين", "لجان الفصل في المنازعات التأمينية"],
  // 165
  ["ديوان-المظالم-جزائية", "ديوان المظالم — الدائرة الجزائية"],
  // 125
  ["المحكمة-العليا", "المحكمة العليا"],
  // 106
  ["محكمة-الاستئناف", "محكمة الاستئناف"],
  // 69
  ["المحكمة-العامة", "المحكمة العامة"],
  // 35 — the entire labour corpus. Thin on purpose: the section is honest and
  // stands as a visible marker that labour judgments need sourcing.
  ["المحكمة-العمالية", "المحكمة العمالية"],
] as const;

/** The closed slug vocabulary, as a type. */
export type CourtSlug = (typeof COURTS)[number][0];

/** The 12 slugs in browse (corpus-volume) order. Never re-sort. */
export const COURT_ORDER: readonly CourtSlug[] = COURTS.map(([slug]) => slug);

/** slug → Arabic display label. This is the H1 and the switcher label. */
export const COURT_LABELS: Record<CourtSlug, string> = Object.fromEntries(
  COURTS,
) as Record<CourtSlug, string>;

/**
 * Segments under `/judgments/courts/` that are NOT court slugs.
 *
 * `/judgments/courts/page/{n}` must never resolve as a court in either
 * direction. Next resolves the static `page` segment first, and
 * `shared/library/courts.py` refuses the same two names server-side — two
 * layers, one vocabulary (mirrors `RESERVED_COURT_SLUGS` there).
 */
export const RESERVED_COURT_SLUGS: ReadonlySet<string> = new Set([
  "page",
  "mine",
]);

/**
 * Decode a `[court]` route param exactly ONCE and trim it.
 *
 * Next hands a non-ASCII dynamic param percent-encoded, so this is what turns
 * `%D8%A7%D9%84%D9%85%D8%AD%D9%83%D9%85%D8%A9-…` back into a slug the closed
 * vocabulary can be asked about. Malformed input (a lone `%`) throws inside
 * `decodeURIComponent`; that is not a crash, it is simply not one of the 12, so
 * the raw value falls through and fails validation a line later.
 *
 * Same decode-first normalisation as `encodeSlug()` in `lib/library/api.ts`,
 * minus the re-encode: the court reaches the backend as a QUERY param, where
 * `URLSearchParams` performs the single encode. Encoding here as well is the
 * `%25…` double-encode trap.
 */
export function normalizeCourtSlug(raw: string): string {
  let decoded = raw;
  try {
    decoded = decodeURIComponent(raw);
  } catch {
    decoded = raw;
  }
  return decoded.trim();
}

/** True when `value` is one of the 12 — pass it through `normalizeCourtSlug` first. */
export function isCourtSlug(value: string): value is CourtSlug {
  if (RESERVED_COURT_SLUGS.has(value)) return false;
  return value in COURT_LABELS;
}

/**
 * Arabic label for a court slug, or `null` when it is not one of the 12.
 *
 * Mirrors `courts.court_for_slug()`, including its contract: `null` means 404
 * and the caller MUST NOT spend a backend round-trip on it.
 */
export function courtLabel(slug: string): string | null {
  const normalized = normalizeCourtSlug(slug);
  return isCourtSlug(normalized) ? COURT_LABELS[normalized] : null;
}

/** `/judgments/courts/{slug}` — page 1 of one court section. */
export function courtPath(slug: string): string {
  return `/judgments/courts/${slug}`;
}

/** `/judgments/courts/{slug}/page/{n}` — a deep page of one court section. */
export function courtPagePath(slug: string, page: number): string {
  return `${courtPath(slug)}/page/${page}`;
}

/** H1 / `<title>` for a court section. The label is always the Arabic one. */
export function courtHeading(label: string): string {
  return `أحكام ${label}`;
}

/**
 * The facet's own name. **«الجهة القضائية», never «نوع المحكمة».**
 *
 * `المحكمة العليا` and `محكمة الاستئناف` are court LEVELS that leak into the
 * `court` column on the وزارة العدل feed, so those two buckets sit alongside the
 * existing درجة المحكمة chips. The two axes compose; calling this one «نوع
 * المحكمة» would make the pair read as a contradiction.
 */
export const COURT_FACET_LABEL = "الجهة القضائية";

/** One row of `GET /public/library/judgments/courts`, as the grid needs it. */
export interface CourtCountRow {
  slug: string;
  label?: string | null;
  count?: number | null;
}

/** One tile in the court switcher. `count` is null when the endpoint is down. */
export interface CourtNavItem {
  slug: CourtSlug;
  label: string;
  count: number | null;
}

/**
 * The 12 switcher tiles, counts attached where the API supplied them.
 *
 * ⚠ ORDER AND MEMBERSHIP COME FROM THE MIRROR, COUNTS COME FROM THE SERVER —
 * and that split is the point. The endpoint already returns browse order, so the
 * two agree by construction; iterating the mirror instead means (a) the switcher
 * still renders all 12 links when the counts call soft-fails, and (b) every href
 * is guaranteed to be a route that exists. A slug the server knows and this file
 * does not has no page to link to, so it is skipped — which is precisely the
 * drift the lockstep rule at the top exists to prevent, and it degrades to a
 * missing tile rather than a 404 in the reader's face.
 */
export function courtNavItems(
  rows?: readonly CourtCountRow[] | null,
): CourtNavItem[] {
  const counts = new Map<string, number>();
  for (const row of rows ?? []) {
    if (typeof row.count === "number") counts.set(row.slug, row.count);
  }
  return COURT_ORDER.map((slug) => ({
    slug,
    label: COURT_LABELS[slug],
    count: counts.get(slug) ?? null,
  }));
}
