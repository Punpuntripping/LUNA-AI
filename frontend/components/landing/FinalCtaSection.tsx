import Link from "next/link";
import { ArrowLeft, MessageCircle } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  PRIMARY_CTA_HREF,
  SUPPORT_EMAIL,
  SUPPORT_WHATSAPP,
  SUPPORT_WHATSAPP_HREF,
  SUPPORT_WHATSAPP_NOTE,
} from "./content";

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
            كن من أوائل المستخدمين
          </h2>
          <p className="mx-auto mt-3 max-w-xl text-base leading-relaxed text-primary-foreground/85">
            ريحان في مرحلة الإطلاق التجريبي. انضمّ الآن واحصل على وصول مبكر
            للمنصة.
          </p>

          {/* flex-wrap: three pills no longer fit on one line at the sm break. */}
          <div className="mt-8 flex flex-col flex-wrap items-center justify-center gap-3 sm:flex-row">
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
            {/* WhatsApp above the inbox — fastest channel first. Chat only, so
                the «واتساب فقط» qualifier travels with the number everywhere. */}
            <a
              href={SUPPORT_WHATSAPP_HREF}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={`تواصل معنا عبر واتساب على الرقم ${SUPPORT_WHATSAPP} — ${SUPPORT_WHATSAPP_NOTE}`}
              className={cn(
                buttonVariants({ variant: "outline", size: "lg" }),
                "w-full gap-2 border-primary-foreground/30 bg-transparent text-base text-primary-foreground hover:bg-primary-foreground/10 hover:text-primary-foreground sm:w-auto",
              )}
            >
              <MessageCircle className="h-4 w-4 shrink-0" />
              <span dir="ltr">{SUPPORT_WHATSAPP}</span>
              <span className="text-sm opacity-80">
                {SUPPORT_WHATSAPP_NOTE}
              </span>
            </a>
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
