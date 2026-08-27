/**
 * Where the anon conversion popup is allowed to fire
 * (`.claude/plans/anon_conversion_popup.md` §2).
 *
 * DOCUMENTS ONLY, under the public content wings. A hub is a directory
 * grid: it has no reading depth, so a scroll trigger there measures nothing but
 * a flick — and hubs already carry `HubCtaWall`, a full-page conversion surface
 * of their own for anyone who tries to reach page 2.
 *
 * Extending coverage later is adding a string to `WINGS`. No new mount point,
 * no new component: both shells already mount the popup for every route they
 * serve, and everything outside this list is filtered out here.
 */

/**
 * The public wings, named exactly as their first path segment.
 *
 * `compliance` joined on 2026-08-19 with the service guides: a guide is a long,
 * fully ungated read (our own rewrite of an entity's official user-guide, صور
 * included), which is precisely the shape a scroll-depth trigger measures
 * honestly — unlike a gated page, where the scroll ends at the wall.
 *
 * `forms` + `calculators` joined on 2026-08-25, closing the list: those were the
 * last two public ITEM surfaces `LibraryPageShell` served that the popup still
 * filtered out, so a نموذج or a حاسبة was the one place an anonymous reader
 * could read to the end and never be asked for an account. Every document route
 * under the shell is now a wing; what is left outside is hubs and the app.
 *
 * ⚠ GATED vs UNGATED IS NOT DECIDED HERE, and must not be. This list is about
 * SURFACES; whether a given page is walled is a per-page, per-reader fact the
 * server decides (a نموذج truncates, 65.8% of مواد do not). Gate 5 in
 * `AnonCtaPopup` already reads that fact off the DOM — any `[data-anon-cta]`
 * panel on screen (`FullContentGate`'s reveal, `HubCtaWall`, `BlogConversionCta`)
 * drops the fire, because a reader looking at «سجّل مجاناً لعرض النص كاملاً» does
 * not need a modal saying the same thing. So adding a wing that is SOMETIMES
 * gated — `forms` is exactly that — costs nothing: the popup fires on the
 * ungated instances and stands down on the walled ones, per page, at fire time.
 * Never re-derive that here with a slug list; it would go stale the first time
 * the exposure budget moves.
 */
const WINGS = [
  "regulations",
  "circulars",
  "judgments",
  "blog",
  "compliance",
  "forms",
  "calculators",
] as const;

type Wing = (typeof WINGS)[number];

/**
 * A DOCUMENT under one of the wings — not the hub, not a paginated hub.
 *
 *   /regulations                → false  (the bare hub)
 *   /regulations/page/2         → false  (a hub page)
 *   /regulations/labor-law      → true
 *   /regulations/labor-law/74   → true   (a مادة)
 *   /blog/<token>               → true
 *   /compliance/<slug>          → true   (a service guide)
 *   /forms/<slug>               → true   (walled ones stand down at fire time)
 *   /calculators/<slug>         → true   (a حاسبة — short page, dwell path)
 *   /library/..., /learn/...    → false  (not a wing — same shell, no popup)
 */
export function isEligibleDoc(pathname: string): boolean {
  const seg = pathname.split("/").filter(Boolean);
  if (seg.length < 2) return false; // the bare hub (or "/")
  if (!WINGS.includes(seg[0] as Wing)) return false;
  if (seg[1] === "page") return false; // /{wing}/page/{n} is a hub
  return true;
}
