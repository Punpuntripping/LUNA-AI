import Link from "next/link";
import {
  ArrowLeft,
  BookOpenCheck,
  BrainCircuit,
  Check,
  HelpCircle,
  PenLine,
  Radar,
  Scale,
  ShieldCheck,
  TrendingUp,
} from "lucide-react";
import { HERO_TRUST } from "@/components/landing/content";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * «ريحان للقانونيين» — the objection-handling page in the «عن ريحان» menu.
 * Where /vs-chatgpt answers *why not a general tool?*, this one answers the
 * question that comes before it: *should I let an AI near my practice at all?*
 *
 * Structure is the owner's brief. Three fears, then the first one — «هل يأخذ
 * ريحان وظيفتي؟» — gets the whole page; the other two get a short honest answer
 * and a link to the document that actually binds us. Every data claim here is a
 * RESTATEMENT of /privacy or /masking (same hard rule as the /learn lessons):
 * change the legal page first and this follows.
 *
 * The regulation titles in the gap cards are copied verbatim out of
 * `regulations_v2` and are all `in_force` — see the substitution note in
 * `.claude/plans/for_lawyers_page.md` before editing them.
 */

// ---------------------------------------------------------------------------
// The two short-answer fears
// ---------------------------------------------------------------------------

interface Fear {
  icon: typeof ShieldCheck;
  title: string;
  body: string;
  links: { label: string; href: string }[];
}

const SHORT_FEARS: Fear[] = [
  {
    icon: BrainCircuit,
    title: "هل يأخذ ريحان معرفتي ويستخدمها؟",
    body: "لا نستخدم محتواك المُدخَل لتدريب نماذج ذكاء اصطناعي عامة أو لمصلحة الغير، ولا نُتيحه لهذا الغرض دون موافقتك الصريحة. قوالبك ومذكراتك وأسلوبك في الصياغة تبقى في حسابك وحده، ولا يُرسَل إلى مزوّدي النماذج إلا ما يلزم لإنتاج ما طلبته أنت.",
    links: [{ label: "كيف نحمي بياناتك", href: "/learn/data-protection" }],
  },
  {
    icon: ShieldCheck,
    title: "هل يستخدم بيانات عملائي؟",
    body: "بيانات عملائك لا تغادر خوادمنا إلا للمعالجة اللازمة. و«وضع السرية» مفعّل افتراضياً: يستبدل أرقام الهوية والجوال والآيبان والبريد بأرقام بديلة قبل أي معالجة خارجية، ثم يعيد الأصل في إجابتك.",
    links: [
      { label: "الخصوصية والسياسة العامة", href: "/privacy" },
      { label: "تقنيع المعرّفات", href: "/masking" },
    ],
  },
];

// ---------------------------------------------------------------------------
// Pillar 1 — the researched numbers. Global studies, each attributed inline.
// ---------------------------------------------------------------------------

const RESEARCH: { value: string; label: string; source: string }[] = [
  {
    value: "40–60%",
    label: "من وقت المحامي يذهب إلى الصياغة ومراجعة العقود",
    source: "Thomson Reuters",
  },
  {
    value: "17%",
    label: "من يوم المحامي في البحث القانوني وحده",
    source: "ABA — نقابة المحامين الأمريكية",
  },
  {
    value: "2.9 / 8",
    label: "ساعات قابلة للفوترة من يوم عمل من ثماني ساعات",
    source: "Clio Legal Trends",
  },
  {
    value: "200",
    label: "ساعة يمكن للذكاء الاصطناعي أن يوفّرها سنوياً لكل مختصّ",
    source: "Thomson Reuters",
  },
];

// ---------------------------------------------------------------------------
// Pillar 3 — the coverage gap. Left column is what a Saudi practitioner knows
// cold; right is a live نظام in the library they have likely never opened.
// ---------------------------------------------------------------------------

const GAPS: { known: string; unknown: string; field: string }[] = [
  {
    known: "نظام الأحوال الشخصية",
    unknown: "نظام المواد الهيدروكربونية",
    field: "الطاقة",
  },
  {
    known: "نظام الإثبات",
    unknown: "كود البناء السعودي العام",
    field: "الإسكان والبناء",
  },
  {
    known: "نظام العمل",
    unknown: "نظام الاستثمار التعديني",
    field: "الصناعة والتعدين",
  },
];

// ---------------------------------------------------------------------------

interface Pillar {
  icon: typeof PenLine;
  eyebrow: string;
  title: string;
  body: string;
}

