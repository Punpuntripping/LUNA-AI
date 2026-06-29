import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { PRIMARY_CTA_HREF, SUPPORT_EMAIL } from "./content";

/**
 * Closing call to action — early-adopter framing. Rayhan is in trial launch;
 * access is currently granted via an activation code (see /pricing), so the
 * secondary path points at the support inbox for early access.
 */
export function FinalCtaSection() {
  return (
    <section className="mx-auto max-w-4xl px-4 pb-20 pt-4">
      <div className="relative overflow-hidden rounded-3xl bg-primary px-6 py-14 text-center text-primary-foreground sm:px-12">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-x-0 -top-20 mx-auto h-56 max-w-md rounded-full bg-primary-foreground/10 blur-3xl"
        />
        <div className="relative">
          <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">
            كن من أوائل المحامين المستخدمين
          </h2>
          <p className="mx-auto mt-3 max-w-xl text-base leading-relaxed text-primary-foreground/85">
            ريحان في مرحلة الإطلاق التجريبي. انضمّ الآن واحصل على وصول مبكر
            للمنصة.
          </p>

          <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <Link
              href={PRIMARY_CTA_HREF}
              className={cn(
                buttonVariants({ variant: "secondary", size: "lg" }),
                "w-full gap-2 text-base font-semibold sm:w-auto",
              )}
            >
              انضمّ الآن
              <ArrowLeft className="h-4 w-4" />
            </Link>
            <a
              href={`mailto:${SUPPORT_EMAIL}`}
              dir="ltr"
              className={cn(
                buttonVariants({ variant: "outline", size: "lg" }),
                "w-full border-primary-foreground/30 bg-transparent text-base text-primary-foreground hover:bg-primary-foreground/10 hover:text-primary-foreground sm:w-auto",
              )}
            >
              {SUPPORT_EMAIL}
            </a>
          </div>
        </div>
      </div>
    </section>
  );
}
