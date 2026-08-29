// The 33 issuing bodies of the /compliance wing — «الجهة».
//
// ⚠ `shared/library/entities.py` IS THE SOURCE OF TRUTH. This file is a MIRROR
// of its vocabulary: the slug keys, the Arabic labels, and — critically — the
// INSERTION ORDER, which is corpus-volume order and IS the browse order. Both
// files are GENERATED from the live corpus in one pass, so the Arabic is copied
// byte-for-byte and never retyped (the `taqeem` label carries a real fatha on
// its ق). Re-ordering when the corpus moves is expected — the 2026-08-25 ingest
// took the wing 337 → 533 guides and وزارة البلديات والإسكان from 4 to 144,
// i.e. from fifteenth place to first, and the 2026-08-29 one took it 533 → 825
// and put هيئة الزكاة والضريبة والجمارك (14 → 150) at the top — but a slug's
// TEXT is permanent.
// Alphabetical would bury وزارة العدل (130 guides) in the middle of a tail of
// one-guide entities. The two files must be edited in LOCKSTEP, in the same
// commit; the Python module additionally owns the QUERY PREDICATE (slug → the
// raw `services.provider_name` it matches with `eq`), which has no reason to
// exist in the browser and is deliberately NOT mirrored here.
//
// Same relationship `lib/library/courts.ts` has with `shared/library/courts.py`
// and `lib/library/court-levels.ts` has with the deep-search module: pure data +
// pure string helpers, no fetching, no client state, safe in both graphs.
//
// ── WHY THIS VOCABULARY IS SIMPLER THAN `courts.ts` ─────────────────────────
// `cases.court` is free text — 30 raw spellings collapsing to 12 buckets — so
// `courts.py` had to BE the normalizer and carry a variant list.
// `services.provider_name` is already canonical: 33 strings, one per body, no
// city suffixes, no numbered دوائر, no residual bucket. So this is a
// slug ⇄ single-name map, and there is nothing to normalise.
//
// ── THE SLUGS ARE LATIN kebab-case, WHICH REMOVES THE DECODE DANCE ──────────
// ⚠ THERE IS DELIBERATELY NO `normalizeEntitySlug()`. Do not port
// `normalizeCourtSlug` from `courts.ts`. That function exists solely because the
// court slugs are ARABIC: Next hands a non-ASCII dynamic param back
// PERCENT-ENCODED (`%D8%A7%D9%84…`), so every entry point there has to decode
// exactly once before it can even ask the vocabulary a question. These slugs are
// ASCII, so Next hands them back verbatim, a raw `params.slug` compares directly
// against `ENTITY_LABELS`, and there is nothing to decode. That is the whole
// reason the plan chose Latin (compliance_entity_sections.md D2 · §3):
// /compliance is the INDEXED wing — these slugs become permanent public URLs,
// and they also have to survive ISR revalidation, where a percent-encoding
// mismatch is a 200 that silently refreshes nothing (memory
// `isr-revalidate-encoding`).
//
// Counts live server-side. Nothing here reads the corpus.

/**
 * `[slug, provider_name]` for the 33 entities, in the Python module's insertion
 * order — which is corpus volume, which is the browse order.
 *
 * One tuple per entity rather than two parallel literals ON PURPOSE: the label
 * is Arabic and the slug is Latin, so an editor showing an RTL line beside an
 * LTR one is exactly where a mismatched pair gets shipped unnoticed. The comment
 * over each row is that entity's live guide count as measured 2026-08-29 —
 * documentation for the reader of this file, never a number the UI prints (the
 * switcher's counts come off the API).
 */
