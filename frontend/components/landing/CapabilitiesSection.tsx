import { CAPABILITIES } from "./content";

/** Verb-led capability grid: what Rayhan actually does for the lawyer. */
export function CapabilitiesSection() {
  return (
    <section className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
      <div className="mx-auto max-w-2xl text-center">
        <h2 className="text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
          ماذا يفعل ريحان؟
        </h2>
        <p className="mt-3 text-base leading-relaxed text-muted-foreground">
          أداة واحدة تغطّي رحلة العمل القانوني — من البحث إلى الصياغة إلى
          الإجراء الحكومي.
        </p>
      </div>

      <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {CAPABILITIES.map((c) => {
          const Icon = c.icon;
          return (
            <div
              key={c.title}
              className="group flex flex-col rounded-2xl border border-border bg-card p-6 shadow-sm transition-all duration-200 hover:-translate-y-1 hover:shadow-md"
            >
              <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-primary/10 text-primary transition-colors group-hover:bg-primary/15">
                <Icon className="h-5 w-5" />
              </div>
              <h3 className="mt-4 text-base font-bold text-foreground">
                {c.title}
              </h3>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                {c.body}
              </p>
            </div>
          );
        })}
      </div>
    </section>
  );
}
