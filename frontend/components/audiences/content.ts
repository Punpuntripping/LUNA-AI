/**
 * Single source of truth for the public «ريحان يستهدف مين؟» page (`/audiences`)
 * and the homepage teaser that links to it.
 *
 * The narrative: Rayhan isn't for lawyers alone. Its corpus spans every sector
 * of Saudi regulation, so anyone whose work touches a نظام finds their source
 * here. Every example question below is grounded in a REAL row mined from the
 * live corpus (regulations_v2 / cases / services) — each carries the official
 * link to the source it traces to, mirroring the in-app citation model. Counts
 * are live database floors as of 2026-06-30, phrased "أكثر من" to stay honest.
 *
 * Provenance of the mined data: agents_reports scratchpad enrich_* reports.
 */
import {
  Gavel,
  BadgeCheck,
  Rocket,
  Users,
  Scale,
  Stethoscope,
  HardHat,
  Calculator,
  PenLine,
  Briefcase,
  type LucideIcon,
} from "lucide-react";

import { PRIMARY_CTA_HREF } from "@/components/landing/content";

export { PRIMARY_CTA_HREF };

// ---------------------------------------------------------------------------
// Page header + hero
// ---------------------------------------------------------------------------

export const AUDIENCES_PAGE = {
  badge: "ريحان يستهدف مين؟",
  titleLead: "ريحان ليس للمحامين وحدهم،",
  titleEmphasis: "بل لكل من يتعامل مع نظامٍ سعودي",
  subtitle:
    "قاعدة ريحان تغطّي أنظمة وأحكام كل القطاعات في المملكة — فأينما تقاطع عملك مع نظام سعودي، يجد لك ريحان مصدره الرسمي ويربط كل معلومة برابطها المباشر.",
} as const;

/** Corpus credibility strip under the hero — live database floors. */
export const AUDIENCES_STATS: { value: string; label: string }[] = [
  { value: "+3,000", label: "نظام ولائحة" },
  { value: "+20,000", label: "حكم قضائي" },
  { value: "38", label: "قطاعاً نظامياً" },
  { value: "+200", label: "كيان حكومي" },
];

// ---------------------------------------------------------------------------
// Audiences — one block per persona
// ---------------------------------------------------------------------------

/** A single corpus-grounded example question, with the official source it
 *  traces to. ``tag`` labels the sub-discipline (used by the المختصون card). */
export interface AudienceExample {
  /** The natural-language question, in the user's voice. */
  q: string;
  /** Short label of the real regulation / case / service it traces to. */
  source: string;
  /** Official link to that source (laws.moj, istitlaa, my.gov, zatca, sama…). */
  url: string;
  /** Optional discipline tag — e.g. مختص صحي · مهندس · محاسب · صياغة. */
  tag?: string;
  /** Optional discipline icon paired with the tag. */
  tagIcon?: LucideIcon;
  /** "search" (default) traces a question to a source; "write" is a drafting
   *  task (لائحة/عقد/مذكرة) — the lead icon switches to a pen accordingly. */
  mode?: "search" | "write";
}

export interface Audience {
  id: string;
  icon: LucideIcon;
  title: string;
  /** One-line who/why. */
  tagline: string;
  examples: AudienceExample[];
  /** "يستند إلى" footer — what corpus depth backs this audience. */
  basis: string;
}

