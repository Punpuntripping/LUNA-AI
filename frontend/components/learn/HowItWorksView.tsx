import Link from "next/link";
import {
  BookOpenText,
  Bot,
  Compass,
  FolderOpen,
  Library,
  PenLine,
  Scale,
  ShieldCheck,
  Sparkles,
  Users,
} from "lucide-react";
import { AgentsDiagram } from "@/components/onboarding/AgentsDiagram";
import { CORPUS_STATS } from "@/components/onboarding/onboarding-content";
import { ShowcaseReportCard } from "@/components/landing/ShowcaseReportCard";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * «كيف يعمل ريحان» — the first اكتشف ريحان lesson (/learn/how-it-works).
 * Long-form version of onboarding Step 1: the popup stays the summary of THIS
 * page (see .claude/plans/discover_rayhan_agents.md — if copy drifts, update
 * the popup to match, never the other way). Reader-facing vocabulary only:
 * الموجّه / الباحث / الكاتب / المكتبة القانونية / مساحة العمل — no internal
 * agent names, models, or tiers. Models are described as open-source, never
 * named; search mechanics stay deliberately vague (no query-expansion /
 * parallelism / filtering-stage specifics — owner decision 2026-08-02).
 * Diagram + corpus numbers are imported from the onboarding module so they
 * stay single-sourced.
 */

const AI_INTRO_CARDS = [
  {
    icon: Sparkles,
    title: "النموذج اللغوي",
    body: "ذكاء اصطناعي يجيد فهم اللغة: يقرأ ويلخّص ويحلّل ويصيغ. لكنه حين يُسأل وحده يجيب من ذاكرته — وقد يخطئ بثقة تامة.",
  },
  {
    icon: Bot,
    title: "الوكيل",
    body: "نموذج أُسندت إليه مهمة محددة وأدوات حقيقية يستخدمها: يبحث ويقرأ ويتحقق، ثم يبني إجابته على ما وجده أمامه — لا على ما يتذكره.",
  },
  {
    icon: Users,
    title: "فريق من الوكلاء",
    body: "في ريحان يتعاون وكلاء متخصصون على سؤالك، مبنيون على نماذج مفتوحة المصدر نطوّعها للعمل القانوني السعودي، ويعملون جميعًا على مكتبة قانونية واحدة موثّقة.",
  },
] as const;

const SEARCH_SCOPES = [
  {
    icon: BookOpenText,
    title: "وكيل الأنظمة واللوائح",
    body: "يبحث في الأنظمة واللوائح والأدلة التنظيمية والتعاميم الصادرة عن الجهات السعودية، ويجمع منها ما ينطبق على حالتك.",
  },
  {
    icon: Scale,
    title: "وكيل الأحكام القضائية",
    body: "يبحث في أكثر من 20,000 حكم منشور ليجد السوابق المشابهة لواقعتك والمبادئ التي استقرت عليها المحاكم.",
  },
] as const;

const SEARCH_STEPS = [
  {
    title: "يدرس سؤالك",
    body: "يقرأ سؤالك من زواياه المختلفة ويحدد ما يحتاج الوصول إليه — لا يكتفي بظاهر الصياغة.",
  },
  {
    title: "يغوص في المكتبة القانونية",
    body: "مصادر سعودية رسمية مفهرسة، يبحث فيها من أكثر من اتجاه حتى يغطي سؤالك كاملًا.",
  },
  {
    title: "ينتقي الأنسب ويعيده تقريرًا موثّقًا",
    body: "لا يصلك إلا ما يخدم سؤالك: تقرير واحد مرتّب، كل معلومة فيه تحمل مرجعًا مرقّمًا يفتح لك النص الرسمي من مصدره.",
  },
] as const;

const WRITER_POINTS = [
  {
    icon: FolderOpen,
    title: "يبدأ من القوالب",
    body: "يستعرض عناوين القوالب المخزّنة — قوالب ريحان الجاهزة وقوالبك الخاصة التي أضفتها في «قوالبي» — ويختار الأنسب لطلبك، فيخرج مستندك على البنية التي اعتدتها.",
  },
  {
    icon: Library,
    title: "يكتب من بحث موثّق، لا من فراغ",
    body: "يعتمد على نتائج البحث في المكتبة القانونية، وتنتقل المراجع المرقّمة معه إلى داخل المستند.",
  },
  {
    icon: PenLine,
    title: "مسودته تظهر في مساحة العمل",
    body: "بجانب المحادثة مباشرة — تراجعها وتطلب تعديلها حتى تصل إلى الصيغة النهائية.",
  },
] as const;

