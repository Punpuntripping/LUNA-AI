"use client";

import { AgentsDiagram } from "../AgentsDiagram";
import { CORPUS_STATS, STEP_AGENTS } from "../onboarding-content";

export function StepAgents() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-base font-semibold">{STEP_AGENTS.heading}</h3>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">
          {STEP_AGENTS.intro}
        </p>
      </div>

      <AgentsDiagram />

      {/* the knowledge base the searcher works against */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {CORPUS_STATS.map((stat) => (
          <div
            key={stat.label}
            className="rounded-lg border border-border bg-muted/30 p-2.5 text-center"
          >
            <div className="text-base font-bold text-primary" dir="ltr">
              {stat.value}
            </div>
            <div className="mt-0.5 text-[11px] leading-4 text-muted-foreground">
              {stat.label}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
