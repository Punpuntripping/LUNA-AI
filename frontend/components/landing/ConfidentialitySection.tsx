import Link from "next/link";
import { ArrowLeft, BadgeCheck, EyeOff, Lock, ShieldCheck } from "lucide-react";
import { LEGAL_ROUTES } from "@/lib/legal";
import { CONFIDENTIALITY } from "./content";

/**
 * Client confidentiality + verifiable sources — the lawyer-facing replacement
 * for the generic TrustSection. Claims restate /masking and /privacy.
 */
export function ConfidentialitySection() {
  return (
    <section className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
      <div className="rounded-3xl border border-border bg-card p-6 shadow-sm sm:p-12">
        <div className="mx-auto max-w-2xl text-center">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10 text-primary">
            <ShieldCheck className="h-6 w-6" />
          </div>
          <span className="mt-4 block text-sm font-semibold text-primary">
            {CONFIDENTIALITY.eyebrow}
          </span>
          <h2 className="mt-2 text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
            {CONFIDENTIALITY.title}
          </h2>
          <p className="mt-3 text-base leading-relaxed text-muted-foreground">
            {CONFIDENTIALITY.subtitle}
          </p>
        </div>

        {/* The masked identifier classes */}
        <div className="mx-auto mt-7 flex max-w-2xl flex-wrap justify-center gap-2">
          {CONFIDENTIALITY.maskedFields.map((f) => (
            <span
              key={f}
              className="inline-flex items-center gap-1.5 rounded-full border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground"
            >
              <EyeOff className="h-3.5 w-3.5 text-primary" />
              {f}
            </span>
          ))}
        </div>

        <div className="mx-auto mt-8 grid max-w-3xl gap-4 sm:grid-cols-3">
          <TrustCard
            icon={EyeOff}
            title="وضع السرية"
            body="مفعّل افتراضياً على كل محادثة، دون أي إعداد منك."
            href="/masking"
            linkLabel="تقنيع المعرّفات"
          />
          <TrustCard
            icon={Lock}
            title="لا تدريب على محتواك"
            body="لا نستخدم محتواك لتدريب نماذج عامة، ومذكراتك وقوالبك تبقى في حسابك."
            href={LEGAL_ROUTES.privacy}
            linkLabel="سياسة الخصوصية"
          />
          <TrustCard
            icon={BadgeCheck}
            title="مصادر قابلة للتحقق"
            body="كل استشهاد مربوط بنصّه الحرفي ورابطه الرسمي لدى الجهة المختصة."
          />
        </div>
      </div>
    </section>
  );
}

function TrustCard({
  icon: Icon,
  title,
  body,
  href,
  linkLabel,
}: {
  icon: typeof Lock;
  title: string;
  body: string;
  href?: string;
  linkLabel?: string;
}) {
  return (
    <div className="flex flex-col rounded-xl border border-border/70 bg-background p-4">
      <Icon className="h-5 w-5 text-primary" />
      <h3 className="mt-2 text-sm font-bold text-foreground">{title}</h3>
      <p className="mt-1 flex-1 text-xs leading-relaxed text-muted-foreground">
        {body}
      </p>
      {href && linkLabel && (
        <Link
          href={href}
          className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-primary underline-offset-4 hover:underline"
        >
          {linkLabel}
          <ArrowLeft className="h-3 w-3" />
        </Link>
      )}
    </div>
  );
}