export const AUDIENCES: Audience[] = [
  {
    id: "lawyers",
    icon: Gavel,
    title: "المحامون",
    tagline:
      "ابحث في الأنظمة واللوائح، واجمع السوابق القضائية على مسألةٍ بعينها موثّقةً بالأحكام التي قُضي بها، وصُغ لوائحك الاعتراضية ومذكراتك وعقودك بلغة قانونية دقيقة.",
    examples: [
      {
        q: "ما استقر عليه القضاء التجاري في فسخ عقد المقاولة وتقدير التعويض؟",
        source: "حكم تجاري (نموذج) — المحكمة التجارية",
        url: "https://laws.moj.gov.sa/ar/JudicialDecisionsList/1/DpCFi0HoAHUZM2MpoD-d4IajnCkOR1CXx4h_evYDRtZkevTZA_1IxlxGNikKEkXs",
      },
      {
        q: "اجمع الأحكام التجارية في بطلان حكم التحكيم والأسباب التي قُبلت",
        source: "حكم تجاري (نموذج) — محكمة الاستئناف",
        url: "https://laws.moj.gov.sa/ar/JudicialDecisionsList/2/rj1c6QChagN64oiusjyqIEQGAxMqLsisE3z-HmlMjB_tSxcXIYZIkWuz_zz3kCuO",
      },
      {
        q: "ما إجراءات الاعتراض على الحكم أمام محكمة الاستئناف؟",
        source: "اللائحة التنفيذية لإجراءات الاستئناف — وزارة العدل",
        url: "https://laws.moj.gov.sa/ar/legislation/314SBg_7tcu6IQv02z2tkQ",
      },
      {
        q: "كم تبلغ التكاليف القضائية في دعوى مطالبة مالية، وما الحالات المُعفاة؟",
        source: "نظام التكاليف القضائية",
        url: "https://laws.boe.gov.sa/BoeLaws/Laws/LawDetails/3e368087-7b31-46e7-8005-ada100b8f703/1",
      },
      {
        tag: "صياغة",
        tagIcon: PenLine,
        mode: "write",
        q: "صِغ لائحة اعتراضية كاملة على دعوى فض الشيوع وبيع العقار، مع طلب ندب خبير لإثبات نقص المنفعة",
        source: "نظام المرافعات الشرعية",
        url: "https://laws.moj.gov.sa/ar/legislation/sSe-gyvwrajdndY5P08WZg",
      },
      {
        tag: "صياغة",
        tagIcon: PenLine,
        mode: "write",
        q: "راجع عقد حراسات أمنية وأعد صياغته بما يحفظ حقوق الطرف الثاني",
        source: "نظام المعاملات المدنية",
        url: "https://laws.moj.gov.sa/ar/legislation/PBbHmywh1XMp-Kyv3NtQLg",
      },
    ],
    basis:
      "يستند إلى أكثر من 20,000 حكم قضائي والأنظمة واللوائح التنفيذية، ويصوغ لوائحك وعقودك مبنيةً عليها — كل سابقة موثّقة بمصدرها.",
  },
  {
    id: "specialists",
    icon: BadgeCheck,
    title: "المختصون",
    tagline:
      "مختص صحي أو مهندس أو محاسب أو موارد بشرية — مهما كان تخصّصك، يجمع لك ريحان اشتراطات قطاعك واعتماداته ويصوغ مستنداتك من مصادرها الرسمية.",
    examples: [
      {
        tag: "مختص صحي",
        tagIcon: Stethoscope,
        q: "شروط اعتماد المنشآت الصحية لدى المركز السعودي للاعتماد (CBAHI)",
        source: "لائحة الاعتماد الصحي — CBAHI",
        url: "https://istitlaa.ncc.gov.sa/ar/health/cbahi/bylaw",
      },
      {
        tag: "مختص صحي",
        tagIcon: Stethoscope,
        q: "متطلبات تسجيل دواء بشري لدى هيئة الغذاء والدواء",
        source: "الدليل الإرشادي لتسجيل الأدوية البشرية — SFDA",
        url: "https://istitlaa.ncc.gov.sa/ar/health/sfda/DataRequirementsHumanDrugsSubmission",
      },
      {
        tag: "مهندس",
        tagIcon: HardHat,
        q: "اشتراطات اعتماد جهات التفتيش على كود البناء السعودي",
        source: "متطلبات اعتماد جهات التفتيش على كود البناء — الهيئة السعودية للمواصفات",
        url: "https://istitlaa.ncc.gov.sa/ar/Trade/saso/BuildCode",
      },
      {
        tag: "مهندس",
        tagIcon: HardHat,
        q: "ما متطلبات تصنيف المقاولين ودرجاته؟",
        source: "خدمة تصنيف المقاولين — هيئة الحكومة الرقمية",
        url: "https://my.gov.sa/ar/services/24989",
      },
      {
        tag: "محاسب",
        tagIcon: Calculator,
        q: "أحكام التسجيل في ضريبة القيمة المضافة وتقديم الإقرار",
        source: "اللائحة التنفيذية لضريبة القيمة المضافة — هيئة الزكاة والضريبة والجمارك",
        url: "https://zatca.gov.sa/ar/RulesRegulations/Taxes/Pages/VATImplementingRegulations.aspx",
      },
      {
        tag: "محاسب",
        tagIcon: Calculator,
        q: "قواعد جباية الزكاة وحسابها للمنشآت",
        source: "خدمة سداد الزكاة للمنشآت — هيئة الزكاة والضريبة والجمارك",
        url: "https://my.gov.sa/ar/services/20348",
      },
      {
        tag: "مختص موارد بشرية",
        tagIcon: Briefcase,
        mode: "write",
        q: "اكتب لي عقد عمل مؤقت متوافقًا مع نظام العمل",
        source: "نظام العمل",
        url: "https://laws.boe.gov.sa/boelaws/laws/lawdetails/08381293-6388-48e2-8ad2-a9a700f2aa94/1",
      },
    ],
    basis:
      "يغطّي قطاعات الصحة والبناء والمالية والموارد البشرية وغيرها — اعتمادات وتراخيص واشتراطات وصياغة مستندات، كلٌّ بمصدره الرسمي.",
  },
  {
    id: "founders",
    icon: Rocket,
    title: "رواد الأعمال والمستثمرون",
    tagline:
      "من تأسيس الشركة والامتياز التجاري إلى الاستثمار الأجنبي والأسواق المالية — اعرف نظامك قبل أن تبدأ.",
    examples: [
      {
        q: "شروط قيد عقد الامتياز التجاري والتزامات مانح الامتياز قبل التعاقد",
        source: "خدمة قيد الامتياز التجاري — وزارة التجارة",
        url: "https://my.gov.sa/ar/services/18785",
      },
      {
        q: "الفرق بين الشركة ذات المسؤولية المحدودة والمساهمة المبسطة وأيّهما أنسب؟",
        source: "خدمة تأسيس شركة ذات مسؤولية محدودة — وزارة التجارة",
        url: "https://my.gov.sa/ar/services/18740",
      },
      {
        q: "كيف أسجّل علامتي التجارية وأحميها من التقليد؟",
        source: "خدمة تسجيل علامة تجارية — الهيئة السعودية للملكية الفكرية",
        url: "https://my.gov.sa/ar/services/245210",
      },
      {
        q: "ضوابط استثمار الأجنبي غير المقيم في الأسهم المدرجة بالسوق الرئيسية",
        source: "الأطر التنظيمية للمستثمرين الأجانب غير المقيمين — هيئة السوق المالية",
        url: "https://istitlaa.ncc.gov.sa/ar/Trade/CMA/NonResidentForeignInvestors",
      },
      {
        q: "مزايا وقواعد تأسيس شركة داخل المناطق الاقتصادية الخاصة",
        source: "قواعد الشركات في المناطق الاقتصادية الخاصة — هيئة المدن والمناطق الاقتصادية الخاصة",
        url: "https://istitlaa.ncc.gov.sa/ar/Energy/ecza/SEZcompanies",
      },
      {
        q: "كيف أكون في النطاق الأخضر من حيث توظيف السعوديين؟",
        source: "الدليل الإجرائي لبرنامج نطاقات — وزارة الموارد البشرية والتنمية الاجتماعية",
        url: "https://www.hrsd.gov.sa/sites/default/files/2023-06/20210523.pdf",
      },
    ],
    basis:
      "يستند إلى أنظمة الشركات والامتياز والتجارة الإلكترونية والملكية الفكرية ولوائح هيئة السوق المالية وبرامج التوطين.",
  },
  {
    id: "individuals",
    icon: Users,
    title: "الأفراد",
    tagline:
      "حقوقك اليومية — عملك وميراثك وتأمينك وتعاملاتك البنكية — بإجابةٍ واضحة موثّقة برابط الخدمة الرسمية.",
    examples: [
      {
        tag: "عمل",
        q: "فُصلت من عملي، فهل أستحق إعانة التأمين ضد التعطل (ساند)؟",
        source: "خدمة التحقق من أهلية ساند — المؤسسة العامة للتأمينات الاجتماعية",
        url: "https://my.gov.sa/ar/services/23513",
      },
      {
        tag: "تأمين",
        q: "شركة التأمين قدّرت أضرار سيارتي بأقل من قيمتها، كيف أعترض؟",
        source: "قواعد عمل معايني ومقدّري الخسائر التأمينية — البنك المركزي",
        url: "https://istitlaa.ncc.gov.sa/ar/Finance/SAMA/LossAdjusters",
      },
      {
        tag: "بنوك",
        q: "لدي نزاع مع البنك حول قرض التمويل الاستهلاكي، أين أرفع شكواي؟",
        source: "الصيغة النموذجية لعقد التمويل الاستهلاكي للأفراد — البنك المركزي",
        url: "https://istitlaa.ncc.gov.sa/ar/Finance/SAMA/FinanceContract",
      },
      {
        tag: "إيجار",
        q: "بيني وبين المؤجّر خلاف على عقد الإيجار السكني، كيف أحلّه؟",
        source: "خدمة التحكيم العقاري في منازعات إيجار — الهيئة العامة للعقار",
        url: "https://my.gov.sa/ar/services/572191",
      },
    ],
    basis:
      "نظام العمل والأحوال الشخصية ولوائح التأمين والتعاملات المصرفية والأحكام القضائية المنشورة.",
  },
];

