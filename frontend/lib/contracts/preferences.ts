// `/api/v1/preferences` — the user's JSONB preference blob. Shared by the web
// and the mobile app. The app only reads/writes `detail_level` (مستوى التفصيل).
//
// Additive: the web still declares `DetailLevel` / `UserPreferencesData` in
// `@/types` and `DETAIL_LEVEL_OPTIONS` in `components/Settings/DetailLevelToggle`.

/** Deep-search verbosity. */
export type DetailLevel = "low" | "medium" | "high";

export const DEFAULT_DETAIL_LEVEL: DetailLevel = "medium";

export interface DetailLevelOption {
  value: DetailLevel;
  label: string;
}

/** The one list every detail-level surface renders (web DETAIL_LEVEL_OPTIONS). */
export const DETAIL_LEVEL_OPTIONS: readonly DetailLevelOption[] = [
  { value: "low", label: "مختصر" },
  { value: "medium", label: "متوسط" },
  { value: "high", label: "مفصّل" },
];

/** Unknown / absent → `medium` (web preferences-store coerceDetailLevel). */
export function coerceDetailLevel(value: unknown): DetailLevel {
  return DETAIL_LEVEL_OPTIONS.some((o) => o.value === value) ? (value as DetailLevel) : DEFAULT_DETAIL_LEVEL;
}

export function detailLevelLabel(level: DetailLevel): string {
  return DETAIL_LEVEL_OPTIONS.find((o) => o.value === level)?.label ?? "";
}

/**
 * The stored blob. The server merges a PATCH SHALLOWLY into it, so a PATCH
 * carries only the keys it changes (`{ detail_level }`), never a nested object.
 * Other keys (tour flags, privacy_masking, edu_*) belong to the web.
 */
export interface PreferencesBlob {
  detail_level?: DetailLevel;
  [key: string]: unknown;
}

/** `GET` / `PATCH /api/v1/preferences` response. */
export interface PreferencesResponse {
  user_id: string;
  preferences: PreferencesBlob;
}

/** `PATCH /api/v1/preferences` body. */
export interface UpdatePreferencesBody {
  preferences: PreferencesBlob;
}
