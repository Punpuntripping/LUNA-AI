import Link from "next/link";
import { Search, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { COURT_LEVEL_FILTERS } from "@/lib/library/court-levels";
import { hrefWithFilters } from "@/lib/library/hub-query";
import type { JudgmentsFilters } from "@/lib/library/api";

const BASE_PATH = "/judgments";

/** Ties the too-short hint to the search box for assistive tech. */
const HINT_ID = "judgments-q-hint";

/** The server's floor for any free-text hub filter (navigation hardening 2.1). */
const MIN_QUERY_LENGTH = 3;

/**
 * The /judgments hub filter row: درجة المحكمة chips + a free-text search box.
 *
 * NO CLIENT STATE — and this is deliberate, not a shortcut. Every other library
 * hub is a pure server component, and a filter here is just a URL: the court
 * level chips are plain `<Link>`s that rewrite the query string, and the search
 * box is a plain HTML GET `<form>` whose submission navigates to
 * `/judgments?q=…`. That keeps the filtered views crawlable, shareable,
 * back-button-correct and ISR-cacheable (the fetch layer still caches per
 * filter combination), and it works with JS disabled. A `"use client"` island
 * would buy nothing here and would break the server-rendered card grid.
 *
 * Every filter change RESETS to page 1 by targeting the base path — landing on
 * `/judgments/page/7` of a freshly-narrowed result set would show an empty page.
 * The form carries the other active filters as hidden inputs so searching never
 * silently drops the court-level selection.
 *
 * The search box also carries the server's 3-character floor (navigation
 * hardening 2.1) as a native `minLength` plus an inline Arabic hint, so a
 * one-letter search reads as «اكتب ٣ أحرف على الأقل للبحث» instead of a 400.
 * Native constraint validation only — nothing is disabled, and an EMPTY box
 * still submits, which is how a reader clears the search.
 */
export function JudgmentsFilterBar({ filters }: { filters: JudgmentsFilters }) {
  const { court_level = "", domain = "", q = "" } = filters;
  // The court level needs no removable chip below — its own «الكل» option is
  // the clear action. Only the domain + free-text filters get a dismiss row.
  const hasDismissable = Boolean(domain || q);

  return (
    <div dir="rtl" className="space-y-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        {/* درجة المحكمة — three real values plus «الكل». The vocabulary comes
            from lib/library/court-levels (never re-derived inline: the two-branch
            copy is the bug that dropped every supreme-court ruling). */}
        <ul className="flex flex-wrap items-center gap-1.5">
          {COURT_LEVEL_FILTERS.map((option) => {
            const isActive = option.value === court_level;
            return (
              <li key={option.value || "all"}>
                <Link
                  href={hrefWithFilters(BASE_PATH, {
                    court_level: option.value,
                    domain,
                    q,
                  })}
                  aria-current={isActive ? "true" : undefined}
                  className={cn(
                    "inline-flex h-8 items-center rounded-full border px-3 text-[13px] font-medium transition-colors",
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

        {/* Plain GET form → full navigation to /judgments?q=… (page 1). */}
        <form
          action={BASE_PATH}
          method="get"
          role="search"
          className="flex w-full items-center gap-2 sm:w-auto"
        >
          {court_level && (
            <input type="hidden" name="court_level" value={court_level} />
          )}
          {domain && <input type="hidden" name="domain" value={domain} />}

          <div className="relative flex-1 sm:w-64 sm:flex-none">
            {/* RTL: `start-3` is the physical RIGHT edge — the icon leads the
                text the way the reader enters it. */}
            <Search
              aria-hidden="true"
              className="pointer-events-none absolute start-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted"
            />
            {/* `minLength` mirrors the server's own floor (navigation hardening
                2.1: a free-text hub filter under 3 characters is refused with a
                400). Catching it here turns a round-trip refusal into an inline
                hint — but it is deliberately the ONLY guard: the submit button
                stays live, and an empty box still submits, so clearing the
                search to see everything again always works. */}
            <input
              type="search"
              name="q"
              defaultValue={q}
              minLength={MIN_QUERY_LENGTH}
              aria-describedby={HINT_ID}
              placeholder="ابحث في الأحكام…"
              aria-label="ابحث في الأحكام القضائية"
              className="peer h-9 w-full rounded-full border border-border bg-card ps-9 pe-4 text-[13px] text-foreground outline-none transition-colors placeholder:text-text-muted focus:border-primary/50"
            />
            {/* Shown ONLY while the reader's own typing is too short —
                `:tooShort` needs a dirty, non-empty value, so this never fires
                on load and never nags an untouched box. Absolutely positioned so
                appearing costs no layout shift in the filter row. */}
            <p
              id={HINT_ID}
              className="pointer-events-none absolute inset-x-0 top-full z-10 mt-1 hidden ps-9 pe-4 text-[11px] leading-tight text-text-muted peer-invalid:block"
            >
              اكتب ٣ أحرف على الأقل للبحث
            </p>
          </div>
          <button
            type="submit"
            className="h-9 shrink-0 rounded-full bg-primary px-4 text-[13px] font-semibold text-primary-foreground transition-colors hover:bg-primary-hover"
          >
            بحث
          </button>
        </form>
      </div>

      {/* Active-filter summary — the only place a domain filter can be cleared
          (hub cards can't carry filter links: they are themselves one <Link>). */}
      {hasDismissable && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span>الفلاتر النشطة:</span>
          {domain && (
            <Link
              href={hrefWithFilters(BASE_PATH, { court_level, q })}
              aria-label={`إزالة فلتر المجال ${domain}`}
              className="inline-flex items-center gap-1 rounded-full bg-accent-soft px-2.5 py-0.5 text-[11px] font-medium text-primary transition-colors hover:bg-pill"
            >
              {domain}
              <X aria-hidden="true" className="h-3 w-3 shrink-0" />
            </Link>
          )}
          {q && (
            <Link
              href={hrefWithFilters(BASE_PATH, { court_level, domain })}
              aria-label={`إزالة البحث عن ${q}`}
              className="inline-flex items-center gap-1 rounded-full bg-accent-soft px-2.5 py-0.5 text-[11px] font-medium text-primary transition-colors hover:bg-pill"
            >
              «{q}»
              <X aria-hidden="true" className="h-3 w-3 shrink-0" />
            </Link>
          )}
          <Link
            href={BASE_PATH}
            className="underline-offset-2 transition-colors hover:text-foreground hover:underline"
          >
            مسح الكل
          </Link>
        </div>
      )}
    </div>
  );
}
