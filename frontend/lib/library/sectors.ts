// THE mapping table for the `/library/{sector}/{type}` wing — one place, one
// vocabulary (library_sectors.md §4).
//
// `{type}` uses the EXISTING PUBLIC WING NAMES (`regulations` / `judgments` /
// `compliance` / `circulars`), NOT the مكتبتي content-type tokens (`regulation` /
// `judgment` / `service` / `circular`). A reader arriving from /regulations must
// recognise the segment; the shelf's tokens are an internal storage vocabulary
// and never appear in a URL. If a second copy of this table ever appears, the two
// WILL drift — every consumer imports from here.
//
// ⚠ `compliance` IS WIRED AND EMPTY, and it is NOT the wing that was retired on
// 2026-08-03. That one republished the `services` corpus — الشروط / المستندات /
// الخطوات restated under our chrome — and was deleted for it. This one is backed
// by `compliance_table`: «دليل مبسط لأكثر الخدمات استخداماً», our own short guide
// plus a link out, and it stays empty until that table exists. The tab renders
// today (a 0-count chip is hidden only on a SECTOR page) so the wing is reachable
// and its plumbing is exercised; nothing about it may grow a service's procedure.
//
// Pure data + pure string helpers. No fetching, no client state, no React —
// safe in both the server and the browser graph.

/**
 * The four tabs, in the D3 order: الأنظمة (absorbs nested مواد) · الأحكام ·
 * الخدمات · التعاميم. النماذج + الحاسبات stay parked — `forms` has no sector
 * column and calculators have no table at all.
 */
export const LIBRARY_TYPES = [
  "regulations",
  "judgments",
  "compliance",
  "circulars",
] as const;

export type LibraryType = (typeof LIBRARY_TYPES)[number];

export interface LibraryTypeMeta {
  /** Short chip label — the D3 vocabulary. */
  label: string;
  /** Heading / `<title>` wording. */
  longLabel: string;
  /** The unfiltered public wing this type is a slice of. */
  wingPath: string;
  /** One-line description under the hub heading. */
  description: string;
  /** Empty-state sentence (backend down / nothing to show). */
  empty: string;
}

export const LIBRARY_TYPE_META: Record<LibraryType, LibraryTypeMeta> = {
  regulations: {
    label: "الأنظمة",
    longLabel: "الأنظمة واللوائح",
    wingPath: "/regulations",
    description: "الأنظمة واللوائح والأدلة الرسمية مع ملخّصاتها وموادها.",
    empty: "لا توجد أنظمة لعرضها حالياً.",
  },
  judgments: {
    label: "الأحكام",
    longLabel: "الأحكام القضائية",
    wingPath: "/judgments",
    description: "أحكام المحاكم السعودية — وقائعها وأسبابها ومنطوقها.",
    empty: "لا توجد أحكام لعرضها حالياً.",
  },
  compliance: {
    label: "الخدمات",
    longLabel: "دليل الخدمات الحكومية",
    wingPath: "/compliance",
    // Describes the GUIDE, not the services corpus. «الشروط والمستندات وخطوات
    // التنفيذ» was the retired wing's line and must not come back — this wing
    // orients a reader and sends them to the issuing entity.
    description: "دليل مبسط لأكثر الخدمات الحكومية استخداماً، وأين تُنجز كل خدمة.",
    empty: "لا توجد خدمات لعرضها حالياً.",
  },
  circulars: {
    label: "التعاميم",
    longLabel: "التعاميم التنظيمية",
    wingPath: "/circulars",
    description: "التعاميم التنظيمية الصادرة عن الجهات السعودية ونصوصها.",
    empty: "لا توجد تعاميم لعرضها حالياً.",
  },
};

/**
 * Path segments under `/library` that are NOT sector slugs and must never be
 * resolved as one (trap T2).
 *
 *   `mine` → «مكتبتي», the AUTHED per-user shelf. Next resolves the static
 *            `app/library/mine` segment before the dynamic `[sector]` one, so
 *            this is belt-and-braces — but a per-user shelf rendered to an
 *            anonymous visitor is the exact regression T2 exists to prevent, and
 *            a guard that costs one Set lookup is cheaper than finding out.
 *   `page`  → reserved for pagination segments. `/library/page/{n}` is
 *            DELIBERATELY not a route (the four corpora share no sortable
 *            column, so a merged cross-corpus feed has no ordering — §9); the
 *            name stays reserved so it can never become a sector.
 *
 * The backend 404s both for the same reason. Two layers, one vocabulary.
 */
