import Link from "next/link";
import {
  Anchor,
  FileSearch,
  History,
  ListOrdered,
  MessagesSquare,
  Paperclip,
  PenLine,
  ShieldAlert,
  ShieldCheck,
  StickyNote,
} from "lucide-react";
import { ShowcaseReportCard } from "@/components/landing/ShowcaseReportCard";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * «مساحة العمل» — the second اكتشف ريحان lesson (/learn/workspace).
 * Long-form version of onboarding STEP_WORKSPACE: the popup stays the summary
 * of THIS page (see .claude/plans/discover_rayhan_workspace.md — if copy
 * drifts, update the popup to match, never the other way). Same voice rules as
 * how-it-works: reader-facing vocabulary only (الموجّه / الباحث / الكاتب /
 * المكتبة القانونية / مساحة العمل), models are open-source and never named,
 * no internal kind names — the six group cards use the exact Arabic labels the
 * workspace pane itself shows (WorkspaceList KIND_LABELS).
 */

const WHY_CARDS = [
  {
    icon: ShieldAlert,
    title: "الذاكرة وحدها لا تؤتمن",
    body: "النموذج اللغوي إذا سُئل وحده أجاب من ذاكرته، وقد يخطئ بثقة تامة — وهو ما يُعرف بهلوسة الذكاء الاصطناعي: معلومة مصاغة بإتقان ولا وجود لها. وفي القانون لا مكان لذلك.",
  },
  {
    icon: Anchor,
    title: "حقائق محفوظة أمام الوكلاء",
    body: "نطوّع نماذج مفتوحة المصدر للعمل القانوني السعودي، وقاعدتها الأولى: البناء على ما هو محفوظ في مساحة العمل — بحث موثّق ومستنداتك وحقائق مثبتة — لا على ما تتذكره النماذج.",
  },
  {
    icon: History,
    title: "سياق لا يضيع مهما طالت المحادثة",
    body: "ما يدخل مساحة العمل يبقى حاضرًا لكل الوكلاء بقية الجلسة: التفاصيل المهمة لا تتبدد في زحام حوار طويل، بل تبقى بطاقات كاملة يعود إليها أي وكيل في أي خطوة.",
  },
] as const;

// The six group labels are the exact ones the workspace pane shows.
const KIND_CARDS = [
  {
    icon: PenLine,
    title: "المسودات",
    body: "ما يكتبه الكاتب لك: مذكرة، عقد، صحيفة دعوى… تفتحها بجانب المحادثة وتحرّرها بنفسك أو تطلب تعديلها حتى تصل إلى الصيغة النهائية.",
  },
  {
    icon: FileSearch,
    title: "نتائج البحث",
    body: "تقارير الباحث الموثّقة من المكتبة القانونية: تقرير مرتّب لكل بحث، كل معلومة فيه تحمل مرجعًا مرقّمًا يفتح نصّها الرسمي.",
  },
  {
    icon: StickyNote,
    title: "الملاحظات",
    body: "ما تدوّنه بنفسك، والحقائق التي تطلب من ريحان حفظها أثناء الحوار — تبقى أمام الوكلاء في كل خطوة تالية.",
  },
  {
    icon: Paperclip,
    title: "المرفقات",
    body: "الملفات التي ترفعها: عقد موقّع، لائحة اعتراضية، مذكرة الخصم… يقرأ ريحان نصّها حتى لو كانت صورة ممسوحة، فتدخل في حساب الوكلاء وإجاباتهم.",
  },
  {
    icon: ListOrdered,
    title: "المراجع",
    body: "قائمة المصادر التي بُنيت عليها إجاباتك في هذه المحادثة، ولكل مصدر رقم ثابت تعود إليه متى شئت.",
  },
  {
    icon: MessagesSquare,
    title: "ملخص المحادثة",
    body: "حين تطول الجلسة يلخّص ريحان ما مضى حتى لا يفقد أولُ الحديث آخرَه — ويبقى الأصل الكامل محفوظًا في عناصره.",
  },
] as const;

const REFERENCE_STEPS = [
  {
    title: "كل معلومة تحمل رقمها",
    body: "في تقارير الباحث ومسودات الكاتب تجد بجانب كل معلومة مرجعًا مرقّمًا يحدد مصدرها بدقة.",
  },
  {
    title: "الرقم يفتح النص الرسمي",
    body: "اضغط عليه فيظهر لك نص المادة أو الحكم كما ورد في مصدره — تتحقق بعينك، لا تأخذ الخلاصة على علّاتها.",
  },
  {
    title: "ومن المرجع إلى المكتبة",
    body: "من النافذة نفسها تنتقل إلى النظام كاملًا في مكتبة ريحان القانونية لتقرأ المادة في سياقها.",
  },
] as const;

const CONTROL_POINTS = [
  {
    title: "أضف بنفسك",
    body: "ارفع ملفًا أو دوّن ملاحظة من زر «إضافة عنصر» في اللوحة، فيتعامل معها الوكلاء كأنها جزء من الحوار.",
  },
  {
    title: "قل «احفظ هذه المعلومة»",
    body: "أي حقيقة مهمة تمرّ في الحوار — اسم طرف، تاريخ جوهري، رقم عقد — اطلب حفظها فتُضاف بطاقة تبقى حاضرة بقية الجلسة.",
  },
  {
    title: "حرّر وقيّم",
    body: "عدّل أي مسودة مباشرة داخل مساحة العمل، وقيّم العناصر بإعجاب أو عدمه — تقييمك يساعدنا على تحسين المخرجات.",
  },
  {
    title: "شارك خارج ريحان",
    body: "أي عنصر يمكن مشاركته برابط، ومسودة أعجبتك تحفظها مدونة في «مدوناتي».",
  },
] as const;

