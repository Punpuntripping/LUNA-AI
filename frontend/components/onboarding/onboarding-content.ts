/**
 * Single source for all «اتعرف على ريحان» onboarding copy.
 * Edit text/questions/stats HERE — the step components only render this.
 *
 * Corpus numbers verified live from Supabase on 2026-07-12:
 *   regulations_v2 = 3,373 documents across 132 government entities,
 *   chunks_v2 (provisions) = 33,729, cases = 20,671.
 * Circulars (تعاميم) are NOT a distinct corpus today (~1 document), so the
 * stats intentionally group all regulatory documents under one figure.
 */

import {
  BadgeCheck,
  Gavel,
  Rocket,
  Users,
  type LucideIcon,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Profession step («وش أقرب وصف لك؟») — 2×2 card grid + decline row.
// Stored on users.profession_group/profession_label (migration 115) via
// PATCH /auth/profession. Group slugs must match the DB check constraint.
// ---------------------------------------------------------------------------

export type ProfessionGroupKey =
  | "legal"
  | "entrepreneur"
  | "specialist"
  | "individual";

export interface ProfessionGroup {
  key: ProfessionGroupKey;
  icon: LucideIcon;
  label: string;
  /** One-line small print under the label — descriptive, not clickable. */
  hint: string;
  /** Finer-segment chips. Only مختص and فرد have them; the other two cards
   *  are single-tap answers. An «أخرى» free-text input always accompanies
   *  the chips (rendered by the step component, not listed here). */
  options?: readonly string[];
}

/** Order matters: first card renders top-RIGHT in the RTL 2×2 grid. */
export const PROFESSION_GROUPS: readonly ProfessionGroup[] = [
  {
    key: "legal",
    icon: Gavel,
    label: "قانوني",
    hint: "محامٍ · طالب قانون · باحث قانوني",
  },
  {
    key: "entrepreneur",
    icon: Rocket,
    label: "رائد أعمال",
    hint: "يشمل الأعمال الحرة",
  },
  {
    key: "specialist",
    icon: BadgeCheck,
    label: "مختص",
    hint: "صحي · هندسي · محاسبي · امتثال",
    options: [
      "مختص صحي",
      "مهندس",
      "محاسب",
      "مختص موارد بشرية",
      "مختص امتثال",
    ],
  },
  {
    key: "individual",
    icon: Users,
    label: "فرد",
    hint: "موظف · طالب · متقاعد",
    options: ["موظف حكومي", "موظف خاص", "متسبب", "متقاعد", "طالب"],
  },
] as const;

export const STEP_PROFESSION = {
  heading: "وش أقرب وصف لك؟",
  intro:
    "إجابتك تساعدنا نعرف مين يستخدم ريحان ونطوّره ليخدمك أفضل — ولن تؤثر على إجاباتك.",
  /** Shown under the cards when the picked group has finer options. */
  optionsHint: "اختياري — حدّد أقرب وصف أو اكتبه بنفسك:",
  otherChip: "أخرى",
  otherPlaceholder: "اكتب وصفك…",
  declineLabel: "أفضل عدم الإجابة",
  /** Primary button label when the dialog shows the profession step alone. */
  saveLabel: "حفظ",
} as const;

export const CORPUS_STATS = [
  { value: "+3,300", label: "نظام ولائحة ودليل تنظيمي" },
  { value: "+33,000", label: "مادة وبند مفهرس" },
  { value: "+20,000", label: "حكم وقضية" },
  { value: "132", label: "جهة حكومية" },
] as const;

export const STEP_AGENTS = {
  heading: "كيف يشتغل ريحان؟",
  intro:
    "خلف كل إجابة ثلاثة وكلاء يعملون معًا. الباحث هو من يغوص في قاعدة معرفية ضخمة عبر وضعين: بحث الأنظمة واللوائح، وبحث الأحكام القضائية.",
} as const;

export const STEP_WORKSPACE = {
  heading: "مساحة العمل والمخرجات",
  intro:
    "بجانب المحادثة توجد مساحة العمل — المكان الذي تتجمع فيه «المخرجات» أثناء الجلسة.",
  bullets: [
    {
      title: "ما هي المخرجات؟",
      text: "كل ما يُنتج أثناء المحادثة — الملفات التي ترفعها، نتائج البحث، المسودات التي يكتبها ريحان — يُحفظ كبطاقة مستقلة في مساحة العمل.",
    },
    {
      title: "كل الوكلاء يصلون إليها",
      text: "أي وكيل يعمل على سؤالك يستطيع الوصول إلى أي عنصر في مساحة العمل والاستفادة منه في إجابته.",
    },
    {
      title: "معلومة تبقى طوال الجلسة",
      text: "اطلب من ريحان «احفظ هذه المعلومة» وستُضاف إلى مساحة العمل وتبقى حاضرة لكل الوكلاء بقية الجلسة.",
    },
  ],
} as const;

export const STEP_QUESTIONS = {
  heading: "ابدأ معنا بسؤال",
  hint: "اختر مجالًا ثم اضغط على سؤال ليوضع في صندوق الكتابة — لن يُرسل شيء حتى تضغط زر الإرسال بنفسك.",
} as const;

export type QuestionCategoryKey = "real_estate" | "labor" | "judicial";

export interface QuestionCategory {
  key: QuestionCategoryKey;
  label: string;
  questions: readonly [string, string, string];
}

export const STARTER_CATEGORIES: readonly QuestionCategory[] = [
  {
    key: "real_estate",
    label: "عقاري",
    questions: [
      "ما حقوق المستأجر إذا أنهى المؤجر عقد الإيجار قبل نهاية مدته؟",
      "ما التزامات المطور العقاري في مشاريع البيع على الخارطة؟",
      "ما أثر عدم توثيق عقد الإيجار في منصة إيجار؟",
    ],
  },
  {
    key: "labor",
    label: "عمالي",
    questions: [
      "كيف تُحسب مكافأة نهاية الخدمة وفق نظام العمل السعودي؟",
      "ما حقوق العامل عند الفصل دون سبب مشروع؟",
      "ما الضوابط النظامية لفترة التجربة وهل يجوز تمديدها؟",
    ],
  },
  {
    key: "judicial",
    label: "قضائي",
    questions: [
      "ما خطوات رفع دعوى تجارية وما المستندات المطلوبة؟",
      "متى يسقط الحق في الاعتراض على الحكم بالاستئناف؟",
      "ابحث عن أحكام قضائية مشابهة لنزاع شراكة تجارية",
    ],
  },
] as const;
