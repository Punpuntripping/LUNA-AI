/**
 * Single source of truth for the public landing page (`/`) copy + numbers.
 *
 * Keep every marketing claim and headline string here so the page stays easy
 * to tweak in one place. The corpus counts below are the live database floors
 * (regulations_v2 / cases / services) as of 2026-06-29 — phrased as "أكثر من"
 * so they stay honest as the corpus grows. Round them up here if you ever
 * want bolder numbers; nothing downstream hard-codes them.
 */
import {
  Scale,
  Gavel,
  Building2,
  PenLine,
  ShieldCheck,
  Clock,
  Sparkles,
  Landmark,
  type LucideIcon,
} from "lucide-react";

/** Where the primary "ابدأ الآن" CTA sends prospects. Signup lives on /login. */
export const PRIMARY_CTA_HREF = "/login";

/** Support inbox used for early-access / activation-code requests. */
export const SUPPORT_EMAIL = "support@rayhanai.com";

// ---------------------------------------------------------------------------
// Hero
// ---------------------------------------------------------------------------

export const HERO = {
  badge: "منصة سعودية · إطلاق تجريبي",
  // Split so the differentiator clause renders in the brand color.
  titleLead: "من سؤالك إلى تقرير قانوني كامل،",
  titleEmphasis: "موثّق بمصادره الرسمية",
  subtitle:
    "ريحان يبحث في الأنظمة السعودية والأحكام القضائية والخدمات الحكومية، ويعطيك إجابة مكتملة — كل معلومة فيها مربوطة بمصدرها الرسمي ورابطه المباشر.",
  primaryCta: "ابدأ الآن",
  secondaryCta: "شاهد كيف يعمل",
} as const;

/** Compact data-moat strip shown under the hero CTAs — front-loads credibility
 *  so the corpus scale is visible above the fold. */
export const HERO_TRUST: { value: string; label: string }[] = [
  { value: "+3,000", label: "نظام ولائحة ودليل" },
  { value: "+20,000", label: "حكم قضائي" },
  { value: "+4,500", label: "خدمة حكومية" },
];

// ---------------------------------------------------------------------------
// Problem
// ---------------------------------------------------------------------------

export interface ProblemCard {
  icon: LucideIcon;
  title: string;
  body: string;
}

export const PROBLEMS: ProblemCard[] = [
  {
    icon: Clock,
    title: "البحث والصياغة يستهلكان الوقت",
    body: "تُصرف ساعاتٌ يومياً في البحث القانوني وصياغة الدعاوى والمذكرات؛ عبءٌ مكتبيٌّ يثقل يوم المحامي.",
  },
  {
    icon: Sparkles,
    title: "الأدوات العامة لا تكفي",
    body: "لا توفّر أدوات الذكاء الاصطناعي العامة الدقة التي يتطلّبها العمل القانوني، وقد تستند إلى مصادر وأنظمة غير صحيحة.",
  },
  {
    icon: Landmark,
    title: "الإجراءات الحكومية مُعقّدة",
    body: "تحديد الإجراء الحكومي الصحيح بخطواته ومتطلّباته ومصادره الرسمية يستغرق وقتاً ثميناً من المحامي.",
  },
];

// ---------------------------------------------------------------------------
// Capabilities (verb-led grid)
// ---------------------------------------------------------------------------

export interface Capability {
  icon: LucideIcon;
  title: string;
  body: string;
}

export const CAPABILITIES: Capability[] = [
  {
    icon: Scale,
    title: "بحث موثّق",
    body: "بحث في الأنظمة السعودية والأحكام القضائية بإجابة مكتملة، كل استشهاد فيها مربوط بمصدره الرسمي.",
  },
  {
    icon: PenLine,
    title: "صياغة المستندات",
    body: "صياغة الدعاوى والمذكرات والعقود بلغة قانونية دقيقة، مبنية على ما يخص قضيتك.",
  },
  {
    icon: Building2,
    title: "الإجراءات الحكومية",
    body: "خطوات الخدمة الحكومية ومتطلباتها ومستنداتها وروابطها على المنصة الوطنية، جاهزة أمامك.",
  },
  {
    icon: ShieldCheck,
    title: "الامتثال للنظام",
    body: "تغطية واسعة لبيانات الامتثال للأنظمة السعودية تساعدك على الالتزام بثقة.",
  },
];

// ---------------------------------------------------------------------------
// Data-moat stats band
// ---------------------------------------------------------------------------

