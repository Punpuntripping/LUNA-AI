// Typed registry for the public /calculators wing (SEO Public Library, Phase 3).
//
// Each entry is a self-contained, code-only calculator: an input schema the
// generic `CalculatorForm` renders, a pure `compute()` that turns input values
// into result rows, the plain-language formula explanation, the legal basis
// (which مواد it derives from — powers the bidirectional CalculatorBlock embed
// on مادة pages), and an FAQ block. NO data fetching, NO React — safe to bundle
// on the client (the form computes live).
//
// ⚠⚠⚠ FORMULAS PENDING USER LEGAL VALIDATION ⚠⚠⚠
// Every `compute()` below encodes نظام العمل rules from memory and MUST be signed
// off by the user against real worked examples before these pages are trusted as
// authoritative. Each function carries an inline ⚠ note at its formula core. The
// pages themselves always render «نتيجة استرشادية — راجع مختصاً».

import type { FaqItem } from "@/types/library";
import { AR_NUM_LOCALE } from "@/lib/format/numerals";

// ------------------------------------------------------------------
// Schema + result types
// ------------------------------------------------------------------

interface CalculatorInputBase {
  /** Key in the values object handed to `compute`. */
  name: string;
  /** Arabic field label. */
  label: string;
  /** Optional hint under the field. */
  help?: string;
}

export interface CalculatorNumberInput extends CalculatorInputBase {
  kind: "number";
  min?: number;
  max?: number;
  step?: number;
  /** Trailing unit chip, e.g. «ريال»، «سنة»، «ساعة». */
  unit?: string;
  defaultValue: number;
}

export interface CalculatorSelectInput extends CalculatorInputBase {
  kind: "select";
  options: { value: string; label: string }[];
  defaultValue: string;
}

export type CalculatorInput = CalculatorNumberInput | CalculatorSelectInput;

/** A map of input `name` → current value (numbers for number inputs, the option
 * `value` string for selects). */
export type CalculatorValues = Record<string, number | string>;

/** One row in the instant result panel. */
export interface CalculatorResultRow {
  label: string;
  /** Pre-formatted, RTL-ready display string (Arabic-Indic numerals). */
  value: string;
  /** Optional muted note under the row. */
  hint?: string;
  /** The headline result (bold, highlighted). */
  emphasis?: boolean;
}

/** A pointer to the مواد a calculator derives from (bidirectional mesh). */
export interface CalculatorLegalBasis {
  /** Regulation sidecar slug, e.g. «نظام-العمل». */
  regSlug: string;
  /** The article numbers the formula rests on. */
  articleNos: number[];
  /** Human label for the regulation, e.g. «نظام العمل». */
  label: string;
}

export interface CalculatorDef {
  /** Arabic URL slug, e.g. «مكافأة-نهاية-الخدمة». */
  slug: string;
  title_ar: string;
  description: string;
  inputs: CalculatorInput[];
  compute: (values: CalculatorValues) => CalculatorResultRow[];
  formulaExplanation_md: string;
  legalBasis: CalculatorLegalBasis[];
  faq: FaqItem[];
}

// ------------------------------------------------------------------
// Formatting helpers (Latin digits, matching the app-wide numeral convention
// used by UsageLimitsDialog / TrustLine — see `lib/format/numerals`).
// ------------------------------------------------------------------

/** Format a number with grouping + up to 2 fraction digits, Latin digits. */
function fmt(n: number): string {
  const safe = Number.isFinite(n) ? n : 0;
  return safe.toLocaleString(AR_NUM_LOCALE, { maximumFractionDigits: 2 });
}

/** Format a monetary amount as «{n} ريال». */
function money(n: number): string {
  return `${fmt(n)} ريال`;
}

/** Coerce a value-map entry to a finite number (0 on anything unparseable). */
function num(values: CalculatorValues, key: string): number {
  const raw = values[key];
  const n = typeof raw === "number" ? raw : Number(raw);
  return Number.isFinite(n) ? n : 0;
}