export function WorkspaceView() {
  return (
    <main>
      {/* Hero */}
      <section className="border-b border-border/60 bg-muted/20">
        <div className="mx-auto max-w-3xl px-4 py-16 text-center sm:py-20">
          <span className="inline-flex items-center rounded-full border border-primary/30 bg-primary/5 px-3 py-1 text-xs font-medium text-primary">
            اكتشف ريحان
          </span>
          <h1 className="mt-5 text-3xl font-bold leading-tight tracking-tight text-foreground sm:text-4xl">
            مساحة العمل — ذاكرة محادثتك الموثّقة
          </h1>
          <p className="mx-auto mt-4 max-w-2xl text-base leading-relaxed text-muted-foreground">
            بجانب كل محادثة في ريحان توجد مساحة العمل: المكان الذي تتجمع فيه
            مخرجات جلستك — مسودات الكاتب، تقارير الباحث، ملفاتك وملاحظاتك —
            فيبني الوكلاء إجاباتهم على ما هو محفوظ أمامهم، لا على ما تتذكره
            النماذج.
          </p>
        </div>
      </section>

      {/* Why a workspace at all — the anti-hallucination rationale */}
      <section className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
            لماذا مساحة العمل؟
          </h2>
          <p className="mt-3 text-base leading-relaxed text-muted-foreground">
            لأن الفرق بين إجابة قانونية موثوقة وأخرى مرتجلة هو المصدر الذي
            بُنيت عليه.
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

      {/* What's inside — the six groups, labelled exactly as in the pane */}
      <section className="border-t border-border bg-muted/20">
        <div className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
          <div className="mx-auto max-w-2xl text-center">
            <h2 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
              ماذا تجد في مساحة العمل؟
            </h2>
            <p className="mt-3 text-base leading-relaxed text-muted-foreground">
              كل ما يُنتج أثناء المحادثة يُحفظ بطاقة مستقلة، مصنّفة في مجموعات
              تراها في اللوحة بجانب الحوار:
            </p>
          </div>
          <div className="mt-10 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {KIND_CARDS.map((card) => {
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
        </div>
      </section>

      {/* References — how a claim traces back to its official source */}
      <section className="border-t border-border">
        <div className="mx-auto max-w-3xl px-4 py-16 sm:py-20">
          <div className="text-center">
            <h2 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
              المرجع قبل الإجابة
            </h2>
            <p className="mt-3 text-base leading-relaxed text-muted-foreground">
              القاعدة في ريحان أن المعلومة القانونية لا تُقدَّم بلا مصدر. هكذا
              تتحقق بنفسك:
            </p>
          </div>
          <ol className="mt-8 space-y-5">
            {REFERENCE_STEPS.map((step, i) => (
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
          {/* Shown not told — the same live showcase about_us renders
              (shared ShowcaseReportCard, «عرض المصدر» works). */}
          <div className="mt-10">
            <p className="mb-4 text-center text-sm text-muted-foreground">
              هكذا تبدو في التطبيق — مثال من تقرير حقيقي، جرّب «عرض المصدر»
              بنفسك:
            </p>
            <ShowcaseReportCard />
          </div>

          <p className="mt-8 rounded-xl border border-primary/20 bg-primary/5 p-4 text-center text-sm leading-relaxed text-foreground">
            بهذا تعرف دائمًا إجابة أهم سؤال في العمل القانوني: «من أين جاءت
            هذه المعلومة؟»
          </p>
        </div>
      </section>

      {/* You control it */}
      <section className="border-t border-border bg-muted/20">
        <div className="mx-auto max-w-3xl px-4 py-16 sm:py-20">
          <div className="text-center">
            <h2 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
              مساحتك — وأنت تتحكم بها
            </h2>
            <p className="mt-3 text-base leading-relaxed text-muted-foreground">
              الوكلاء يملؤونها أثناء عملهم، وأنت تضيف وتعدّل متى شئت:
            </p>
          </div>
          <ul className="mt-8 space-y-4">
            {CONTROL_POINTS.map((point) => (
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
      <section className="border-t border-border">
        <div className="mx-auto max-w-3xl px-4 py-16 text-center sm:py-20">
          <h2 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
            جرّب مساحة العمل بنفسك
          </h2>
          <p className="mx-auto mt-3 max-w-xl text-base leading-relaxed text-muted-foreground">
            ابدأ محادثة، ارفع مستندك أو اطلب بحثًا، وشاهد كيف تتجمع مخرجاتك
            الموثّقة بجانب الحوار.
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
              href="/learn/how-it-works"
              className={cn(
                buttonVariants({ variant: "outline", size: "lg" }),
                "text-sm font-semibold",
              )}
            >
              كيف يعمل ريحان؟
            </Link>
          </div>
        </div>
      </section>
    </main>
  );
}
