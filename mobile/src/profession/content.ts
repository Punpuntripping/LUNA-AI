import type { ProfessionGroupKey } from "@contracts";

import type { IconName } from "@/components/Icon";

// «وش أقرب وصف لك؟» — the copy of the web profession step, COPIED byte-for-byte
// from frontend/components/onboarding/onboarding-content.ts (PROFESSION_GROUPS
// + STEP_PROFESSION). Change it there first, then here; the parity test in
// __tests__/profession.test.ts fails while the two differ. Only the icons are
// the app's own (Icon.tsx names for the web's lucide Gavel / Rocket /
// BadgeCheck / Users).

export interface ProfessionGroup {
  key: ProfessionGroupKey;
  icon: IconName;
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
    icon: "professionLegal",
    label: "قانوني",
    hint: "محامٍ · طالب قانون · باحث قانوني",
  },
  {
    key: "entrepreneur",
    icon: "professionEntrepreneur",
    label: "رائد أعمال",
    hint: "يشمل الأعمال الحرة",
  },
  {
    key: "specialist",
    icon: "professionSpecialist",
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
    icon: "professionIndividual",
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
