// Typed server-side fetchers for the SEO Public Library hub + document
// endpoints (`/api/v1/public/library/*`). These run ONLY in server components —
// plain `fetch`, no auth header, no token client. ISR is driven by the fetch
// `next.revalidate` window (hubs 1h, docs 24h), NOT `force-dynamic`.
//
// Every fetcher returns `null` on a non-OK / unreachable backend so the caller
// can `notFound()` (docs) or render an empty state (hubs) — Google must never
// see a 5xx from a public page, and `npm run build` must survive an offline
// backend during static prerender of the base hubs.
//
// Shapes mirror the live backend payloads verified against prod data 2026-07-22.

// `cache()` is React's per-request memo. It is imported here rather than in a
// `server-only` module because the SECTOR helpers below are read by server
// components AND their shapes are shared with the cards — and unlike
// `next/headers` or `node:crypto`, `cache` is exported from the main `react`
// package in BOTH builds (the browser implementation falls through to calling
// the function directly when there is no request dispatcher). So it is safe in
// the client graph this file unavoidably joins — see the `EDGE_SECRET` note.
import { cache } from "react";

import type {
  DocStatus,
  JudgmentDoc,
  JudgmentHubItem,
  JudgmentHubResponse,
} from "@/types/library";
import type { LibraryType } from "@/lib/library/sectors";

/**
 * Backend origin for SERVER→SERVER calls. Precedence:
 * `INTERNAL_API_URL` → `NEXT_PUBLIC_API_URL` → localhost.
 *
 * `INTERNAL_API_URL` carries no `NEXT_PUBLIC_` prefix, so it never reaches the
 * browser bundle and — unlike `NEXT_PUBLIC_API_URL`, which is a Docker build ARG
 * baked into the image at build time (`Dockerfile:26-32`) — it is read from the
 * container's environment at RUNTIME. That difference is the point: it holds the
 * Railway private-network origin (`http://luna-backend.railway.internal:8000` —
 * plain http and an explicit port, the private network does no TLS termination
 * and no port mapping), which resolves ONLY inside a running container, never in
 * the build sandbox.
 *
 * WHY: anonymous library pages are rendered by the ISR renderer, so every cache
 * miss in the world leaves Railway from ONE egress IP. Over the public internet
 * that puts the entire anon surface in a single edge rate-limit bucket — one
 * crawl wave and the library 429s site-wide. Private networking keeps
 * server→server traffic off the edge completely (and off the egress bill).
 *
 * Unset → byte-identical to the previous behaviour, which is what keeps local
 * dev and `npm run build` working (the private network is runtime-only, so the
 * static prerender pass MUST fall through to the public origin).
 *
 * Exported because `lib/seo/sitemap.ts` feeds off the same backend and must
 * resolve the origin identically — one definition, not two.
 */
export const SERVER_API_BASE =
  process.env.INTERNAL_API_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://localhost:8000";

/**
 * Shared secret proving a request came from something we trust.
 *
 * The origin lock (`backend/app/middleware/origin_lock.py`, plan step 3.4)
 * rejects any request that arrives without `X-Edge-Secret`. Cloudflare's
 * Transform Rule injects it on every request that TRANSITS the edge — and these
 * fetchers deliberately no longer do, since they go over `SERVER_API_BASE` =
 * the private network. So the Next server has to present the secret itself, or
 * the lock 403s every anonymous library page the moment it is armed (and
 * because `fetchJson` maps a non-OK to `notFound()` on document routes, Google
 * would be served 404s on live pages).
 *
 * ⚠ THE SECRET IS SAFE, BUT NOT FOR THE REASON THIS COMMENT USED TO GIVE.
 *
 * It claimed this module reaches the browser only through `import type`, "erased
 * at compile time". That is FALSE, and verified false: `hub/RegulationCard.tsx`
 * VALUE-imports `toDocStatus` from here, and `RegulationCard` is imported by two
 * `'use client'` modules (`hub/HubCtaWall.tsx`, `mine/ShelfCard.tsx`). A
 * non-`'use client'` module pulled in from a client module joins the CLIENT
 * graph, so **this file is compiled into the browser bundle**. Do not restore the
 * old claim, and do not lean on it to add a `next/headers`, `server-only` or
 * `node:crypto` import here — that is exactly why the §3.7 crawler signal lives
 * in `lib/library/crawler-signal.ts` and not in this file.
 *
 * What actually keeps the value out of the bundle is that the SUBSTITUTION never
 * happens, which is a stronger guarantee than the module boundary anyway:
 *
 *   1. Next only inlines env vars whose key starts with `NEXT_PUBLIC_`.
 *      `getNextPublicEnvironmentVariables()` (`next/dist/lib/static-env.js`)
 *      walks `process.env` and emits a webpack `define` ONLY for that prefix, so
 *      `process.env.EDGE_SECRET` survives into client code as a literal property
 *      read on `process.env` — never as its value.
 *   2. In the browser there is no `process`. `webpack-config.js` aliases it to
 *      `next/dist/build/polyfills/process`, which falls through to
 *      `next/dist/compiled/process` whose `env` is `{}`.
 *
 * So on the client `EDGE_SECRET` evaluates to `undefined`, `serverFetchInit`
 * attaches no header, and nothing leaks — with or without the module boundary.
 *
 * Consequences to preserve: keep the `NEXT_PUBLIC_` prefix OFF this key (that
 * prefix IS the mechanism), never attach the secret to a browser-side fetch
 * (browser traffic reaches the backend THROUGH Cloudflare, which supplies the
 * header itself), and never log it.
 */
