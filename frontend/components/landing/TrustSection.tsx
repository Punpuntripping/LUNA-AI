import Link from "next/link";
import { ShieldCheck, BadgeCheck, Lock } from "lucide-react";
import { LEGAL_ROUTES } from "@/lib/legal";

/** Trust pillar — verifiable sources + PDPL-aligned privacy. */
export function TrustSection() {
  return (
    <section className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
      <div className="rounded-3xl border border-border bg-card p-8 shadow-sm sm:p-12">
        <div className="mx-auto max-w-2xl text-center">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10 text-primary">
            <ShieldCheck className="h-6 w-6" />
          </div>
          <h2 className="mt-4 text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
            نُظهر مصادرنا
          </h2>
          <p className="mt-3 text-base leading-relaxed text-muted-foreground">
            ريحان لا يختلق المعلومة. كل إجابة مبنية على مصادر رسمية موثّقة وقابلة
            للتحقق برابطها المباشر — وخصوصيتك محمية وفق نظام حماية البيانات
            الشخصية في المملكة (PDPL).
          </p>
        </div>

        <div className="mx-auto mt-8 grid max-w-2xl gap-4 sm:grid-cols-2">
          <div className="flex items-start gap-3 rounded-xl border border-border/70 bg-background p-4">
            <BadgeCheck className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
            <div>
              <h3 className="text-sm font-bold text-foreground">
                مصادر قابلة للتحقق
              </h3>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                كل استشهاد مربوط برابط مصدره الرسمي من الجهة الحكومية المختصّة.
              </p>
            </div>
          </div>
          <div className="flex items-start gap-3 rounded-xl border border-border/70 bg-background p-4">
            <Lock className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
            <div>
              <h3 className="text-sm font-bold text-foreground">
                خصوصيتك محمية
              </h3>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                نلتزم بحماية بياناتك وفق الأنظمة السعودية. اطّلع على{" "}
                <Link
                  href={LEGAL_ROUTES.privacy}
                  className="font-medium text-primary underline-offset-4 hover:underline"
                >
                  سياسة الخصوصية
                </Link>
                .
              </p>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
