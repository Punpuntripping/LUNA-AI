"use client";

import { FileStack, Pin, Users } from "lucide-react";
import { WorkspaceDiagram } from "../WorkspaceDiagram";
import { STEP_WORKSPACE } from "../onboarding-content";

// Icon per bullet, same order as STEP_WORKSPACE.bullets (content stays
// icon-free so copy edits never touch component code).
const BULLET_ICONS = [FileStack, Users, Pin] as const;

export function StepWorkspace() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-base font-semibold">{STEP_WORKSPACE.heading}</h3>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">
          {STEP_WORKSPACE.intro}
        </p>
      </div>

      <WorkspaceDiagram />

      <div className="space-y-3">
        {STEP_WORKSPACE.bullets.map((bullet, i) => {
          const Icon = BULLET_ICONS[i] ?? Pin;
          return (
            <div key={bullet.title} className="flex gap-3">
              <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-primary/10">
                <Icon className="h-3.5 w-3.5 text-primary" />
              </div>
              <div>
                <div className="text-sm font-semibold">{bullet.title}</div>
                <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
                  {bullet.text}
                </p>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
