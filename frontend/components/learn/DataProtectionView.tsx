import Link from "next/link";
import {
  Cpu,
  Database,
  EyeOff,
  Lock,
  ScanText,
  Server,
  ShieldCheck,
} from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * «كيف نحمي بياناتك وبيانات عملائك؟» — the third اكتشف ريحان lesson
 * (/learn/data-protection). Marketing-register companion to the two legal
 * pages: every claim here is a restatement of /privacy (processor categories,
 * no-selling / no-training, RLS isolation) or /masking (the تقنيع المعرّفات
 * mechanics) — if a claim changes, those pages change FIRST and this lesson
 * follows (see .claude/plans/discover_rayhan_data_protection.md). Alibaba
 * Cloud is the one named processing partner (owner decision 2026-08-02);
 * models stay open-source and unnamed as in the sibling lessons.
 */

const STAY_CARDS = [
  {
    icon: Database,
    title: "معزولة على حسابك وحده",
    body: "محادثاتك ومستنداتك وقوالبك محفوظة في خوادمنا، معزولة على مستوى حسابك بسياسات أمان تُطبَّق في قاعدة البيانات نفسها — فلا يصل إليها مستخدم آخر ولا تُستخدم لخدمته.",
  },
  {
    icon: Lock,
    title: "مشفّرة ومحكومة الوصول",
    body: "الاتصال مشفّر من جهازك إلى خوادمنا، والوصول الداخلي محكوم بضوابط صارمة — نبني كل ميزة على مبدأ «الخصوصية حسب التصميم».",
  },
  {
    icon: EyeOff,
    title: "لا بيع ولا تدريب",
    body: "لا نبيع بياناتك لأي طرف، ولا نستخدم محتواك لتدريب نماذج عامة أو لمصلحة غيرك — ما تُدخله في ريحان يخدمك أنت وحدك.",
  },
] as const;

const PROCESSOR_CARDS = [
  {
    icon: Cpu,
    title: "تشغيل النماذج",
    body: "نماذجنا مفتوحة المصدر تعمل لدى مزوّدي حوسبة عالميين من الطراز الأول — مثل Alibaba Cloud — يصلهم من طلبك ما يلزم لإنتاج إجابتك فقط، ولا يُستخدم لغير ذلك.",
  },
  {
    icon: ScanText,
    title: "قراءة مستنداتك",
    body: "استخلاص النصوص من الملفات والصور الممسوحة يتولاه مزوّد متخصص، فيتحول مستندك إلى نص يعمل عليه الوكلاء.",
  },
  {
    icon: Server,
    title: "الاستضافة والمراقبة",
    body: "البنية السحابية وقواعد البيانات وأدوات المراقبة الفنية التي تُبقي الخدمة آمنة ومستقرة على مدار الساعة.",
  },
] as const;

const MASKING_STEPS = [
  {
    title: "قبل أن يغادر النص",
    body: "تُكتشف المعرّفات الشخصية — أرقام الهوية والجوال والآيبان والبريد الإلكتروني — وتُستبدل ببدائل عشوائية تشبهها شكلًا وطولًا.",
  },
  {
    title: "أثناء المعالجة",
    body: "يفهم النموذج أن هذا رقم هوية وذاك رقم جوال دون أن يرى القيم الحقيقية — فجدول الاستبدال يبقى على خوادمنا ولا يغادرها أبدًا.",
  },
  {
    title: "عند عرض الرد",
    body: "تُستعاد قيمك الحقيقية تلقائيًا قبل أن يصلك الجواب — تقرأ أرقامك كما كتبتها تمامًا.",
  },
] as const;

const CONTROL_POINTS = [
  {
    title: "وضع السرية بيدك",
    body: "التقنيع مفعّل تلقائيًا لكل حساب، وتستطيع إيقافه أو إعادته في أي وقت من الإعدادات.",
  },
  {
    title: "حقوقك محفوظة نظامًا",
    body: "العلم والوصول والتصحيح والحذف وسحب الموافقة — وفق نظام حماية البيانات الشخصية، وتفصّلها سياسة الخصوصية.",
  },
  {
    title: "حذف حسابك متى شئت",
    body: "من إعدادات الحساب، مع مهلة تراجع قبل الحذف النهائي.",
  },
  {
    title: "المشاركة قرارك",
    body: "لا يخرج شيء من محادثاتك إلى العلن إلا ما شاركته أنت بنفسك — برابط أو مدونة.",
  },
] as const;

