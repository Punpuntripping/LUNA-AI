import Link from "next/link";
import { Mail } from "lucide-react";
import { LEGAL_ROUTES } from "@/lib/legal";
import { SUPPORT_EMAIL } from "@/components/landing/content";

/**
 * Full site footer for every public page (landing, /about_us, /audiences,
 * /pricing, and the مدونة surfaces). Four zones in an RTL grid: brand +
 * company blurb, platform navigation, legal links, and contact — closed by a
 * copyright bar. Server component — plain links only.
 */

const PLATFORM_LINKS = [
  { href: "/about_us", label: "عن ريحان" },
  { href: "/audiences", label: "ريحان يستهدف مين؟" },
  { href: "/pricing", label: "الباقات والأسعار" },
  { href: "/blog", label: "المدونة" },
] as const;

const LEGAL_LINKS = [
  { href: LEGAL_ROUTES.terms, label: "الشروط والأحكام" },
  { href: LEGAL_ROUTES.privacy, label: "سياسة الخصوصية" },
] as const;

export function SiteFooter() {
  return (
    <footer className="border-t border-border bg-muted/30">
      <div className="mx-auto grid max-w-6xl gap-10 px-4 py-12 sm:grid-cols-2 lg:grid-cols-4">
        {/* Brand + company blurb */}
        <div className="flex flex-col gap-3 lg:col-span-2 lg:max-w-sm">
          <div className="flex items-center gap-2.5">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary text-sm font-bold text-primary-foreground">
              ريحان
            </span>
            <span className="text-sm font-bold text-foreground">
              شركة ريحان تك
            </span>
          </div>
          <p className="text-sm leading-relaxed text-muted-foreground">
            المساعد القانوني الذكي في الأنظمة السعودية — بحث موثّق في الأنظمة
            والأحكام القضائية والخدمات الحكومية، وصياغة قانونية كل استشهاد فيها
            مربوط بمصدره الرسمي.
          </p>
          <p className="text-xs text-muted-foreground">منصة سعودية</p>
        </div>

        {/* Platform navigation */}
        <nav aria-label="روابط المنصة" className="flex flex-col gap-2.5">
          <h3 className="text-sm font-semibold text-foreground">المنصة</h3>
          {PLATFORM_LINKS.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="text-sm text-muted-foreground transition-colors hover:text-foreground"
            >
              {link.label}
            </Link>
          ))}
        </nav>

        {/* Legal + contact */}
        <div className="flex flex-col gap-2.5">
          <h3 className="text-sm font-semibold text-foreground">
            قانوني وتواصل
          </h3>
          {LEGAL_LINKS.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="text-sm text-muted-foreground transition-colors hover:text-foreground"
            >
              {link.label}
            </Link>
          ))}
          <a
            href={`mailto:${SUPPORT_EMAIL}`}
            className="inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
          >
            <Mail className="h-3.5 w-3.5 shrink-0" />
            <span dir="ltr">{SUPPORT_EMAIL}</span>
          </a>
        </div>
      </div>

      {/* Copyright bar */}
      <div className="border-t border-border/60">
        <p className="mx-auto max-w-6xl px-4 py-4 text-center text-xs text-muted-foreground">
          © 2026 شركة ريحان تك — جميع الحقوق محفوظة.
        </p>
      </div>
    </footer>
  );
}
