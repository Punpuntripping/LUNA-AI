import { LegalLinksFooter } from "@/components/legal/LegalLinksFooter";

/** Page footer — brand line + legal links. */
export function LandingFooter() {
  return (
    <footer className="border-t border-border bg-background">
      <div className="mx-auto flex max-w-5xl flex-col items-center gap-4 px-4 py-10 text-center">
        <div className="flex items-center gap-2.5">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary text-sm font-bold text-primary-foreground">
            ريحان
          </span>
          <span className="text-sm font-semibold text-foreground">
            شركة ريحان تك
          </span>
        </div>
        <p className="text-xs text-muted-foreground">
          المساعد القانوني الذكي للمحامي السعودي
        </p>
        <LegalLinksFooter />
        <p className="text-xs text-muted-foreground">
          © 2026 ريحان. جميع الحقوق محفوظة.
        </p>
      </div>
    </footer>
  );
}
