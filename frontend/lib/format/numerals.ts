/**
 * ONE numeral convention for the whole product: **Western (Latin) digits**.
 *
 * Arabic-Indic digits (٠-٩) are never rendered by the app's own chrome — not in
 * prices, not in dates, not in counters, not in copy. The interface reads
 * «49.90 ريال» and «18 أغسطس 2026», never «٤٩٫٩٠» / «١٨ أغسطس ٢٠٢٦».
 *
 * Two things are deliberately OUT of scope:
 *
 *   1. **Agent output.** Whatever the model writes into a message, an artifact,
 *      or a workspace item is its own text and passes through untouched.
 *   2. **Corpus content.** Regulation/judgment bodies, article numbers, and
 *      footnote markers come from the source documents. We do not rewrite the
 *      law's own typography — but every parser that reads it must keep
 *      accepting Arabic-Indic input, which is what `toLatinDigits` is for.
 *
 * ── How to use ───────────────────────────────────────────────────────────────
 *
 * Never pass a bare `"ar-EG"` / `"ar-SA"` to `Intl` or `toLocaleString`: those
 * locales resolve to the `arab` numbering system and will emit Arabic-Indic
 * digits. Use the constants below — they are the same locales with the
 * numbering system pinned to `latn`, so month names stay Arabic («أغسطس») while
 * the digits stay Latin.
 *
 *   new Intl.DateTimeFormat(AR_DATE_LOCALE, { day: "numeric", month: "long" })
 *   value.toLocaleString(AR_NUM_LOCALE, { maximumFractionDigits: 2 })
 *
 * An ESLint `no-restricted-syntax` rule fails the build on a bare `"ar-EG"` /
 * `"ar-SA"` literal so this cannot silently regress.
 */

/**
 * Numbers: Arabic locale, Latin digits, Latin separators — `20,182.5`.
 *
 * `ar-EG` (not `ar-SA`) because its default grouping/decimal behaviour is the
 * one the pricing and usage surfaces were built against; `-u-nu-latn` swaps
 * `٢٠٬١٨٢٫٥` for `20,182.5` and changes nothing else.
 */
export const AR_NUM_LOCALE = "ar-EG-u-nu-latn";

/**
 * Dates: Arabic month names, Latin digits — `18 أغسطس 2026`.
 *
 * Pair it with `calendar: "gregory"` wherever the date is a subscription or
 * publication instant. `ar-SA` can resolve to the Umm al-Qura calendar
 * depending on the ICU build, and a Hijri rendering of a billing date reads as
 * a different date than the one the plan pages quote.
 */
export const AR_DATE_LOCALE = "ar-SA-u-nu-latn";

/* eslint-disable no-restricted-syntax -- this module IS the normaliser; the
   two tables below are the only Arabic-Indic literals the app is allowed to
   contain, and they exist to convert those digits away. */

/** U+0660–U+0669 — Arabic-Indic. */
const ARABIC_INDIC = "٠١٢٣٤٥٦٧٨٩";
/** U+06F0–U+06F9 — Extended Arabic-Indic (Persian/Urdu). */
const EXTENDED_ARABIC_INDIC = "۰۱۲۳۴۵۶۷۸۹";

/**
 * Any Arabic-Indic or Persian digit → its ASCII equivalent. Everything else —
 * letters, separators, the «مكرر» in an article number — passes through
 * verbatim, so this is safe on a whole string.
 *
 * Use it on text that arrives from OUTSIDE the app's own copy: corpus content,
 * a document title, an article number pulled from `articles_v2`. The app's own
 * strings are already Latin and do not need it.
 */
export function toLatinDigits(input: string): string {
  return input.replace(/[٠-٩۰-۹]/g, (d) => {
    const arabic = ARABIC_INDIC.indexOf(d);
    if (arabic >= 0) return String(arabic);
    return String(EXTENDED_ARABIC_INDIC.indexOf(d));
  });
}
