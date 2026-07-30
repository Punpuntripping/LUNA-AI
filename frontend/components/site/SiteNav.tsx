"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ChevronDown } from "lucide-react";
import { useAuthStore } from "@/stores/auth-store";
import { SITE_NAV } from "@/lib/nav/site-nav";
import {
  groupChildrenBySection,
  resolveNav,
  type ResolvedSlot,
} from "@/lib/nav/resolve-nav";
import { cn } from "@/lib/utils";

/**
 * Desktop (`lg+`) primary navigation for the global site header.
 *
 * Auth-aware in principle — `resolveNav` drops any group flagged
 * `hideWhenAuthed` — but NO slot currently carries the flag, so signed-in and
 * anonymous visitors get the same bar. «عن ريحان» used to be dropped and was
 * reinstated: the drop only ran after the session probe resolved, so a signed-in
 * user watched the slot paint and then vanish.
 *
 * If a slot is ever flagged again, that flash is the thing to solve first. While
 * the probe is in flight (`isLoading`) this renders the full anonymous nav, which
 * keeps the server HTML and the first client render identical (no hydration
 * mismatch, and the dropdown links stay in the crawled markup) — but it also
 * means a hidden-when-authed slot is necessarily painted before it is removed.
 * `HeaderAuthActions` avoids the equivalent flash by reserving an invisible
 * same-footprint placeholder during `isLoading`; a nav slot needs the same trick,
 * or a pre-paint auth marker on `<html>` so CSS can hide it before first paint.
 *
 * Dropdown links are rendered unconditionally into the DOM — only their
 * visibility is toggled — so they sit in the server-rendered HTML and stay
 * crawlable (the whole reason the library dropdown is the SEO crawl skeleton).
 * Radix's portalled menus would inject them on open instead, which is why this
 * is hand-rolled.
 */
export function SiteNav() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isLoading = useAuthStore((s) => s.isLoading);
  const [openIndex, setOpenIndex] = useState<number | null>(null);

  const authed = isAuthenticated && !isLoading;
  // Reversed: the bar sits at the END of the header (beside the auth buttons), and
  // reads left-to-right — عن ريحان closest to «تسجيل الدخول», الباقات والأسعار
  // closest to the brand. In an RTL row, DOM order maps right-to-left, so the
  // logical order in `SITE_NAV` has to be flipped to land that way. Reversed HERE
  // and not in the data, because the mobile drawer is a vertical list that should
  // still read عن ريحان first, top-down.
  const slots = resolveNav(SITE_NAV, authed).reverse();

  return (
    <nav
      aria-label="التنقّل الرئيسي"
      className="hidden items-center gap-0.5 lg:flex"
    >
      {slots.map((slot, i) =>
        slot.kind === "link" ? (
          <Link
            key={slot.label}
            href={slot.href}
            className="rounded-md px-3 py-2 text-sm font-medium text-foreground transition-colors hover:bg-muted"
          >
            {slot.label}
          </Link>
        ) : (
          <MenuSlot
            key={slot.label}
            slot={slot}
            open={openIndex === i}
            onOpen={() => setOpenIndex(i)}
            onClose={() => setOpenIndex((cur) => (cur === i ? null : cur))}
          />
        ),
      )}
    </nav>
  );
}

