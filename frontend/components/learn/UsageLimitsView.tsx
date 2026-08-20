import Link from "next/link";
import type { LucideIcon } from "lucide-react";
import {
  Clock,
  Cpu,
  FileSignature,
  MessageCircle,
  Receipt,
  Scale,
  ScanText,
  Search,
  BookOpen,
} from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * «سياسة حد الاستخدام» — the fourth اكتشف ريحان lesson (/learn/usage-limits).
 *
 * EVERY number on this page is measured, not estimated. The per-operation costs
 * come from the `llm_calls` ledger (45-day window, 188 real deep_search turns,
 * points = cost_usd × 100 — see shared/quota POINTS_PER_USD):
 *
 *     router     median 0.09  p90 0.18   →  «≈ 0.1 نقطة»
 *     writing    median 0.77  p90 1.41   →  «≈ 1 نقطة»
 *     deep_search median 3.96 p25–p75 3.19–4.61 → «3–5 نقاط»
 *
 * The per-plan counts are those medians divided into `plans.points_session` /
 * `points_weekly` (live values: basic 10/50 · pro 15/75 · max 50/250).
 *
 * `free` is deliberately NOT in that table any more (migration 129): it has no
 * session and no weekly limit at all, just 5 points per rolling 30 days. Its
 * old row read «5 / 5» in the session and weekly columns, which stopped being
 * true the moment those limits went NULL.
 * An earlier draft claimed 4–6 deep searches per session on pro; the ledger says
 * 3–4 at the 15-point cap, and the honest number shipped (owner decision
 * 2026-08-02). If the model mix or the caps change, RE-RUN the ledger query
 * before touching a digit here — a stale number on this page is a broken promise,
 * not a typo.
 *
 * Numerals are Arabic-Indic to match /pricing and the usage dialog.
 */

const WHY_CARDS = [
  {
    icon: Scale,
    title: "الرسالة ليست وحدة عادلة",
    body: "سؤال عن تعريف نظامي ليس كسؤال يُشغّل بحثاً في آلاف الأنظمة والأحكام. من يحاسبك بعدد الرسائل يجعلك تدفع ثمن أثقل رسالة في كل رسالة.",
  },
  {
    icon: Cpu,
    title: "النقطة = تشغيل حقيقي",
    body: "النقطة تمثّل ما استهلكته عمليتك فعلياً من تشغيل النماذج — لا تقدير ولا تقريب لأعلى. الأرقام أدناه مقاسة على استخدام حقيقي في ريحان.",
  },
  {
    icon: Receipt,
    title: "تدفع بقدر ما تُشغّل",
    body: "إن كان يومك أسئلة سريعة ومراجعات، فلن تقترب من حدّك أصلاً. الحد موجود ليمنع الاستنزاف الشاذ، لا ليحدّ عملك اليومي.",
  },
] as const;

interface OperationRow {
  icon: LucideIcon;
  label: string;
  cost: string;
  note: string;
  /** The deep-search row — the one the whole page is about. */
  emphasis?: boolean;
}

const OPERATION_ROWS: readonly OperationRow[] = [
  {
    icon: MessageCircle,
    label: "سؤال عام أو توجيه",
    cost: "≈ 0.1 نقطة",
    note: "الردود المباشرة والتوجيه بين الوكلاء",
  },
  {
    icon: FileSignature,
    label: "صياغة مستند أو تعديله",
    cost: "≈ 1 نقطة",
    note: "المذكرات والعقود والخطابات",
  },
  {
    icon: Search,
    label: "البحث المعمّق",
    cost: "3–5 نقاط",
    note: "أثقل عملية في ريحان — عدة وكلاء على المصادر الرسمية",
    emphasis: true,
  },
  {
    icon: ScanText,
    label: "استخراج نص من ملف",
    cost: "لا يُخصم من النقاط",
    note: "يُحتسب بالصفحات، بحدّ شهري مستقل",
  },
  {
    icon: BookOpen,
    label: "فتح مصدر من المكتبة",
    cost: "لا يُخصم من النقاط",
    note: "له حدّ «فتح المصادر» المستقل",
  },
] as const;

interface PlanRow {
  plan: string;
  session: string;
  sessionRuns: string;
  weekly: string;
  weeklyRuns: string;
  /** Mirrors the `highlighted` pro card on /pricing. */
  highlighted?: boolean;
}