const ENTITIES = [
  // 150
  ["zatca", "هيئة الزكاة والضريبة والجمارك"],
  // 144
  ["ministry-of-municipalities-housing", "وزارة البلديات والإسكان"],
  // 130
  ["ministry-of-justice", "وزارة العدل"],
  // 103
  ["gosi", "المؤسسة العامة للتأمينات الاجتماعية"],
  // 70
  ["ministry-of-human-resources", "وزارة الموارد البشرية والتنمية الاجتماعية"],
  // 43
  ["ministry-of-commerce", "وزارة التجارة"],
  // 25
  ["ministry-of-health", "وزارة الصحة"],
  // 21
  ["citc", "هيئة الاتصالات وتقنية المعلومات"],
  // 19
  ["jeddah-municipality", "أمانة محافظة جدة"],
  // 19
  ["ministry-of-energy", "وزارة الطاقة"],
  // 14
  ["board-of-grievances", "ديوان المظالم"],
  // 12
  ["ministry-of-education", "وزارة التعليم"],
  // 9
  ["real-estate-general-authority", "الهيئة العامة للعقار"],
  // 8
  ["hrdf", "صندوق تنمية الموارد البشرية"],
  // 8
  ["ministry-of-environment", "وزارة البيئة والمياه والزراعة"],
  // 7
  ["media-regulation-authority", "الهيئة العامة لتنظيم الإعلام"],
  // 7
  ["ministry-of-foreign-affairs", "وزارة الخارجية"],
  // 6
  ["etimad", "المركز الوطني لنظم الموارد الحكومية"],
  // 5
  ["social-development-bank", "بنك التنمية الاجتماعية"],
  // 5
  ["ministry-of-industry", "وزارة الصناعة والثروة المعدنية"],
  // 4
  ["human-rights-commission", "هيئة حقوق الإنسان"],
  // 2
  ["ministry-of-tourism", "وزارة السياحة"],
  // 2
  ["environmental-compliance-center", "المركز الوطني للرقابة على الإلتزام البيئي"],
  // 2
  ["transport-general-authority", "الهيئة العامة للنقل"],
  // 2
  ["riyadh-municipality", "أمانة منطقة الرياض"],
  // 1
  ["awqaf-general-authority", "الهيئة العامة للأوقاف"],
  // 1
  ["taqeem", "الهيئة السعودية للمقَيّمين المعتمدين (تقييم)"],
  // 1
  ["saudi-business-center", "المركز السعودي للأعمال الاقتصادية"],
  // 1
  ["ministry-of-finance", "وزارة المالية"],
  // 1
  ["royal-commission-alula", "الهيئة الملكية لمحافظة العلا"],
  // 1
  ["monshaat", "الهيئة العامة للمنشآت الصغيرة والمتوسطة"],
  // 1
  ["tvtc", "المؤسسة العامة للتدريب التقني والمهني"],
  // 1
  ["sfda", "الهيئة العامة للغذاء والدواء"],
] as const;

/** The closed slug vocabulary, as a type. */
export type EntitySlug = (typeof ENTITIES)[number][0];

/** The 33 slugs in browse (corpus-volume) order. Never re-sort. */
export const ENTITY_ORDER: readonly EntitySlug[] = ENTITIES.map(
  ([slug]) => slug,
);

/** slug → Arabic display label. This is the H1 and the switcher tile label. */
export const ENTITY_LABELS: Record<EntitySlug, string> = Object.fromEntries(
  ENTITIES,
) as Record<EntitySlug, string>;

/**
 * Segments under `/compliance/` that are NOT entity slugs.
 *
 * ⚠ THIS WING SHARES ONE SLUG NAMESPACE BETWEEN TWO KINDS OF THING (D2):
 * `/compliance/{entity}` and `/compliance/{guide}` are the SAME dynamic segment.
 * `/compliance/page/{n}` keeps working because Next resolves the STATIC `page`
 * segment ahead of `[slug]`, and `shared/library/entities.py` refuses the same
 * three names server-side — two layers, one vocabulary (mirrors
 * `RESERVED_SLUGS` there). The 33 entity slugs are additionally reserved against
 * the live guide slugs by `scripts/build_compliance_slugs.py`, so an entity can
 * never shadow a guide URL that is already in the sitemap.
 */
export const RESERVED_ENTITY_SLUGS: ReadonlySet<string> = new Set([
  "page",
  "entities",
  "mine",
]);

