/** A `#RRGGBB` theme token at an opacity — the web's Tailwind `/opacity`
 *  modifier (`bg-primary/5` → `withAlpha(colours.primary, 0.05)`). Anything
 *  that is not a 6-digit hex (an `rgba(…)` token) is returned unchanged. */
export function withAlpha(hex: string, alpha: number): string {
  const m = /^#([0-9a-f]{6})$/i.exec(hex);
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}