/** Session/weekly points are the live `plans` rows; the عمليات columns are those
 *  divided by the measured 3.19–4.61 deep_search range. */
const PLAN_ROWS: readonly PlanRow[] = [
  {
    plan: "الأساسية",
    session: "10",
    sessionRuns: "2–3",
    weekly: "50",
    weeklyRuns: "10–15",
  },
  {
    plan: "الاحترافية",
    session: "15",
    sessionRuns: "3–4",
    weekly: "75",
    weeklyRuns: "16–23",
    highlighted: true,
  },
  {
    plan: "القصوى",
    session: "50",
    sessionRuns: "10–16",
    weekly: "250",
    weeklyRuns: "54–78",
  },
] as const;

const WINDOW_POINTS = [
  {
    title: "الجلسة — كتلة 5 ساعات",
    body: "تبدأ من أول رسالة ترسلها، لا من منتصف الليل. تنتهي الجلسة فتعود نقاطها كاملة دون انتظار يوم جديد.",
  },
  {
    title: "الأسبوع — نافذة متحركة 7 أيام",
    body: "تبدأ من أول استخدام لاشتراكك وتتحرك معه، فلا يضيع عليك جزء من الأسبوع لأن اشتراكك بدأ يوم أربعاء.",
  },
  {
    title: "المجانية — نافذة واحدة 30 يوماً",
    body: "لا جلسة ولا أسبوع في الباقة المجانية: 5 نقاط تتحرك على آخر 30 يوماً، تكفي لتجربة بحث معمّق قبل أن تقرر.",
  },
  {
    title: "النقاط لا تُرحّل",
    body: "ما لا تستخدمه في نافذته لا ينتقل إلى التي بعدها — وهذا ما يبقي الحدود مرتفعة والسعر منخفضاً.",
  },
] as const;

const FREE_ITEMS = [
  "قراءة محادثاتك السابقة ومساحة عملك ومكتبتك",
  "تصفّح المدونة والمكتبة القانونية العامة",
  "حفظ العناصر وتصديرها ومشاركتها",
  "إنشاء القضايا والقوالب وتنظيمها",
] as const;

