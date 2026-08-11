/**
 * Human-readable countdown to a quota window's recovery instant.
 *
 * Extracted from `QuotaBanner` so the banner and the upgrade dialog cannot
 * describe the same `resets_at` differently — they render simultaneously (the
 * banner stays behind the modal), so a drift between them is visible on screen.
 *
 * `now` is passed in rather than read here: both callers already tick a
 * one-minute interval to keep the text fresh, and taking the clock as an
 * argument keeps this pure and testable.
 */
const HOUR_MS = 60 * 60 * 1000;
const MIN_MS = 60 * 1000;

export function formatReset(resetsAt: string, now: number): string {
  const target = Date.parse(resetsAt);
  if (Number.isNaN(target)) return "";
  const delta = target - now;
  if (delta <= 0) return "خلال لحظات";
  const hours = Math.floor(delta / HOUR_MS);
  const minutes = Math.floor((delta % HOUR_MS) / MIN_MS);
  if (hours >= 1) {
    return minutes > 0
      ? `خلال ${hours} ساعة و${minutes} دقيقة`
      : `خلال ${hours} ساعة`;
  }
  if (minutes >= 1) return `خلال ${minutes} دقيقة`;
  return "خلال أقل من دقيقة";
}
