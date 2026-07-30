// Canonical `cases.court_level` vocabulary for the frontend — the TS mirror of
// `agents/deep_search_v4/shared/court_levels.py`. Pure data + string helpers, no
// data fetching.
//
// The column has THREE values in prod (2026-07-24):
//   first_instance  23,932   appeal  6,474   supreme  125
//
// The Python module exists because that third value was silently collapsed in
// FOUR independent places, each written as a two-branch ternary — every
// supreme-court ruling was relabelled ابتدائي downstream. Import from here
// instead of re-deriving the mapping locally; a two-branch conditional over a
// three-value column is the bug, and it reappears every time someone re-writes
// it inline.
//
// DISPLAY RULE: the backend ships a rendered `court_level_label` on every
// judgment payload and OWNS the display vocabulary. `courtLevelLabel()` prefers
// that label and only falls back to the map below when it's missing — so a
// future fourth value shows the backend's wording, not a stale local guess.

import type { CourtLevel } from "@/types/library";

/** Canonical enum values, ordered by judicial seniority (ascending). */
export const COURT_LEVELS: readonly CourtLevel[] = [
  "first_instance",
  "appeal",
  "supreme",
] as const;

/** enum → Arabic display label. */
export const COURT_LEVEL_AR: Record<CourtLevel, string> = {
  first_instance: "ابتدائي",
  appeal: "استئناف",
  supreme: "المحكمة العليا",
};

/** True when `raw` is one of the three canonical values. */
export function isCourtLevel(raw: string | null | undefined): raw is CourtLevel {
  return !!raw && (COURT_LEVELS as readonly string[]).includes(raw);
}

/**
 * Arabic label for a court level. Prefers the backend's own `court_level_label`
 * when supplied; falls back to the local map; returns `""` for an unrecognised
 * value with no label — printing ابتدائي for an unknown level would assert
 * something false about a judgment, so display paths fail to silence, not to a
 * default.
 */
export function courtLevelLabel(
  raw: string | null | undefined,
  backendLabel?: string | null,
): string {
  const fromBackend = backendLabel?.trim();
  if (fromBackend) return fromBackend;
  return isCourtLevel(raw) ? COURT_LEVEL_AR[raw] : "";
}

/** One option in the hub's court-level filter row. `value: ""` = «الكل». */
export interface CourtLevelFilterOption {
  value: string;
  label: string;
}

/**
 * The hub filter row, in seniority order behind «الكل». Values are the raw enum
 * tokens the backend's `court_level` query param expects.
 */
export const COURT_LEVEL_FILTERS: readonly CourtLevelFilterOption[] = [
  { value: "", label: "الكل" },
  ...COURT_LEVELS.map((level) => ({
    value: level,
    label: COURT_LEVEL_AR[level],
  })),
] as const;
