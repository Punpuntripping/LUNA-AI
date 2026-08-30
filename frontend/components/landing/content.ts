/**
 * Single source of truth for the public landing page (`/`) copy + numbers.
 *
 * Keep every marketing claim and headline string here so the page stays easy
 * to tweak in one place. The corpus counts below are the live database floors
 * (regulations_v2 / cases / circulars) as of 2026-06-29 — phrased as "أكثر من"
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
  Library,
  ScanText,
  type LucideIcon,
} from "lucide-react";

/** Where the primary "ابدأ الآن" CTA sends prospects. Signup lives on /login. */
export const PRIMARY_CTA_HREF = "/login";

/** Support inbox used for early-access / activation-code requests. */
export const SUPPORT_EMAIL = "support@rayhanai.com";

/**
 * Support line — **WhatsApp only**, it is not a callable phone number. Every
 * surface that renders it must carry the «واتساب فقط» qualifier below so nobody
 * tries to dial it, and must wrap the digits in `dir="ltr"` so the leading «+»
 * stays on the left inside our RTL shell. Latin digits only (app-wide policy).
 */
export const SUPPORT_WHATSAPP = "+966552517086";

/** Qualifier rendered next to the number — never show the number without it. */
export const SUPPORT_WHATSAPP_NOTE = "واتساب فقط";

/** wa.me wants the number bare: no «+», no spaces, no dashes. */
export const SUPPORT_WHATSAPP_HREF = "https://wa.me/966552517086";

// ---------------------------------------------------------------------------
// Hero
// ---------------------------------------------------------------------------

export const HERO = {
  badge: "منصة سعودية · إطلاق تجريبي",
  // Split so the differentiator clause renders in the brand color.
  titleLead: "من سؤالك إلى تقرير قانوني كامل،",
  titleEmphasis: "موثّق بمصادره الرسمية",
  subtitle:
    "ريحان يبحث في الأنظمة السعودية والأحكام القضائية، ويعطيك إجابة مكتملة — كل معلومة فيها مربوطة بمصدرها الرسمي ورابطه المباشر.",
  primaryCta: "ابدأ الآن",
  secondaryCta: "شاهد كيف يعمل",
} as const;

/** Compact data-moat strip shown under the hero CTAs — front-loads credibility
 *  so the corpus scale is visible above the fold. */
export const HERO_TRUST: { value: string; label: string }[] = [
  { value: "+3,000", label: "نظام ولائحة ودليل" },
  { value: "+20,000", label: "حكم قضائي" },
  { value: "+1,000", label: "تعميم رسمي" },
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
];

// ---------------------------------------------------------------------------
// Comparison — Rayhan vs. general-purpose AI (ChatGPT & friends)
//
// Head-to-head table. Tone stays gain-framed: the "others" column is neutral,
// not fear-mongering — general tools are fine for general questions, they just
// weren't built for Saudi legal work. Each row is one dimension the lawyer feels.
// ---------------------------------------------------------------------------

export const COMPARISON_HEADER = {
  title: "ريحان مقابل الأدوات العامة",
  subtitle:
    "أدوات الذكاء الاصطناعي العامة مفيدة للأسئلة اليومية، لكنها لم تُصمَّم للعمل القانوني السعودي. هذا هو الفرق حين تكون الدقة والمصدر أساس عملك.",
  rayhanLabel: "ريحان",
  rayhanHint: "مساعد قانوني سعودي متخصص",
  othersLabel: "الأدوات العامة",
  othersHint: "ChatGPT وأمثالها",
} as const;

export interface ComparisonRow {
  icon: LucideIcon;
  dimension: string;
  /** Rayhan's answer — the ✓ column. */
  rayhan: string;
  /** General-purpose tools — the ✗ column. */
  others: string;
}

export const COMPARISON: ComparisonRow[] = [
  {
    icon: ShieldCheck,
    dimension: "دقّة المصادر",
    rayhan:
      "كل معلومة ورقم مربوطان بمصدرهما الرسمي ورابطه المباشر — بلا هلوسة.",
    others: "قد يستشهد بأنظمة أو أرقام غير حقيقية يصعب التحقّق منها.",
  },
  {
    icon: Library,
    dimension: "تغطية الأنظمة السعودية",
    rayhan:
      "أكثر من 3,000 نظام ولائحة، و20,000 حكم قضائي، و1,000 تعميم رسمي.",
    others: "يُلمّ بالأنظمة الشهيرة فقط كنظام العمل، وتغيب عنه بقية المصادر.",
  },
  {
    icon: PenLine,
    dimension: "التخصّص لعمل المحامي",
    rayhan:
      "وكيل بحث ووكيل صياغة متخصّصان، مع إمكانية إضافة قوالبك الخاصة.",
    others: "أداة عامة لا تستهدف احتياجات المحامي ولا سير عمله.",
  },
  {
    icon: ScanText,
    dimension: "استخراج بيانات المستندات",
    rayhan:
      "يستخرج الأسماء والأرقام من مستنداتك بدقة تصل إلى 99٪ للملفات الواضحة (OCR).",
    others: "لا يستخرج الأسماء والأرقام من المستندات بدقة.",
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
  { value: "+200", label: "كيان حكومي", hint: "مصادر مجمّعة" },
];

// ---------------------------------------------------------------------------
// Search-WI showcase — a REAL Rayhan output (blog share c6f6b05f…).
// The conclusion excerpt + citations are taken verbatim from a real answer so
// the showcase reflects the actual product, not a mock. This example cites 16
// sources across regulations AND government services.
//
// ⚠ THE خدمة حكومية CARDS BELONG HERE, AND THEY SURVIVED THE 2026-08-03 RETIREMENT
// OF THE COMPLIANCE WING. They were briefly removed with it and put back the same
// day, on purpose: a citation card is a NAME, A PROVIDER, ONE LINE AND THE
// ENTITY'S OWN LINK — it never restates الشروط / المستندات / الخطوات, which is the
// only thing the retirement was about. That makes this block the honest picture of
// what ريحان still does with a service: cite it and hand you its official page.
// Do not "tidy" them away again, and do not let a card here grow a body.
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

/** The source types every search report can cite, each with the kind of official
 *  link its card carries. Mirrors ReferencePanel's DOMAIN_META. */
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
    // ⚠ NOT «رابط المنصة الوطنية». The portal link was removed from the product
    // on 2026-08-03 — `service_url`, the entity's own page, is the only exit a
    // service citation offers now, so this label names that and nothing else.
    linkLabel: "رابط الخدمة الرسمي",
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
 *  (المواد 84–88). Exactly what the in-app «عرض المصدر» shows. */
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

إذا انتهت خدمة العامل وجب على صاحب العمل دفع أجره وتصفية حقوقه خلال أسبوع - على الأكثر - من تاريخ انتهاء العلاقة العقدية. أما إذا كان العامل هو الذي أنهى العقد، وجب على صاحب العمل تصفية حقوقه كاملة خلال مدة لا تزيد على أسبوعين. ولصاحب العمل أن يحسم أي دين مستحق له بسبب العمل من المبالغ المستحقة للعامل.`;

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
  // No `sourceMd` on either service card, and that is the point: «عرض المصدر»
  // renders only when one is present, so a service card offers exactly one
  // action — «فتح المصدر الرسمي», out to the issuing entity.
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
