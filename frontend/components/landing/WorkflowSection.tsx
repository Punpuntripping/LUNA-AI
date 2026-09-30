import { WORKFLOW, WORKFLOW_HEADER } from "./content";

/** Four-step strip: case file → research → draft → Najiz. */
export function WorkflowSection() {
  return (
    <section className="border-y border-border bg-muted/30 py-16 sm:py-20">
      <div className="mx-auto max-w-5xl px-4">
        <div className="mx-auto max-w-2xl text-center">
          <span className="text-sm font-semibold text-primary">
            {WORKFLOW_HEADER.eyebrow}
          </span>
          <h2 className="mt-2 text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
            {WORKFLOW_HEADER.title}
          </h2>
          <p className="mt-3 text-base leading-relaxed text-muted-foreground">
            {WORKFLOW_HEADER.subtitle}
          </p>
        </div>

        <ol className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {WORKFLOW.map((step, i) => {
            const Icon = step.icon;
            return (
              <li
                key={step.title}
                className="relative flex flex-col rounded-2xl border border-border bg-card p-5 shadow-sm"
              >
                <div className="flex items-center justify-between">
                  <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
                    <Icon className="h-5 w-5" />
                  </div>
                  <span className="text-2xl font-bold tabular-nums text-muted-foreground/40">
                    {i + 1}
                  </span>
                </div>
                <h3 className="mt-4 text-base font-bold text-foreground">
                  {step.title}
                </h3>
                <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                  {step.body}
                </p>
              </li>
            );
          })}
        </ol>
      </div>
    </section>
  );
}
