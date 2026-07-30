"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, Loader2, PenLine } from "lucide-react";
import { cn } from "@/lib/utils";
import { buttonVariants } from "@/components/ui/button";
import { useAuthStore } from "@/stores/auth-store";
import { formsApi } from "@/lib/api";
import { setPendingIntent } from "@/lib/post-login-intent";

interface OpenInRayhanCtaProps {
  /** The published form slug to copy into the caller's قوالبي. */
  slug: string;
  /** Form title (for the CTA copy). */
  title: string;
}

/**
 * «افتح هذا النموذج في ريحان» — the main /forms conversion CTA.
 *
 *   Authed → copy the form into قوالبي (`POST /forms/{slug}/open-in-writer`) and
 *            open the writer at `/templates/{template_id}`.
 *   Anon   → stash the `open_form_in_writer` intent → /login; the AuthGuard
 *            consumer finishes the copy + writer handoff after sign-up.
 *
 * Client component — the ONE interactive element on an otherwise static form page.
 */
export function OpenInRayhanCta({ slug, title }: OpenInRayhanCtaProps) {
  const router = useRouter();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleClick(): Promise<void> {
    setError(null);
    if (!isAuthenticated) {
      setPendingIntent({ type: "open_form_in_writer", slug });
      router.push("/login");
      return;
    }
    setBusy(true);
    try {
      const template = await formsApi.openInWriter(slug);
      router.push(`/templates/${template.template_id}`);
    } catch {
      setBusy(false);
      setError("تعذّر فتح النموذج، حاول مجدداً");
    }
  }

  return (
    <section
      dir="rtl"
      className="rounded-2xl border border-primary/30 bg-primary/5 p-5 text-center"
    >
      <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10 text-primary">
        <PenLine aria-hidden="true" className="h-6 w-6" />
      </div>
      <h2 className="text-base font-bold text-foreground">
        افتح هذا النموذج في ريحان
      </h2>
      <p className="mx-auto mt-1.5 max-w-md text-sm leading-relaxed text-text-secondary">
        انسخ «{title}» إلى قوالبك وعدّله مباشرة داخل محرّر ريحان مع الحفاظ على
        صياغته النظامية.
      </p>
      <button
        type="button"
        onClick={handleClick}
        disabled={busy}
        className={cn(
          buttonVariants({ size: "lg" }),
          "mt-4 disabled:cursor-not-allowed disabled:opacity-60",
        )}
      >
        {busy ? (
          <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
        ) : (
          <ArrowLeft aria-hidden="true" className="h-4 w-4" />
        )}
        افتح النموذج في ريحان
      </button>
      {error && (
        <p role="alert" className="mt-2 text-xs font-medium text-destructive">
          {error}
        </p>
      )}
    </section>
  );
}
