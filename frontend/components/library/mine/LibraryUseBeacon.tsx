"use client";

import { useEffect, useRef } from "react";
import { useAuthStore } from "@/stores/auth-store";
import { useRecordLibraryUse } from "@/hooks/use-my-library";
import type { MyLibraryContentType } from "@/lib/api";

interface LibraryUseBeaconProps {
  contentType: MyLibraryContentType;
  /** The public page slug — resolved to the canonical id server-side. */
  slug: string;
  /** Required for a مادة: its نظام's slug («المادة-74» repeats across statutes). */
  parentSlug?: string;
  /**
   * The item's gate, straight off the document payload.
   *
   * ⚠ THE BEACON FIRES ONLY WHEN THIS IS `"open"`. Everything in مكتبتي is
   * ungated (user decision 2026-07-28), so shelving a gated item the reader has
   * not unlocked would put an unreadable row on the shelf — and shelving it by
   * CHARGING for a mere page view would burn a free user's whole allowance on
   * ten skimmed summaries (§5.1). A gated page view therefore does nothing at
   * all; the item enters مكتبتي only via reveal, «عرض المصدر» or «حفظ», each of
   * which unlocks it.
   *
   * Defaults to `"gated"` — the SAFE direction. A caller that forgets to pass
   * the gate shelves nothing, rather than silently shelving a locked row.
   */
  gate?: "open" | "gated";
}

/**
 * The implicit-save half of §5B.2: opening an item shelves it.
 *
 * Mount this on a public document page. It is a NULL renderer that fires one
 * authed `POST /library/mine/use` per mount for signed-in visitors.
 *
 * ⚠ OPEN ITEMS ONLY. Everything in مكتبتي is ungated (user decision 2026-07-28),
 * so the shelf may only ever receive something the reader can actually read:
 *
 *   viewing a GATED page  → nothing. Not shelved, not charged. This is what
 *                           keeps the free summary layer free (§5.1) — ten
 *                           skimmed judgment summaries must not cost ten unlocks.
 *   viewing an OPEN item  → shelved here, free. Services are never gated, which
 *                           is exactly why the الخدمات tab fills at all.
 *   reveal / «عرض المصدر» / «حفظ» → unlock AND shelf, recorded server-side by
 *                           whichever endpoint performed the unlock.
 *
 * The beacon and those endpoints cover DISJOINT sets, so nothing double-counts.
 *
 * WHY A CLIENT COMPONENT (§5B.3 ISR trap): the counter upsert must never run
 * inside a cached server render. A server-side write would either poison the
 * shared ISR cache or — worse — be skipped on every cache hit and undercount
 * silently. That is the blog's exact view-count-on-read mistake. Riding the
 * authed client call is the fix, and it also means anonymous visitors (who have
 * no shelf) write nothing.
 */
export function LibraryUseBeacon({
  contentType,
  slug,
  parentSlug,
  gate = "gated",
}: LibraryUseBeaconProps) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isLoading = useAuthStore((s) => s.isLoading);
  const recordUse = useRecordLibraryUse();
  const firedFor = useRef<string | null>(null);

  useEffect(() => {
    if (isLoading || !isAuthenticated) return;
    // Gated => the shelf must not receive it (see the docstring).
    if (gate !== "open") return;
    if (!slug) return;

    // One use per mounted item — also neutralises the StrictMode double-effect
    // in dev, which would otherwise inflate `use_count` by 2 on every open.
    const key = `${contentType}:${parentSlug ?? ""}:${slug}`;
    if (firedFor.current === key) return;
    firedFor.current = key;

    recordUse.mutate({
      content_type: contentType,
      slug,
      parent_slug: parentSlug,
    });
  }, [isLoading, isAuthenticated, gate, contentType, slug, parentSlug, recordUse]);

  return null;
}