const EDGE_SECRET = process.env.EDGE_SECRET;

/**
 * Header carrying the §3.7 verified-crawler signal to the backend
 * (`public_library.VERIFIED_BOT_HEADER`). Same name Cloudflare's Transform Rule
 * sets at the edge — this renderer is just re-emitting a signal the
 * Googlebot → Next page → server-side fetch hop would otherwise destroy.
 */
const VERIFIED_BOT_HEADER = "X-Verified-Bot";

/**
 * Per-call knobs for one server→server fetch. Every field is OPT-IN; omitting
 * the whole object reproduces the pre-§3.7 request exactly.
 */
export interface ServerFetchOptions {
  /**
   * Forward the §3.7 verified-crawler signal on this call, letting a VERIFIED
   * search crawler past the anonymous hub depth cap.
   *
   * ⚠ Set this ONLY from `readVerifiedBotSignal()` in
   * `lib/library/crawler-signal.ts`. That function refuses to report `true`
   * unless the incoming request proves it transited our Cloudflare edge, which
   * is what stops this renderer from laundering a forged `X-Verified-Bot` into a
   * backend that (once `TRUST_CF_HEADERS` is on) treats the header as
   * authoritative. Never derive it from a raw request header in THIS file — see
   * the `EDGE_SECRET` note above: `api.ts` is in the browser bundle and must not
   * grow a `next/headers` import.
   *
   * Falsy/absent ⇒ NO header at all, not `X-Verified-Bot: 0`. The backend reads
   * an absent header and an explicit `0` identically, so sending nothing keeps
   * the human request byte-identical to today's — including its cache key.
   */
  verifiedBot?: boolean;
}

/**
 * `fetch` init for one server→server call: the ISR window, the edge secret when
 * the origin lock is armed, and the §3.7 crawler signal when one was proven.
 *
 * Neither header set ⇒ NO headers are attached (not empty ones), so the request
 * on the wire is byte-identical to the pre-3.2/3.4 one. Exported for the sitemap
 * feed in `lib/seo/sitemap.ts`, which calls the same backend and must satisfy
 * the same lock.
 *
 * ⚠ THE NEXT DATA-CACHE KEY INCLUDES `init.headers`, AND THAT IS LOAD-BEARING.
 * `IncrementalCache#generateCacheKey` hashes
 * `[prefix, url, method, headers, mode, …, body]`
 * (`next/dist/server/lib/incremental-cache/index.js`), so a `verifiedBot` call
 * and a plain one land in DIFFERENT Data Cache entries. That is what stops an
 * uncapped crawler payload from being replayed out of the shared Data Cache to
 * every anonymous human for an hour — the trap plan §3.7 flags at the edge
 * cache, reproduced one layer down. Do not "simplify" by hoisting the header
 * onto every request, and do not strip headers out of the key.
 */
