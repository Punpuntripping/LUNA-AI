import { AR_DATE_LOCALE, AR_NUM_LOCALE, toLatinDigits } from "@contracts";

// Every number/date the app formats goes through here: Arabic words, Latin
// digits, Gregorian calendar. `toLatinDigits` is a belt-and-braces pass in case
// a device's Hermes/ICU build ignores the `-u-nu-latn` extension.

const dateFmt = new Intl.DateTimeFormat(AR_DATE_LOCALE, {
  day: "numeric",
  month: "long",
  year: "numeric",
  calendar: "gregory",
});

const numFmt = new Intl.NumberFormat(AR_NUM_LOCALE, { maximumFractionDigits: 2 });

/** «18 أغسطس 2026». Invalid input → "". */
export function formatDate(value: string | number | Date): string {
  const d = value instanceof Date ? value : new Date(value);
  return Number.isNaN(d.getTime()) ? "" : toLatinDigits(dateFmt.format(d));
}

/** «20,182.5». */
export function formatNumber(value: number): string {
  return toLatinDigits(numFmt.format(value));
}

/** Arabic count with the dual/plural forms (`2` → «يومين», `5` → «5 أيام»,
 *  `11` → «11 يوم»). Digits stay Latin (plain template literal). */
export interface ArabicPlural {
  one: string;
  two: string;
  few: string;
  many: string;
}

export function formatCount(n: number, forms: ArabicPlural): string {
  if (n === 1) return forms.one;
  if (n === 2) return forms.two;
  if (n >= 3 && n <= 10) return `${n} ${forms.few}`;
  return `${n} ${forms.many}`;
}

const MINUTES: ArabicPlural = { one: "دقيقة", two: "دقيقتين", few: "دقائق", many: "دقيقة" };
const HOURS: ArabicPlural = { one: "ساعة", two: "ساعتين", few: "ساعات", many: "ساعة" };
const DAYS: ArabicPlural = { one: "يوم", two: "يومين", few: "أيام", many: "يوم" };
const WEEKS: ArabicPlural = { one: "أسبوع", two: "أسبوعين", few: "أسابيع", many: "أسبوع" };

/** «الآن» / «منذ 5 دقائق» / «أمس» / «منذ أسبوعين» / «18 أغسطس 2026» — port of
 *  the web's `getRelativeTimeAr` (frontend/lib/utils.ts). Invalid input → "". */
export function formatRelativeTime(value: string | number | Date, nowMs: number = Date.now()): string {
  const d = value instanceof Date ? value : new Date(value);
  const t = d.getTime();
  if (Number.isNaN(t)) return "";
  const diffMs = Math.max(0, nowMs - t);
  const mins = Math.floor(diffMs / 60_000);
  const hours = Math.floor(diffMs / 3_600_000);
  const days = Math.floor(diffMs / 86_400_000);
  if (mins < 1) return "الآن";
  if (mins < 60) return `منذ ${formatCount(mins, MINUTES)}`;
  if (hours < 24) return `منذ ${formatCount(hours, HOURS)}`;
  if (days < 2) return "أمس";
  if (days < 7) return `منذ ${formatCount(days, DAYS)}`;
  if (days < 30) return `منذ ${formatCount(Math.floor(days / 7), WEEKS)}`;
  return formatDate(d);
}

/** Stopwatch «m:ss» (deep-search tracker). */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}
