"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import { Menu, X } from "lucide-react";
import { useAuthStore } from "@/stores/auth-store";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { HeaderAuthActions } from "@/components/site/HeaderAuthActions";
import { SITE_NAV } from "@/lib/nav/site-nav";
import { groupChildrenBySection, resolveNav } from "@/lib/nav/resolve-nav";

/**
 * Mobile navigation drawer for the global header, shown below `lg` where the
 * desktop `SiteNav` is hidden. Until this shipped the public header had NO
 * navigation on phones at all — logo + buttons only — which for a
 * search-traffic site (majority mobile) meant zero internal links on the
 * viewport most visitors arrive on.
 *
 * The panel is a fixed slide-in from the inline-start (right, in RTL). Every
 * slot is expanded — flat links inline, dropdown groups as labelled sections —
 * so the whole IA is one tap deep. The drawer only translates off-screen when
 * closed rather than unmounting.
 *
 * PORTALLED TO <body>, and it must stay that way. `SiteHeader` carries
 * `backdrop-blur`, and an element with `backdrop-filter` becomes the containing
 * block for its `position: fixed` descendants — rendered inline, this panel
 * resolved `inset-y-0` against the 64px bar and opened as a sliver with no
 * visible nav. The header's `z-30` also trapped it below page-level fixed UI
 * such as the `AskRayhanWidget` pill (`z-40`). The portal escapes both.
 *
 * The panel is therefore absent from the server HTML. That costs no
 * crawlability: `SiteNav` (CSS-hidden below `lg`, still server-rendered) and
 * `SiteFooter` carry the same `SITE_NAV` links on every page.
 */
export function SiteMobileNav() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isLoading = useAuthStore((s) => s.isLoading);
  const [open, setOpen] = useState(false);
  // `createPortal` needs a live DOM node, so the drawer mounts client-side only.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const authed = isAuthenticated && !isLoading;
  const slots = resolveNav(SITE_NAV, authed);

  // Lock body scroll and close on Escape while the drawer is open.
  useEffect(() => {
    if (!open) return;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prevOverflow;
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Portalled out of the header, so `lg:hidden` must sit on these nodes
  // themselves — they no longer inherit it from the trigger's wrapper.
  const drawer = (
    <>
      {/* Overlay */}
      <div
        aria-hidden="true"
        onClick={() => setOpen(false)}
        className={`fixed inset-0 z-40 bg-black/40 transition-opacity lg:hidden ${
          open ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
      />

      {/* Panel — slides in from the inline-start (right in RTL) */}
      <div
        role="dialog"
        aria-modal="true"
        aria-label="التنقّل"
        dir="rtl"
        className={`fixed inset-y-0 right-0 z-50 flex w-80 max-w-[85vw] flex-col border-l border-border bg-background shadow-xl transition-transform duration-200 lg:hidden ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <span className="flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-xs font-bold text-primary-foreground">
              ريحان
            </span>
            <span className="text-sm font-bold text-foreground">ريحان</span>
          </span>
          <Button
            variant="ghost"
            size="icon"
            aria-label="إغلاق"
            onClick={() => setOpen(false)}
          >
            <X className="h-5 w-5" />
          </Button>
        </div>

        <nav
          aria-label="التنقّل الرئيسي"
          className="flex-1 overflow-y-auto px-2 py-3"
        >
          {slots.map((slot) =>
            slot.kind === "link" ? (
              <Link
                key={slot.label}
                href={slot.href}
                onClick={() => setOpen(false)}
                className="block rounded-lg px-3 py-2.5 text-base font-semibold text-foreground transition-colors hover:bg-muted"
              >
                {slot.label}
              </Link>
            ) : (
              <div key={slot.label} className="py-1.5">
                <p className="px-3 pb-1 text-base font-semibold text-foreground">
                  {slot.href ? (
                    <Link
                      href={slot.href}
                      onClick={() => setOpen(false)}
                      className="transition-colors hover:text-primary"
                    >
                      {slot.label}
                    </Link>
                  ) : (
                    slot.label
                  )}
                </p>
                {groupChildrenBySection(slot.children).map((bucket, bi) => (
                  <div key={bucket.section ?? `bucket-${bi}`} className="mt-0.5">
                    {bucket.section && (
                      <p className="px-3 pb-0.5 pt-1.5 text-[0.7rem] font-semibold uppercase tracking-wide text-muted-foreground">
                        {bucket.section}
                      </p>
                    )}
                    {bucket.items.map((child) => (
                      <Link
                        key={child.href}
                        href={child.href}
                        onClick={() => setOpen(false)}
                        className="block rounded-lg px-3 py-2 pr-5 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                      >
                        {child.label}
                      </Link>
                    ))}
                  </div>
                ))}
              </div>
            ),
          )}
        </nav>

        <div className="flex items-center justify-between gap-2 border-t border-border px-4 py-3">
          <div onClick={() => setOpen(false)} className="flex items-center gap-2">
            <HeaderAuthActions />
          </div>
          <ThemeToggle />
        </div>
      </div>
    </>
  );

  return (
    <div className="lg:hidden">
      <Button
        variant="ghost"
        size="icon"
        aria-label="القائمة"
        aria-expanded={open}
        onClick={() => setOpen(true)}
      >
        <Menu className="h-5 w-5" />
      </Button>

      {mounted ? createPortal(drawer, document.body) : null}
    </div>
  );
}
