import Link from "next/link";
import { cn } from "@/lib/utils";

/**
 * The frame every hub card renders inside — one place for the card chrome, and
 * the one place that decides whether a card is a link.
 *
 * WHY A CARD MIGHT NOT BE A LINK: مكتبتي lists items the reader has unlocked,
 * and an item can be unlocked from a CHAT CITATION long before it has a public
 * library page. Only 100 of 3,373 regulations carry a slug today (the library is
 * still in sample mode), so "unlocked but unpublished" is the COMMON case on the
 * shelf, not an edge case. Those rows must still render as proper cards — the
 * reader owns them — just without a dead `<Link>`.
 *
 * `href={null}` renders the identical card as a plain `<div>`. Passing a string
 * is the normal hub behaviour.
 *
 * ⚠ `footer` IS RENDERED OUTSIDE THE ANCHOR, AND THAT IS THE WHOLE POINT.
 * The card used to BE the `<Link>`, which made every chip inside it a dead
 * `<span>` by necessity — nesting `<a>` inside `<a>` is invalid HTML and the
 * browser's parser silently un-nests it, breaking hydration. That constraint is
 * documented in two places it blocked (`JudgmentCard`'s domain chips,
 * `JudgmentsFilterBar`'s "hub cards can't carry filter links"), and D11 needs it
 * gone: sector pills must navigate to `/library/{slug}`.
 *
 * So the frame moved OUT to a wrapper `<div>` and the anchor moved IN, covering
 * only the card body. `footer` is then a SIBLING of the anchor inside the same
 * frame — real links, valid HTML, no z-index overlay tricks, and text selection
 * over the body is unaffected. The body still owns `flex-1`, so anything a card
 * pins with `mt-auto` keeps pinning to the bottom of the body.
 */
const CARD_CLASS =
  "group flex h-full flex-col rounded-xl border border-border bg-card p-4 " +
  "shadow-xs transition-all duration-200 sm:p-5";

const LINK_CLASS =
  "hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md";

export function CardShell({
  href,
  footer,
  children,
}: {
  href: string | null;
  /** Interactive chrome that must NOT sit inside the card's anchor. */
  footer?: React.ReactNode;
  children: React.ReactNode;
}) {
  const body = href ? (
    <Link
      href={href}
      className="flex flex-1 flex-col rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {children}
    </Link>
  ) : (
    // Not interactive: no hover lift, no pointer — nothing here can be opened.
    <div className="flex flex-1 flex-col">{children}</div>
  );

  return (
    <div dir="rtl" className={cn(CARD_CLASS, href && LINK_CLASS)}>
      {body}
      {footer}
    </div>
  );
}