export interface Stat {
  value: string;
  label: string;
  /** Optional secondary line — e.g. the entities a source class comes from. */
  hint?: string;
}

export const STATS: Stat[] = [
  { value: "+3,000", label: "نظام ولائحة ودليل" },
  {
    value: "+1,000",
    label: "تعميم رسمي",
    hint: "وزارة العدل · هيئة الغذاء والدواء · البنك المركزي",
  },
  { value: "+20,000", label: "قضية وحكم قضائي" },
  { value: "+4,500", label: "خدمة حكومية" },
  { value: "+200", label: "كيان حكومي", hint: "مصادر مجمّعة" },
  { value: "+95٪", label: "تغطية بيانات الامتثال" },
];

// ---------------------------------------------------------------------------
// Search-WI showcase — a REAL Rayhan output (blog share c6f6b05f…).
// The conclusion excerpt + citations are taken verbatim from a real answer so
// the showcase reflects the actual product, not a mock. This example cites 16
// sources across regulations AND government services.
// ---------------------------------------------------------------------------

export const SHOWCASE = {
  eyebrow: "ما الذي يميّز ريحان",
  title: "بحثٌ يُظهر مصادره",
  subtitle:
    "كل تقرير يعطيك إجابة مكتملة، وكل استشهاد فيها مربوط بمصدره الرسمي ورابطه المباشر — من الأنظمة، والأحكام القضائية، والخدمات الحكومية.",
  exampleTag: "مثال حقيقي من ريحان",
  question:
    "كيف أقدر آخذ حقوقي من الشركة بعد فسخ العقد، وقد مضى على الفسخ أكثر من شهر؟",
  answerLead:
    "بعد فسخ العقد — ولا سيّما عقد العمل — يستحق الطرف المتضرر مجموعة من الحقوق المالية والإجرائية التي حدّدها النظام، ولا يُسقِط مرور أكثر من شهر على الفسخ هذه الحقوق؛ بل يصبح الطرف المخلّ ملزماً بتصفيتها والتعويض عن التأخير.",
  answerBody:
    "ومن أبرز هذه الحقوق مكافأة نهاية الخدمة: تُحسب على أساس أجر نصف شهر عن كل سنة من السنوات الخمس الأولى، وأجر شهر عن كل سنة من السنوات التالية، ويُتّخذ الأجر الأخير أساساً لحسابها.",
  citationN: 1,
} as const;

/** The three source types every search report can cite, each with the kind of
 *  official link its card carries. Mirrors ReferencePanel's DOMAIN_META. */
export interface SourceType {
  icon: LucideIcon;
  label: string;
  linkLabel: string;
  tint: string;
}

export const SOURCE_TYPES: SourceType[] = [
  {
    icon: Scale,
    label: "نظام",
    linkLabel: "رابط النظام الرسمي",
    tint: "text-sky-600 dark:text-sky-400",
  },
  {
    icon: Gavel,
    label: "قضية",
    linkLabel: "تفاصيل الحكم القضائي",
    tint: "text-amber-600 dark:text-amber-400",
  },
  {
    icon: Building2,
    label: "خدمة حكومية",
    linkLabel: "رابط المنصة الوطنية",
    tint: "text-emerald-600 dark:text-emerald-400",
  },
];

/** Total sources the real answer cited — drives the "المراجع (16)" count. */
export const SHOWCASE_TOTAL_REFS = 16;

export interface ShowcaseCitation {
  n: number;
  label: string;
  // A serializable domain key (NOT an icon component) — this object crosses the
  // server→client boundary, and RSC can't serialize a function/component.
  domain: "regulations" | "cases" | "compliance";
  tint: string;
  title: string;
  /** Owning gov entity — shown for خدمة حكومية citations. */
  provider?: string;
  snippet: string;
  url: string;
  /** Full verbatim source text — when present, «عرض المصدر» opens it in a
   *  dialog (a live demo of the in-app source viewer). */
  sourceMd?: string;
}

/** Verbatim source text behind citation [1] — نظام العمل, مكافأة نهاية الخدمة
 *  (المواد ٨٤–٨٨). Exactly what the in-app «عرض المصدر» shows. */