const ROUTER_POINTS = [
  {
    title: "يفهم قصدك",
    body: "من سؤالك وسياق محادثتك وما جمعته في مساحة العمل.",
  },
  {
    title: "يقرّر من يتولى المهمة",
    body: "يجيبك مباشرة على الأسئلة العامة والسريعة، ويكلّف الباحث أو الكاتب بما يحتاج تخصصًا.",
  },
  {
    title: "يسألك عند اللبس",
    body: "إذا احتمل سؤالك أكثر من وجه — أطراف متعددة، جهة غير محددة — طرح عليك سؤال توضيح قبل أن يبدأ؛ فدقيقة توضيح توفّر بحثًا كاملًا في الاتجاه الخطأ.",
  },
] as const;

export function HowItWorksView() {
  return (
    <main>
      {/* Hero */}
      <section className="border-b border-border/60 bg-muted/20">
        <div className="mx-auto max-w-3xl px-4 py-16 text-center sm:py-20">
          <span className="inline-flex items-center rounded-full border border-primary/30 bg-primary/5 px-3 py-1 text-xs font-medium text-primary">
            اكتشف ريحان
          </span>
          <h1 className="mt-5 text-3xl font-bold leading-tight tracking-tight text-foreground sm:text-4xl">
            كيف يعمل ريحان؟
          </h1>
          <p className="mx-auto mt-4 max-w-2xl text-base leading-relaxed text-muted-foreground">
            خلف كل إجابة في ريحان فريق من الوكلاء المتخصصين يعمل معًا: موجّه
            يفهم طلبك، وباحث يغوص في المكتبة القانونية، وكاتب يصوغ مستنداتك.
            كلٌّ منهم متخصص في مهمته — وهذا ما يجعل الإجابة موثّقة لا مرتجلة.
          </p>
        </div>
      </section>

      {/* Primer: what an AI agent even is, before meeting Rayhan's team */}
      <section className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
            قبل أن تتعرف على الفريق — ما وكيل الذكاء الاصطناعي؟
          </h2>
          <p className="mt-3 text-base leading-relaxed text-muted-foreground">
            نماذج الذكاء الاصطناعي الحديثة تجيد اللغة إجادة مدهشة، لكنها وحدها
            تجيب من ذاكرتها — وفي القانون قد يعني ذلك مادة نظامية لا وجود لها.
            الوكيل هو الحل.
          </p>
        </div>
        <div className="mt-10 grid gap-5 sm:grid-cols-3">
          {AI_INTRO_CARDS.map((card) => {
            const Icon = card.icon;
            return (
              <div
                key={card.title}
                className="rounded-2xl border border-border bg-card p-6 shadow-sm"
              >
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
                  <Icon className="h-5 w-5" />
                </div>
                <h3 className="mt-4 text-base font-bold text-foreground">
                  {card.title}
                </h3>
                <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                  {card.body}
                </p>
              </div>
            );
          })}
        </div>
      </section>

      {/* The team at a glance — diagram + the corpus it works against */}
      <section className="border-t border-border bg-muted/20">
        <div className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
          <div className="mx-auto max-w-2xl text-center">
            <h2 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
              الوكلاء — من يعمل على سؤالك؟
            </h2>
            <p className="mt-3 text-base leading-relaxed text-muted-foreground">
              سؤالك يمر أولًا على الموجّه، ومنه إلى المتخصص المناسب: الباحث
              بوضعيه، أو الكاتب.
            </p>
          </div>
          <div className="mx-auto mt-10 max-w-md rounded-2xl border border-border bg-card p-6 shadow-sm">
            <AgentsDiagram />
          </div>
          <div className="mx-auto mt-6 grid max-w-2xl grid-cols-2 gap-2 sm:grid-cols-4">
            {CORPUS_STATS.map((stat) => (
              <div
                key={stat.label}
                className="rounded-lg border border-border bg-muted/30 p-3 text-center"
              >
                <div
                  className="text-lg font-bold tabular-nums text-primary"
                  dir="ltr"
                >
                  {stat.value}
                </div>
                <div className="mt-0.5 text-xs leading-4 text-muted-foreground">
                  {stat.label}
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* 1 — Deep search: regulations & judgments */}
      <section className="border-t border-border">
        <div className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
          <div className="mx-auto max-w-2xl text-center">
            <h2 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
              البحث المعمّق: الأنظمة والأحكام
            </h2>
            <p className="mt-3 text-base leading-relaxed text-muted-foreground">
              عندما يحتاج سؤالك إلى بحث، يتولاه أحد وكيلين بحسب ما تبحث عنه:
            </p>
          </div>

          <div className="mt-10 grid gap-5 sm:grid-cols-2">
            {SEARCH_SCOPES.map((scope) => {
              const Icon = scope.icon;
              return (
                <div
                  key={scope.title}
                  className="rounded-2xl border border-border bg-card p-6 shadow-sm"
                >
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
                    <Icon className="h-5 w-5" />
                  </div>
                  <h3 className="mt-4 text-base font-bold text-foreground">
                    {scope.title}
                  </h3>
                  <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                    {scope.body}
                  </p>
                </div>
              );
            })}
          </div>

          <div className="mx-auto mt-12 max-w-2xl">
            <h3 className="text-center text-lg font-bold text-foreground">
              كلاهما يعمل بالطريقة نفسها
            </h3>
            <ol className="mt-6 space-y-5">
              {SEARCH_STEPS.map((step, i) => (
                <li key={step.title} className="flex items-start gap-4">
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary text-sm font-bold text-primary-foreground">
                    {i + 1}
                  </span>
                  <div>
                    <div className="text-sm font-bold text-foreground">
                      {step.title}
                    </div>
                    <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
                      {step.body}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
            <p className="mt-8 rounded-xl border border-primary/20 bg-primary/5 p-4 text-center text-sm leading-relaxed text-foreground">
              وأثناء البحث ترى تقدّمه أمامك خطوة بخطوة — تعرف ماذا يبحث ولماذا.
            </p>
          </div>

          {/* The outcome, shown not told — the same live showcase about_us
              renders (shared ShowcaseReportCard, «عرض المصدر» works). */}
          <div className="mx-auto mt-12 max-w-3xl">
            <p className="mb-4 text-center text-sm text-muted-foreground">
              وهذا ما يصلك في النهاية — مثال من تقرير حقيقي، جرّب «عرض
              المصدر» بنفسك:
            </p>
            <ShowcaseReportCard />
          </div>
        </div>
      </section>

      {/* 2 — The writer */}
      <section className="border-t border-border bg-muted/20">
        <div className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
          <div className="mx-auto max-w-2xl text-center">
            <h2 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
              الكاتب
            </h2>
            <p className="mt-3 text-base leading-relaxed text-muted-foreground">
              عندما تطلب مستندًا — مذكرة، صحيفة دعوى، لائحة اعتراضية، عقدًا —
              يتولى الكاتب المهمة:
            </p>
          </div>
          <div className="mt-10 grid gap-5 sm:grid-cols-3">
            {WRITER_POINTS.map((point) => {
              const Icon = point.icon;
              return (
                <div
                  key={point.title}
                  className="rounded-2xl border border-border bg-card p-6 shadow-sm"
                >
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
                    <Icon className="h-5 w-5" />
                  </div>
                  <h3 className="mt-4 text-base font-bold text-foreground">
                    {point.title}
                  </h3>
                  <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                    {point.body}
                  </p>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      {/* 3 — The router */}
      <section className="border-t border-border">
        <div className="mx-auto max-w-3xl px-4 py-16 sm:py-20">
          <div className="text-center">
            <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <Compass className="h-6 w-6" />
            </div>
            <h2 className="mt-4 text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
              الموجّه
            </h2>
            <p className="mt-3 text-base leading-relaxed text-muted-foreground">
              أول من يقرأ رسالتك:
            </p>
          </div>
          <ul className="mt-8 space-y-4">
            {ROUTER_POINTS.map((point) => (
              <li
                key={point.title}
                className="flex items-start gap-3 rounded-xl border border-border bg-card p-4"
              >
                <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
                <p className="text-sm leading-relaxed text-muted-foreground">
                  <b className="text-foreground">{point.title}</b> —{" "}
                  {point.body}
                </p>
              </li>
            ))}
          </ul>
        </div>
      </section>

      {/* CTA */}
      <section className="border-t border-border bg-muted/20">
        <div className="mx-auto max-w-3xl px-4 py-16 text-center sm:py-20">
          <h2 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
            شاهد الوكلاء يعملون على سؤالك
          </h2>
          <p className="mx-auto mt-3 max-w-xl text-base leading-relaxed text-muted-foreground">
            اطرح سؤالك القانوني وتابع البحث خطوة بخطوة حتى يصلك التقرير الموثّق
            بمراجعه.
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
            <Link
              href="/login"
              className={cn(
                buttonVariants({ size: "lg" }),
                "text-sm font-semibold",
              )}
            >
              جرّب ريحان مجاناً
            </Link>
            <Link
              href="/library"
              className={cn(
                buttonVariants({ variant: "outline", size: "lg" }),
                "text-sm font-semibold",
              )}
            >
              تصفّح المكتبة القانونية
            </Link>
          </div>
        </div>
      </section>
    </main>
  );
}
