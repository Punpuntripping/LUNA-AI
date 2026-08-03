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
