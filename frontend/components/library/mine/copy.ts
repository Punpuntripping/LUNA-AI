import { getDateGroupAr } from "@/lib/utils";
import { AR_DATE_LOCALE } from "@/lib/format/numerals";
import type { MyLibraryContentType, MyLibrarySort } from "@/lib/api";

/**
 * Every Arabic string «مكتبتي» renders, in ONE file — same convention D10 sets
 * for the gate copy (`lib/library/gate-copy.ts`), so the wording can be edited
 * in a single pass without touching component logic.
 *
 * VOCABULARY RULE (§5B.3): the user-facing concept is USAGE — «استخدام». Never
 * label anything «فتح» here; the labels must match `use_count` / `last_used_at`
 * / `sort=most_used` end to end so there is no translation layer between the
 * copy and the column.
 */

export const MY_LIBRARY_COPY = {
  pageTitle: "مكتبتي",
  pageSubtitle: "كل مصدر تفتحه أو تحفظه يظهر هنا، مرتّباً حسب استخدامك له.",
  /** Sidebar shelf → the full surface. */
  openFullShelf: "عرض المكتبة كاملة",
  /**
   * مكتبتي → the PUBLIC library. The shelf only ever contains what this user has
   * already touched, so an empty or thin shelf has no way forward from inside
   * itself; this is that way out. Deliberately says «العامة» — the two surfaces
   * share the word «مكتبة» and the reader needs to know which one they land on.
   */
  browsePublicLibrary: "تصفّح المكتبة العامة",

  sortLabel: "الترتيب",
  sorts: {
    recent: "الأحدث",
    most_used: "الأكثر استخداماً",
    saved: "الأحدث حفظاً",
  } satisfies Record<MyLibrarySort, string>,

  tabs: {
    regulation: "الأنظمة",
    judgment: "الأحكام",
    service: "الخدمات",
    circular: "التعاميم",
    // Service GUIDES — our own authored rewrite of an entity's official PDF user
    // guide, published at /compliance/{slug}. Separate from «الخدمات», which is
    // the bare government service a chat citation shelved and which has no page
    // of ours: two id spaces, two shelf types, so two labels.
    compliance: "أدلة الخدمات",
    form: "النماذج",
    calculator: "الحاسبات",
    article: "المواد",
  } satisfies Record<MyLibraryContentType, string>,

  emptyShelf: "لم تفتح أي مصدر بعد. كل ما تفتحه أو تحفظه سيظهر هنا.",
  emptyTab: {
    regulation: "لا توجد أنظمة في مكتبتك بعد.",
    judgment: "لا توجد أحكام في مكتبتك بعد.",
    service: "لا توجد خدمات في مكتبتك بعد.",
    compliance: "لا توجد أدلة خدمات في مكتبتك بعد.",
    circular: "لا توجد تعاميم في مكتبتك بعد.",
    form: "لا توجد نماذج في مكتبتك بعد.",
    calculator: "لا توجد حاسبات في مكتبتك بعد.",
    article: "لا توجد مواد في مكتبتك بعد.",
  } satisfies Record<MyLibraryContentType, string>,
  emptyTabHint: "كل ما تفتحه أو تحفظه سيظهر هنا.",

  loadError: "تعذّر تحميل مكتبتك.",
  retry: "إعادة المحاولة",

  save: "حفظ",
  saved: "محفوظ",
  unsave: "إزالة الحفظ",

  frozenBadge: "محفوظ في مكتبتك",
  frozenCtaAction: "عرض الباقات",

  unavailableBadge: "غير متاح",
  unavailableNote: "هذا المصدر غير متاح للعرض حالياً.",

  groupHeaderNote: "مواد محفوظة من هذا النظام",
  untitled: "بدون عنوان",
  calculatorFallbackTitle: "حاسبة",

  neverUsed: "لم يُستخدم بعد",
  lastUsedPrefix: "آخر استخدام:",

  previousPage: "السابق",
  nextPage: "التالي",

  sessionLoading: "جارٍ تحميل الجلسة...",
} as const;

/** «لديك {n} مصدراً محفوظاً في مكتبتك — رقِّ باقتك لفتحها من جديد.» (D10) */
export function frozenCtaText(count: number): string {
  return `لديك ${count} مصدراً محفوظاً في مكتبتك — رقِّ باقتك لفتحها من جديد.`;
}

/** «صفحة {page} من {total}» */
export function pageIndicator(page: number, totalPages: number): string {
  return `صفحة ${page} من ${totalPages}`;
}

/** Arabic count agreement: 1 · 2 · 3–10 · 11+. */
function pluralAr(
  n: number,
  one: string,
  two: string,
  few: string,
  many: string,
): string {
  if (n === 1) return one;
  if (n === 2) return two;
  if (n >= 3 && n <= 10) return few.replace("{n}", String(n));
  return many.replace("{n}", String(n));
}

/** «استُخدم مرة واحدة» / «استُخدم مرتين» / «استُخدم {n} مرات» / «استُخدم {n} مرة». */
export function usageLabel(useCount: number): string {
  if (useCount <= 0) return MY_LIBRARY_COPY.neverUsed;
  return pluralAr(
    useCount,
    "استُخدم مرة واحدة",
    "استُخدم مرتين",
    "استُخدم {n} مرات",
    "استُخدم {n} مرة",
  );
}

/**
 * The nested-مواد counter chip on a نظام card.
 *
 * ⚠ IT MUST SAY «محفوظة». A bare «مادة واحدة» beside «اللائحة التنفيذية لنظام
 * ضريبة القيمة المضافة» reads as a claim about the STATUTE — that the لائحة has
 * one مادة — which is nonsense for a document with 79 of them. What the number
 * actually means is "how many of its مواد are on YOUR shelf", so the label has to
 * carry that or it is simply false.
 */
export function articlesLabel(count: number): string {
  return pluralAr(
    count,
    "مادة واحدة محفوظة",
    "مادتان محفوظتان",
    "{n} مواد محفوظة",
    "{n} مادة محفوظة",
  );
}

/**
 * «آخر استخدام: اليوم / أمس / هذا الأسبوع / هذا الشهر / 12 مارس 2026».
 * Reuses the app-wide grouping (`getDateGroupAr`) so مكتبتي speaks the same
 * date vocabulary as the sidebar; anything older than a month falls back to a
 * full Arabic date rather than the useless «أقدم».
 */
export function lastUsedLabel(iso: string | null): string | null {
  if (!iso) return null;
  const group = getDateGroupAr(iso);
  const when =
    group === "أقدم"
      ? new Intl.DateTimeFormat(AR_DATE_LOCALE, {
          year: "numeric",
          month: "long",
          day: "numeric",
        }).format(new Date(iso))
      : group;
  return `${MY_LIBRARY_COPY.lastUsedPrefix} ${when}`;
}
