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
