import type { Metadata, Viewport } from "next";
import { Noto_Naskh_Arabic } from "next/font/google";
import "./globals.css";
import { Providers } from "@/components/providers";
import { JsonLd } from "@/components/seo/JsonLd";
import { buildOrganization, buildWebSite } from "@/lib/seo/schema";

const notoNaskhArabic = Noto_Naskh_Arabic({
  subsets: ["arabic"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-arabic",
  display: "swap",
});

export const metadata: Metadata = {
  metadataBase: new URL("https://rayhanai.com"),
  title: "ريحان - المساعد القانوني الذكي",
  description: "مساعد ذكاء اصطناعي للمحامين السعوديين",
};

/**
 * Mobile browser-chrome control. These are the only levers a web app actually
 * has over the browser's own UI — everything else (hiding the address bar on
 * demand) requires either a user gesture or an installed PWA.
 *
 * - `themeColor` tints Chrome Android's address bar and the iOS status bar to
 *   the app canvas, so the browser chrome visually merges into the page.
 *   Values are --canvas from globals.css (light L1 / dark D6).
 * - `viewportFit: "cover"` lets the page paint under the notch; layouts that
 *   reach a screen edge must pair it with env(safe-area-inset-*).
 * - `interactiveWidget: "resizes-content"` makes the on-screen keyboard shrink
 *   the layout instead of floating over it — without this the composer is
 *   covered by the keyboard on Chrome Android.
 */
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  interactiveWidget: "resizes-content",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#F7F2EC" },
    { media: "(prefers-color-scheme: dark)", color: "#1A1917" },
  ],
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ar" dir="rtl" suppressHydrationWarning>
      <body className={`${notoNaskhArabic.variable} font-sans antialiased`}>
        {/* Site-wide structured data — rendered once, brand + site identity. */}
        <JsonLd data={[buildOrganization(), buildWebSite()]} />
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