const PILLARS: Pillar[] = [
  {
    icon: Radar,
    eyebrow: "الإلمام الشامل",
    title: "ترى القضية كاملة — بما فيها ما لم تفكّر أن تسأل عنه",
    body: "وكيل البحث يوسّع سؤالك إلى عدة زوايا، ثم يغوص في الأنظمة واللوائح وأكثر من 30,000 حكم قضائي، وينتقي ما يخدم مسألتك ويعيد تقريراً مرقّم المراجع. فتظهر لك الاحتمالات التي تفوت العين المستعجلة: نصّ عُدِّل حديثاً، أو حكم سابق مشابه، أو التزام إجرائي في قطاع لست خبيراً فيه — قبل أن يظهر في مذكّرة الخصم.",
  },
  {
    icon: TrendingUp,
    eyebrow: "توسيع قاعدة العملاء",
    title: "الوقت الذي توفّره يعود إليك — والمجالات التي كنت ترفضها تُصبح مفتوحة",
    body: "كل ساعة لا تقضيها في المسودّة الأولى هي ساعة لموكّل جديد، أو لعرض تقدّمه، أو لقضية إضافية تستطيع قبولها. والأهم: لم تعد مضطراً لردّ قضية لأنها خارج تخصّصك.",
  },
];

export function ForLawyersView() {
  return (
    <main>
      {/* ------------------------------------------------------------------ */}
      {/* Hero                                                               */}
      {/* ------------------------------------------------------------------ */}
      <section className="border-b border-border/60 bg-muted/20">
        <div className="mx-auto max-w-3xl px-4 py-16 text-center sm:py-20">
          <span className="inline-flex items-center rounded-full border border-primary/30 bg-primary/5 px-3 py-1 text-xs font-medium text-primary">
            ريحان للقانونيين
          </span>
          <h1 className="mt-5 text-3xl font-bold leading-tight tracking-tight text-foreground sm:text-4xl">
            الذكاء الاصطناعي لن يحلّ محلّك — لكنه يغيّر حجم ما تستطيع إنجازه
          </h1>
          <p className="mx-auto mt-4 max-w-2xl text-base leading-relaxed text-muted-foreground">
            قبل أن يجرّب أي محامٍ سعودي ريحان، تدور في ذهنه ثلاثة أسئلة. نجيب
            عليها هنا بصراحة — ثم نشرح بالتفصيل ما الذي يتغيّر فعلاً في يوم عملك.
          </p>

          <div className="mx-auto mt-8 flex max-w-lg flex-wrap items-center justify-center gap-x-8 gap-y-3">
            {HERO_TRUST.map((stat) => (
              <div key={stat.label} className="text-center">
                <div className="text-2xl font-bold tabular-nums text-foreground">
                  {stat.value}
                </div>
                <div className="text-xs text-muted-foreground">{stat.label}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ------------------------------------------------------------------ */}
      {/* The three fears                                                    */}
      {/* ------------------------------------------------------------------ */}
      <section className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
            ثلاثة أسئلة مشروعة
          </h2>
          <p className="mt-3 text-base leading-relaxed text-muted-foreground">
            لا نعتبرها تشكيكاً. من يحمل أمانة موكّل يسأل قبل أن يثق.
          </p>
        </div>

        {/* Fear 1 — the one this whole page exists to answer */}
        <div className="mt-10 rounded-2xl border border-primary/30 bg-primary/[0.03] p-6 shadow-sm sm:p-8">
          <div className="flex items-start gap-4">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <Scale className="h-5 w-5" />
            </div>
            <div>
              <h3 className="text-lg font-bold text-foreground sm:text-xl">
                هل يأخذ ريحان وظيفتي؟
              </h3>
              <p className="mt-3 text-base leading-relaxed text-muted-foreground">
                لا. ريحان لا يترافع أمام دائرة، ولا يوقّع مذكّرة، ولا يتحمّل
                مسؤولية مهنية أمام موكّل أو محكمة — وهذه هي المهنة. ما يتولّاه
                ريحان هو الجزء الذي يبتلع وقتك دون أن يميّزك عن غيرك: البحث في
                الأنظمة، وتجميع الأحكام، وكتابة المسودّة الأولى.
              </p>
              <p className="mt-3 text-base leading-relaxed text-muted-foreground">
                الرأي يبقى رأيك، والتوقيع توقيعك، والمسؤولية مسؤوليتك. ما يتغيّر
                ليس مكانك — بل عدد القضايا التي تستطيع قبولها، وعمق ما تبني عليه
                مذكّرتك.
              </p>
              <a
                href="#impact"
                className="mt-5 inline-flex items-center gap-1.5 text-sm font-semibold text-primary hover:underline"
              >
                ما الذي يتغيّر في يومك فعلاً؟
                <ArrowLeft className="h-4 w-4" />
              </a>
            </div>
          </div>
        </div>

        {/* Fears 2 and 3 — short answers, each pointing at the binding document */}
        <div className="mt-5 grid gap-5 sm:grid-cols-2">
          {SHORT_FEARS.map((fear) => {
            const Icon = fear.icon;
            return (
              <div
                key={fear.title}
                className="flex flex-col rounded-2xl border border-border bg-card p-6 shadow-sm"
              >
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                  <Icon className="h-5 w-5" />
                </div>
                <h3 className="mt-4 text-base font-bold text-foreground">
                  {fear.title}
                </h3>
                <p className="mt-2 flex-1 text-sm leading-relaxed text-muted-foreground">
                  {fear.body}
                </p>
                <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2">
                  {fear.links.map((link) => (
                    <Link
                      key={link.href}
                      href={link.href}
                      className="inline-flex items-center gap-1.5 text-sm font-semibold text-primary hover:underline"
                    >
                      {link.label}
                      <ArrowLeft className="h-3.5 w-3.5" />
                    </Link>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* ------------------------------------------------------------------ */}
      {/* Pillar 1 — drafting time, carried by the research numbers          */}
      {/* ------------------------------------------------------------------ */}
      <section
        id="impact"
        className="scroll-mt-20 border-y border-border bg-muted/20"
      >
        <div className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
          <div className="mx-auto max-w-2xl text-center">
            <span className="text-xs font-semibold text-primary">
              تقليل وقت الصياغة
            </span>
            <h2 className="mt-2 text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
              أكبر بند في يوم المحامي ليس الترافع — بل الصياغة والبحث
            </h2>
            <p className="mt-3 text-base leading-relaxed text-muted-foreground">
              هذا ليس انطباعاً. المهنة قِيست في أسواق أخرى، والنتيجة متكرّرة.
            </p>
          </div>

          <div className="mt-10 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
            {RESEARCH.map((stat) => (
              <div
                key={stat.label}
                className="flex flex-col rounded-2xl border border-border bg-card p-6 shadow-sm"
              >
                <div className="text-3xl font-bold tabular-nums text-primary">
                  {stat.value}
                </div>
                <p className="mt-3 flex-1 text-sm leading-relaxed text-foreground">
                  {stat.label}
                </p>
                <p className="mt-3 border-t border-border/60 pt-3 text-xs text-muted-foreground">
                  {stat.source}
                </p>
              </div>
            ))}
          </div>

          <p className="mx-auto mt-6 max-w-3xl text-center text-xs leading-relaxed text-muted-foreground">
            أرقام من دراسات أمريكية وعالمية على مهنة المحاماة؛ لا توجد بعد دراسة
            مكافئة منشورة على السوق السعودي. لكن بنية العمل واحدة: البحث والصياغة
            يبتلعان اليوم قبل أن يبدأ العمل الذي يميّزك.
          </p>

          <div className="mx-auto mt-10 max-w-3xl rounded-2xl border border-border bg-card p-6 shadow-sm sm:p-8">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <PenLine className="h-5 w-5" />
            </div>
            <h3 className="mt-4 text-lg font-bold text-foreground">
              ماذا يفعل ريحان بهذا البند؟
            </h3>
            <p className="mt-3 text-base leading-relaxed text-muted-foreground">
              يكتب المسودّة الأولى — مذكّرة، عقداً، خطاباً — مبنيّة على نصوص
              نظامية حقيقية، كل استشهاد فيها مرفق برقم مادته ورابط مصدره الرسمي
              لتتحقّق منه قبل أن توقّع. وإن كان لك قالبك الخاص، ارفعه مرة واحدة
              في «قوالبي» فيصوغ على أسلوبك أنت لا على أسلوب عام.
            </p>
            <p className="mt-3 text-base leading-relaxed text-muted-foreground">
              أنت لا تبدأ من صفحة بيضاء — تبدأ من مسودّة موثّقة تراجعها وتصحّحها
              وتضيف إليها اجتهادك. وهذا هو الجزء الذي يستحق وقتك.
            </p>
          </div>
        </div>
      </section>

      {/* ------------------------------------------------------------------ */}
      {/* Pillars 2 and 3                                                     */}
      {/* ------------------------------------------------------------------ */}
      <section className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
        <div className="grid gap-5 sm:grid-cols-2">
          {PILLARS.map((pillar) => {
            const Icon = pillar.icon;
            return (
              <div
                key={pillar.title}
                className="rounded-2xl border border-border bg-card p-6 shadow-sm sm:p-8"
              >
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
                  <Icon className="h-5 w-5" />
                </div>
                <span className="mt-4 block text-xs font-semibold text-primary">
                  {pillar.eyebrow}
                </span>
                <h3 className="mt-1.5 text-lg font-bold leading-snug text-foreground">
                  {pillar.title}
                </h3>
                <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
                  {pillar.body}
                </p>
              </div>
            );
          })}
        </div>
      </section>

      {/* ------------------------------------------------------------------ */}
      {/* The coverage gap — the card set the brief asked for                */}
      {/* ------------------------------------------------------------------ */}
      <section className="border-y border-border bg-muted/20">
        <div className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
          <div className="mx-auto max-w-2xl text-center">
            <span className="text-xs font-semibold text-primary">
              الفجوة التي يملؤها ريحان
            </span>
            <h2 className="mt-2 text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
              أنت خبير في مجالك. ريحان يغطّي البقية.
            </h2>
            <p className="mt-3 text-base leading-relaxed text-muted-foreground">
              القانون السعودي يتطوّر في كل قطاع في وقت واحد. لا أحد يتابع 38
              قطاعاً — ولا يُفترض به ذلك. لكنك تخسر القضية حين لا تعرف أن النظام
              موجود أصلاً.
            </p>
          </div>

          <div className="mt-10 space-y-4">
            {GAPS.map((gap) => (
              <div
                key={gap.known}
                className="rounded-2xl border border-border bg-card p-5 shadow-sm sm:p-6"
              >
                <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
                  {/* Known — RTL puts this first block on the right */}
                  <div className="flex flex-1 items-start gap-3">
                    <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                      <Check className="h-4 w-4" />
                    </div>
                    <div>
                      <div className="text-xs text-muted-foreground">
                        تعرفه عن ظهر قلب
                      </div>
                      <div className="mt-0.5 text-base font-bold text-foreground">
                        {gap.known}
                      </div>
                    </div>
                  </div>

                  <ArrowLeft className="hidden h-5 w-5 shrink-0 text-muted-foreground/50 sm:block" />

                  {/* The gap */}
                  <div className="flex flex-1 items-start gap-3 border-t border-border pt-4 sm:border-t-0 sm:pt-0">
                    <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
                      <HelpCircle className="h-4 w-4" />
                    </div>
                    <div>
                      <div className="text-xs text-muted-foreground">
                        وماذا عن… ({gap.field})
                      </div>
                      <div className="mt-0.5 text-base font-bold text-foreground">
                        {gap.unknown}
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>

          <div className="mx-auto mt-10 max-w-3xl rounded-2xl border border-border bg-card p-6 shadow-sm sm:p-8">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <BookOpenCheck className="h-5 w-5" />
            </div>
            <h3 className="mt-4 text-lg font-bold text-foreground">
              الأنظمة الثلاثة على اليسار موجودة في مكتبة ريحان الآن
            </h3>
            <p className="mt-3 text-base leading-relaxed text-muted-foreground">
              بموادها كاملة، ومصادرها الرسمية، وحالتها النظامية — سارٍ أو معدَّل
              أو ملغى. أكثر من 3,900 نظام ولائحة تغطّي 38 قطاعاً، من الطاقة
              والتعدين إلى البناء والملكية الفكرية.
            </p>
            <p className="mt-3 text-base leading-relaxed text-muted-foreground">
              ولأن ريحان يبحث في القطاعات كلها لا في تخصّصك وحده، فهو يعرض عليك ما
              لم تطلبه — الالتزام النظامي الذي لم تكن تعرف أنه يخصّ قضيتك — فتبني
              مذكّرتك عليه بدل أن تكتشفه متأخراً.
            </p>
            <Link
              href="/regulations"
              className="mt-5 inline-flex items-center gap-1.5 text-sm font-semibold text-primary hover:underline"
            >
              تصفّح الأنظمة واللوائح
              <ArrowLeft className="h-4 w-4" />
            </Link>
          </div>
        </div>
      </section>

      {/* ------------------------------------------------------------------ */}
      {/* CTA                                                                */}
      {/* ------------------------------------------------------------------ */}
      <section className="mx-auto max-w-3xl px-4 py-16 text-center sm:py-20">
        <h2 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
          جرّبه على قضية حقيقية بين يديك
        </h2>
        <p className="mx-auto mt-3 max-w-xl text-base leading-relaxed text-muted-foreground">
          اطرح المسألة التي تشتغل عليها الآن، واحكم على التقرير ومسودّته بمعيارك
          المهني أنت.
        </p>
        <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
          <Link
            href="/login"
            className={cn(buttonVariants({ size: "lg" }), "text-sm font-semibold")}
          >
            جرّب ريحان مجاناً
          </Link>
          <Link
            href="/vs-chatgpt"
            className={cn(
              buttonVariants({ variant: "outline", size: "lg" }),
              "text-sm font-semibold",
            )}
          >
            ولماذا لا أستخدم ChatGPT؟
          </Link>
        </div>
      </section>
    </main>
  );
}
