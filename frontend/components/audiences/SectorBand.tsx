import { SECTORS, SECTORS_INTRO } from "./content";

/**
 * The breadth proof — all 38 regulatory sectors Rayhan's corpus spans, each
 * pill carrying its live regulation count. This is what makes "covers every
 * sector" concrete rather than a slogan.
 */
export function SectorBand() {
  return (
    <section className="border-y border-border bg-muted/30 py-16 sm:py-20">
      <div className="mx-auto max-w-5xl px-4">
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
            {SECTORS_INTRO.title}
          </h2>
          <p className="mt-3 text-base leading-relaxed text-muted-foreground">
            {SECTORS_INTRO.subtitle}
          </p>
        </div>

        <ul className="mt-10 flex flex-wrap justify-center gap-2.5">
          {SECTORS.map((s) => (
            <li
              key={s.name}
              className="inline-flex items-center gap-2 rounded-full border border-border bg-card px-3.5 py-1.5 text-sm text-foreground shadow-sm transition-colors hover:border-primary/40"
            >
              <span>{s.name}</span>
              <span className="rounded-full bg-primary/10 px-1.5 text-xs font-semibold tabular-nums text-primary">
                {s.count}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
