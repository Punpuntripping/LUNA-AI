import { STATS } from "./content";

/**
 * Data-moat band — the corpus that lets Rayhan ground answers in real Saudi
 * sources instead of guessing. Numbers are live database floors (see content.ts).
 */
export function StatsBand() {
  return (
    <section className="bg-primary text-primary-foreground">
      <div className="mx-auto max-w-5xl px-4 py-14 sm:py-16">
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-2xl font-bold tracking-tight sm:text-3xl">
            قاعدة بياناتنا تغطّي أكثر من 95٪ من بيانات الامتثال السعودي
          </h2>
          <p className="mt-2 text-sm leading-relaxed text-primary-foreground/80">
            مصادر رسمية مجمّعة من أكثر من 200 كيان حكومي — لهذا لا يختلق ريحان
            المصادر، بل يبحث في بيانات موثّقة.
          </p>
        </div>

        <dl className="mx-auto mt-10 grid max-w-3xl grid-cols-2 gap-x-6 gap-y-9 sm:grid-cols-3">
          {STATS.map((s) => (
            <div key={s.label} className="text-center">
              <dt className="text-3xl font-bold tabular-nums sm:text-4xl">
                {s.value}
              </dt>
              <dd className="mt-1.5 text-sm font-medium text-primary-foreground/90">
                {s.label}
              </dd>
              {s.hint && (
                <dd className="mt-1 text-[11px] leading-snug text-primary-foreground/60">
                  {s.hint}
                </dd>
              )}
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}