// ---------------------------------------------------------------------------
// Sector breadth band — the 38 sectors that prove "covers every sector".
// reg_count = number of regulations_v2 rows tagged with that sector (live).
// ---------------------------------------------------------------------------

export interface SectorStat {
  name: string;
  count: number;
}

export const SECTORS_INTRO = {
  title: "يغطّي ريحان 38 قطاعاً نظامياً",
  subtitle:
    "من المواصفات والمعاملات التجارية إلى الصحة والمالية والعمل والبلديات — أكثر من 3,000 نظام ولائحة موزّعة على كل قطاعات المملكة.",
} as const;

/** Ordered by regulation count (descending) — the full sector landscape. */
export const SECTORS: SectorStat[] = [
  { name: "المواصفات والمقاييس", count: 695 },
  { name: "المعاملات التجارية", count: 693 },
  { name: "الصحة", count: 526 },
  { name: "المالية والضرائب", count: 505 },
  { name: "الأمن الغذائي", count: 406 },
  { name: "العمل والتوظيف", count: 402 },
  { name: "النقل", count: 390 },
  { name: "الحوكمة", count: 383 },
  { name: "حوكمة الشركات والاستثمار", count: 369 },
  { name: "تقنية المعلومات والأمن السيبراني", count: 357 },
  { name: "البلديات والتخطيط العمراني", count: 353 },
  { name: "المياه والبيئة", count: 312 },
  { name: "القضاء والمحاكم", count: 252 },
  { name: "التعليم", count: 240 },
  { name: "الأمن والدفاع", count: 239 },
  { name: "الصناعة والتعدين", count: 237 },
  { name: "الزراعة", count: 219 },
  { name: "المهن المرخصة", count: 212 },
  { name: "الجنايات والجرائم", count: 198 },
  { name: "التنمية الاجتماعية", count: 181 },
  { name: "الجمارك والتجارة الدولية", count: 164 },
  { name: "السياحة والترفيه", count: 157 },
  { name: "الرقابة", count: 156 },
  { name: "العقار", count: 154 },
  { name: "الثقافة والإعلام", count: 143 },
  { name: "الطاقة", count: 140 },
  { name: "التأمين", count: 123 },
  { name: "الإسكان", count: 120 },
  { name: "البحث والابتكار", count: 116 },
  { name: "المنظمات غير الربحية", count: 92 },
  { name: "الاتصالات والفضاء", count: 91 },
  { name: "الشؤون الخارجية", count: 77 },
  { name: "الملكية الفكرية", count: 67 },
  { name: "الشؤون الإسلامية والأوقاف", count: 50 },
  { name: "حقوق الإنسان", count: 47 },
  { name: "الحج والعمرة", count: 41 },
  { name: "الرياضة", count: 41 },
  { name: "التعاملات والأحوال المدنية", count: 23 },
];

// ---------------------------------------------------------------------------
// Homepage teaser — compact block on `/` that links to this page.
// ---------------------------------------------------------------------------

export const AUDIENCES_TEASER = {
  eyebrow: "ريحان يستهدف مين؟",
  title: "ليس للمحامين وحدهم",
  subtitle:
    "قاعدة ريحان تغطّي كل قطاعات الأنظمة السعودية — للمحامي والمختص ورائد الأعمال والفرد.",
  cta: "اكتشف من يستفيد من ريحان",
  href: "/audiences",
} as const;

/** Compact persona pills for the teaser — icon + label only. */
export const TEASER_PERSONAS: { icon: LucideIcon; label: string }[] = [
  { icon: Gavel, label: "المحامون" },
  { icon: BadgeCheck, label: "المختصون" },
  { icon: Rocket, label: "رواد الأعمال والمستثمرون" },
  { icon: Users, label: "الأفراد" },
];

// Re-export for any consumer that wants the brand scale icon.
export { Scale };
