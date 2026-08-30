import Link from "next/link";
import { Mail, MessageCircle } from "lucide-react";
import { LEGAL_ROUTES } from "@/lib/legal";
import {
  SUPPORT_EMAIL,
  SUPPORT_WHATSAPP,
  SUPPORT_WHATSAPP_HREF,
  SUPPORT_WHATSAPP_NOTE,
} from "@/components/landing/content";

/**
 * Full site footer for every public page (landing, /about_us, /audiences,
 * /pricing, and the مدونة surfaces). Four zones in an RTL grid: brand +
 * company blurb, platform navigation, legal links, and contact — closed by a
 * copyright bar. Server component — plain links only.
 */

// Footer nav columns mirror the header IA (see lib/nav/site-nav.ts). Keep the
// «المكتبة» column in sync when a library section flips from placeholder to live.
const PLATFORM_LINKS = [
  { href: "/about_us", label: "عن ريحان" },
  { href: "/audiences", label: "لمن ريحان؟" },
  { href: "/vs-chatgpt", label: "ريحان مقابل ChatGPT" },
  { href: "/pricing", label: "الباقات والأسعار" },
] as const;

const LIBRARY_LINKS = [
  { href: "/library", label: "المكتبة القانونية" },
  { href: "/learn", label: "اكتشف ريحان" },
  { href: "/blog", label: "المدونة" },
] as const;

// «سياسة حد الاستخدام» sits under اكتشف ريحان in the header (it is a lesson, not
// a legal document) but is listed here too: it is named a سياسة, so this column
// is where a reader hunting for it will look, and it is the page the pricing
// argument leans on — two clicks deep in a dropdown is too far to hide it.
const LEGAL_LINKS = [
  { href: LEGAL_ROUTES.terms, label: "الشروط والأحكام" },
  { href: LEGAL_ROUTES.privacy, label: "سياسة الخصوصية" },
  { href: "/learn/usage-limits", label: "سياسة حد الاستخدام" },
] as const;

export function SiteFooter() {
  return (
    <footer className="border-t border-border bg-muted/30">
      <div className="mx-auto grid max-w-6xl gap-10 px-4 py-12 sm:grid-cols-2 lg:grid-cols-5">
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
            والأحكام القضائية، وصياغة قانونية كل استشهاد فيها مربوط بمصدره
            الرسمي.
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

        {/* Library navigation */}
        <nav aria-label="المكتبة القانونية" className="flex flex-col gap-2.5">
          <h3 className="text-sm font-semibold text-foreground">المكتبة</h3>
          {LIBRARY_LINKS.map((link) => (
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
          {/* WhatsApp sits above the inbox: it is the fastest reply channel we
              have. It is a chat line only — hence the «واتساب فقط» qualifier. */}
          <a
            href={SUPPORT_WHATSAPP_HREF}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={`تواصل معنا عبر واتساب على الرقم ${SUPPORT_WHATSAPP} — ${SUPPORT_WHATSAPP_NOTE}`}
            className="inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
          >
            <MessageCircle className="h-3.5 w-3.5 shrink-0" />
            <span dir="ltr">{SUPPORT_WHATSAPP}</span>
            <span className="text-xs opacity-75">{SUPPORT_WHATSAPP_NOTE}</span>
          </a>
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
