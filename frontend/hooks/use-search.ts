import { useCallback, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { getAccessToken } from "@/lib/api";
import { useDebounce } from "@/hooks/use-debounce";
import {
  SEARCH_DEBOUNCE_MS,
  SEARCH_MIN_LENGTH,
  type SearchSurface,
} from "@/lib/search/copy";
import { CORPUS_PARAM, type FacetBag, type SearchCorpus } from "@/lib/search/corpora";
import type { HubItem } from "@/components/library/hub/HubCards";

/**
 * Server state for the shared BM25 navigation search
 * (`.claude/plans/bm25_navigation_search.md`, Wave C).
 *
 * ── WHICH ENDPOINT, AND WHY ─────────────────────────────────────────────────
 * A public-hub search calls the wing's OWN list endpoint with `?q=`, not
 * `/api/v1/search`. Wave B swaps that endpoint's `ilike` for `bm25_search()`
 * behind the unchanged `q` contract (§5.2, D8), so the response is the exact
 * hub envelope the server render already produces — same cards, same static
 * free snippet, same sector pills, ranked by BM25 instead of substring luck.
 *
 * `/api/v1/search` answers with `SearchHit` (corpus + content_id + slug + title
 * + facets + score, and per D3 **no snippet**), which is the right shape for the
 * cross-wing `/library` page and the wrong one here: cards built from it would
 * lose the excerpt D3 exists to preserve. Wave E's `useLibrarySearch` therefore
 * lives further down THIS file, beside `useHubSearch` — not instead of it. Two
 * hooks, two endpoints, one ranking function underneath both.
 *
 * ── WHY IT IS AUTHED, AND CLIENT-SIDE ONLY ──────────────────────────────────
 * D9: search is registered-only, enforced server-side (`q` is ignored for an
 * anonymous caller). The library's server-side fetchers in `lib/library/api.ts`
 * are UNAUTHENTICATED and ISR-cached with a shared cache key, so they can never
 * carry a per-user search — a search result set reaching that cache would be
 * replayed to every anonymous visitor for the next hour. Per-user bytes reach
 * the browser through a client-side authed fetch and nowhere else. Identical
 * constraint to `HubCtaWall`'s authed reveal; see its ISR note.
 */

/** Rows per search page — the backend's own `HUB_PAGE_SIZE`. */
export const SEARCH_PAGE_SIZE = 9;

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/** The hub envelope, as read from the client-side authed search call. */
export interface HubSearchResponse {
  items: HubItem[];
  page: number;
  total_pages: number;
  /**
   * How many rows matched, not how many are shown. **Null on a browse page** —
   * the backend only counts when `q` is present, because a hub browsing its own
   * corpus already knows its size and the count query is what §2.1 exists to
   * skip.
   */
  total_count?: number | null;
  /**
   * False when a cut bound the count: the ranked id set came back at the
   * backend's `HUB_SEARCH_LIMIT` (200), or `bm25_search` hit `p_candidates`
   * (500). Then `total_count` is a CEILING, and `searchResultCount()` says
   * «أفضل N نتيجة» rather than presenting it as a total.
   */
  total_count_is_exact?: boolean;
}

/**
 * Why a search produced nothing, when it was not simply an empty result set.
 *
 *   rate_limited → 429: the per-user reach meter (navigation hardening 2.2) is
 *                  full for this window. A real, explainable answer — search
 *                  results charge the item budget exactly like browse results
 *                  (§5.4, no exemption), so this is reachable by normal use and
 *                  must never render as «something broke».
 *   failed       → dead session, transport fault, 5xx, unparsable body. All
 *                  indistinguishable to a reader, and all mean «try again».
 */
export type SearchErrorKind = "rate_limited" | "failed";

export class SearchRequestError extends Error {
  readonly kind: SearchErrorKind;

  constructor(kind: SearchErrorKind) {
    super(`search request failed: ${kind}`);
    this.name = "SearchRequestError";
    this.kind = kind;
  }
}

/** Non-empty filter values, in a stable order, for the key and the query. */
type SearchFilters = Record<string, string | undefined>;

function cleanFilters(filters?: SearchFilters): Array<[string, string]> {
  if (!filters) return [];
  return Object.entries(filters)
    .map(([key, value]) => [key, (value ?? "").trim()] as [string, string])
    .filter(([, value]) => value.length > 0)
    .sort(([a], [b]) => a.localeCompare(b));
}

export const searchKeys = {
  all: ["search"] as const,
  hub: (section: SearchSurface, query: string, filters?: SearchFilters) =>
    [...searchKeys.all, "hub", section, query, cleanFilters(filters)] as const,
  /**
   * The cross-wing `/library` search. `corpora` is already normalised to
   * `SEARCH_CORPORA` order and de-duplicated by `parseCorpora`/`toggleCorpus`,
   * so two URLs that mean the same selection produce ONE cache entry.
   */
  library: (query: string, corpora: readonly SearchCorpus[]) =>
    [...searchKeys.all, "library", query, corpora.join(",")] as const,
};

/**
 * Is this non-OK response the project's standard rate-limit refusal?
 *
 * Status first, because that is the one signal every layer preserves: the
 * backend's 429 is JSON, but an edge-injected one (Cloudflare) is HTML and
 * would never parse. The `RATE_LIMITED` code is a second chance only. Same
 * check `HubCtaWall` makes — deliberately duplicated rather than shared,
 * because it is four lines and the alternative is a client component importing
 * from another client component for a helper.
 */
async function isRateLimited(res: Response): Promise<boolean> {
  if (res.status === 429) return true;
  try {
    const body = (await res.json()) as { error?: { code?: string } };
    return body?.error?.code === "RATE_LIMITED";
  } catch {
    return false;
  }
}

/**
 * One authed search request against a wing's hub endpoint.
 *
 * Plain `fetch`, not the shared `apiFetch` — same reason as the authed hub
 * reveal and the full-content unlock: a dead-session 401 must never trigger the
 * global «redirect to /login» while a reader is on a PUBLIC library page. It
 * surfaces here as `failed`, the box keeps working, and the page they were
 * reading stays on screen.
 */
async function fetchHubSearch(
  section: SearchSurface,
  query: string,
  filters?: SearchFilters,
): Promise<HubSearchResponse> {
  const token = getAccessToken();
  if (!token) throw new SearchRequestError("failed");

  const params = new URLSearchParams({ page: "1", q: query });
  for (const [key, value] of cleanFilters(filters)) params.set(key, value);

  let res: Response;
  try {
    res = await fetch(
      `${API_BASE}/api/v1/public/library/${section}?${params.toString()}`,
      { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" },
    );
  } catch {
    throw new SearchRequestError("failed");
  }

  if (!res.ok) {
    throw new SearchRequestError(
      (await isRateLimited(res)) ? "rate_limited" : "failed",
    );
  }

  const payload = (await res.json()) as HubSearchResponse;
  if (!Array.isArray(payload?.items)) throw new SearchRequestError("failed");
  return payload;
}

export interface UseHubSearchParams {
  section: SearchSurface;
  /** The DEBOUNCED, trimmed query. The caller owns the debounce. */
  query: string;
  /** Hub filters that must survive the search (judgments' court-level chips). */
  filters?: SearchFilters;
  /**
   * The caller's own gate — in practice `isAuthenticated`. False keeps the
   * query idle: an anonymous caller's `q` is dropped server-side anyway (D9),
   * so firing the request would spend a round trip to be told nothing.
   */
  enabled?: boolean;
}

/**
 * One page of BM25 results for a public hub.
 *
 * ONE PAGE, not an infinite list, and that is a decision rather than a
 * shortcut: BM25 pins an exact title match at rank 1 by construction (§4.3) and
 * ranks the rest, so page 2 of a navigation search is almost always the wrong
 * answer to a better query. It also keeps the item budget honest — every
 * yielded row is charged (§5.4). When the set spills over,
 * `SEARCH_COPY.moreResults` asks for another word instead of paging.
 *
 * `keepPreviousData` holds the previous result set on screen while the next
 * keystroke's query is in flight, so the grid does not collapse to a skeleton
 * on every letter. `retry: false` because a 429 must not be hammered and a
 * search is re-triggered by typing anyway.
 */
export function useHubSearch({
  section,
  query,
  filters,
  enabled = true,
}: UseHubSearchParams) {
  const trimmed = query.trim();
  return useQuery({
    queryKey: searchKeys.hub(section, trimmed, filters),
    queryFn: () => fetchHubSearch(section, trimmed, filters),
    enabled: enabled && trimmed.length >= SEARCH_MIN_LENGTH,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    retry: false,
  });
}

/** Narrow an unknown TanStack error to the refusal kind, for the copy branch. */
export function searchErrorKind(error: unknown): SearchErrorKind {
  return error instanceof SearchRequestError ? error.kind : "failed";
}

// ==========================================================================
// CROSS-WING SEARCH — `GET /api/v1/search` (Wave E, D5)
// ==========================================================================

/**
 * How many hits `/library` asks for. The endpoint's own `DEFAULT_PAGE_SIZE`, and
 * its `MAX_PAGE_SIZE` is 50 — asking for more is silently clamped, not refused.
 *
 * ONE PAGE, no paginator, and that is the same decision `useHubSearch` records
 * one screen up rather than a shortcut repeated: BM25 pins an exact title match
 * at rank 1 by construction, so page 2 of a navigation search is almost always
 * the wrong answer to a better query. It is also what keeps the item budget
 * honest — every yielded row is charged (§5.4), and the endpoint returns nothing
 * at all past offset 200 (`MAX_RESULTS`), so a paginator built on this response
 * would eventually page into a wall.
 */
export const LIBRARY_SEARCH_PAGE_SIZE = 20;

/**
 * One ranked cross-wing hit.
 *
 * ⚠ THERE IS NO `snippet`, AND ITS ABSENCE IS THE DESIGN (D3). `search_index`
 * holds only text the anonymous card and doc page already publish, so a response
 * is structurally incapable of carrying gated bytes — which is what deletes the
 * whole per-hit gating apparatus from this path. `backend/app/models/search.py`
 * states the same rule from the other side; if a `snippet` ever appears here,
 * someone has re-opened the leak surface the design closed.
 *
 * `url` is assembled SERVER-SIDE (`search_service.public_url`) so no caller
 * re-derives the corpus→route map. It is nullable because an item whose slug the
 * sidecar has not minted is unlinkable — render no link rather than a guessed
 * one.
 *
 * `score` is meaningful for ORDERING and nothing else: unnormalised, and two
 * corpora carry different IDF tables. Do not render it, do not threshold on it.
 */
export interface LibrarySearchHit {
  corpus: string;
  content_id: string;
  slug: string | null;
  title: string;
  facets: FacetBag;
  url: string | null;
  score: number;
}

export interface LibrarySearchResponse {
  items: LibrarySearchHit[];
  /**
   * ⚠ NOT A CORPUS COUNT WHEN `total_is_exact` IS FALSE. `bm25_search` cuts to
   * `p_candidates` (500) by `ts_rank_cd` before scoring, so this counts THAT set
   * and is exact only when fewer documents matched than the cut. There is
   * deliberately no `total_pages`: a ceiling-derived page count would paginate
   * to pages that do not exist. `searchResultCount()` owns the phrasing.
   */
  total: number;
  total_is_exact: boolean;
}

/** Defensive per-hit narrowing — the wire is `unknown` until proven otherwise. */
function toLibraryHit(raw: unknown): LibrarySearchHit | null {
  if (!raw || typeof raw !== "object") return null;
  const hit = raw as Record<string, unknown>;
  const corpus = typeof hit.corpus === "string" ? hit.corpus : "";
  const title = typeof hit.title === "string" ? hit.title.trim() : "";
  // A hit with no corpus cannot be labelled and a hit with no title cannot be
  // read. Both are impossible from this endpoint; dropping them costs nothing
  // and beats rendering an unlabelled blank row.
  if (!corpus || !title) return null;
  return {
    corpus,
    content_id: typeof hit.content_id === "string" ? hit.content_id : "",
    slug: typeof hit.slug === "string" ? hit.slug : null,
    title,
    facets:
      hit.facets && typeof hit.facets === "object" && !Array.isArray(hit.facets)
        ? (hit.facets as FacetBag)
        : {},
    url: typeof hit.url === "string" && hit.url.length > 0 ? hit.url : null,
    score: typeof hit.score === "number" ? hit.score : 0,
  };
}

/**
 * One authed cross-wing search.
 *
 * Plain `fetch` rather than the shared `apiFetch`, for the reason `fetchHubSearch`
 * gives above: a dead-session 401 must never fire the global «redirect to /login»
 * while a reader is standing on a PUBLIC page. `/library` is that page for the
 * whole library, so the failure has to stay local — it surfaces as `failed`, the
 * box keeps working, and the sector hub they were browsing stays on screen.
 *
 * The response is `Cache-Control: private, no-store` at the origin (every byte is
 * metered against ONE caller's item budget); `cache: "no-store"` here says the
 * same thing on the browser side so a shared search link never replays someone
 * else's charged result set out of the bfcache.
 */
async function fetchLibrarySearch(
  query: string,
  corpora: readonly SearchCorpus[],
): Promise<LibrarySearchResponse> {
  const token = getAccessToken();
  if (!token) throw new SearchRequestError("failed");

  const params = new URLSearchParams({
    q: query,
    page: "1",
    page_size: String(LIBRARY_SEARCH_PAGE_SIZE),
  });
  // Repeatable, per the endpoint's contract. An EMPTY list is «all four» and
  // must send no `corpus` at all — sending zero values would still be an empty
  // list server-side, but the URL and the request should say the same thing.
  for (const corpus of corpora) params.append(CORPUS_PARAM, corpus);

  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/v1/search?${params.toString()}`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
  } catch {
    throw new SearchRequestError("failed");
  }

  if (!res.ok) {
    throw new SearchRequestError(
      (await isRateLimited(res)) ? "rate_limited" : "failed",
    );
  }

  const payload = (await res.json()) as Record<string, unknown>;
  if (!Array.isArray(payload?.items)) throw new SearchRequestError("failed");

  const items = payload.items
    .map(toLibraryHit)
    .filter((hit): hit is LibrarySearchHit => hit !== null);

  return {
    items,
    // `total` counts what the RPC scored, which is >= what this page shows.
    // Falling back to `items.length` (rather than 0) keeps the count line
    // truthful if the field ever goes missing.
    total: typeof payload.total === "number" ? payload.total : items.length,
    // Missing ⇒ assume EXACT is wrong in the safe direction, so default to the
    // honest-but-vaguer «أفضل N نتيجة» only when the backend actually says so.
    total_is_exact: payload.total_is_exact !== false,
  };
}

export interface UseLibrarySearchParams {
  /** The DEBOUNCED, trimmed query. The caller owns the debounce. */
  query: string;
  /** Selected wings. EMPTY = all four (the endpoint's own default). */
  corpora: readonly SearchCorpus[];
  /**
   * The caller's own gate — in practice `isAuthenticated`. False keeps the query
   * idle: `/api/v1/search` 401s an anonymous caller by design (D9), so firing it
   * would spend a round trip to be refused.
   */
  enabled?: boolean;
}

/**
 * The cross-wing result set behind `/library` — the one surface that calls
 * `GET /api/v1/search` (§6.2).
 *
 * Every other search surface in the app calls its OWN wing endpoint with `?q=`,
 * because those cards need the static free excerpt only the hub envelope
 * carries. This page renders a card-agnostic result ROW instead, which is
 * exactly why it can consume the snippet-less `SearchHit` — and exactly why the
 * hub cards must not be reused for it.
 *
 * `keepPreviousData` holds the previous set on screen while the next keystroke
 * is in flight, so the list does not collapse to a spinner on every letter.
 * `retry: false` because a 429 must not be hammered and a search is re-triggered
 * by typing anyway.
 */
export function useLibrarySearch({
  query,
  corpora,
  enabled = true,
}: UseLibrarySearchParams) {
  const trimmed = query.trim();
  return useQuery({
    queryKey: searchKeys.library(trimmed, corpora),
    queryFn: () => fetchLibrarySearch(trimmed, corpora),
    enabled: enabled && trimmed.length >= SEARCH_MIN_LENGTH,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    retry: false,
  });
}

// ==========================================================================
// CLIENT STATE — the box's own state machine (Wave D)
// ==========================================================================

export interface SearchQueryState {
  /** Raw, un-debounced input. Bind straight to `<SearchBar value>`. */
  value: string;
  /** Bind straight to `<SearchBar onChange>`. */
  setValue: (next: string) => void;
  /**
   * The term to actually SEND: debounced, trimmed, and `""` until it clears the
   * floor. A caller can pass it to a list hook unconditionally — an empty
   * string means "no search", which is precisely what the unfiltered list is.
   */
  query: string;
  /** Is a search currently narrowing this surface? `query.length > 0`. */
  isSearching: boolean;
  /** Drop the search and return the surface to its unfiltered listing. */
  reset: () => void;
}

/**
 * The three-part state every live search box needs — raw value, debounced term,
 * and the minimum-length floor — in ONE place (plan §7, Wave D's one new hook).
 *
 * ── WHY THE FLOOR LIVES HERE AND NOT IN THE COMPONENT ───────────────────────
 * `SearchBar` RENDERS the floor (`SEARCH_COPY.minLengthHint` under a 1–2
 * character value) but does not enforce it — it is a controlled input and has
 * no idea what the caller does with the value. Enforcement has to sit between
 * the box and the request, which is here. Getting this wrong is not cosmetic:
 * `search_service.normalize_query` REJECTS a shorter term with a 400 in Arabic,
 * so a surface that forwarded two characters would show its reader a hint that
 * says "type three" *and* an error toast at the same time.
 *
 * ── ONE FLOOR, AND THE ONE SURFACE THAT IS NOT ON IT ────────────────────────
 * `minLength` defaults to three and every BM25 surface takes the default. Three
 * is the number `SearchBar` prints and the number `search_service.normalize_query`
 * enforces, so a library surface that passed two characters would show a hint
 * saying "type three" *and* an error toast at once.
 *
 * The parameter exists for exactly one caller, and not as a style choice:
 * `/chats` is NOT a BM25 surface. It never enters `search_index` — it searches
 * `conversations` via `GET /api/v1/conversations?q=` (trigram over `title_ar` +
 * `messages.content`), which imposes NO minimum length (`api/conversations.py:41`).
 * Defaulting it to three would delete one- and two-character search from a
 * shipped feature unrelated to this project. Guard the parameter jealously: any
 * surface backed by `bm25_search()` uses the default, no exceptions.
 *
 * The debounce is `SEARCH_DEBOUNCE_MS` — the `/chats` value, which is where the
 * live-search pattern in this app started.
 */
export function useSearchQuery(opts?: { minLength?: number }): SearchQueryState {
  const minLength = opts?.minLength ?? SEARCH_MIN_LENGTH;
  const [value, setValue] = useState("");
  const debounced = useDebounce(value, SEARCH_DEBOUNCE_MS).trim();
  // Below the floor is not "search for a short thing", it is "do not search".
  const query = debounced.length >= minLength ? debounced : "";
  const reset = useCallback(() => setValue(""), []);

  return { value, setValue, query, isSearching: query.length > 0, reset };
}
