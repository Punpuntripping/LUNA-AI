import { PROBLEMS } from "./content";

/** The three pain points Rayhan targets — gain-framed, not fear-framed. */
export function ProblemSection() {
  return (
    <section className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
      <div className="mx-auto max-w-2xl text-center">
        <h2 className="text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
          التحديات التي نعالجها
        </h2>
        <p className="mt-3 text-base leading-relaxed text-muted-foreground">
          يقضي المحامي السعودي وقتاً طويلاً في البحث القانوني وصياغة الدعاوى،
          فيما لا توفّر أدوات الذكاء الاصطناعي العامة الدقة التي يتطلّبها العمل
          القانوني.
        </p>
      </div>

      <div className="mt-10 grid gap-4 sm:grid-cols-3">
        {PROBLEMS.map((p) => {
          const Icon = p.icon;
          return (
            <div
              key={p.title}
              className="group rounded-2xl border border-border bg-card p-6 shadow-sm transition-all duration-200 hover:-translate-y-1 hover:shadow-md"
            >
              <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-primary/10 text-primary transition-colors group-hover:bg-primary/15">
                <Icon className="h-5 w-5" />
              </div>
              <h3 className="mt-4 text-base font-bold text-foreground">
                {p.title}
              </h3>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                {p.body}
              </p>
            </div>
          );
        })}
      </div>
    </section>
  );
}
