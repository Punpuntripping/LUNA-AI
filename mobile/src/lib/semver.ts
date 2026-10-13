/** Parse `1`, `1.2`, `1.2.3` (optional `v`, optional `-pre`/`+build` suffix)
 *  into [major, minor, patch]. Same grammar as backend/app/middleware/app_version.py.
 *  Returns null for anything else — callers treat null as "unknown, don't block". */
export function parseVersion(raw: string | null | undefined): [number, number, number] | null {
  if (!raw) return null;
  const m = /^v?(\d{1,6})(?:\.(\d{1,6}))?(?:\.(\d{1,6}))?(?:[-+][0-9A-Za-z.\-+]*)?$/.exec(raw.trim());
  if (!m) return null;
  return [Number(m[1]), Number(m[2] ?? 0), Number(m[3] ?? 0)];
}

/** True only when BOTH parse and `installed` is strictly below `required`. */
export function isVersionBelow(installed: string, required: string): boolean {
  const a = parseVersion(installed);
  const b = parseVersion(required);
  if (!a || !b) return false;
  for (let i = 0; i < 3; i++) {
    if (a[i] !== b[i]) return a[i] < b[i];
  }
  return false;
}
