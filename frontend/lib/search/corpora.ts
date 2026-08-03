// The cross-wing search vocabulary — the `corpus` wire values, their Arabic
// chip labels, the `?corpus=` URL contract, and the rules for which facet
// values may be PRINTED (spoiler: fewer than you would guess — see below).
//
// Spec: `.claude/plans/bm25_navigation_search.md` D5 · §5.1 · §6.2.
// Wire source of truth: `backend/app/services/search_service.py:PUBLIC_CORPORA`
// and the `facets` objects built by `refresh_search_index()`
// (`shared/db/migrations/111_bm25_search_index.sql` §8).
//
// Pure data + pure functions. No React, no fetching — safe in both graphs.

import {
  LIBRARY_TYPE_META,
  type LibraryType,
} from "@/lib/library/sectors";
import { courtLevelLabel } from "@/lib/library/court-levels";

// ------------------------------------------------------------------
// The three public corpora
// ------------------------------------------------------------------

/**
 * The wings `GET /api/v1/search` ranks over, in the order the backend echoes
 * them back (`clean_corpora` orders by its `allowed` tuple, not by query-string
 * order) — so a chip row built from this array and a `corpora` array read off a
 * response are always in the same order and never appear to "reshuffle".
 *
 * ⚠ `blog` and `template` are DELIBERATELY absent. They exist in `search_index`
 * but only `/api/v1/search/mine` may reach them: `bm25_search` matches
 * `owner_user_id IS NULL` **or** `= p_owner`, never both, which is precisely
 * what makes a private row structurally unable to fall out of a public search.
 * Adding either here would build a chip for a filter the endpoint drops.
 */
export const SEARCH_CORPORA = [
  "regulation",
  "judgment",
  "circular",
] as const;

export type SearchCorpus = (typeof SEARCH_CORPORA)[number];

/**
 * corpus (the storage token) → library type (the URL/label token).
 *
 * ⚠ `service` (الخدمات الحكومية) IS GONE, 2026-08-03 — the compliance wing was
 * retired, so there is no `/compliance/{slug}` for a service hit to link to and
 * a `service` chip would offer a filter whose every result 404s. The backend
 * dropped it from `search_service.PUBLIC_CORPORA` in the same change; if it ever
 * comes back, both layers move together.
 */
export const CORPUS_LIBRARY_TYPE: Record<SearchCorpus, LibraryType> = {
  regulation: "regulations",
  judgment: "judgments",
  circular: "circulars",
};

/**
 * The chip label for a corpus.
 *
 * Taken from `LIBRARY_TYPE_META` rather than spelled out again, because the
 * SAME page renders `LibraryTypeChips` (الأنظمة · الأحكام · التعاميم) a few
 * pixels away. Two chip rows on one page naming the same three things
 * differently is a bug, not a style choice — and the label set already has an
 * owner.
 */
export function corpusLabel(corpus: SearchCorpus): string {
  return LIBRARY_TYPE_META[CORPUS_LIBRARY_TYPE[corpus]].label;
}

/** Narrow a raw wire/URL value onto the closed three-value vocabulary. */
export function isSearchCorpus(value: string): value is SearchCorpus {
  return (SEARCH_CORPORA as readonly string[]).includes(value);
}

// ------------------------------------------------------------------
// The `?corpus=` URL contract
// ------------------------------------------------------------------

/** The query-string key. Repeatable: `?corpus=regulation&corpus=judgment`. */
export const CORPUS_PARAM = "corpus";

/**
 * Read the selected wings off a URL.
 *
 * Unknown values are DROPPED rather than treated as an error — same posture as
 * the backend's `clean_corpora`, and for the same reason: a corpus name is a UI
 * affordance in a shared link, not a secret, and a stale link naming a renamed
 * wing should degrade to searching the rest.
 *
 * The result is normalised to `SEARCH_CORPORA` order and de-duplicated, so
 * `?corpus=judgment&corpus=regulation&corpus=judgment` and
 * `?corpus=regulation&corpus=judgment` produce the same state — and therefore
 * the same TanStack Query key, rather than two cache entries for one search.
 */
export function parseCorpora(params: URLSearchParams): SearchCorpus[] {
  const wanted = new Set(
    params
      .getAll(CORPUS_PARAM)
      .map((value) => value.trim().toLowerCase())
      .filter(isSearchCorpus),
  );
  return SEARCH_CORPORA.filter((corpus) => wanted.has(corpus));
}

/**
 * Write the selected wings back onto a `URLSearchParams`, in place.
 *
 * ⚠ AN EMPTY SELECTION AND A FULL ONE BOTH ERASE THE PARAM, and that is the
 * whole normalisation. `[]` is this UI's «الكل» state, and the endpoint's own
 * default for an absent `corpus` is all four — so the shortest URL is also the
 * correct one, and a reader who ticks every wing gets `/library?q=…` to share
 * rather than `/library?q=…&corpus=regulation&corpus=judgment&corpus=…`.
 */