/** Coerce a value-map entry to its select-option string. */
function str(values: CalculatorValues, key: string): string {
  const raw = values[key];
  return typeof raw === "string" ? raw : String(raw ?? "");
}

// ------------------------------------------------------------------
// 1) مكافأة نهاية الخدمة — نظام العمل م84 / م85
// ------------------------------------------------------------------

function computeEndOfService(values: CalculatorValues): CalculatorResultRow[] {
  const wage = num(values, "monthlyWage");
  const years = num(values, "years");
  const months = num(values, "months");
  const endType = str(values, "endType");

  const totalYears = years + months / 12;

  // ⚠ PENDING USER LEGAL VALIDATION — م84: نصف شهر أجر عن كل سنة من السنوات الخمس
  // الأولى + أجر شهر كامل عن كل سنة بعدها، مع احتساب أجزاء السنة بالتناسب.
  const firstFiveYears = Math.min(totalYears, 5);
  const yearsBeyondFive = Math.max(totalYears - 5, 0);
  const monthsOfWage = firstFiveYears * 0.5 + yearsBeyondFive * 1;
  const baseAward = monthsOfWage * wage;

  // ⚠ PENDING USER LEGAL VALIDATION — تعديل حسب سبب انتهاء العلاقة:
  //   • إنهاء من صاحب العمل / انتهاء عقد محدد المدة → المكافأة كاملة (المادة 84).
  //   • استقالة (المادة 85): أقل من سنتين → لا شيء · من 2 إلى <5 → الثلث ·
  //                          من 5 إلى <10 → الثلثان · 10 فأكثر → كاملة.
  //   • فصل بموجب المادة 80 → لا تُستحق مكافأة.
  let factor = 1;
  let factorLabel = "المكافأة كاملة (إنهاء من صاحب العمل)";
  let note: string | undefined;

  if (endType === "resignation") {
    if (totalYears < 2) {
      factor = 0;
      factorLabel = "لا تُستحق — الخدمة أقل من سنتين";
    } else if (totalYears < 5) {
      factor = 1 / 3;
      factorLabel = "ثلث المكافأة (استقالة، من سنتين إلى أقل من خمس)";
    } else if (totalYears < 10) {
      factor = 2 / 3;
      factorLabel = "ثلثا المكافأة (استقالة، من خمس إلى أقل من عشر)";
    } else {
      factor = 1;
      factorLabel = "المكافأة كاملة (استقالة، عشر سنوات فأكثر)";
    }
  } else if (endType === "article_80") {
    factor = 0;
    factorLabel = "لا تُستحق مكافأة";
    note = "الفصل بموجب المادة 80 يسقط الحق في المكافأة.";
  }

  const award = baseAward * factor;

  const rows: CalculatorResultRow[] = [
    {
      label: "إجمالي مدة الخدمة",
      value: `${fmt(years)} سنة${months > 0 ? ` و${fmt(months)} شهر` : ""}`,
    },
    { label: "الأجر الشهري", value: money(wage) },
    {
      label: "أشهر الأجر المستحقة (أساس المادة 84)",
      value: `${fmt(monthsOfWage)} شهر`,
      hint: `${fmt(firstFiveYears * 0.5)} عن أول خمس سنوات + ${fmt(
        yearsBeyondFive,
      )} عمّا بعدها`,
    },
    { label: "المكافأة الأساسية قبل التعديل", value: money(baseAward) },
    { label: "نسبة الاستحقاق حسب سبب الانتهاء", value: factorLabel },
    {
      label: "المكافأة المستحقة",
      value: money(award),
      hint: note,
      emphasis: true,
    },
  ];
  return rows;
}

// ------------------------------------------------------------------
// 2) مدة الإشعار (الإخطار) — نظام العمل م75
// ------------------------------------------------------------------