export function serverFetchInit(
  revalidate: number,
  opts?: ServerFetchOptions,
): RequestInit {
  const init: RequestInit = { next: { revalidate } };
  const headers: Record<string, string> = {};
  if (EDGE_SECRET) {
    headers["X-Edge-Secret"] = EDGE_SECRET;
  }
  if (opts?.verifiedBot) {
    // Canonical `"1"`, never the incoming value verbatim: one extra cache-key
    // variant instead of one per spelling of "true".
    headers[VERIFIED_BOT_HEADER] = "1";
  }
  if (Object.keys(headers).length > 0) {
    init.headers = headers;
  }
  return init;
}

const HUB_REVALIDATE = 3600; // 1 hour
const DOC_REVALIDATE = 86400; // 24 hours

// ------------------------------------------------------------------
// Shared envelope + status mapping
// ------------------------------------------------------------------

/**
 * The paged list envelope returned by every hub endpoint.
 *
 * ACCESS TIERS: `max_page` is the CALLER's real browse-depth cap (anon 1 · free
 * 3 · paid unbounded, reported as 9999). `max_anon_page` is a same-valued
 * DEPRECATED alias kept for one release — read `max_page` and fall back.
 *
 * ⚠ These fetchers are server-only and unauthenticated, so a server-rendered
 * envelope ALWAYS carries the anon cap; that is deliberate (the ISR cache is
 * shared — PART 9 trap 2). The caller's real cap reaches the browser only via
 * the client-side authed fetch inside `HubCtaWall`.
 */
export interface LibraryHubResponse<TItem> {
  items: TItem[];
  page: number;
  total_pages: number;
  cap_reached: boolean;
  /** The caller's cap. Optional until every wing has shipped the field. */
  max_page?: number;
  /** @deprecated same value as `max_page`; removed after one release. */
  max_anon_page: number;
}

/**
 * API document status — a SUPERSET of the badge's `DocStatus` (adds `"draft"`).
 * The badge only renders active/amended/repealed; a draft speaks through its
 * own banner instead.
 */
export type ApiDocStatus = "active" | "amended" | "repealed" | "draft";

/**
 * Map an API status onto the StatusBadge `DocStatus`, or `null` when it has no
 * badge (draft). Unknown values map to `null` too — a repealed law must NEVER
 * be rendered as current, so we fail closed to "no badge" rather than guessing.
 */
export function toDocStatus(status: string): DocStatus | null {
  if (status === "active" || status === "amended" || status === "repealed") {
    return status;
  }
  return null;
}

/** One row in a document metadata grid. */
export interface DocMetadataRow {
  label: string;
  value: string;
}

/** First metadata value whose label contains `labelIncludes` (or undefined). */
export function findMetadataValue(
  metadata: DocMetadataRow[],
  labelIncludes: string,
): string | undefined {
  return metadata.find((row) => row.label.includes(labelIncludes))?.value;
}

/**
 * Strip light markdown + collapse whitespace into a plain meta-description
 * snippet, truncated to `max` chars with an ellipsis.
 */
