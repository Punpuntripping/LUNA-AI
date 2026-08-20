// EVERY Arabic string the shared search surfaces render lives HERE, in ONE file
// — the same discipline `components/library/mine/copy.ts` and
// `lib/library/gate-copy.ts` enforce for their surfaces. No component under
// `components/search/` (nor `HubSearchPanel`) may hardcode a single word.
//
// Spec: `.claude/plans/bm25_navigation_search.md` §6.1.

import { formatCount } from "@/lib/library/sectors";

// ------------------------------------------------------------------
// The surfaces
// ------------------------------------------------------------------

/**
 * The public wings that carry a search box today.
 *
 * `/forms` and `/calculators` are deliberately ABSENT — D7 puts them out of
 * scope (not indexed, not wired), and a placeholder here would be a promise the
 * backend cannot keep.
 *
 * ⚠ `library` IS NOT A MEMBER, AND WAVE E DID NOT MAKE IT ONE. An earlier draft
 * of this comment promised it would. It cannot be: `useHubSearch` interpolates
 * this union straight into `/api/v1/public/library/{section}`, and there is no
 * `/public/library/library` wing — the member would compile and then 404. The
 * cross-wing page calls a DIFFERENT endpoint (`/api/v1/search`) over a different
 * vocabulary (`SearchCorpus` in `lib/search/corpora.ts`) and takes its strings
 * from `SEARCH_LIBRARY_COPY` below. Same reasoning that keeps
 * `PrivateSearchSurface` separate.
 */
export type SearchSurface = "regulations" | "judgments" | "circulars";

// ------------------------------------------------------------------
// The 3-character floor
// ------------------------------------------------------------------

/**
 * The server's own floor for a free-text hub filter
 * (`public_library._search_text`: absent, or >= 3 characters — anything shorter
 * is a 400 in Arabic). The live search box never SENDS a shorter query, so the
 * refusal is turned into the inline hint below instead of a round trip.
 *
 * ⚠ THE NUMBER AND `minLengthHint` MUST AGREE. They live one line apart for
 * exactly that reason: the hint spells the digit out in Arabic-Indic («3»), so
 * a change here that skips the sentence produces a box that lies about itself.
 */
export const SEARCH_MIN_LENGTH = 3;

/** Milliseconds of quiet before a keystroke becomes a request — the `/chats`
 *  value (`ChatsPage`), so every live search box in the app feels identical. */
export const SEARCH_DEBOUNCE_MS = 250;

// ------------------------------------------------------------------
// Shared strings
// ------------------------------------------------------------------

export const SEARCH_COPY = {
  /** Under the box while the typed value is 1–2 characters (see the floor). */
  minLengthHint: "اكتب 3 أحرف على الأقل للبحث",
  /** The clear («×») button's accessible name. */
  clear: "مسح البحث",

  /** In-flight, announced politely to assistive tech. */
  searching: "جارٍ البحث…",

  /** Empty result set — shared by every surface (§6.1). */
  emptyTitle: "لا توجد نتائج",
  emptyHint: "جرّب كلمات بحث أخرى",

  /**
   * A transport fault, a dead session, a 5xx — all indistinguishable to a
   * reader and all meaning «try again». Mirrors `hubWallCopy.error`'s posture:
   * neutral, no blame, one action.
   */
  errorTitle: "تعذّر تنفيذ البحث",
  retry: "إعادة المحاولة",

  /**
   * The reach meter (navigation hardening 2.2) refused this search. A REAL
   * answer, not a broken page — so it never collapses into `errorTitle`. Same
   * wording family as `rateLimitedCopy` in `lib/library/gate-copy.ts`, and it
   * likewise carries no retry button: at an hour of `Retry-After` a button
   * could only fail again.
   */
  rateLimitedTitle: "طلبات كثيرة في وقت قصير",
  rateLimitedBody: "انتظر قليلاً ثم أعد البحث.",

  /**
   * Shown when the result set spills past the first page. Says «narrow it»
   * rather than a count, because the hub envelope carries `total_pages`, not a
   * total — and an invented number is worse than none.
   */
  moreResults: "تُعرض أفضل النتائج — أضف كلمة أخرى لتضييق البحث.",
} as const;

