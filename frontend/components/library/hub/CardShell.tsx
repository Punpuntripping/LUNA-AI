import Link from "next/link";

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
 */
const CARD_CLASS =
  "group flex h-full flex-col rounded-xl border border-border bg-card p-4 " +
  "shadow-xs transition-all duration-200 sm:p-5";

const LINK_CLASS =
  "hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md";

export function CardShell({
  href,
  children,
}: {
  href: string | null;
  children: React.ReactNode;
}) {
  if (!href) {
    // Not interactive: no hover lift, no pointer — nothing here can be opened.
    return (
      <div dir="rtl" className={CARD_CLASS}>
        {children}
      </div>
    );
  }
  return (
    <Link href={href} dir="rtl" className={`${CARD_CLASS} ${LINK_CLASS}`}>
      {children}
    </Link>
  );
}
