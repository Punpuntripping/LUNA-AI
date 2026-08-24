"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { ChevronLeft, ListTree, Lock, Search, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { parseTocLabel } from "@/lib/library/toc";
import { toLatinDigits } from "@/lib/format/numerals";
import { useTocScrollspy, TOC_GATE_ANCHOR_ID } from "@/hooks/use-toc-scrollspy";
import type { TocEntry, TocFloatingProps } from "@/types/library";

/** Rows in the compact window: the active مادة plus two on each side. */
const WINDOW_SIZE = 5;

/**
 * Phone header is 64px (`SiteHeader`'s h-16 bar, which `LibraryPageShell`
 * composes), not the desktop
 * rail's 96px allowance — the spy's top inset is retuned to match, so the pill
 * names the مادة that is actually under the header.
 */
const PHONE_ROOT_MARGIN = "-72px 0px -60% 0px";

const GATED_HINT = "محتوى محجوب — سجّل مجاناً لعرضه";

/**
 * The floating phone TOC — «فهرس المواد» compressed into a thumb-reachable pill.
 *
 * A 700-مادة نظام is ~156,000px tall on a 390px screen, and the only mobile
 * index was the collapsed `<details>` at the very top: once a reader scrolled
 * into المواد there was no way back to the index short of scrolling to the top
 * of the document. This widget is that index, always within reach:
 *
 *   pill  → the مادة being read right now (scrollspy-driven), tap to open
 *   panel → a 5-row window centred on that مادة (active ±2, clamped)
 *   sheet → «عرض الكل» — every entry, with a filter over number + title
 *
 * The inline `TocList` STAYS: it is the first impression and the crawlable copy
 * of the index. This widget only appears once that list has scrolled away —
 * which is exactly what the in-flow sentinel below measures.
 *
 * Client island: the pages are server components and hand it the same
 * serializable `entries` they already computed for `TocList`. Desktop never
 * sees it — the pages wrap it in `lg:hidden`, where the sticky `TocRail` owns
 * the job.
 */