const SOURCE_LABOR_LAW_EOS = `# الفصل الرابع

## مكافأة نهاية الخدمة

### المادة الرابعة والثمانون:

إذا انتهت علاقة العمل وجب على صاحب العمل أن يدفع إلى العامل مكافأة عن مدة خدمته تحسب على أساس أجر نصف شهر عن كل سنة من السنوات الخمس الأولى، وأجر شهر عن كل سنة من السنوات التالية، ويتخذ الأجر الأخير أساساً لحساب المكافأة، ويستحق العامل مكافأة عن أجزاء السنة بنسبة ما قضاه منها في العمل.

### المادة الخامسة والثمانون:

إذا كان انتهاء علاقة العمل بسبب استقالة العامل يستحق في هذه الحالة ثلث المكافأة بعد خدمة لا تقل مدتها عن سنتين متتاليتين، ولا تزيد على خمس سنوات، ويستحق ثلثيها إذا زادت مدة خدمته على خمس سنوات متتالية ولم تبلغ عشر سنوات ويستحق المكافأة كاملة إذا بلغت مدة خدمته عشر سنوات فأكثر.

### المادة السادسة والثمانون:

استثناء من حكم المادة (الثامنة) من هذا النظام، يجوز الاتفاق على ألا تحسب في الأجر الذي تُسوى على أساسه مكافأة نهاية الخدمة جميع مبالغ العمولات أو بعضها والنسب المئوية عن ثمن المبيعات وما أشبه ذلك من عناصر الأجر الذي يدفع إلى العامل وتكون قابلة بطبيعتها للزيادة والنقص.

### المادة السابعة والثمانون:

استثناء مما ورد في المادة (الخامسة والثمانين) من هذا النظام تستحق المكافأة كاملة في حالة ترك العامل العمل نتيجة لقوة قاهرة خارجة عن إرادته، كما تستحقها العاملة إذا أنهت العقد خلال ستة أشهر من تاريخ عقد زواجها أو ثلاثة أشهر من تاريخ وضعها.

### المادة الثامنة والثمانون:

إذا انتهت خدمة العامل وجب على صاحب العمل دفع أجره وتصفية حقوقه خلال أسبوع - على الأكثر - من تاريخ انتهاء العلاقة العقدية. أما إذا كان العامل هو الذي أنهى العقد، وجب على صاحب العمل تصفية حقوقه كاملة خلال مدة لا تزيد على أسبوعين. ولصاحب العمل أن يحسم أي دين مستحق له بسبب العمل من المبالغ المستحقة للعامل.

يقع هذا المقطع ضمن الباب الخامس (علاقات العمل) بعد الفصل الثالث الذي نظم حالات انتهاء العقد. وهو يحدد الحقوق المالية المترتبة على انتهاء العلاقة العمالية، مكملاً لأحكام الفصل السابق بتفصيل مكافأة نهاية الخدمة وشروط استحقاقها.`;

/** A representative slice of the real example's 16 citations — one نظام + two
 *  خدمة حكومية, each with its verbatim official link. */
export const SHOWCASE_CITATIONS: ShowcaseCitation[] = [
  {
    n: 1,
    label: "نظام",
    domain: "regulations",
    tint: "text-sky-600 dark:text-sky-400",
    title: "نظام العمل",
    snippet:
      "مكافأة نهاية الخدمة: أجر نصف شهر عن كل سنة من السنوات الخمس الأولى، وأجر شهر عن كل سنة تالية، على أساس الأجر الأخير.",
    url: "https://laws.boe.gov.sa/boelaws/laws/lawdetails/08381293-6388-48e2-8ad2-a9a700f2aa94/1",
    sourceMd: SOURCE_LABOR_LAW_EOS,
  },
  {
    n: 16,
    label: "خدمة حكومية",
    domain: "compliance",
    tint: "text-emerald-600 dark:text-emerald-400",
    title: "إنهاء العلاقة التعاقدية",
    provider: "وزارة الموارد البشرية والتنمية الاجتماعية",
    snippet:
      "الخدمة الرسمية لإنهاء العلاقة التعاقدية بين صاحب العمل والعامل وإجراءاتها.",
    url: "https://hrsd.gov.sa/node/5573760",
  },
  {
    n: 19,
    label: "خدمة حكومية",
    domain: "compliance",
    tint: "text-emerald-600 dark:text-emerald-400",
    title: "الحاسبة العمالية",
    provider: "وزارة العدل",
    snippet:
      "حاسبة رسمية لاحتساب مستحقات العامل ومكافأة نهاية الخدمة بدقة.",
    url: "https://www.moj.gov.sa/ar/eServices/Pages/ServiceDetailsNew.aspx?itemId=299",
  },
];