function computeNoticePeriod(values: CalculatorValues): CalculatorResultRow[] {
  const wageType = str(values, "wageType");

  // ⚠ PENDING USER LEGAL VALIDATION — م75 (العقد غير محدد المدة): إشعار لا يقل عن
  // 60 يوماً إذا كان الأجر يُدفع شهرياً، و30 يوماً في الحالات الأخرى.
  const days = wageType === "monthly" ? 60 : 30;
  const wageLabel =
    wageType === "monthly"
      ? "يُدفع الأجر شهرياً"
      : "يُدفع الأجر بغير الشهر (أسبوعي/يومي/بالقطعة)";

  return [
    { label: "نوع العقد", value: "غير محدّد المدة" },
    { label: "طريقة صرف الأجر", value: wageLabel },
    {
      label: "أقل مدة إشعار مطلوبة",
      value: `${fmt(days)} يوماً`,
      emphasis: true,
    },
  ];
}

// ------------------------------------------------------------------
// 3) أجر العمل الإضافي — نظام العمل م107
// ------------------------------------------------------------------

function computeOvertime(values: CalculatorValues): CalculatorResultRow[] {
  const wage = num(values, "monthlyWage");
  const hours = num(values, "overtimeHours");

  // ⚠ PENDING USER LEGAL VALIDATION — أجر الساعة الأساسي يُشتق من الأجر الشهري
  // باعتماد 30 يوماً في الشهر و8 ساعات في اليوم (اصطلاح شائع — قابل للتعديل).
  const hourlyWage = wage / 30 / 8;
  // م107: أجر الساعة الأساسي + 50% عن كل ساعة عمل إضافي.
  const overtimeHourRate = hourlyWage * 1.5;
  const total = overtimeHourRate * hours;

  return [
    {
      label: "أجر الساعة الأساسي",
      value: money(hourlyWage),
      hint: "الأجر الشهري ÷ 30 يوماً ÷ 8 ساعات",
    },
    { label: "أجر ساعة العمل الإضافي (+50%)", value: money(overtimeHourRate) },
    { label: "عدد ساعات العمل الإضافي", value: `${fmt(hours)} ساعة` },
    {
      label: "إجمالي أجر العمل الإضافي",
      value: money(total),
      emphasis: true,
    },
  ];
}

// ------------------------------------------------------------------
// Registry
// ------------------------------------------------------------------

const LABOR_LAW_SLUG = "نظام-العمل";