function MenuSlot({
  slot,
  open,
  onOpen,
  onClose,
}: {
  slot: Extract<ResolvedSlot, { kind: "menu" }>;
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
}) {
  const buckets = groupChildrenBySection(slot.children);

  // Closing on a bare `mouseleave` is too trigger-happy: the pointer crosses a
  // sliver of header on its way from the trigger down into the panel, and any
  // overshoot past a panel edge counts as a leave. Both read to the user as the
  // menu randomly vanishing. So leaving only ARMS a close, and re-entering
  // anywhere in the group disarms it.
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Hover already opened the panel, so a click on the trigger would toggle it
  // shut under the user's cursor. Only let a click close it when the menu was
  // opened without a pointer (keyboard focus), where toggling is the expectation.
  const pointerInside = useRef(false);

  const cancelClose = () => {
    if (closeTimer.current) {
      clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  };

  const scheduleClose = () => {
    cancelClose();
    closeTimer.current = setTimeout(onClose, 180);
  };

  useEffect(() => cancelClose, []);

  const openNow = () => {
    cancelClose();
    onOpen();
  };

  return (
    <div
      className="relative"
      onMouseEnter={() => {
        pointerInside.current = true;
        openNow();
      }}
      onMouseLeave={() => {
        pointerInside.current = false;
        scheduleClose();
      }}
      onFocus={openNow}
      onBlur={(e) => {
        // Close only when focus leaves the whole group, not when it moves
        // between the trigger and a menu link.
        if (!e.currentTarget.contains(e.relatedTarget as Node | null))
          onClose();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
    >
      <button
        type="button"
        aria-haspopup="true"
        aria-expanded={open}
        onClick={() => (open && !pointerInside.current ? onClose() : openNow())}
        className={cn(
          "inline-flex items-center gap-1 rounded-md px-3 py-2 text-sm font-medium text-foreground transition-colors",
          open ? "bg-muted" : "hover:bg-muted",
        )}
      >
        {slot.label}
        <ChevronDown
          className={cn(
            "h-3.5 w-3.5 transition-transform",
            open && "rotate-180",
          )}
        />
      </button>

      {/* Panel — always in the DOM (crawlable); visibility toggled by state.
          The offset below the trigger is `pt-1` on this positioner rather than a
          margin on the card, so the gap is INSIDE the hover target: the pointer
          travels from trigger to panel without ever landing on bare header.
          `invisible` also kills pointer events, so the closed panel can't
          swallow clicks meant for the page. */}
      {/* Anchored `left-0`, not `right-0`: the bar now sits at the END of the
          header, so a 288px panel hung off a trigger's RIGHT edge would run off
          the left of the viewport for the outermost slot. Aligning the panel's
          left edge to the trigger's lets it open inward, where the space is. */}
      <div
        className={cn(
          "absolute top-full left-0 z-40 pt-1 transition",
          open
            ? "visible translate-y-0 opacity-100"
            : "invisible -translate-y-1 opacity-0",
        )}
      >
        <div className="w-72 rounded-xl border border-border bg-popover p-2 shadow-lg">
          {/* Hub link — rendered as an ordinary menu item so the panel reads as one
            uniform list. */}
          {slot.href && (
            <Link
              href={slot.href}
              className="block rounded-lg px-3 py-2 transition-colors hover:bg-muted"
            >
              <span className="block text-sm font-medium text-foreground">
                {slot.hubLabel ?? `كل ${slot.label}`}
              </span>
              {slot.hubDescription && (
                <span className="mt-0.5 block text-xs leading-snug text-muted-foreground">
                  {slot.hubDescription}
                </span>
              )}
            </Link>
          )}

          {buckets.map((bucket, bi) => (
            <div key={bucket.section ?? `bucket-${bi}`}>
              {/* Divide between buckets, and after the hub link only when the first
                bucket is headed — an unheaded run should flow straight on from it. */}
              {(bi > 0 || (slot.href && bucket.section)) && (
                <div className="my-1 border-t border-border/60" />
              )}
              {bucket.section && (
                <p className="px-3 pb-1 pt-1.5 text-[0.7rem] font-semibold uppercase tracking-wide text-muted-foreground">
                  {bucket.section}
                </p>
              )}
              {bucket.items.map((child) => (
                <Link
                  key={child.href}
                  href={child.href}
                  className="block rounded-lg px-3 py-2 transition-colors hover:bg-muted"
                >
                  <span className="block text-sm font-medium text-foreground">
                    {child.label}
                  </span>
                  {child.description && (
                    <span className="mt-0.5 block text-xs leading-snug text-muted-foreground">
                      {child.description}
                    </span>
                  )}
                </Link>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
