import type { Metadata } from "next";
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