export function UsageLimitsView() {
  return (
    <main>
      {/* Hero */}
      <section className="border-b border-border/60 bg-muted/20">
        <div className="mx-auto max-w-3xl px-4 py-16 text-center sm:py-20">
          <span className="inline-flex items-center rounded-full border border-primary/30 bg-primary/5 px-3 py-1 text-xs font-medium text-primary">
            اكتشف ريحان
          </span>
          <h1 className="mt-5 text-3xl font-bold leading-tight tracking-tight text-foreground sm:text-4xl">
            سياسة حد الاستخدام
          </h1>
          <p className="mx-auto mt-4 max-w-2xl text-base leading-relaxed text-muted-foreground">
            نحسب استخدامك بالنقطة لا بعدد الرسائل — لأن رسالة تسأل عن تعريف ليست
            كرسالة تُشغّل بحثاً في آلاف الأنظمة والأحكام. هنا كل رقم كما هو، بلا
            تقدير ولا نجمة صغيرة في الأسفل.
          </p>
        </div>
      </section>

      {/* 1 — why points at all */}
      <section className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
            لماذا النقاط بدل عدد الرسائل؟
          </h2>
          <p className="mt-3 text-base leading-relaxed text-muted-foreground">
            لأن الرسائل تختلف في كلفتها اختلافاً هائلاً، ومحاسبتك بعددها تعني أن
            تدفع عن أثقلها دائماً:
          </p>
        </div>
        <div className="mt-10 grid gap-5 sm:grid-cols-3">
          {WHY_CARDS.map((card) => {
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

      {/* 2 — what each operation costs */}
      <section className="border-t border-border bg-muted/20">
        <div className="mx-auto max-w-3xl px-4 py-16 sm:py-20">
          <div className="text-center">
            <h2 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
              ماذا تستهلك كل عملية؟
            </h2>
            <p className="mt-3 text-base leading-relaxed text-muted-foreground">
              هذه أرقام مقاسة على استخدام حقيقي في ريحان، وتشمل ما تحتاجه العملية
              من خطوات داخلية:
            </p>
          </div>

          <ul className="mt-10 space-y-3">
            {OPERATION_ROWS.map((row) => {
              const Icon = row.icon;
              return (
                <li
                  key={row.label}
                  className={cn(
                    "flex items-center gap-4 rounded-xl border bg-card p-4",
                    row.emphasis
                      ? "border-primary/30 bg-primary/5"
                      : "border-border",
                  )}
                >
                  <div
                    className={cn(
                      "flex h-10 w-10 shrink-0 items-center justify-center rounded-lg",
                      row.emphasis
                        ? "bg-primary text-primary-foreground"
                        : "bg-primary/10 text-primary",
                    )}
                  >
                    <Icon className="h-5 w-5" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-bold text-foreground">
                      {row.label}
                    </div>
                    <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
                      {row.note}
                    </p>
                  </div>
                  <div className="shrink-0 text-sm font-bold text-primary">
                    {row.cost}
                  </div>
                </li>
              );
            })}
          </ul>

          <p className="mt-6 text-center text-sm leading-relaxed text-muted-foreground">
            البحث المعمّق هو أغلى ما تفعله في ريحان — يُشغّل عدة وكلاء متخصصين
            على الأنظمة واللوائح والأحكام القضائية والتعاميم، ثم يحرّر لك تقريراً
            موثّقاً بالمصادر. وهو أيضاً ما لن تجده في أداة عامة بأي سعر.
          </p>
        </div>
      </section>

      {/* 3 — what that buys per plan */}
      <section className="border-t border-border">
        <div className="mx-auto max-w-4xl px-4 py-16 sm:py-20">
          <div className="mx-auto max-w-2xl text-center">
            <h2 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
              وكم يكفيك ذلك؟
            </h2>
            <p className="mt-3 text-base leading-relaxed text-muted-foreground">
              بحساب البحث المعمّق — أثقل العمليات — هذا ما تتيحه كل باقة. أما
              الأسئلة والصياغة فتكلفتها جزء بسيط من ذلك:
            </p>
          </div>

          <div className="mt-10 overflow-x-auto">
            <table className="w-full min-w-[34rem] border-collapse text-sm">
              <thead>
                <tr className="border-b border-border text-right">
                  <th className="p-3 font-bold text-foreground">الباقة</th>
                  <th className="p-3 font-medium text-muted-foreground">
                    نقاط الجلسة
                  </th>
                  <th className="p-3 font-medium text-muted-foreground">
                    بحث معمّق / جلسة
                  </th>
                  <th className="p-3 font-medium text-muted-foreground">
                    نقاط الأسبوع
                  </th>
                  <th className="p-3 font-medium text-muted-foreground">
                    بحث معمّق / أسبوع
                  </th>
                </tr>
              </thead>
              <tbody>
                {PLAN_ROWS.map((row) => (
                  <tr
                    key={row.plan}
                    className={cn(
                      "border-b border-border/60 text-right",
                      row.highlighted && "bg-primary/5",
                    )}
                  >
                    <td className="p-3 font-bold text-foreground">
                      {row.plan}
                    </td>
                    <td className="p-3 text-muted-foreground">{row.session}</td>
                    <td
                      className={cn(
                        "p-3 font-semibold",
                        row.highlighted ? "text-primary" : "text-foreground",
                      )}
                    >
                      {row.sessionRuns}
                    </td>
                    <td className="p-3 text-muted-foreground">{row.weekly}</td>
                    <td
                      className={cn(
                        "p-3 font-semibold",
                        row.highlighted ? "text-primary" : "text-foreground",
                      )}
                    >
                      {row.weeklyRuns}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="mt-6 text-center text-xs leading-relaxed text-muted-foreground">
            الأعمدة تفترض أن كل نقطة تذهب إلى البحث المعمّق وحده. الاستخدام
            الواقعي يخلط الأسئلة والصياغة، فالعدد الفعلي أعلى من ذلك عادةً.
          </p>
          {/* free is out of the table on purpose — it has neither of the two
              columns the table is built on. Stating its single window here is
              clearer than a row of «—». */}
          <p className="mt-3 text-center text-xs leading-relaxed text-muted-foreground">
            الباقة المجانية خارج الجدول: نافذة واحدة بـ5 نقاط كل 30 يوماً — بحث
            معمّق واحد تقريباً، لتجربة ريحان قبل الاشتراك.
          </p>
        </div>
      </section>

      {/* 4 — how the windows work */}
      <section className="border-t border-border bg-muted/20">
        <div className="mx-auto max-w-3xl px-4 py-16 sm:py-20">
          <div className="text-center">
            <h2 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
              كيف تُحسب النوافذ؟
            </h2>
            <p className="mt-3 text-base leading-relaxed text-muted-foreground">
              الحدود تعمل على نوافذ تتحرك معك، لا على تقويم ثابت:
            </p>
          </div>
          <ol className="mt-8 space-y-5">
            {WINDOW_POINTS.map((point, i) => (
              <li key={point.title} className="flex items-start gap-4">
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary text-sm font-bold text-primary-foreground">
                  {i + 1}
                </span>
                <div>
                  <div className="text-sm font-bold text-foreground">
                    {point.title}
                  </div>
                  <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
                    {point.body}
                  </p>
                </div>
              </li>
            ))}
          </ol>
          <p className="mt-8 flex items-start gap-3 rounded-xl border border-border bg-card p-4 text-sm leading-relaxed text-muted-foreground">
            <Clock className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
            <span>
              تستطيع رؤية رصيدك ومتى يتجدد في أي وقت من نافذة «الاستخدام» في
              إعدادات حسابك — الأرقام هناك هي نفسها التي تُطبَّق، لا تقدير لها.
            </span>
          </p>
        </div>
      </section>

      {/* 5 — what never costs points */}
      <section className="border-t border-border">
        <div className="mx-auto max-w-3xl px-4 py-16 sm:py-20">
          <div className="text-center">
            <h2 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
              ما لا يُخصم من نقاطك
            </h2>
            <p className="mt-3 text-base leading-relaxed text-muted-foreground">
              النقاط تُصرف على تشغيل الذكاء الاصطناعي فقط. أما هذه فمفتوحة:
            </p>
          </div>
          <ul className="mt-8 grid gap-3 sm:grid-cols-2">
            {FREE_ITEMS.map((item) => (
              <li
                key={item}
                className="rounded-xl border border-border bg-card p-4 text-sm leading-relaxed text-muted-foreground"
              >
                {item}
              </li>
            ))}
          </ul>

          <div className="mt-6 space-y-3">
            <p className="rounded-xl border border-border bg-muted/30 p-4 text-sm leading-relaxed text-muted-foreground">
              <b className="text-foreground">حدّان مستقلان</b> — لا يمسّان
              نقاطك: <b className="text-foreground">فتح المصادر</b> من المكتبة
              (100 للأساسية · 200 للاحترافية · 1000 للقصوى)، و
              <b className="text-foreground"> استخراج النص</b> من الملفات
              بالصفحات (15 · 40 · 200 شهرياً).
            </p>
            {/* The «سقف شهري احتياطي (300 / 1000)» line that stood here was
                removed with migration 129: those numbers were set to NULL so
                re-enabling the monthly window for the free plan would not
                quietly start capping people who had already paid. There is no
                monthly backstop on the paid plans now — so the page must not
                claim one. Restore this line only alongside the limits. */}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="border-t border-border bg-muted/20">
        <div className="mx-auto max-w-3xl px-4 py-16 text-center sm:py-20">
          <h2 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
            حدود واضحة، وسعر يقابلها
          </h2>
          <p className="mx-auto mt-3 max-w-xl text-base leading-relaxed text-muted-foreground">
            نشرنا هذه الأرقام لأن مقارنتها بغيرها في مصلحتنا. اطّلع على الباقات،
            أو جرّب ريحان وقِس بنفسك.
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
            <Link
              href="/pricing"
              className={cn(
                buttonVariants({ size: "lg" }),
                "text-sm font-semibold",
              )}
            >
              الباقات والأسعار
            </Link>
            <Link
              href="/login"
              className={cn(
                buttonVariants({ variant: "outline", size: "lg" }),
                "text-sm font-semibold",
              )}
            >
              جرّب ريحان مجاناً
            </Link>
          </div>
        </div>
      </section>
    </main>
  );
}
