/**
 * Anon conversion popup — the tunable numbers, in ONE file
 * (`.claude/plans/anon_conversion_popup.md` §3, revised 2026-08-02).
 *
 * They live together because they are meant to be RE-TUNED from observed
 * behaviour after the surface ships: how deep a reader gets before the pitch is
 * earned, how long a one-screen مادة has to hold attention, and how loudly the
 * session may repeat itself. Nothing here is a constant of nature — every value
 * is a product judgement, and changing one must never mean hunting through a
 * component.
 *
 * Deliberately NOT here: the "is this page scrollable at all" factor (1.2×
 * viewport). That is a DOM measurement owned by `AnonCtaPopup`, not a cadence
 * knob, so it stays beside the code that measures it.
 */

/**
 * The scroll depths that earn a popup, as a share of the document's scrollable
 * distance. Measured as `(scrollY + innerHeight) / scrollHeight`, so the
 * viewport itself counts.
 *
 * TWO thresholds, each firing AT MOST ONCE per document (2026-08-02): the same
 * «{n+1} period» idea the session ladder uses, applied WITHIN a long document.
 * A reader who works through a whole نظام meets the pitch about a third of the
 * way in and again near the end; a reader who stops halfway meets it once.
 *
 * Must stay ASCENDING — `AnonCtaPopup` fires the lowest unfired threshold the
 * reader has crossed, so an out-of-order list would fire them out of order.
 */
export const ENGAGE_RATIOS = [0.35, 0.8] as const;

/**
 * Floor on the scroll path, measured from the moment the document mounted.
 * Without it, a fling-scroll to the bottom of a long نظام — the most common
 * gesture on a phone — fires the popup in under two seconds, which reads as an
 * ambush and converts nobody.
 */
export const MIN_DWELL_MS = 8_000;

/**
 * Minimum quiet stretch between two impressions on the SAME document.
 *
 * One fling from the top to the bottom crosses BOTH thresholds in a single
 * gesture; without this gap the reader would dismiss one popup straight into
 * another. Measured from the moment the previous popup LEFT the screen, not
 * from when it appeared — see `AnonCtaPopup`.
 */
export const MIN_GAP_MS = 5_000;

/**
 * The substitute signal for a page that does not meaningfully scroll (a مادة
 * shorter than the viewport, whose scroll progress is 1.0 on load). Twenty
 * seconds on a one-screen page is a reader who finished it and is thinking.
 *
 * A page with no scrollable distance has no 35% or 80% to cross, so this path
 * yields exactly ONE impression for that document — there is no second timer.
 */
export const SHORT_PAGE_DWELL = 20_000;

/**
 * Hard cap per session, counted in ROUNDS — documents that showed the popup —
 * not in raw impressions.
 *
 * ⚠ This is why the constant was renamed. Counting raw impressions with two
 * thresholds per document would exhaust the session midway through document 2
 * and quietly undo the cadence the `{n+1}` ladder is built on. Three rounds
 * therefore means three DOCUMENTS, i.e. up to six impressions.
 */
export const MAX_ROUNDS_PER_SESSION = 3;

/**
 * Impressions allowed on one document — one per threshold, by construction.
 * Derived rather than typed out so the two can never drift apart.
 */
export const MAX_SHOTS_PER_DOC = ENGAGE_RATIOS.length;

/**
 * How many further eligible DOCUMENTS stay silent after a round — the `{n+1}`
 * period (§4). Decremented per newly-opened document, never per render, and
 * armed ONCE per round (on that document's FIRST impression) so the second
 * threshold of the same document is not blocked by the cooldown it just armed.
 */
export const QUIET_DOCS = 2;