export function TocFloating({
  entries,
  title = "محتويات النظام",
  badge,
}: TocFloatingProps) {
  const { activeId, jumpTo, handleAnchorClick, hasTarget } = useTocScrollspy(
    entries,
    { rootMargin: PHONE_ROOT_MARGIN },
  );

  const [panelOpen, setPanelOpen] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [pastInlineToc, setPastInlineToc] = useState(false);
  // `null` = not measured yet. Filled lazily on open: `FullContentGate` swaps
  // the full section list in after mount for a signed-in reader, so a snapshot
  // taken at mount would lock rows that are, by then, right there on the page.
  const [gatedIds, setGatedIds] = useState<Set<string> | null>(null);

  const sentinelRef = useRef<HTMLSpanElement | null>(null);
  const filterRef = useRef<HTMLInputElement | null>(null);

  // The pill exists only below the inline TOC. The sentinel sits in normal flow
  // right after that list, so "has it scrolled off the top" is one observer and
  // needs no scroll listener and no magic pixel threshold.
  useEffect(() => {
    const node = sentinelRef.current;
    if (!node) return;
    const observer = new IntersectionObserver(
      ([observed]) => {
        if (!observed) return;
        setPastInlineToc(
          !observed.isIntersecting && observed.boundingClientRect.top < 0,
        );
      },
      { threshold: 0 },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  // Re-measure which rows have no section in the DOM, once per open.
  useEffect(() => {
    if (!panelOpen && !sheetOpen) return;
    const missing = new Set<string>();
    for (const entry of entries) {
      if (!entry.href || !hasTarget(entry.href)) missing.add(entry.id);
    }
    setGatedIds(missing);
  }, [panelOpen, sheetOpen, entries, hasTarget]);

  // Escape closes; the full sheet also locks the page behind it.
  useEffect(() => {
    if (!panelOpen && !sheetOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setSheetOpen(false);
      setPanelOpen(false);
    };
    document.addEventListener("keydown", onKey);
    if (!sheetOpen) {
      return () => document.removeEventListener("keydown", onKey);
    }
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prevOverflow;
      document.removeEventListener("keydown", onKey);
    };
  }, [panelOpen, sheetOpen]);

  const activeIndex = useMemo(() => {
    if (!activeId) return -1;
    return entries.findIndex((entry) => entry.href === `#${activeId}`);
  }, [entries, activeId]);

  // Active ±2, clamped at both ends so the window is always WINDOW_SIZE rows.
  const windowStart = useMemo(() => {
    const last = Math.max(0, entries.length - WINDOW_SIZE);
    if (activeIndex < 0) return 0;
    return Math.min(Math.max(activeIndex - 2, 0), last);
  }, [activeIndex, entries.length]);

  const windowEntries = useMemo(
    () => entries.slice(windowStart, windowStart + WINDOW_SIZE),
    [entries, windowStart],
  );

  // Digits are normalised on BOTH sides so «45» finds «المادة ٤٥»: the corpus
  // labels are inconsistent about numerals, and the reader types whatever their
  // keyboard produces.
  const filtered = useMemo(() => {
    const needle = toLatinDigits(query.trim().toLowerCase());
    if (!needle) return entries;
    return entries.filter((entry) =>
      toLatinDigits(entry.label.toLowerCase()).includes(needle),
    );
  }, [entries, query]);

  const pillLabel =
    activeIndex >= 0 ? entries[activeIndex]!.label : "المحتويات";

  const closeAll = useCallback(() => {
    setPanelOpen(false);
    setSheetOpen(false);
  }, []);

  const openSheet = useCallback(() => {
    setPanelOpen(false);
    setSheetOpen(true);
    // Focus after paint — a phone keyboard opening on an unmounted input does
    // nothing, and iOS ignores focus() issued in the same tick as the render.
    requestAnimationFrame(() => filterRef.current?.focus());
  }, []);

  const jumpToGate = useCallback(() => {
    jumpTo(`#${TOC_GATE_ANCHOR_ID}`);
    closeAll();
  }, [jumpTo, closeAll]);

  if (entries.length === 0) return null;

  const showPill = pastInlineToc && !sheetOpen;

  return (
    <>
      {/* In-flow marker: "the inline TOC ends here". 1px, not 0 — a zero-area
          target is a degenerate case for IntersectionObserver and some engines
          never fire the enter/leave callback for one. */}
      <span ref={sentinelRef} aria-hidden="true" className="block h-px" />

      {showPill && (
        <button
          type="button"
          onClick={() => setPanelOpen((open) => !open)}
          aria-expanded={panelOpen}
          aria-label={`${title} — ${pillLabel}`}
          // `start-4` = the RTL right corner, deliberately opposite the
          // «اسأل ريحان» FAB (`left-5`, physical left). Both are z-40 and never
          // overlap. Offsets clear the home indicator (`viewportFit:"cover"`).
          className="animate-fab-in fixed bottom-[calc(1.25rem+env(safe-area-inset-bottom))] start-4 z-40 inline-flex h-11 max-w-[60vw] items-center gap-2 rounded-full border border-border bg-card px-3 text-sm font-semibold text-foreground shadow-md transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 active:bg-accent-soft"
        >
          <ListTree aria-hidden="true" className="h-4 w-4 shrink-0 text-primary" />
          <span className="truncate">{pillLabel}</span>
        </button>
      )}

      {/* ── Compact window ─────────────────────────────────────────────── */}
      {panelOpen && !sheetOpen && (
        <>
          <div
            aria-hidden="true"
            onClick={() => setPanelOpen(false)}
            className="fixed inset-0 z-30"
          />
          <div
            dir="rtl"
            role="dialog"
            aria-label={title}
            // 4.5rem = pill offset (1.25) + pill height (2.75) + gap (0.5).
            className="fixed bottom-[calc(4.5rem+env(safe-area-inset-bottom))] start-4 z-40 w-72 max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-border bg-card shadow-lg"
          >
            <ul className="p-1.5">
              {windowEntries.map((entry) => (
                <li key={entry.id}>
                  <TocFloatingRow
                    entry={entry}
                    active={Boolean(activeId) && entry.href === `#${activeId}`}
                    gated={gatedIds?.has(entry.id) ?? false}
                    onAnchorClick={handleAnchorClick}
                    onGatedTap={jumpToGate}
                    onDone={closeAll}
                  />
                </li>
              ))}
            </ul>

            <div className="flex items-center justify-between gap-2 border-t border-border px-1.5 py-1.5">
              <button
                type="button"
                onClick={openSheet}
                className="inline-flex h-9 items-center gap-1.5 rounded-lg px-2.5 text-sm font-semibold text-primary transition-colors active:bg-accent-soft"
              >
                عرض الكل ({entries.length})
              </button>
              <button
                type="button"
                onClick={() => setPanelOpen(false)}
                aria-label="إغلاق"
                className="inline-flex h-9 w-9 items-center justify-center rounded-lg text-text-muted transition-colors active:bg-accent-soft"
              >
                <X aria-hidden="true" className="h-4 w-4" />
              </button>
            </div>
          </div>
        </>
      )}

      {/* ── Full sheet ─────────────────────────────────────────────────── */}
      {sheetOpen && (
        <>
          <div
            aria-hidden="true"
            onClick={() => setSheetOpen(false)}
            className="fixed inset-0 z-40 bg-black/50"
          />
          <div
            dir="rtl"
            role="dialog"
            aria-modal="true"
            aria-label={title}
            className="fixed inset-x-0 bottom-0 z-50 flex max-h-[82vh] flex-col rounded-t-2xl border-t border-border bg-card pb-[env(safe-area-inset-bottom)] shadow-2xl"
          >
            <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
              <span className="flex min-w-0 items-center gap-2">
                <ListTree
                  aria-hidden="true"
                  className="h-4 w-4 shrink-0 text-primary"
                />
                <span className="truncate text-sm font-bold text-foreground">
                  {title}
                </span>
                {badge && (
                  <span className="shrink-0 rounded-full bg-accent-soft px-2 py-0.5 text-xs font-semibold text-primary">
                    {badge}
                  </span>
                )}
              </span>
              <button
                type="button"
                onClick={() => setSheetOpen(false)}
                aria-label="إغلاق"
                className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-text-muted transition-colors active:bg-accent-soft"
              >
                <X aria-hidden="true" className="h-4 w-4" />
              </button>
            </div>

            <div className="relative border-b border-border px-4 py-2.5">
              <Search
                aria-hidden="true"
                className="pointer-events-none absolute end-7 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted"
              />
              <input
                ref={filterRef}
                type="search"
                dir="rtl"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="ابحث برقم المادة أو عنوانها..."
                aria-label="تصفية المحتويات"
                // 16px minimum comes from the global phone input rule in
                // globals.css — never hardcode a smaller size here or iOS
                // zooms the whole document on focus.
                className="h-11 w-full rounded-xl border border-border bg-background pe-10 ps-3 text-sm text-foreground outline-none transition-colors placeholder:text-text-muted focus:border-primary/50 [&::-webkit-search-cancel-button]:appearance-none"
              />
            </div>

            <ul className="scrollbar-thin min-h-0 flex-1 overflow-y-auto overscroll-contain p-1.5">
              {filtered.map((entry) => (
                <li
                  key={entry.id}
                  style={{
                    paddingInlineStart: `${((entry.level ?? 1) - 1) * 12}px`,
                  }}
                >
                  <TocFloatingRow
                    entry={entry}
                    active={Boolean(activeId) && entry.href === `#${activeId}`}
                    gated={gatedIds?.has(entry.id) ?? false}
                    onAnchorClick={handleAnchorClick}
                    onGatedTap={jumpToGate}
                    onDone={closeAll}
                  />
                </li>
              ))}
              {filtered.length === 0 && (
                <li className="px-3 py-6 text-center text-sm text-text-muted">
                  لا توجد نتائج مطابقة
                </li>
              )}
            </ul>
          </div>
        </>
      )}
    </>
  );
}

/**
 * One TOC row, 44px tall (the touch floor). Three shapes, all tappable:
 *   * outbound مادة page → a real `<Link>` that navigates;
 *   * same-page anchor → smooth scroll, or the signup gate when the section
 *     isn't in the DOM (anon reader) — the hook owns that fallback;
 *   * locked entry (no href at all) → straight to the gate.
 */
function TocFloatingRow({
  entry,
  active,
  gated,
  onAnchorClick,
  onGatedTap,
  onDone,
}: {
  entry: TocEntry;
  active: boolean;
  gated: boolean;
  onAnchorClick: (
    event: React.MouseEvent<HTMLAnchorElement>,
    href: string,
  ) => void;
  onGatedTap: () => void;
  onDone: () => void;
}) {
  const { chip, text } = parseTocLabel(entry.label);
  const rowClass = cn(
    "flex h-11 w-full items-center gap-2 rounded-lg px-2.5 text-start transition-colors",
    active
      ? "bg-primary/10 font-medium text-primary"
      : "text-text-secondary active:bg-accent-soft",
  );

  const inner = (
    <>
      <span className="flex-1 truncate text-sm">{text}</span>
      {gated && (
        <Lock
          aria-hidden="true"
          className="h-3.5 w-3.5 shrink-0 text-text-subtle"
        />
      )}
      {chip ? (
        <span
          className={cn(
            "shrink-0 rounded-md px-1.5 py-0.5 font-mono text-xs font-semibold tabular-nums",
            active
              ? "bg-primary text-primary-foreground"
              : "bg-surface-2 text-text-muted",
          )}
        >
          {chip}
        </span>
      ) : (
        <ChevronLeft
          aria-hidden="true"
          className="h-3.5 w-3.5 shrink-0 text-text-subtle"
        />
      )}
    </>
  );

  if (!entry.href) {
    return (
      <button
        type="button"
        onClick={onGatedTap}
        title={GATED_HINT}
        className={rowClass}
      >
        {inner}
      </button>
    );
  }

  return (
    <Link
      href={entry.href}
      onClick={(event) => {
        onAnchorClick(event, entry.href!);
        onDone();
      }}
      aria-current={active ? "location" : undefined}
      title={gated ? GATED_HINT : undefined}
      className={rowClass}
    >
      {inner}
    </Link>
  );
}