/**
 * True when `value` is one of the 33.
 *
 * ⚠ THE DISPATCHER'S ENTIRE COST IS THIS CALL — an in-process dict lookup, zero
 * fetch — which is why `app/compliance/[slug]/page.tsx` asks it BEFORE spending
 * a round trip on `getComplianceGuide` (D2.1).
 */
export function isEntitySlug(value: string): value is EntitySlug {
  if (RESERVED_ENTITY_SLUGS.has(value)) return false;
  return value in ENTITY_LABELS;
}

/**
 * Arabic label for an entity slug, or `null` when it is not one of the 33.
 *
 * Mirrors `entities.name_for_slug()`, including its contract: `null` means «this
 * is not an entity», which on the dispatcher means «fall through to the guide
 * fetch», never a bare 404.
 */
export function entityLabel(slug: string): string | null {
  return isEntitySlug(slug) ? ENTITY_LABELS[slug] : null;
}

/**
 * `provider_name` → slug, or `null` for a name outside the 33.
 *
 * `ComplianceCard` uses it to turn the entity chip it already prints into a link
 * into that entity's section. `null` is the load-bearing case: a pipeline
 * re-ingest of `services` can introduce a provider this mirror has not learned
 * yet, and that card must degrade to PLAIN TEXT rather than link to a 404 — the
 * same rule `SectorPills` applies to a sector name with no slug, and the browser
 * half of the log-and-omit posture `entities.py` takes server-side.
 */
export function entitySlugForName(name: string): EntitySlug | null {
  const wanted = name.trim();
  if (!wanted) return null;
  const match = ENTITIES.find(([, label]) => label === wanted);
  return match ? match[0] : null;
}

/** `/compliance/{slug}` — page 1 of one entity section. */
export function entityPath(slug: string): string {
  return `/compliance/${slug}`;
}

/** `/compliance/{slug}/page/{n}` — a deep page of one entity section. */
export function entityPagePath(slug: string, page: number): string {
  return `${entityPath(slug)}/page/${page}`;
}

/** H1 / `<title>` for an entity section. The label is always the Arabic one. */
export function entityHeading(label: string): string {
  return `دليل خدمات ${label}`;
}

/**
 * The facet's own name. **«الجهة», never «مقدّم الخدمة».**
 *
 * The wing already has a free-text `provider` filter on the backend, and it is a
 * DIFFERENT axis — an `ilike` substring over the same column. This one is a
 * closed, server-owned SECTION vocabulary matched with `eq`, and it is the one a
 * reader browses by. The cards call it «الجهة» and the breadcrumb says «الجهة»,
 * so the switcher says it too.
 */
export const ENTITY_FACET_LABEL = "الجهة";

/** One row of `GET /public/library/compliance/entities`, as the grid needs it. */
export interface EntityCountRow {
  slug: string;
  label?: string | null;
  count?: number | null;
}

/** One tile in the entity switcher. `count` is null when the endpoint is down. */
export interface EntityNavItem {
  slug: EntitySlug;
  label: string;
  count: number | null;
}

/**
 * The 33 switcher tiles, counts attached where the API supplied them.
 *
 * ⚠ ORDER AND MEMBERSHIP COME FROM THE MIRROR, COUNTS COME FROM THE SERVER —
 * and that split is the point. The endpoint already returns browse order, so the
 * two agree by construction; iterating the mirror instead means (a) the switcher
 * still renders all 33 links when the counts call soft-fails, and (b) every href
 * is guaranteed to be a route that exists. A slug the server knows and this file
 * does not has no page to link to, so it is skipped — which is precisely the
 * drift the lockstep rule at the top exists to prevent, and it degrades to a
 * missing tile rather than a 404 in the reader's face.
 */
export function entityNavItems(
  rows?: readonly EntityCountRow[] | null,
): EntityNavItem[] {
  const counts = new Map<string, number>();
  for (const row of rows ?? []) {
    if (typeof row.count === "number") counts.set(row.slug, row.count);
  }
  return ENTITY_ORDER.map((slug) => ({
    slug,
    label: ENTITY_LABELS[slug],
    count: counts.get(slug) ?? null,
  }));
}
