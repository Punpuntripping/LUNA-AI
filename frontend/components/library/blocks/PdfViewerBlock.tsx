import Link from "next/link";
import { FileText, Lock } from "lucide-react";
import { cn } from "@/lib/utils";
import { buttonVariants } from "@/components/ui/button";
import type { PdfViewerBlockProps } from "@/types/library";

/**
 * Gate-consistent PDF surface.
 *
 *   mode="preview" (anon-gated): a first-page preview image under a lock
 *     overlay + signup CTA — the gated bytes never ship.
 *   mode="full": an inline `<object>`/`<iframe>` embed of `pdfUrl`.
 *
 * Server component. NOTE: `full` mode uses the native browser PDF plugin for
 * now; a lazy pdf.js renderer can drop in here later (progressive pages,
 * consistent chrome, no reliance on the UA viewer) without changing this API.
 * The `pdfUrl` should be the backend proxy path (served with
 * `X-Robots-Tag: noindex`).
 */
export function PdfViewerBlock({
  mode,
  pdfUrl,
  title = "المستند الكامل (PDF)",
  previewImageUrl,
  ctaHref = "/login",
  className,
}: PdfViewerBlockProps) {
  if (mode === "full") {
    return (
      <div
        dir="rtl"
        className={cn(
          "overflow-hidden rounded-xl border border-border bg-muted/30",
          className,
        )}
      >
        {/* Native browser PDF viewer — swap for lazy pdf.js later. */}
        <object
          data={pdfUrl}
          type="application/pdf"
          title={title}
          className="h-[70vh] min-h-[420px] w-full"
        >
          <div className="flex flex-col items-center gap-3 p-8 text-center">
            <FileText aria-hidden="true" className="h-8 w-8 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              تعذّر عرض الملف داخل الصفحة.
            </p>
            <a
              href={pdfUrl}
              target="_blank"
              rel="noopener noreferrer"
              className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
            >
              فتح الملف في نافذة جديدة
            </a>
          </div>
        </object>
      </div>
    );
  }

  // Preview (anon-gated): first-page image + lock overlay + CTA.
  return (
    <div
      dir="rtl"
      className={cn(
        "relative overflow-hidden rounded-xl border border-border bg-card",
        className,
      )}
    >
      <div className="relative">
        {previewImageUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={previewImageUrl}
            alt={`${title} — معاينة الصفحة الأولى`}
            loading="lazy"
            className="w-full [mask-image:linear-gradient(to_bottom,black_35%,transparent)]"
          />
        ) : (
          <div
            aria-hidden="true"
            className="flex aspect-[3/4] w-full items-center justify-center bg-muted/40 [mask-image:linear-gradient(to_bottom,black_35%,transparent)]"
          >
            <FileText className="h-12 w-12 text-text-subtle" />
          </div>
        )}

        <div className="absolute inset-0 flex items-end justify-center pb-6">
          <div className="w-full max-w-sm rounded-xl border border-border bg-card/95 p-5 text-center shadow-md backdrop-blur-sm">
            <div className="mx-auto mb-2.5 flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/10 text-primary">
              <Lock aria-hidden="true" className="h-5 w-5" />
            </div>
            <p className="text-sm font-bold text-foreground">
              المستند الكامل متاح للأعضاء
            </p>
            <p className="mx-auto mt-1 max-w-xs text-xs leading-relaxed text-muted-foreground">
              سجّل مجاناً لعرض وتنزيل ملف PDF كاملاً.
            </p>
            <Link
              href={ctaHref}
              className={cn(buttonVariants({ size: "default" }), "mt-3.5 w-full sm:w-auto")}
            >
              سجّل مجاناً لعرض الملف
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
