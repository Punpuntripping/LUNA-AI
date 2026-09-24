import type { Metadata } from "next";
import { LayoutGrid, Maximize2, MessageSquare } from "lucide-react";
import { SitePageShell } from "@/components/site/SitePageShell";
import { AppInstallGuide } from "@/components/install/AppInstallGuide";
import { ogImageUrl } from "@/lib/seo/og";

const TITLE = "ريحان على جوالك — ثبّت ريحان على شاشتك الرئيسية";
const DESCRIPTION =
  "ثبّت ريحان على جوالك في خطوتين: أيقونة على الشاشة الرئيسية، تفتح مباشرة على المحادثة وبدون شريط المتصفح. خطوات الآيفون والأندرويد.";
const OG_IMAGE = ogImageUrl("ريحان على جوالك");

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: "/app" },
  openGraph: {
    title: TITLE,
    description: DESCRIPTION,
    type: "website",
    url: "/app",
    siteName: "ريحان",
    locale: "ar_SA",
    images: [{ url: OG_IMAGE, width: 1200, height: 630, alt: TITLE }],
  },
  twitter: {
    card: "summary_large_image",
    title: TITLE,
    images: [OG_IMAGE],
  },
};

const BENEFITS = [
  { icon: LayoutGrid, text: "أيقونة على الشاشة الرئيسية" },
  { icon: Maximize2, text: "بدون شريط المتصفح" },
  { icon: MessageSquare, text: "يفتح مباشرة على المحادثة" },
] as const;

// «ريحان على جوالك» — the shareable install guide (install_app_nudge.md §C).
// Static, no auth, indexable and listed in the sitemap's `static` section.
// Linked from the footer only — never the desktop header, and no popup on the
// public wings. The platform-aware steps are the client `AppInstallGuide`.
// eslint-disable-next-line import/no-default-export
export default function AppInstallPage() {
  return (
    <SitePageShell>
      <main className="mx-auto flex w-full max-w-xl flex-col gap-8 px-4 py-12">
        <header className="flex flex-col gap-3 text-center">
          <h1 className="text-3xl font-bold text-foreground">ريحان على جوالك</h1>
          <p className="text-base leading-relaxed text-muted-foreground">
            ثبّته على شاشتك الرئيسية وافتحه كتطبيق بضغطة واحدة — بلا متجر
            تطبيقات ولا تحميل.
          </p>
        </header>

        <ul className="grid gap-3 sm:grid-cols-3">
          {BENEFITS.map(({ icon: Icon, text }) => (
            <li
              key={text}
              className="flex items-center gap-3 rounded-xl border border-border bg-card p-4 text-sm font-medium text-foreground sm:flex-col sm:text-center"
            >
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
                <Icon className="h-4 w-4" />
              </span>
              {text}
            </li>
          ))}
        </ul>

        <section aria-labelledby="install-steps-heading" className="flex flex-col gap-4">
          <h2 id="install-steps-heading" className="text-lg font-semibold text-foreground">
            خطوات التثبيت
          </h2>
          <AppInstallGuide />
        </section>
      </main>
    </SitePageShell>
  );
}