export function DataProtectionView() {
  return (
    <main>
      {/* Hero */}
      <section className="border-b border-border/60 bg-muted/20">
        <div className="mx-auto max-w-3xl px-4 py-16 text-center sm:py-20">
          <span className="inline-flex items-center rounded-full border border-primary/30 bg-primary/5 px-3 py-1 text-xs font-medium text-primary">
            اكتشف ريحان
          </span>
          <h1 className="mt-5 text-3xl font-bold leading-tight tracking-tight text-foreground sm:text-4xl">
            كيف نحمي بياناتك وبيانات عملائك؟
          </h1>
          <p className="mx-auto mt-4 max-w-2xl text-base leading-relaxed text-muted-foreground">
            سرية معلومات موكليك ليست ميزة إضافية في ريحان، بل أساس في تصميم
            المنتج: بياناتك محفوظة في خوادمنا ولا تغادرها إلا للمعالجة اللازمة
            لخدمتك — مقنَّعة المعرّفات، وبالقدر الأدنى.
          </p>
        </div>
      </section>

      {/* 1 — Data at rest: stays on our servers */}
      <section className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
            بياناتك تبقى عندنا
          </h2>
          <p className="mt-3 text-base leading-relaxed text-muted-foreground">
            كل ما تحفظه في ريحان — محادثات ومستندات وقوالب وملاحظات — يقيم في
            خوادمنا وتحت حراستها:
          </p>
        </div>
        <div className="mt-10 grid gap-5 sm:grid-cols-3">
          {STAY_CARDS.map((card) => {
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

      {/* 2 — Why processors at all, and who they are */}
      <section className="border-t border-border bg-muted/20">
        <div className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
          <div className="mx-auto max-w-2xl text-center">
            <h2 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
              لماذا نستعين بموردين للمعالجة؟
            </h2>
            <p className="mt-3 text-base leading-relaxed text-muted-foreground">
              الأصل عندنا أن بياناتك لا تغادر خوادمنا. لكن تشغيل ذكاء اصطناعي
              بمستوى ريحان يتطلب معالجة متخصصة لا يوفرها خادم واحد — فنستعين
              بشركاء معالجة عالميين، بالقدر اللازم لخدمتك فقط:
            </p>
          </div>
          <div className="mt-10 grid gap-5 sm:grid-cols-3">
            {PROCESSOR_CARDS.map((card) => {
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
          <p className="mx-auto mt-8 max-w-2xl rounded-xl border border-primary/20 bg-primary/5 p-4 text-center text-sm leading-relaxed text-foreground">
            نختار شركاءنا بسمعتهم العالمية، ونُلزمهم تعاقديًا بمعالجة بياناتك
            وفق تعليماتنا فقط وبالقدر اللازم لتقديم الخدمة — كما تفصّل{" "}
            <Link
              href="/privacy"
              className="font-semibold text-primary underline-offset-4 hover:underline"
            >
              سياسة الخصوصية
            </Link>
            .
          </p>
        </div>
      </section>

      {/* 3 — Identifier masking: the shield before anything leaves */}
      <section className="border-t border-border">
        <div className="mx-auto max-w-3xl px-4 py-16 sm:py-20">
          <div className="text-center">
            <h2 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
              تقنيع المعرّفات — درع إضافي قبل المغادرة
            </h2>
            <p className="mt-3 text-base leading-relaxed text-muted-foreground">
              وماذا عن المعرّفات الشخصية في رسائلك — هوية موكلك، جواله، حسابه
              البنكي؟ هنا تعمل خدمة تقنيع المعرّفات، انسجامًا مع نظام حماية
              البيانات الشخصية:
            </p>
          </div>
          <ol className="mt-8 space-y-5">
            {MASKING_STEPS.map((step, i) => (
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

          {/* Worked example — same numbers as the /masking legal page */}
          <div className="mt-8 space-y-3 rounded-xl border border-border bg-card p-5">
            <div>
              <p className="text-xs font-semibold text-muted-foreground">
                ما تكتبه أنت:
              </p>
              <p className="mt-1 text-sm leading-relaxed text-foreground">
                موكلي رقم هويته 1032323434 وجواله 0501234567، ويطالب بمبلغ
                500,000 ريال وفق المادة 77.
              </p>
            </div>
            <div className="border-t border-border/60 pt-3">
              <p className="text-xs font-semibold text-muted-foreground">
                ما يصل إلى نموذج الذكاء الاصطناعي:
              </p>
              <p className="mt-1 text-sm leading-relaxed text-foreground">
                موكلي رقم هويته 1032849275 وجواله 0501778392، ويطالب بمبلغ
                500,000 ريال وفق المادة 77.
              </p>
            </div>
          </div>

          <p className="mt-6 text-center text-sm leading-relaxed text-muted-foreground">
            المبالغ والتواريخ وأرقام المواد تصل كما هي — فالتحليل القانوني
            يحتاجها. والأسماء خارج نطاق هذه التقنية، فالأفضل الإشارة إلى
            الأشخاص بصفاتهم («الطرف الأول»، «المدعى عليه»). التفاصيل الكاملة في
            صفحة{" "}
            <Link
              href="/masking"
              className="font-semibold text-primary underline-offset-4 hover:underline"
            >
              تقنيع المعرّفات
            </Link>
            .
          </p>
        </div>
      </section>

      {/* 4 — Your controls */}
      <section className="border-t border-border bg-muted/20">
        <div className="mx-auto max-w-3xl px-4 py-16 sm:py-20">
          <div className="text-center">
            <h2 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
              وأنت بيدك القرار
            </h2>
            <p className="mt-3 text-base leading-relaxed text-muted-foreground">
              الحماية عندنا افتراضية، والتحكم فيها لك:
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
            بياناتك أمانة — هكذا نصونها
          </h2>
          <p className="mx-auto mt-3 max-w-xl text-base leading-relaxed text-muted-foreground">
            ابدأ بثقة: اطرح سؤالك وارفع مستنداتك، والحماية تعمل في الخلفية من
            أول رسالة.
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
              href="/privacy"
              className={cn(
                buttonVariants({ variant: "outline", size: "lg" }),
                "text-sm font-semibold",
              )}
            >
              سياسة الخصوصية
            </Link>
          </div>
        </div>
      </section>
    </main>
  );
}