export const CALCULATORS: CalculatorDef[] = [
  {
    slug: "مكافأة-نهاية-الخدمة",
    title_ar: "مكافأة نهاية الخدمة",
    description:
      "احسب مكافأة نهاية الخدمة وفق نظام العمل السعودي — نصف شهر أجر عن كل سنة من السنوات الخمس الأولى، وشهر كامل عن كل سنة بعدها، مع مراعاة سبب انتهاء العلاقة (إنهاء أو استقالة).",
    inputs: [
      {
        kind: "number",
        name: "monthlyWage",
        label: "الأجر الشهري",
        unit: "ريال",
        min: 0,
        step: 100,
        defaultValue: 10000,
        help: "الأجر الأساسي شهرياً بالريال السعودي.",
      },
      {
        kind: "number",
        name: "years",
        label: "سنوات الخدمة",
        unit: "سنة",
        min: 0,
        step: 1,
        defaultValue: 7,
      },
      {
        kind: "number",
        name: "months",
        label: "أشهر إضافية",
        unit: "شهر",
        min: 0,
        max: 11,
        step: 1,
        defaultValue: 0,
        help: "أجزاء السنة تُحتسب بالتناسب.",
      },
      {
        kind: "select",
        name: "endType",
        label: "سبب انتهاء العلاقة",
        defaultValue: "employer",
        options: [
          {
            value: "employer",
            label: "إنهاء من صاحب العمل / انتهاء عقد محدد المدة",
          },
          { value: "resignation", label: "استقالة العامل" },
          { value: "article_80", label: "فصل بموجب المادة 80" },
        ],
      },
    ],
    compute: computeEndOfService,
    formulaExplanation_md: `تُحتسب مكافأة نهاية الخدمة في نظام العمل السعودي على أساس **الأجر الأخير**:

- **نصف شهر** أجر عن كل سنة من **السنوات الخمس الأولى**.
- **شهر كامل** أجر عن كل سنة من **السنوات التالية** لها.
- تُحتسب **أجزاء السنة** بالتناسب.

ثم يُعدَّل الناتج حسب سبب انتهاء العلاقة:

- **إنهاء من صاحب العمل** أو انتهاء عقد محدد المدة: المكافأة **كاملة** (المادة 84).
- **الاستقالة** (المادة 85): أقل من سنتين → لا شيء، ومن سنتين إلى أقل من خمس → **الثلث**، ومن خمس إلى أقل من عشر → **الثلثان**، وعشر سنوات فأكثر → **كاملة**.
- **الفصل بموجب المادة 80**: لا تُستحق مكافأة.

> النتيجة استرشادية ولا تُغني عن مراجعة مختص، فقد تدخل بدلات أو أحكام خاصة في الأجر الخاضع للاحتساب.`,
    legalBasis: [
      { regSlug: LABOR_LAW_SLUG, articleNos: [84, 85], label: "نظام العمل" },
    ],
    faq: [
      {
        q: "كيف تُحتسب مكافأة نهاية الخدمة في السعودية؟",
        a: "نصف شهر أجر عن كل سنة من السنوات الخمس الأولى، وشهر كامل عن كل سنة بعدها، على أساس الأجر الأخير، مع احتساب أجزاء السنة بالتناسب.",
      },
      {
        q: "هل تختلف المكافأة عند الاستقالة؟",
        a: "نعم. وفق المادة 85: من استقال قبل سنتين لا يستحق مكافأة، ومن سنتين إلى أقل من خمس يستحق الثلث، ومن خمس إلى أقل من عشر يستحق الثلثين، ومن أكمل عشر سنوات فأكثر يستحق المكافأة كاملة.",
      },
      {
        q: "هل تُحتسب المكافأة على الأجر الأساسي أم الشامل؟",
        a: "تُحتسب على الأجر الأخير الذي يتقاضاه العامل، وقد تشمل بعض البدلات بحسب طبيعة العقد؛ لذا يُنصح بمراجعة مختص لتحديد الأجر الخاضع للاحتساب.",
      },
    ],
  },
  {
    slug: "مدة-الإشعار",
    title_ar: "مدة الإشعار (الإخطار)",
    description:
      "احسب أقل مدة إشعار مطلوبة لإنهاء عقد العمل غير محدد المدة وفق المادة 75 من نظام العمل — 60 يوماً عند صرف الأجر شهرياً، و30 يوماً في الحالات الأخرى.",
    inputs: [
      {
        kind: "select",
        name: "wageType",
        label: "طريقة صرف الأجر",
        defaultValue: "monthly",
        options: [
          { value: "monthly", label: "يُدفع الأجر شهرياً" },
          {
            value: "other",
            label: "يُدفع الأجر بغير الشهر (أسبوعي/يومي/بالقطعة)",
          },
        ],
        help: "تنطبق الحاسبة على العقود غير محددة المدة.",
      },
    ],
    compute: computeNoticePeriod,
    formulaExplanation_md: `في العقد **غير محدّد المدة**، يجوز لأي من الطرفين إنهاؤه بإشعار كتابي وفق المادة 75:

- **60 يوماً** على الأقل إذا كان الأجر يُدفع **شهرياً**.
- **30 يوماً** على الأقل في الحالات الأخرى (أجر أسبوعي أو يومي أو بالقطعة).

يُشترط أن يكون الإشعار **كتابياً**، ولا يجوز إنهاء العقد أثناء تمتّع العامل بإجازة.

> النتيجة استرشادية؛ العقود محددة المدة والحالات الاستثنائية لها أحكام مختلفة.`,
    legalBasis: [
      { regSlug: LABOR_LAW_SLUG, articleNos: [75], label: "نظام العمل" },
    ],
    faq: [
      {
        q: "ما مدة الإشعار لإنهاء عقد العمل في السعودية؟",
        a: "في العقد غير محدد المدة: 60 يوماً على الأقل إذا كان الأجر يُدفع شهرياً، و30 يوماً في الحالات الأخرى، بإشعار كتابي.",
      },
      {
        q: "هل تنطبق مدة الإشعار على العقود محددة المدة؟",
        a: "لا؛ العقد محدد المدة ينتهي بانتهاء مدته، ولمدة الإشعار في المادة 75 حكم خاص بالعقود غير محددة المدة.",
      },
    ],
  },
  {
    slug: "أجر-العمل-الإضافي",
    title_ar: "أجر العمل الإضافي",
    description:
      "احسب أجر ساعات العمل الإضافي وفق المادة 107 من نظام العمل — أجر الساعة الأساسي مضافاً إليه 50% عن كل ساعة عمل إضافي.",
    inputs: [
      {
        kind: "number",
        name: "monthlyWage",
        label: "الأجر الشهري",
        unit: "ريال",
        min: 0,
        step: 100,
        defaultValue: 8000,
      },
      {
        kind: "number",
        name: "overtimeHours",
        label: "عدد ساعات العمل الإضافي",
        unit: "ساعة",
        min: 0,
        step: 1,
        defaultValue: 10,
      },
    ],
    compute: computeOvertime,
    formulaExplanation_md: `وفق المادة 107 من نظام العمل، يُستحق عن كل ساعة عمل إضافي **أجر الساعة الأساسي مضافاً إليه 50%**.

تُحتسب الحاسبة أجر الساعة الأساسي من الأجر الشهري على أساس:

- **30 يوماً** في الشهر و**8 ساعات** في اليوم (اصطلاح شائع قابل للتعديل حسب العقد).

ثم:

- أجر ساعة العمل الإضافي = أجر الساعة الأساسي × **1.5**.
- الإجمالي = أجر ساعة العمل الإضافي × عدد الساعات الإضافية.

> النتيجة استرشادية؛ قد يختلف أساس احتساب الساعة باختلاف ساعات العمل الفعلية والبدلات.`,
    legalBasis: [
      { regSlug: LABOR_LAW_SLUG, articleNos: [107], label: "نظام العمل" },
    ],
    faq: [
      {
        q: "كم أجر ساعة العمل الإضافي في السعودية؟",
        a: "أجر الساعة الأساسي مضافاً إليه 50% عن كل ساعة عمل إضافي، وفق المادة 107 من نظام العمل.",
      },
      {
        q: "كيف يُحتسب أجر الساعة الأساسي؟",
        a: "يُقسَّم الأجر الشهري على عدد أيام الشهر (30) ثم على ساعات العمل اليومية (8) في الاصطلاح الشائع، وقد يختلف الأساس بحسب العقد.",
      },
    ],
  },
];

// ------------------------------------------------------------------
// Lookups
// ------------------------------------------------------------------

/** Resolve a calculator by its (decoded) Arabic slug. */
export function getCalculator(slug: string): CalculatorDef | undefined {
  return CALCULATORS.find((c) => c.slug === slug);
}

/**
 * Reverse lookup for the bidirectional CalculatorBlock embed: every calculator
 * whose legal basis cites `(regSlug, articleNo)`. Powers «هذه المادة لها حاسبة»
 * on مادة pages.
 */
export function getCalculatorsForArticle(
  regSlug: string,
  articleNo: number,
): CalculatorDef[] {
  return CALCULATORS.filter((c) =>
    c.legalBasis.some(
      (b) => b.regSlug === regSlug && b.articleNos.includes(articleNo),
    ),
  );
}
