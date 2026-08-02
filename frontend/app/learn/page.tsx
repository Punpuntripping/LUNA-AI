import type { Metadata } from "next";
import Link from "next/link";
import { ArrowLeft, Clock } from "lucide-react";
import { SitePageShell } from "@/components/site/SitePageShell";
import { SITE_NAV } from "@/lib/nav/site-nav";

export const metadata: Metadata = {
  title: "اكتشف ريحان",
  description:
    "أدلة ريحان: كيف يعمل ريحان، دليل الاستخدام، أفضل الممارسات لصياغة أسئلتك القانونية، وأمثلة أسئلة حقيقية.",
  // Still noindex: one live lesson is thin for a hub. Lift this (and add /learn
  // to the sitemap static section) once a second lesson lands.
  robots: { index: false, follow: true },
};

// اكتشف ريحان hub — lists the lessons straight from the nav SSoT: an enabled
// child renders as a live card, a disabled one as a «قريباً» card, so each
// lesson phase only flips its site-nav flag and the hub follows. First live
// lesson: /learn/how-it-works (discover_rayhan_agents.md). Public (AuthGuard
// /learn prefix).
const LESSONS = SITE_NAV.find((g) => g.href === "/learn")?.children ?? [];

// eslint-disable-next-line import/no-default-export
export default function LearnHubPage() {
  return (
    <SitePageShell>
      <main className="mx-auto max-w-4xl px-4 py-16 sm:py-20">
        <div className="mx-auto max-w-2xl text-center">
          <h1 className="text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
            اكتشف ريحان
          </h1>
          <p className="mt-4 text-base leading-relaxed text-muted-foreground">
            أدلة تشرح كيف يعمل ريحان خطوة بخطوة، وأفضل الممارسات لصياغة سؤالك
            القانوني والحصول على أدقّ النتائج، مع أمثلة أسئلة حقيقية وإجاباتها.
          </p>
        </div>

        <div className="mt-12 grid gap-5 sm:grid-cols-2">
          {LESSONS.map((lesson) =>
            lesson.enabled ? (
              <Link
                key={lesson.href}
                href={lesson.href}
                className="group rounded-2xl border border-border bg-card p-6 shadow-sm transition-colors hover:border-primary/40 hover:bg-muted/30"
              >
                <h2 className="text-base font-bold text-foreground">
                  {lesson.label}
                </h2>
                <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                  {lesson.description}
                </p>
                <span className="mt-4 inline-flex items-center gap-1.5 text-sm font-semibold text-primary">
                  اقرأ الدليل
                  <ArrowLeft className="h-4 w-4 transition-transform group-hover:-translate-x-0.5" />
                </span>
              </Link>
            ) : (
              <div
                key={lesson.href}
                className="rounded-2xl border border-dashed border-border bg-muted/20 p-6"
              >
                <div className="flex items-center justify-between gap-3">
                  <h2 className="text-base font-bold text-muted-foreground">
                    {lesson.label}
                  </h2>
                  <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-border bg-muted/40 px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
                    <Clock className="h-3 w-3" />
                    قريباً
                  </span>
                </div>
                <p className="mt-2 text-sm leading-relaxed text-muted-foreground/80">
                  {lesson.description}
                </p>
              </div>
            ),
          )}
        </div>
      </main>
    </SitePageShell>
  );
}
