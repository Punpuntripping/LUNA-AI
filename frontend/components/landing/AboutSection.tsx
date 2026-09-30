/** Short "who we are" statement — Saudi company, built for Saudi lawyers. */
export function AboutSection() {
  return (
    <section className="border-y border-border bg-muted/30">
      <div className="mx-auto max-w-3xl px-4 py-14 text-center sm:py-16">
        <span className="text-sm font-semibold text-primary">ريحان</span>
        <p className="mt-3 text-balance text-2xl font-semibold leading-relaxed text-foreground sm:text-3xl sm:leading-relaxed">
          شركة سعودية تبني مساعداً قانونياً ذكياً للمحامي السعودي — يختصر عليه
          البحث والصياغة، بدقّة مبنية على المصادر الرسمية.
        </p>
      </div>
    </section>
  );
}
