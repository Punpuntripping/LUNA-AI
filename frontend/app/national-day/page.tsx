import type { Metadata } from "next";
import { SitePageShell } from "@/components/site/SitePageShell";
import { ogImageUrl } from "@/lib/seo/og";
import { NationalDayCard } from "./NationalDayCard";

// Public greeting-card generator for the 96th Saudi National Day.
//
// The visitor types a name, picks one of six colourways, and saves a 1080×1350
// PNG. Nothing is submitted and nothing is stored: the card is drawn on the
// visitor's own canvas and downloaded as a file. That is why this page needs no
// table, no anon-insert policy against `067_security_rls_lockdown.sql`, and no
// moderation queue — there is no shared surface for anyone to write to.
//
// NOTE the route is also listed in `components/auth/AuthGuard.tsx`'s
// PUBLIC_PREFIXES. `isPublicPath` returns false for anything not on that list,
// so without the entry every logged-out visitor — which is all of them — is
// bounced to /login and the campaign link dies silently.

const TITLE = "هنّئ باليوم الوطني 96 — بطاقة من ريحان";
const DESCRIPTION =
  "اكتب اسمك، اختر لون البطاقة، واحفظ تهنئتك باليوم الوطني السعودي 96. من ريحان، مساعدك القانوني الذكي.";

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: "/national-day" },
  openGraph: {
    title: TITLE,
    description: DESCRIPTION,
    type: "website",
    url: "/national-day",
    siteName: "ريحان",
    locale: "ar_SA",
    images: [
      { url: ogImageUrl(TITLE), width: 1200, height: 630, alt: TITLE },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: TITLE,
    description: DESCRIPTION,
    images: [ogImageUrl(TITLE)],
  },
};

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default function NationalDayPage() {
  return (
    <SitePageShell>
      <main>
        <NationalDayCard />
      </main>
    </SitePageShell>
  );
}