/** «نتائج البحث عن «عقد العمل»» — the results-region heading. */
export function searchResultsHeading(query: string): string {
  return `نتائج البحث عن «${query}»`;
}

// ------------------------------------------------------------------
// The cross-wing `/library` page (Wave E)
// ------------------------------------------------------------------

/**
 * `/library` is the ONE surface that calls `GET /api/v1/search`.
 *
 * It is NOT the only one that can name a count any more: Wave B added
 * `total_count` + `total_count_is_exact` to the hub envelope too, so the four
 * wings print a number via `searchResultCount()` exactly as this page does.
 * `SEARCH_COPY.moreResults` survives as the «add another word» nudge under a
 * spilled result set — it no longer stands in for a missing count.
 *
 * There is no «البحث للأعضاء فقط» line here on purpose. An anonymous visitor's
 * box is live-looking and their click opens `SearchCtaModal` (D9); labelling the
 * box «members only» beforehand would pre-empt the pitch with a refusal, on the
 * softest surface in the product.
 */
export const SEARCH_LIBRARY_COPY = {
  placeholder: "ابحث في المكتبة القانونية كاملة…",
  ariaLabel: "ابحث في الأنظمة والأحكام والتعاميم في وقت واحد",
  /**
   * One line under the box. It describes what the BOX does, not what the
   * library contains — the H1's own paragraph directly above already lists the
   * three corpora, and repeating them here would be the same sentence twice on
   * one screen.
   */
  lead: "اكتب اسم النظام أو موضوع المسألة — يبحث ريحان في الأقسام الثلاثة دفعةً واحدة ويرتّب النتائج حسب المطابقة.",
  /** The `<fieldset>`-style accessible name for the wing chip row. */
  scopeLabel: "نطاق البحث",
  /** The «no wing filter» chip — the state that searches all three. */
  scopeAll: "الكل",
} as const;

/**
 * The result count beside the heading — and the ONE place `total_is_exact` is
 * allowed to change the wording.
 *
 * `bm25_search` is two-stage: it cuts to `p_candidates` (500) by `ts_rank_cd`
 * before scoring, so `total` counts THAT set. When the cut bound the answer,
 * `total` is a ceiling and printing «500 نتيجة» would be a lie — «أفضل 500
 * نتيجة» is the same number told honestly. The backend refuses to dress the
 * ceiling up as a total (`SearchResponse`'s own docstring says so); this is the
 * frontend keeping that bargain.
 *
 * ⚠ THE PLURAL IS NOT COSMETIC. Arabic counts four ways, and «1 نتائج» reads
 * like a bug to every reader of this app. The approximate form takes the
 * 11-and-up shape (تمييز مفرد) because the ceiling is always in the hundreds.
 *
 * Digits are Latin via `formatCount` — pinned there to `en-US` because a
 * runtime-resolved locale differs between the Node render and the browser
 * hydration, and the rest of the library UI does not use Arabic-Indic digits.
 */
export function searchResultCount(total: number, isExact: boolean): string {
  const n = Math.max(0, Math.trunc(total));
  if (!isExact) return `أفضل ${formatCount(n)} نتيجة`;
  if (n === 1) return "نتيجة واحدة";
  if (n === 2) return "نتيجتان";
  if (n <= 10) return `${formatCount(n)} نتائج`;
  return `${formatCount(n)} نتيجة`;
}

// ------------------------------------------------------------------
// Per-surface strings
// ------------------------------------------------------------------

interface SurfaceCopy {
  /** Inside the box. */
  placeholder: string;
  /** The box's accessible name — the placeholder is not a label. */
  ariaLabel: string;
}

/**
 * `judgments` reproduces the wording its own inline box shipped with, so the
 * `JudgmentsFilterBar` refactor is a behaviour change and not a copy change.
 */
