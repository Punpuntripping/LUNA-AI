"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Sparkles } from "lucide-react";
import { ArtifactPreview } from "@/components/workspace/ArtifactPreview";
import { ReferencePanel, referenceLabel } from "@/components/workspace/ReferencePanel";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { BlogPostPublic } from "@/types";

// Subtype → Arabic chip label. Mirrors WorkspaceCard.tsx SUBTYPE_LABEL so the
// public page speaks the same vocabulary as the in-app workspace.
const SUBTYPE_LABEL: Record<string, string> = {
  report: "تقرير",
  contract: "عقد",
  memo: "مذكرة",
  summary: "ملخص",
  memory_file: "ذاكرة",
  legal_opinion: "رأي قانوني",
  legal_synthesis: "تحليل قانوني",
};

interface PublicAnswerViewProps {
  post: BlogPostPublic;
}

/**
 * Public, read-only reading surface for a shared artifact (مدونة).
 *
 * Anon-accessible: receives the frozen snapshot (``content_md`` +
 * ``references``) as props — NO auth hooks, NO useWorkspaceItemReferences.
 *
 * Citation fluidity mirrors AgentSearchViewer EXACTLY: clicking ``[n]`` in the
 * body sets ``focusedN`` (re-armed via requestAnimationFrame so repeat clicks
 * on the same N re-fire), which drives ReferencePanel's ``focusedReferenceN``
 * (scroll-to-card / open source popup). ``handleFlashDone`` clears it.
 */
export function PublicAnswerView({ post }: PublicAnswerViewProps) {
  const [focusedN, setFocusedN] = useState<number | null>(null);

  const handleBodyCitationClick = useCallback((n: number) => {
    // Clear first so ReferencePanel's effect fires even on consecutive clicks
    // of the same N (the effect only runs when the value changes).
    setFocusedN(null);
    window.requestAnimationFrame(() => setFocusedN(n));
  }, []);

  const handleFlashDone = useCallback(() => {
    setFocusedN(null);
  }, []);

  const references = post.references ?? [];
  const subtypeLabel = post.subtype
    ? SUBTYPE_LABEL[post.subtype] ?? post.subtype
    : null;
  const heading = (post.title ?? "").trim() || post.question_text;

  // Copy button: body + a plain «n-title» reference list under «المراجع», so a
  // reader who copies the answer keeps the [n] markers resolvable. Matches
  // AgentSearchViewer.copyContent.
  const body = post.content_md ?? "";
  const copyContent =
    references.length === 0
      ? body
      : (() => {
          const refLines = [...references]
            .sort((a, b) => a.n - b.n)
            .map((ref) => `${ref.n}-${referenceLabel(ref)}`)
            .join("\n");
          return body.trim().length > 0
            ? `${body}\n\nالمراجع\n${refLines}`
            : `المراجع\n${refLines}`;
        })();

  return (
    <div dir="rtl" className="flex min-h-screen flex-col bg-background">
      {/* Header bar */}
      <header className="sticky top-0 z-20 border-b bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="mx-auto flex w-full max-w-3xl items-center justify-between gap-3 px-4 py-3">
          {/* Logo block — mirrors login/page.tsx rounded badge */}
          <Link href="/login" className="flex items-center gap-2">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary text-sm font-bold text-primary-foreground">
              ريحان
            </span>
            <span className="hidden text-sm font-semibold text-foreground sm:inline">
              المساعد القانوني الذكي
            </span>
          </Link>

          <div className="flex items-center gap-1.5">
            <ThemeToggle />
            <Link
              href="/login"
              className={cn(
                buttonVariants({ variant: "ghost", size: "sm" }),
                "hidden sm:inline-flex",
              )}
            >
              تسجيل الدخول
            </Link>
            <Link
              href="/login"
              className={cn(buttonVariants({ variant: "default", size: "sm" }))}
            >
              إنشاء حساب
            </Link>
          </div>
        </div>
      </header>

      {/* Reading column */}
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-6">
        {/* السؤال block */}
        <section className="rounded-xl border bg-card p-4 shadow-sm">
          <div className="mb-2 flex items-center gap-2">
            <span className="text-xs font-semibold text-muted-foreground">
              السؤال
            </span>
            {subtypeLabel && (
              <span className="inline-flex items-center rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
                {subtypeLabel}
              </span>
            )}
          </div>
          <p className="whitespace-pre-wrap text-base font-medium leading-relaxed text-foreground">
            {post.question_text}
          </p>
        </section>

        {/* Heading */}
        {heading !== post.question_text && (
          <h1 className="mt-6 text-xl font-bold tracking-tight text-foreground">
            {heading}
          </h1>
        )}

        {/* Answer + references — same fluidity as the in-app artifact view */}
        <section className="mt-4 flex min-h-[40vh] flex-col rounded-xl border bg-card shadow-sm">
          <ArtifactPreview
            content={body}
            copyContent={copyContent}
            onCitationClick={handleBodyCitationClick}
            footer={
              references.length > 0 ? (
                <ReferencePanel
                  references={references}
                  focusedReferenceN={focusedN}
                  onFlashDone={handleFlashDone}
                />
              ) : null
            }
          />
        </section>

        {/* Footer CTA panel */}
        <section className="mt-8 overflow-hidden rounded-xl border bg-primary/5 p-6 text-center">
          <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
            <Sparkles className="h-6 w-6" />
          </div>
          <h2 className="text-lg font-bold text-foreground">
            جرّب ريحان مجاناً
          </h2>
          <p className="mx-auto mt-1.5 max-w-md text-sm leading-relaxed text-muted-foreground">
            المساعد القانوني الذكي للمحامين السعوديين — أنشئ تحليلاتك القانونية
            ومذكراتك مدعومة بالأنظمة والسوابق.
          </p>
          <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
            <Link
              href="/login"
              className={cn(buttonVariants({ variant: "default", size: "lg" }))}
            >
              <Sparkles className="h-4 w-4" />
              ابدأ الآن
            </Link>
            <Link
              href="/login"
              className={cn(buttonVariants({ variant: "outline", size: "lg" }))}
            >
              تسجيل الدخول
              <ArrowLeft className="h-4 w-4" />
            </Link>
          </div>
        </section>
      </main>

      {/* Slim footer */}
      <footer className="border-t py-4 text-center text-xs text-muted-foreground">
        مُنشأ عبر{" "}
        <Link href="/login" className="font-medium text-primary hover:underline">
          ريحان
        </Link>
      </footer>
    </div>
  );
}