export function toSnippet(text: string, max = 155): string {
  const clean = text
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/[#>*_`~]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (clean.length <= max) return clean;
  return `${clean.slice(0, max - 1).trimEnd()}…`;
}

// ------------------------------------------------------------------
// Regulations
// ------------------------------------------------------------------

export interface RegulationHubItem {
  slug: string;
  title: string;
  entity_name: string;
  status: ApiDocStatus;
  doc_type: string;
  summary_snippet: string;
  sectors: string[];
}

export interface RegulationsFilters {
  entity?: string;
  doc_type?: string;
  sector?: string;
  /**
   * Latin sector slug (`labor-employment`). The BACKEND resolves it to the
   * Arabic `topics.name_ar` and filters `sectors[]` with it — the frontend
   * never carries an Arabic-name lookup, which is what keeps the 38 names a
   * single server-side vocabulary (library_sectors.md §6). Distinct from the
   * legacy `sector` param, which takes the Arabic name directly.
   */
  sector_slug?: string;
  q?: string;
}

export interface RegulationTocEntry {
  id: string;
  title: string;
  position: number;
}

export interface RegulationVisibleSection {
  id: string;
  title: string;
  text: string;
  is_truncated: boolean;
  hidden_placeholder_lines: number;
  /**
   * Extra section ids this ONE section stands in for. Non-empty only on an open
   * نظام whose fallback chunk spans a run of مواد («المادة (1) – المادة (4): …»):
   * the run renders once instead of repeating the same paragraphs per مادة, and
   * these are the مواد it swallowed. The page emits an empty anchor per id so
   * every TOC row still scrolls somewhere.
   *
   * Optional on the wire — an older ISR/fetch-cache entry predates the field.
   */
  also_ids?: string[];
}

export interface RegulationOfficialSource {
  title: string;
  href: string;
}

/**
 * One مادة link in the doc-page TOC, from the derived `seo_articles` index.
 * Additive (Phase 3): the list is empty until the index is built for a given
 * regulation, in which case the doc page falls back to the chunk-based TOC.
 */
export interface ArticleIndexEntry {
  article_no: number;
  article_label: string;
  slug: string;
}

export interface RegulationDoc {
  slug: string;
  title: string;
  status: ApiDocStatus;
  status_raw: string;
  metadata: DocMetadataRow[];
  summary_md: string;
  gate: "open" | "gated";
  toc: RegulationTocEntry[];
  article_index: ArticleIndexEntry[];
  visible_sections: RegulationVisibleSection[];
  hidden_section_count: number;
  official_sources: RegulationOfficialSource[];
  draft_notice: boolean;
}

// ------------------------------------------------------------------
// Regulation article (مادة) page
// ------------------------------------------------------------------

/** The parent-regulation summary embedded in a مادة payload. */
export interface ArticleRegulationRef {
  slug: string;
  title: string;
  status: ApiDocStatus;
}

/** A prev/next مادة link (within the same regulation). */
export interface ArticleNavEntry {
  slug: string;
  article_label: string;
}

/**
 * AI شرح teaser for a مادة (added by the parallel backend agent, additive). When
 * `has_sharh` is true the anon page renders `teaser` (the free 2-line lead) then
 * a gate sized by `hidden_placeholder_lines`; the FULL شرح (`sharh_md`) reaches
 * only signed-in readers via the authed full-content endpoint. Optional on the
 * payload — a doc served before the field lands falls back to the «قريباً» shell.
 */
export interface ArticleSharh {
  has_sharh: boolean;
  teaser: string;
  hidden_placeholder_lines: number;
}

/**
 * Full /regulations/{slug}/articles/{article_slug} payload (mirrors the backend
 * `RegulationArticleResponse`). `text` is ALREADY gate-truncated server-side —
 * the hidden bytes never reach the client; `hidden_placeholder_lines` only sizes
 * the GateBanner skeleton. When `is_fallback_body` is true the body is the whole
 * owning chunk (`context_title`), not the isolated مادة text.
 */
export interface RegulationArticle {
  slug: string;
  article_no: number;
  article_label: string;
  regulation: ArticleRegulationRef;
  gate: "open" | "gated";
  is_fallback_body: boolean;
  context_title: string | null;
  text: string;
  is_truncated: boolean;
  hidden_placeholder_lines: number;
  /**
   * AI شرح teaser (additive — see `ArticleSharh`). Optional: absent on a payload
   * served before the field is populated, in which case the page renders the
   * «شرح قريباً» shell instead of a teaser + gate.
   */
  sharh?: ArticleSharh;
  prev: ArticleNavEntry | null;
  next: ArticleNavEntry | null;
}

// ------------------------------------------------------------------
// Circulars (التعاميم — Phase 5)
// ------------------------------------------------------------------

export interface CircularHubItem {
  slug: string;
  title: string;
  entity_name: string | null;
  /** Internal provenance token ('entity' / 'scraped') — NEVER rendered. */
  source_label: string | null;
  body_snippet: string;
  body_length: number;
  /**
   * القطاعات this تعميم belongs to. OPTIONAL on the wire: `circulars.sectors[]`
   * is 100% populated in the corpus but the hub payload did not carry it before
   * the sector wing, so an ISR entry baked by an older backend simply has no
   * field. Absent ⇒ no pills, never a crash.
   */
  sectors?: string[];
}

export interface CircularsFilters {
  /** Issuing-authority name (ilike) or entity UUID. */
  entity?: string;
  /** Latin sector slug — see `RegulationsFilters.sector_slug`. */
  sector_slug?: string;
  q?: string;
}

/**
 * Full /circulars/{slug} payload. `gate_effective` is the post-`effective_
 * circular_gate` value — a short (<=800-char) تعميم renders fully `'open'`;
 * `text` is already gate-truncated server-side (hidden bytes never shipped),
 * `hidden_placeholder_lines` only sizes the GateBanner skeleton. `source_label`
 * is the internal provenance token and is NEVER rendered — a real URL surfaces
 * in `official_sources` instead.
 */
export interface CircularDoc {
  slug: string;
  title: string;
  entity_name: string | null;
  source_label: string | null;
  official_sources: RegulationOfficialSource[];
  metadata: DocMetadataRow[];
  gate_effective: "open" | "gated";
  text: string;
  is_truncated: boolean;
  hidden_placeholder_lines: number;
  body_length: number;
}

// ------------------------------------------------------------------
// Judgments (الأحكام القضائية)
// ------------------------------------------------------------------
//
// The judgment WIRE types live in `types/library.ts` (they're shared by the hub
// view, the cards and several doc-page blocks) — see the note there. Only the
// filter shape + the fetchers belong here, mirroring the sibling wings.

export interface JudgmentsFilters {
  /**
   * Raw `court_level` enum: `first_instance` | `appeal` | `supreme`. See
   * `lib/library/court-levels.ts` — three values, never two.
   */
  court_level?: string;
  /** One value out of an item's `domains[]`. */
  domain?: string;
  /**
   * Latin sector slug — see `RegulationsFilters.sector_slug`. The judgments
   * corpus stores its sector under `cases.legal_domains[]`, so the backend
   * resolves the slug against THAT column; the wire name stays `sector_slug`
   * for all three wings so one caller shape covers the lot.
   */
  sector_slug?: string;
  q?: string;
}

// ------------------------------------------------------------------
// Forms (نماذج — Phase 3)
// ------------------------------------------------------------------

export interface FormHubItem {
  slug: string;
  title: string;
  category: string | null;
  use_case_snippet: string;
}

export interface FormsFilters {
  category?: string;
  q?: string;
}

/**
 * The gate-truncated preview of a form's template body. The FULL body is NEVER
 * in the anon payload — `text` is only the free preview; `hidden_placeholder_
 * lines` sizes the GateBanner skeleton.
 */
export interface FormBodyPreview {
  text: string;
  is_truncated: boolean;
  hidden_placeholder_lines: number;
}

/** One الأساس النظامي citation — a display LABEL only (no links in v1). */
export interface FormLegalBasisEntry {
  label: string;
}

/**
 * Full /forms/{slug} payload — PUBLISHED forms only (404 otherwise). `use_case_
 * md` (متى تستخدمه) + `intro_md` (شرح) are the FREE SEO layer; `body_preview` is
 * the gate-truncated template body; `legal_basis` labels cite the المواد;
 * `has_docx` flags a gated downloadable (served via the download proxy).
 */
export interface FormDetail {
  slug: string;
  title: string;
  category: string | null;
  use_case_md: string | null;
  intro_md: string | null;
  body_preview: FormBodyPreview;
  legal_basis: FormLegalBasisEntry[];
  has_docx: boolean;
}

// ------------------------------------------------------------------
// Fetch helpers
// ------------------------------------------------------------------

function buildQuery(
  page: number,
  filters?:
    | RegulationsFilters
    | CircularsFilters
    | JudgmentsFilters
    | FormsFilters,
): string {
  const params = new URLSearchParams({ page: String(page) });
  if (filters) {
    for (const [key, value] of Object.entries(filters)) {
      if (value) params.set(key, value);
    }
  }
  return params.toString();
}

/**
 * Encode an Arabic document slug for the backend path exactly ONCE. A Next.js
 * dynamic route param can arrive either already percent-encoded (the raw path
 * segment, e.g. `%D9%86…` — observed on Next 15 for non-ASCII slugs) or decoded
 * (`نظام-العمل`). Decoding first normalizes both cases, so the backend never
 * receives a double-encoded `%25…` path (which 404s).
 */
function encodeSlug(slug: string): string {
  let decoded = slug;
  try {
    decoded = decodeURIComponent(slug);
  } catch {
    decoded = slug;
  }
  return encodeURIComponent(decoded);
}

/**
 * ⚠ A TRANSIENT FAILURE MUST NOT LOOK LIKE A MISSING PAGE.
 *
 * `null` from this function makes document routes call `notFound()`, which Next
 * caches and Google records as a 404 on a real, published page. That is fine for
 * a genuine 404 and actively harmful for anything else — and the access-tiers
 * work made "anything else" reachable: the rate limiter now collapses every item
 * path of a section onto ONE bucket, while anonymous library traffic reaches the
 * backend THROUGH this renderer, so the whole world's cache misses share a single
 * IP and a single budget. A crawl burst, a sitemap wave, or a cold cache after a
 * deploy across a ~60k-page corpus can therefore 429 — and would have turned live
 * pages into 404s for Googlebot, on the exact surface the publishing programme
 * exists for.
 *
 * So: only a real 404 returns `null`. A 429 or 5xx THROWS, which makes Next
 * render the error boundary and — crucially — NOT cache a 404 for the page.
 */
async function fetchJson<T>(
  url: string,
  revalidate: number,
  opts?: ServerFetchOptions,
): Promise<T | null> {
  let res: Response;
  try {
    res = await fetch(url, serverFetchInit(revalidate, opts));
  } catch (e) {
    // Backend unreachable. Transient by nature — never a 404.
    throw new Error(`library fetch failed: ${url}`, { cause: e });
  }

  if (res.status === 404) return null;

  // 400 = the hub filter validation added in plan step 2.1 (`q` under 3 chars,
  // unknown `entity`/`doc_type`/`court_level`/`category`). It is reachable
  // WITHOUT any bad input from the user: `minLength` on the filter inputs only
  // guards the typed path, so a shared link, an old bookmark or a hand-edited
  // `?q=ab` still submits. Throwing here would surface the root error boundary
  // on an indexable hub URL — breaking this module's own contract that Google
  // must never see an error page from a public route. Treated like 404 so the
  // caller renders its normal empty state instead.
  if (res.status === 400) return null;

  if (!res.ok) {
    throw new Error(`library fetch ${res.status}: ${url}`);
  }

  try {
    return (await res.json()) as T;
  } catch (e) {
    throw new Error(`library fetch returned unparsable JSON: ${url}`, {
      cause: e,
    });
  }
}

/**
 * ⚠ `ServerFetchOptions` IS A HUB-ONLY PARAMETER — deliberately.
 *
 * The only thing it carries today is the §3.7 crawler exemption, and the only
 * thing that exemption unlocks is HUB PAGINATION DEPTH. Document fetchers have
 * no depth cap to waive (their gating is the per-document unlock ledger, which
 * §3.7 does not touch), so giving them the option would create a header that
 * fragments the 24h document Data Cache while buying nothing. Leave them
 * single-purpose.
 *
 * `getJudgmentsHub` is also deliberately left without it: the whole /judgments
 * wing ships `robots: { index: false, follow: false }` behind the PDPL gate, so
 * no crawler has — or should have — a discovery path into its deep pages. An
 * exemption there would widen the surface a laundered forgery could reach while
 * buying zero crawl reach. Add it back in the same commit that lifts the gate.
 */
export function getRegulationsHub(
  page: number,
  filters?: RegulationsFilters,
  opts?: ServerFetchOptions,
): Promise<LibraryHubResponse<RegulationHubItem> | null> {
  const qs = buildQuery(page, filters);
  return fetchJson<LibraryHubResponse<RegulationHubItem>>(
    `${SERVER_API_BASE}/api/v1/public/library/regulations?${qs}`,
    HUB_REVALIDATE,
    opts,
  );
}

export function getRegulationDoc(slug: string): Promise<RegulationDoc | null> {
  return fetchJson<RegulationDoc>(
    `${SERVER_API_BASE}/api/v1/public/library/regulations/${encodeSlug(slug)}`,
    DOC_REVALIDATE,
  );
}

/**
 * Fetch one مادة payload. Both slugs are Arabic and are normalized through
 * `encodeSlug` (Next 15 delivers non-ASCII params already percent-encoded — never
 * double-encode). Returns `null` → the route calls `notFound()`.
 */
export function getRegulationArticle(
  slug: string,
  articleSlug: string,
): Promise<RegulationArticle | null> {
  return fetchJson<RegulationArticle>(
    `${SERVER_API_BASE}/api/v1/public/library/regulations/${encodeSlug(slug)}/articles/${encodeSlug(articleSlug)}`,
    DOC_REVALIDATE,
  );
}

export function getCircularsHub(
  page: number,
  filters?: CircularsFilters,
  opts?: ServerFetchOptions,
): Promise<LibraryHubResponse<CircularHubItem> | null> {
  const qs = buildQuery(page, filters);
  return fetchJson<LibraryHubResponse<CircularHubItem>>(
    `${SERVER_API_BASE}/api/v1/public/library/circulars?${qs}`,
    HUB_REVALIDATE,
    opts,
  );
}

export function getCircularDoc(slug: string): Promise<CircularDoc | null> {
  return fetchJson<CircularDoc>(
    `${SERVER_API_BASE}/api/v1/public/library/circulars/${encodeSlug(slug)}`,
    DOC_REVALIDATE,
  );
}

/**
 * /judgments hub page. Same ISR window + null-on-failure contract as every
 * sibling hub, so an offline backend renders an empty state rather than a 5xx.
 *
 * CONTRACT NOTE: the judgments envelope is `{ items, page, total_pages }` — it
 * has no `cap_reached` / `max_anon_page` today, so the response type declares
 * them optional (see `JudgmentHubResponse`). `judgmentsHubTotalPages()` is not
 * needed: `total_pages` rides on the same payload the view already awaits, and
 * HubPagination consumes it directly (identical to circulars/forms).
 */
export function getJudgmentsHub(
  page: number,
  filters?: JudgmentsFilters,
): Promise<JudgmentHubResponse | null> {
  const qs = buildQuery(page, filters);
  return fetchJson<JudgmentHubResponse>(
    `${SERVER_API_BASE}/api/v1/public/library/judgments?${qs}`,
    HUB_REVALIDATE,
  );
}

export function getJudgmentDoc(slug: string): Promise<JudgmentDoc | null> {
  return fetchJson<JudgmentDoc>(
    `${SERVER_API_BASE}/api/v1/public/library/judgments/${encodeSlug(slug)}`,
    DOC_REVALIDATE,
  );
}

export function getFormsHub(
  page: number,
  filters?: FormsFilters,
  opts?: ServerFetchOptions,
): Promise<LibraryHubResponse<FormHubItem> | null> {
  const qs = buildQuery(page, filters);
  return fetchJson<LibraryHubResponse<FormHubItem>>(
    `${SERVER_API_BASE}/api/v1/public/library/forms?${qs}`,
    HUB_REVALIDATE,
    opts,
  );
}

export function getFormDetail(slug: string): Promise<FormDetail | null> {
  return fetchJson<FormDetail>(
    `${SERVER_API_BASE}/api/v1/public/library/forms/${encodeSlug(slug)}`,
    DOC_REVALIDATE,
  );
}

// ------------------------------------------------------------------
// Sectors (القطاعات) — the /library unified hub + the sector wing
// ------------------------------------------------------------------
//
// The 38 sectors are a CLOSED, server-owned vocabulary. Everything below reads
// them from the API; nothing here (and nothing anywhere else in the frontend)
// hardcodes a second copy of the list, the Arabic names, or the counts.

/** Per-corpus item counts, keyed by the three public wing names. */
export type SectorCounts = Record<LibraryType, number>;

/** Sector counts plus their sum — what the sector endpoints return. */
export type SectorCountsWithTotal = SectorCounts & { total: number };

/** `GET /api/v1/public/library` — the three unfiltered tab counts. */
export interface LibraryCountsResponse {
  counts: SectorCounts;
}

/** One row of `GET /api/v1/public/library/sectors`. */
export interface SectorSummary {
  slug: string;
  name_ar: string;
  counts: SectorCountsWithTotal;
}

/**
 * `GET /api/v1/public/library/sectors`.
 *
 * ⚠ ALREADY ORDERED BY VOLUME — that IS the browse order (§3). Never re-sort
 * client-side: alphabetical would bury المعاملات التجارية (20k items) under
 * الأمن الغذائي, and the server owns the ordering so it can change without a
 * frontend deploy.
 */
export interface SectorsResponse {
  sectors: SectorSummary[];
}

/**
 * The first slice of each of the three types for one sector — the strips on
 * `/library/{sector}`. Items are the SAME shapes the wing hub endpoints return,
 * so they render through the existing wing cards verbatim.
 */
export interface SectorPreview {
  regulations: RegulationHubItem[];
  judgments: JudgmentHubItem[];
  circulars: CircularHubItem[];
}

/** `GET /api/v1/public/library/sectors/{slug}` — 404 on an unknown slug. */
export interface SectorDetail {
  slug: string;
  name_ar: string;
  counts: SectorCountsWithTotal;
  preview: SectorPreview;
}

/** Any of the three hub item shapes a sector×type list can carry. */
export type SectorHubItem =
  | RegulationHubItem
  | JudgmentHubItem
  | CircularHubItem;

/**
 * The common envelope across the three wings. `cap_reached` / `max_page` /
 * `max_anon_page` are optional because the judgments contract omits them (see
 * `JudgmentHubResponse`) — `?? false` there simply means "no cap".
 */
export interface SectorHubEnvelope {
  items: SectorHubItem[];
  page: number;
  total_pages: number;
  cap_reached?: boolean;
  max_page?: number;
  max_anon_page?: number;
}

/** The three unfiltered tab counts for `/library`. */
export function getLibraryCounts(): Promise<LibraryCountsResponse | null> {
  return fetchJson<LibraryCountsResponse>(
    `${SERVER_API_BASE}/api/v1/public/library`,
    HUB_REVALIDATE,
  );
}

/**
 * The 38 sectors, in the server's browse order.
 *
 * ⚠ SOFT-FAILS WHERE ITS SIBLINGS THROW, and that is deliberate. Every other
 * fetcher lets a transient failure propagate so a document route renders the
 * error boundary instead of caching a 404 (see `fetchJson`). This one is
 * different because of WHERE it is called: `generateStaticParams` (a throw
 * there fails the whole build) and the sector-pill slug map on every wing hub
 * card (a throw there would take down `/regulations` over a decoration). An
 * empty list degrades to "no browse grid, plain-text pills" — visible, and
 * never a broken page.
 *
 * `cache()` makes it one request per render pass no matter how many cards ask.
 */
export const getSectors = cache(async (): Promise<SectorSummary[]> => {
  try {
    const data = await fetchJson<SectorsResponse>(
      `${SERVER_API_BASE}/api/v1/public/library/sectors`,
      HUB_REVALIDATE,
    );
    return data?.sectors ?? [];
  } catch {
    return [];
  }
});

/**
 * `name_ar → slug`, for turning the Arabic sector names that ride on every hub
 * item into links (D11).
 *
 * A name with NO entry resolves to `undefined` and the pill renders as plain
 * text — never a guessed or broken href. That is the whole reason the map comes
 * from the API rather than a transliteration rule: the five slugs still awaiting
 * sign-off (§3) can change with a one-row `topics` update and nothing here
 * needs to know.
 */
export const getSectorSlugMap = cache(
  async (): Promise<Record<string, string>> => {
    const sectors = await getSectors();
    const map: Record<string, string> = {};
    for (const sector of sectors) {
      map[sector.name_ar] = sector.slug;
    }
    return map;
  },
);

/**
 * One sector: Arabic name, per-type counts, and a ≤3-item preview of each type.
 * `null` ⇒ unknown slug (or a reserved segment) ⇒ the route calls `notFound()`.
 *
 * The slug is Latin by design (D4/D5), so `encodeURIComponent` is enough —
 * `encodeSlug`'s decode-first normalisation exists for Arabic document slugs.
 */
export const getSectorDetail = cache(
  (slug: string): Promise<SectorDetail | null> =>
    fetchJson<SectorDetail>(
      `${SERVER_API_BASE}/api/v1/public/library/sectors/${encodeURIComponent(slug)}`,
      HUB_REVALIDATE,
    ),
);

/**
 * One page of a sector×type list.
 *
 * NO NEW LIST ENDPOINT (§7.2): this reuses each wing's EXISTING hub endpoint
 * with `sector_slug`, so `resolve_gate` / `truncate_for_gate` / the depth cap /
 * `library_budget` metering are all inherited unchanged. One gating path, not
 * two.
 *
 * `getJudgmentsHub` takes no `ServerFetchOptions` on purpose — the whole
 * /judgments wing is `noindex, nofollow` behind the PDPL gate, so it has no
 * crawler to exempt. Restore the argument in the same commit that lifts the
 * gate.
 */
export function getSectorTypeHub(
  type: LibraryType,
  sectorSlug: string,
  page: number,
  opts?: ServerFetchOptions,
): Promise<SectorHubEnvelope | null> {
  switch (type) {
    case "regulations":
      return getRegulationsHub(page, { sector_slug: sectorSlug }, opts);
    case "judgments":
      return getJudgmentsHub(page, { sector_slug: sectorSlug });
    case "circulars":
      return getCircularsHub(page, { sector_slug: sectorSlug }, opts);
  }
}
