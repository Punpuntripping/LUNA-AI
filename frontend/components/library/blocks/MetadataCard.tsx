import Link from "next/link";
import { Info } from "lucide-react";
import { cn } from "@/lib/utils";
import { StatusBadge } from "@/components/library/blocks/StatusBadge";
import type { MetadataCardProps } from "@/types/library";

/**
 * The «المعلومات الأساسية» card: a titled surface with a label/value grid
 * (الجهة المصدرة، رقم المرسوم، القطاع، …) and, when a `status` is supplied, an
 * embedded StatusBadge in the header. Each cell carries a subtle start-side
 * accent rule so the grid reads as a structured info panel. Values with an
 * `href` render as internal links and long decree citations wrap. An optional
 * `footer` renders below the grid behind a divider — the judgment page puts its
 * «ملخص ريحان» reveal there. Server component (a client `footer` is fine: it is
 * passed in as an already-created element). Uses a `<dl>` for semantics + rich-
 * result clarity.
 */
export function MetadataCard({
  items,
  status,
  title = "المعلومات الأساسية",
  footer,
  className,
}: MetadataCardProps) {
  return (
    <section
      dir="rtl"
      className={cn(
        "rounded-xl border border-border bg-card p-4 shadow-xs sm:p-5",
        className,
      )}
    >
      <div className="mb-4 flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-sm font-bold text-foreground">
          <Info aria-hidden="true" className="h-4 w-4 shrink-0 text-primary" />
          {title}
        </h2>
        {status && <StatusBadge status={status} />}
      </div>

      <dl className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2">
        {items.map((item, index) => (
          <div
            key={`${item.label}-${index}`}
            className="flex flex-col gap-1 border-s-2 border-border ps-3"
          >
            <dt className="text-[11px] font-medium text-text-muted">
              {item.label}
            </dt>
            <dd className="break-words text-sm font-semibold text-foreground">
              {item.href ? (
                <Link
                  href={item.href}
                  className="text-primary underline-offset-2 transition-colors hover:underline"
                >
                  {item.value}
                </Link>
              ) : (
                item.value
              )}
            </dd>
          </div>
        ))}
      </dl>

      {/* `empty:hidden` — the footer is a CLIENT component whose whole render
          can go away (the judgment summary button disappears once revealed).
          Keying the divider off `footer` being truthy would leave a stray ruled
          strip behind; keying it off `:empty` follows what actually rendered.
          Do not introduce whitespace inside this div — a text node defeats it. */}
      <div className="mt-4 border-t border-border pt-4 empty:hidden">{footer}</div>
    </section>
  );
}
