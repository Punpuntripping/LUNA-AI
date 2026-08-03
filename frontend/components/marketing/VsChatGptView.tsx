import Link from "next/link";
import { Library, PenLine, ScanText, ShieldCheck } from "lucide-react";
import { ComparisonSection } from "@/components/landing/ComparisonSection";
import { HERO_TRUST } from "@/components/landing/content";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * «ريحان مقابل ChatGPT» — the head-to-head page that lives inside the
 * «عن ريحان» menu. An extension of the /about_us pitch, zoomed in on the one
 * question a lawyer asks before switching tools: *why can't I just use
 * ChatGPT?* Reuses the same `COMPARISON` data the landing page renders (via
 * `ComparisonSection`) so the claim set stays single-sourced, and frames it
 * with a dedicated hero, a why-it-matters strip, and a conversion CTA.
 */

interface Highlight {
  icon: typeof ShieldCheck;
  title: string;
  body: string;
}

const HIGHLIGHTS: Highlight[] = [
  {
    icon: ShieldCheck,
    title: "إجابة بلا هلوسة",
    body: "لا يخترع ريحان مادة نظامية أو رقماً. كل استشهاد مرفق برابطه الرسمي لتتحقّق منه بنفسك — بينما قد تستشهد الأدوات العامة بأنظمة وأرقام لا وجود لها.",
  },
  {
    icon: Library,
    title: "تغطية المصادر السعودية",
    body: "أكثر من 3,000 نظام ولائحة، و20,000 حكم قضائي، و1,000 تعميم رسمي — لا نظام العمل الشهير وحده.",
  },
  {
    icon: PenLine,
    title: "مبنيّ لعمل المحامي",
    body: "وكيل بحث ووكيل صياغة متخصّصان، مع قوالبك الخاصة وسير عمل القضية — لا محادثة عامة لا تعرف احتياج المحامي.",
  },
  {
    icon: ScanText,
    title: "يقرأ مستنداتك",
    body: "يستخرج الأسماء والأرقام من مستنداتك بدقة تصل إلى 99٪ للملفات الواضحة عبر تقنية OCR، ويبني إجابته على ما يخص قضيتك.",
  },
];

export function VsChatGptView() {
  return (
    <main>
      {/* Hero */}
      <section className="border-b border-border/60 bg-muted/20">
        <div className="mx-auto max-w-3xl px-4 py-16 text-center sm:py-20">
          <span className="inline-flex items-center rounded-full border border-primary/30 bg-primary/5 px-3 py-1 text-xs font-medium text-primary">
            ريحان مقابل ChatGPT
          </span>
          <h1 className="mt-5 text-3xl font-bold leading-tight tracking-tight text-foreground sm:text-4xl">
            لماذا لا تكفي الأدوات العامة للعمل القانوني السعودي؟
          </h1>
          <p className="mx-auto mt-4 max-w-2xl text-base leading-relaxed text-muted-foreground">
            أدوات مثل ChatGPT رائعة للأسئلة اليومية، لكنها لم تُصمَّم للقانون
            السعودي. حين تكون الدقة والمصدر أساس عملك، الفرق ليس في جودة اللغة —
            بل في أن تثق بالإجابة وترجع إلى مصدرها الرسمي. ريحان بُني لهذا تحديداً:
            كل معلومة ورقم مربوطان بمصدرهما الرسمي ورابطه المباشر.
          </p>

          {/* Corpus scale — the moat, front-loaded */}
          <div className="mx-auto mt-8 flex max-w-lg flex-wrap items-center justify-center gap-x-8 gap-y-3">
            {HERO_TRUST.map((stat) => (
              <div key={stat.label} className="text-center">
                <div className="text-2xl font-bold tabular-nums text-foreground">
                  {stat.value}
                </div>
                <div className="text-xs text-muted-foreground">
                  {stat.label}
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* The head-to-head table (shared with the landing page) */}
      <ComparisonSection />

      {/* Why the difference matters, one card per dimension */}
      <section className="border-t border-border bg-muted/20">
        <div className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
          <div className="mx-auto max-w-2xl text-center">
            <h2 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
              الفرق العملي حين تسأل ريحان
            </h2>
            <p className="mt-3 text-base leading-relaxed text-muted-foreground">
              نفس السؤال، لكن الإجابة مبنية على مصدر رسمي يمكنك الاستشهاد به أمام
              الجهة أو المحكمة.
            </p>
          </div>

          <div className="mt-10 grid gap-5 sm:grid-cols-2">
            {HIGHLIGHTS.map((item) => {
              const Icon = item.icon;
              return (
                <div
                  key={item.title}
                  className="rounded-2xl border border-border bg-card p-6 shadow-sm"
                >
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
                    <Icon className="h-5 w-5" />
                  </div>
                  <h3 className="mt-4 text-base font-bold text-foreground">
                    {item.title}
                  </h3>
                  <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                    {item.body}
                  </p>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="mx-auto max-w-3xl px-4 py-16 text-center sm:py-20">
        <h2 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
          جرّب الفرق بنفسك
        </h2>
        <p className="mx-auto mt-3 max-w-xl text-base leading-relaxed text-muted-foreground">
          اطرح سؤالك القانوني على ريحان، وقارن الإجابة: مكتملة، ومربوطة بمصدرها
          الرسمي ورابطه المباشر.
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
            href="/about_us"
            className={cn(
              buttonVariants({ variant: "outline", size: "lg" }),
              "text-sm font-semibold",
            )}
          >
            تعرّف أكثر على ريحان
          </Link>
        </div>
      </section>
    </main>
  );
}