export function writeCorpora(
  params: URLSearchParams,
  corpora: readonly string[],
): void {
  params.delete(CORPUS_PARAM);
  if (corpora.length === 0 || corpora.length === SEARCH_CORPORA.length) return;
  for (const corpus of SEARCH_CORPORA) {
    if (corpora.includes(corpus)) params.append(CORPUS_PARAM, corpus);
  }
}

/**
 * Toggle one wing in/out of a selection, normalising «none» and «all» back to
 * the empty «الكل» state so the chip row only ever has one representation of
 * "search everything".
 */
export function toggleCorpus(
  current: readonly SearchCorpus[],
  corpus: SearchCorpus,
): SearchCorpus[] {
  // From «الكل», the first click is «only this wing» rather than «all except
  // this wing» — a reader clicking أحكام wants أحكام, not three-quarters of the
  // library.
  if (current.length === 0) return [corpus];

  const next = current.includes(corpus)
    ? current.filter((c) => c !== corpus)
    : SEARCH_CORPORA.filter((c) => c === corpus || current.includes(c));

  return next.length === 0 || next.length === SEARCH_CORPORA.length
    ? []
    : [...next];
}

// ------------------------------------------------------------------
// Facets — what may be PRINTED, and what must not
// ------------------------------------------------------------------

/** A hit's `facets` object, as it comes off the wire: keys known, values not. */
export type FacetBag = Record<string, unknown>;

function facetText(facets: FacetBag, key: string): string {
  const raw = facets[key];
  return typeof raw === "string" ? raw.trim() : "";
}

function facetList(facets: FacetBag, key: string): string[] {
  const raw = facets[key];
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((value): value is string => typeof value === "string")
    .map((value) => value.trim())
    .filter((value) => value.length > 0);
}

/**
 * The one-line context under a result title: issuer, court, city — the values a
 * reader uses to tell two similarly-titled documents apart.
 *
 * ⚠ THIS IS A WHITELIST, AND THE EXCLUSIONS ARE THE POINT. `search_index.facets`
 * holds the RAW column values, because its job is filtering (`facets @>
 * p_facets`), not display. Several of those raw values are mapped to Arabic
 * SERVER-SIDE before any other surface prints them, and three are not text at
 * all. Printing them here would put pipeline enums and numeric tokens on an
 * Arabic page:
 *
 *   · `doc_type_bucket` → a pipeline enum (`law_statute`, `executive_regulation`
 *     …). `library_service.DOC_TYPE_BUCKET_LABELS` owns the Arabic; the comment
 *     above that map names «نوع الوثيقة: law_statute» as a defect it exists to
 *     prevent.
 *   · `status_class`    → the raw lifecycle value (`in_force`, `cancelled` …),
 *     mapped by `REG_STATUS_MAP` before it reaches a badge. Mirroring THAT map
 *     here is the one duplication worth refusing: its own comment calls it «the
 *     single guard against showing non-enacted text as active», and a second
 *     copy that drifts turns a repealed نظام into a current one. The document
 *     page and the hub card both carry the real badge; a search result is an
 *     address and stays one.
 *   · `reg_ref` / `entity_ref` → numeric source tokens ("17900"), not names —
 *     stated outright at `public_library.py:763` and `:1956`.
 *   · `circ_ref` / `case_number` → identifiers. Useful for citation, useless as
 *     the one line that helps a reader choose between two results.
 *
 * What survives is exactly the set that is already an Arabic display string in
 * the database, plus `court_level`, which goes through `courtLevelLabel()` —
 * the canonical local mirror that exists BECAUSE a three-value column kept
 * being collapsed to two.
 */
export function hitMeta(corpus: SearchCorpus, facets: FacetBag): string[] {
  switch (corpus) {
    case "regulation":
      return [facetText(facets, "entity_name")].filter(Boolean);
    case "judgment":
      return [
        facetText(facets, "court"),
        courtLevelLabel(facetText(facets, "court_level")),
        facetText(facets, "city"),
      ].filter(Boolean);
    case "circular":
      // Migration 112 added `entity_name`, resolved through `entities` from
      // `circulars.entity_id` («هيئة التأمين», «الهيئة العامة للغذاء والدواء»).
      // Before that this returned [] — the only printable circular facets were
      // `circ_ref`/`entity_ref`, which are NUMERIC source tokens, and `doc_type`,
      // a raw enum. Still do not print either of those.
      return [facetText(facets, "entity_name")].filter(Boolean);
  }
}

/**
 * The القطاع names on a hit — already the Arabic 38-value vocabulary in the
 * database (`VALID_SECTORS`), which is why these ARE printable while their
 * neighbours are not.
 *
 * `judgment` keeps its own column name: the sector axis is `legal_domains[]`
 * there, not `sectors[]`. Same vocabulary, different column — the asymmetry is
 * in the corpus, and `JudgmentCard` already feeds `domains` to the same
 * `SectorPills`.
 */
export function hitSectors(corpus: SearchCorpus, facets: FacetBag): string[] {
  return facetList(facets, corpus === "judgment" ? "legal_domains" : "sectors");
}