export const SEARCH_SURFACE_COPY = {
  regulations: {
    placeholder: "ابحث في الأنظمة واللوائح…",
    ariaLabel: "ابحث في الأنظمة واللوائح السعودية",
  },
  judgments: {
    placeholder: "ابحث في الأحكام…",
    ariaLabel: "ابحث في الأحكام القضائية",
  },
  circulars: {
    placeholder: "ابحث في التعاميم…",
    ariaLabel: "ابحث في التعاميم التنظيمية",
  },
} satisfies Record<SearchSurface, SurfaceCopy>;

// ------------------------------------------------------------------
// The private surfaces (Wave D)
// ------------------------------------------------------------------

/**
 * The surfaces that are ALREADY behind auth when their box renders — مدوناتي،
 * قوالبي، مكتبتي and the `/chats` index.
 *
 * Deliberately a SEPARATE union from `SearchSurface` rather than four more
 * members of it. `SearchSurface` is not a label set: `useHubSearch` interpolates
 * it straight into `/api/v1/public/library/{section}`, so a member that has no
 * public wing behind it would compile and then 404. These four call their own
 * private list endpoints (`/blogs/mine`, `/templates`, `/library/mine`,
 * `/conversations`) and share nothing with that URL space but the copy shape.
 *
 * D9 does not apply to any of them: an anonymous visitor never reaches these
 * pages, so their boxes take NO `gate` prop and `SearchBar` never subscribes to
 * the auth store on their behalf.
 */
export type PrivateSearchSurface = "blogs" | "templates" | "myLibrary" | "chats";

export const SEARCH_PRIVATE_COPY = {
  blogs: {
    placeholder: "ابحث في مدوناتي…",
    ariaLabel: "ابحث في مدوناتك المحفوظة",
  },
  templates: {
    placeholder: "ابحث في قوالبي…",
    ariaLabel: "ابحث في قوالبك المحفوظة",
  },
  myLibrary: {
    placeholder: "ابحث في مكتبتي…",
    ariaLabel: "ابحث في مصادر مكتبتك",
  },
  chats: {
    placeholder: "ابحث في المحادثات…",
    ariaLabel: "ابحث في محادثاتك وفي محتوى الرسائل",
  },
} satisfies Record<PrivateSearchSurface, SurfaceCopy>;

/**
 * «مكتبتي» orders by relevance while a search is live, which is why its
 * «الترتيب» menu is hidden for the duration (the backend REPLACES `sort` with
 * the BM25 ranking — a menu still claiming «الأحدث» would be describing an
 * order the server is not using). This is the one-line explanation that takes
 * its place, so the control does not simply vanish unexplained.
 */
export const SEARCH_RELEVANCE_NOTE = "مرتّبة حسب مطابقة البحث";

// ------------------------------------------------------------------
// The anon conversion modal (D9)
// ------------------------------------------------------------------

/**
 * Search is registered-only, and an anonymous visitor meets THIS instead of a
 * result list. Two rules shape the wording, both from the plan (§0.1):
 *
 *   1. **Lead with «اسأل ريحان».** A reader reaching for a search box has a
 *      QUESTION, and chat is the product's answer to it — not a filtered list.
 *   2. **Name what the account unlocks, in the second line.** Without it the
 *      modal reads as a bait-and-switch to someone who only wanted to filter
 *      the grid in front of them.
 *
 * Framing rule inherited from `gate-copy.ts` / `anon-cta/copy.ts`: this is the
 * softest surface in the product — it interrupts a reader who is not being
 * refused anything. No urgency, no countdown, no scarcity, no «you have used N
 * of M searches». One pitch, two buttons, an obvious X.
 */
export const SEARCH_CTA_COPY = {
  title: "اسأل ريحان",
  body: "أنت تبحث عن إجابة، لا عن قائمة روابط — اسأل ريحان مباشرةً وتصلك مستندة إلى الأنظمة والأحكام.",
  unlock:
    "وبحساب مجاني يُفتح لك أيضاً البحث في المكتبة كاملة: الأنظمة والأحكام والتعاميم وخدمات الامتثال.",
  primaryCta: "ابدأ الآن",
  secondaryCta: "تسجيل الدخول",
} as const;
