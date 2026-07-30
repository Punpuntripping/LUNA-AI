// Turns the declarative `SITE_NAV` config into the exact slots the header should
// render for a given auth state. This is where the two rules live:
//
//   1. Auth filter — a group flagged `hideWhenAuthed` disappears once signed in.
//   2. Auto-promote by enabled-child count:
//        • 2+ enabled children → a dropdown (menu). Its header links to the hub
//          (`group.href`) when one exists.
//        • 0 or 1 enabled children → a flat link, to the hub if the group has one,
//          otherwise to the single enabled child.
//        • nothing to link to (no hub, no enabled child) → the slot is dropped.
//
// Because it is a pure function of (config, isAuthenticated), the server-rendered
// HTML always contains the anonymous nav — every dropdown link included — so the
// links are crawlable. Flipping an `enabled` flag in `site-nav.ts` is all it takes
// to promote a slot from hidden → flat link → dropdown.

import type { NavChild, NavGroup } from "./site-nav";

export type ResolvedSlot =
  | { kind: "link"; label: string; href: string }
  | {
      kind: "menu";
      label: string;
      href?: string;
      hubLabel?: string;
      hubDescription?: string;
      children: NavChild[];
    };

export function resolveNav(
  groups: NavGroup[],
  isAuthenticated: boolean,
): ResolvedSlot[] {
  const slots: ResolvedSlot[] = [];

  for (const group of groups) {
    if (group.hideWhenAuthed && isAuthenticated) continue;

    const enabled = (group.children ?? []).filter((child) => child.enabled);

    if (enabled.length >= 2) {
      slots.push({
        kind: "menu",
        label: group.label,
        href: group.href,
        hubLabel: group.hubLabel,
        hubDescription: group.hubDescription,
        children: enabled,
      });
      continue;
    }

    // 0 or 1 enabled children → flat link. Prefer the hub, fall back to the lone
    // child. With neither, there is nothing to point at — drop the slot.
    const href = group.href ?? enabled[0]?.href;
    if (!href) continue;
    slots.push({ kind: "link", label: group.label, href });
  }

  return slots;
}

/**
 * Split a dropdown's children into ordered `{ section, items }` buckets, one per
 * contiguous run of a shared `section` heading. Children with no `section` fall
 * into a leading unlabeled bucket. Used by both the desktop dropdown and the
 * mobile drawer so headings + dividers stay consistent.
 */
export function groupChildrenBySection(
  children: NavChild[],
): { section?: string; items: NavChild[] }[] {
  const buckets: { section?: string; items: NavChild[] }[] = [];

  for (const child of children) {
    const last = buckets[buckets.length - 1];
    if (last && last.section === child.section) {
      last.items.push(child);
    } else {
      buckets.push({ section: child.section, items: [child] });
    }
  }

  return buckets;
}