export const RESERVED_SECTOR_SLUGS: ReadonlySet<string> = new Set([
  "mine",
  "page",
]);

/** Narrow a raw `[type]` route param onto the closed four-value vocabulary. */
export function isLibraryType(value: string): value is LibraryType {
  return (LIBRARY_TYPES as readonly string[]).includes(value);
}

/** Is this `[sector]` param a reserved segment rather than a sector slug? */
export function isReservedSectorSlug(value: string): boolean {
  return RESERVED_SECTOR_SLUGS.has(value.toLowerCase());
}

/** `/library/{sector}` — the sector overview. */
export function sectorPath(slug: string): string {
  return `/library/${slug}`;
}

/** `/library/{sector}/{type}` — page 1 of one sector×type list. */
export function sectorTypePath(slug: string, type: LibraryType): string {
  return `/library/${slug}/${type}`;
}

/**
 * H1 / `<title>` for a sector×type list. D6: the display name is ALWAYS the
 * Arabic `name_ar` — the Latin slug is a URL segment and never surfaces as text.
 */
export function sectorTypeHeading(type: LibraryType, nameAr: string): string {
  return `${LIBRARY_TYPE_META[type].longLabel} — قطاع ${nameAr}`;
}

/** H1 / `<title>` for a sector overview page. Arabic only (D6). */
export function sectorHeading(nameAr: string): string {
  return `قطاع ${nameAr}`;
}

/**
 * D9 — a sector×type list holding fewer than this many items is `noindex,
 * follow`: too thin to earn an index slot, still worth crawling for the links
 * it carries. An EMPTY combination renders no tab at all and 404s on a direct
 * hit (there are 3 of those out of 152, plus 7 more in the 1–2 range).
 */
export const THIN_PAGE_THRESHOLD = 3;

/** A `robots` meta directive, in the shape Next's `Metadata` type accepts. */
export interface RobotsDirective {
  index: boolean;
  follow: boolean;
}

/**
 * ⚠ THE /judgments WING IS STILL BEHIND THE PDPL GATE. Every page of it ships
 * `noindex, nofollow` until the anonymization audit passes — judgment text may
 * still carry party-identifying details, and a crawled page is a page we cannot
 * un-publish. A sector-scoped list of the SAME judgments is the same exposure,
 * so it inherits the same directive; the sector wing must not become a side
 * door into an index the wing itself refuses.
 *
 * TO FLIP: do it in the same commit that deletes the `robots` key from
 * `app/judgments/page.tsx`, `app/judgments/page/[n]/page.tsx` and
 * `app/judgments/[slug]/page.tsx` — one gate, one lift.
 */
const JUDGMENTS_PDPL_ROBOTS: RobotsDirective = { index: false, follow: false };

/**
 * The `robots` directive for one sector×type list page, or `undefined` for
 * "index it normally".
 *
 * Three rules, in precedence order:
 *   1. الأحكام → the PDPL gate above, always.
 *   2. `capped` → the anon depth wall. What a crawler sees on a capped page IS
 *      the signup wall, and a wall carries no SEO value.
 *   3. D9 — under `THIN_PAGE_THRESHOLD` items → `noindex, follow`: too thin to
 *      earn an index slot, still worth crawling for the links it carries.
 *
 * ⚠ THE `capped` INPUT MUST COME FROM AN UNEXEMPTED FETCH (trap T5). It answers
 * the ANON question ("is this depth capped?"). Feeding it a §3.7 crawler-
 * exempted response reports `cap_reached: false` to a crawler and hands it an
 * INDEXABLE deep page — turning crawl reach into index bloat. The page BODY is
 * the half that gets exempted. See `app/regulations/page/[n]/page.tsx`.
 */
export function sectorTypeRobots(
  type: LibraryType,
  count: number,
  capped: boolean,
): RobotsDirective | undefined {
  if (type === "judgments") return JUDGMENTS_PDPL_ROBOTS;
  if (capped || count < THIN_PAGE_THRESHOLD) return { index: false, follow: true };
  return undefined;
}

/**
 * Item counts, grouped with a thousands separator.
 *
 * The locale is PINNED to `en-US` on purpose. `toLocaleString()` with no
 * argument resolves against the runtime's locale, which differs between the
 * Node render and the browser hydration and would produce a hydration mismatch
 * — and an `ar` locale would emit Arabic-Indic digits (٢٠٬١٨٢), which the rest
 * of the UI does not use ("أكثر من 3,000 نظام ولائحة" in the nav copy).
 */
export function formatCount(value: number): string {
  return new Intl.NumberFormat("en-US").format(value);
}
