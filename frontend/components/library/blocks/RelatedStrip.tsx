import { Children } from "react";
import { BookMarked } from "lucide-react";
import { cn } from "@/lib/utils";
import type { RelatedStripProps } from "@/types/library";

/**
 * A horizontal, RTL side-scrolling row of hub cards — the shared frame behind
 * both «الأنظمة المذكورة» and «اقرأ تاليًا» at the foot of every public library
 * document page.
 *
 * IT OWNS THE TRACK, NOT THE CARDS. Children are the wings' EXISTING hub cards
 * (`RegulationCard`, `ComplianceCard`, `CircularCard`, `JudgmentCard`) rendered
 * unchanged; this component only wraps each one in a snap target of the right
 * width. That is deliberate — a second card design for "related" items is how
 * the library ends up with two visual languages for one object.
 *
 * ⚠ NATIVE `overflow-x-auto`, NEVER RADIX `ScrollArea`. `ScrollArea` replaces
 * the scroll container with a viewport `<div>` whose sizing breaks `h-full` and
 * `truncate` on descendants — documented twice in this repo where it bit
 * (`references_window_fixes`, `sidebar_redesign_compact_nav`). Every hub card
 * depends on BOTH: `CardShell` is `h-full flex-col`, and the entity line is
 * `truncate`. So the track is a plain scroll container with CSS scroll-snap.
 *
 * SIZING. 1.15 cards in view on mobile — the cut-off card at the inline end is
 * the entire scrollability affordance, since the scrollbar is painted away
 * (`.scrollbar-none`, `globals.css`) — then 2 from `sm`, 3 from `lg`. The
 * negative inline margin lets the track bleed to the page gutter so a card can
 * sit flush with the reading column's edge; the matching padding keeps the
 * first card off it, and `scroll-ps-*` makes snap stops land on the padding
 * rather than under it.
 *
 * EMPTY ⇒ NOTHING. No children means no heading and no empty box: these strips
 * are genuinely absent on most أحكام pages (the relation floor clears on maybe
 * a quarter of them) and a bare heading over blank space reads as breakage.
 *
 * Server component, and it must stay one: all four host pages are ISR-baked and
 * serve ONE HTML artifact to anon, free and paid readers alike. Nothing here
 * may read auth, cookies or headers — the strip is identical for everyone by
 * construction, not by policy.
 */
export function RelatedStrip({
  title,
  children,
  icon: Icon = BookMarked,
  className,
}: RelatedStripProps) {
  // `Children.toArray` drops null/undefined/false, so a caller mapping over an
  // empty (or fully filtered) list collapses the whole section — the callers
  // don't have to guard, and a `{cond && <Card/>}` child can't leave a hole.
  const cards = Children.toArray(children);
  if (cards.length === 0) return null;

  return (
    <section dir="rtl" className={cn("w-full", className)}>
      <h2 className="mb-3 flex items-center gap-2 text-sm font-bold text-foreground">
        <Icon aria-hidden="true" className="h-4 w-4 shrink-0 text-primary" />
        {title}
      </h2>

      <ul
        aria-label={title}
        // A scroll container that is not otherwise focusable is unreachable by
        // keyboard in Chromium (WCAG 2.1.1). One tab stop buys arrow-key
        // scrolling for the whole strip.
        tabIndex={0}
        className={cn(
          "scrollbar-none flex snap-x snap-mandatory gap-3 overflow-x-auto scroll-smooth",
          // Bleed to the page gutter on mobile, a hair on desktop — the inline
          // padding is what the hover lift + shadow-md need so the card chrome
          // isn't clipped by the scroll container.
          "-mx-4 scroll-ps-4 px-4 py-1 sm:-mx-2 sm:scroll-ps-2 sm:px-2",
          "rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        )}
      >
        {cards.map((card, index) => (
          <li
            key={index}
            // THE TWO MINIMUM-SIZE GUARDS, and the cards do not lay out without
            // them:
            //
            // `min-w-0` — a flex item's default `min-width: auto` is its
            // CONTENT-based minimum, so a card holding a long unbroken title
            // would push the item wider than its basis and quietly break
            // 3-in-view.
            //
            // `grid grid-cols-1` — Tailwind compiles that to
            // `repeat(1, minmax(0, 1fr))`, which is the exact same guard the hub
            // grids rely on: a track whose min sizing function is 0 switches OFF
            // the grid item's automatic minimum size, so the card can be
            // narrower than its own content and `truncate` / `line-clamp`
            // actually clip. A plain `flex` or block wrapper here loses that.
            // Grid stretch also fills the cell in both axes, which is what keeps
            // `CardShell`'s `h-full` (equal-height cards) honest.
            className={cn(
              "grid min-w-0 shrink-0 snap-start grid-cols-1",
              // 3 cards fully in view on desktop plus a sliver of the 4th. The
              // sliver is the ONLY affordance that cards 4-7 exist: the
              // scrollbar is painted away and server components cannot add
              // arrows or a fade. Mobile already peeks at 85%. Drop the .25 back
              // to /3 for a hard three-up with no scroll cue.
              "basis-[85%] sm:basis-[calc((100%-0.75rem)/2)] lg:basis-[calc((100%-1.5rem)/3.25)]",
            )}
          >
            {card}
          </li>
        ))}
      </ul>
    </section>
  );
}
