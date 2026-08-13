import type { ReactNode } from "react";
import Link from "next/link";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { COURT_LEVEL_FILTERS } from "@/lib/library/court-levels";
import { hrefWithFilters } from "@/lib/library/hub-query";
import { HubSearchPanel } from "@/components/library/hub/HubSearchPanel";
import type { JudgmentsFilters } from "@/lib/library/api";

/** The unfiltered wing. A court SECTION passes its own path instead. */
const DEFAULT_BASE_PATH = "/judgments";

/**
 * The /judgments hub filter row: درجة المحكمة chips + the shared search box —
 * and, while a search is live, the results that replace the grid.
 *
 * ── THE SECOND AXIS: «الجهة القضائية» (court sections) ──────────────────────
 * `basePath` is what makes this bar work unchanged inside
 * `/judgments/courts/{slug}`. The court lives in the PATH, not the query string,
 * so every chip href here is rebuilt against the section's base path and a
 * reader who picks «استئناف» inside المحكمة التجارية stays inside المحكمة
 * التجارية. The two axes COMPOSE: the court switcher (`CourtSwitcher`) changes
 * the section, these chips narrow it, and neither clears the other. `court`
 * itself is deliberately NOT a chip and gets no dismiss row — the «جميع الجهات»
 * tile in the switcher and the breadcrumb are its clear actions.
 *
 * ── WHAT CHANGED, AND WHAT DID NOT (bm25_navigation_search.md §6.1/§6.2) ────
 * The court-level chips are UNTOUCHED: still plain `<Link>`s that rewrite the
 * query string, still no client state, still crawlable, shareable,
 * back-button-correct and working with JS disabled. Every filter change still
 * RESETS to page 1 by targeting the base path — landing on `/judgments/page/7`
 * of a freshly-narrowed result set would show an empty page.
 *
 * What changed is the search box. It used to be a GET `<form>` that navigated
 * to `/judgments?q=…` and let the server `ilike` the summary column. D9 makes
 * search registered-only, which means the server-side (anonymous, ISR-cached)
 * fetch can no longer answer a search at all — so the box is now the app-wide
 * `SearchBar`, live and debounced, querying with the reader's own bearer from
 * the browser. It is the same component `/regulations`, `/circulars` and
 * `/compliance` grew in the same wave, with the RTL layout disagreement between
 * the old box and `ConversationSearch` settled in its favour (icon at
 * `start-3`, clear at `end-2.5`).
 *
 * Two consequences worth naming:
 *
 *   · The `:tooShort` CSS hint and the `minLength` attribute are gone with the
 *     `<form>`. `SearchBar` renders the same 3-character message from
 *     `lib/search/copy.ts`, driven by the value rather than by native
 *     constraint validation, so every surface says it identically.
 *   · The chips no longer carry `q`. A live query is client state mirrored onto
 *     the URL; a chip is a full navigation, and preserving a query the
 *     server-rendered href cannot see would restore it stale. Changing the
 *     court level is a fresh browse — which is what «reset to page 1» already
 *     means here.
 *
 * The active-filter row survives for `domain`, which is still the only place a
 * domain filter can be cleared (hub cards can't carry filter links: they are
 * themselves one `<Link>`). Its `q` chip is gone — the box's own × is the clear
 * action now, and two dismiss controls for one filter is one too many.
 */
export function JudgmentsFilterBar({
  filters,
  sectorSlugs,
  basePath = DEFAULT_BASE_PATH,
  children,
}: {
  filters: JudgmentsFilters;
  /** `name_ar → slug` for the sector pills on search result cards (D11). */
  sectorSlugs?: Record<string, string>;
  /**
   * Where a chip navigates: `/judgments`, or `/judgments/courts/{slug}` inside
   * a court section. Always the PAGE-1 path — every filter change resets to
   * page 1, because landing on page 7 of a freshly-narrowed set shows nothing.
   */
  basePath?: string;
  /** The hub's normal body — shown whenever no search is live. */
  children: ReactNode;
}) {
  const { court_level = "", domain = "", court = "" } = filters;

  return (
    <HubSearchPanel
      section="judgments"
      sectorSlugs={sectorSlugs}
      // Searching INSIDE the active chips AND inside the active court section,
      // not around them: a reader who opened المحكمة التجارية, picked «استئناف»
      // and then typed expects all three to apply. `court` reaches the wing
      // endpoint as a query param on the authed search call — which is the same
      // contract the server-side browse fetch uses, so search and browse narrow
      // to the identical slice.
      filters={{ court_level, domain, court }}
      leading={
        <CourtLevelChips
          basePath={basePath}
          courtLevel={court_level}
          domain={domain}
        />
      }
      below={
        domain ? (
          <ActiveFilters
            basePath={basePath}
            courtLevel={court_level}
            domain={domain}
          />
        ) : null
      }
    >
      {children}
    </HubSearchPanel>
  );
}

/**
 * درجة المحكمة — three real values plus «الكل». The vocabulary comes from
 * `lib/library/court-levels` (never re-derived inline: the two-branch copy is
 * the bug that dropped every supreme-court ruling). The «الكل» option is this
 * filter's own clear action, which is why it gets no dismiss chip below.
 */
function CourtLevelChips({
  basePath,
  courtLevel,
  domain,
}: {
  basePath: string;
  courtLevel: string;
  domain: string;
}) {
  return (
    <ul className="flex flex-wrap items-center gap-1.5">
      {COURT_LEVEL_FILTERS.map((option) => {
        const isActive = option.value === courtLevel;
        return (
          <li key={option.value || "all"}>
            <Link
              href={hrefWithFilters(basePath, {
                court_level: option.value,
                domain,
              })}
              aria-current={isActive ? "true" : undefined}
              className={cn(
                "inline-flex h-8 items-center rounded-full border px-3 text-sm font-medium transition-colors",
                isActive
                  ? "border-primary bg-primary text-primary-foreground shadow-xs"
                  : "border-border bg-card text-text-secondary hover:border-primary/40 hover:text-primary",
              )}
            >
              {option.label}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

/**
 * The dismiss row — `domain` only; see the component note above.
 *
 * Both links target `basePath`, so clearing a domain inside a court section
 * clears the DOMAIN and keeps the section. «مسح الكل» means "all the filters on
 * this page", not "leave the section" — the switcher owns that.
 */
function ActiveFilters({
  basePath,
  courtLevel,
  domain,
}: {
  basePath: string;
  courtLevel: string;
  domain: string;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      <span>الفلاتر النشطة:</span>
      <Link
        href={hrefWithFilters(basePath, { court_level: courtLevel })}
        aria-label={`إزالة فلتر المجال ${domain}`}
        className="inline-flex items-center gap-1 rounded-full bg-accent-soft px-2.5 py-0.5 text-xs font-medium text-primary transition-colors hover:bg-pill"
      >
        {domain}
        <X aria-hidden="true" className="h-3 w-3 shrink-0" />
      </Link>
      <Link
        href={basePath}
        className="underline-offset-2 transition-colors hover:text-foreground hover:underline"
      >
        مسح الكل
      </Link>
    </div>
  );
}
