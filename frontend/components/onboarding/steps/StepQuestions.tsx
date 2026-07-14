"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import {
  STARTER_CATEGORIES,
  STEP_QUESTIONS,
  type QuestionCategoryKey,
} from "../onboarding-content";

interface StepQuestionsProps {
  /** Puts the question in the composer (does NOT send) and closes the tour. */
  onPickQuestion: (question: string) => void;
}

export function StepQuestions({ onPickQuestion }: StepQuestionsProps) {
  const [activeKey, setActiveKey] = useState<QuestionCategoryKey>(
    STARTER_CATEGORIES[0].key,
  );
  const active =
    STARTER_CATEGORIES.find((c) => c.key === activeKey) ??
    STARTER_CATEGORIES[0];

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-base font-semibold">{STEP_QUESTIONS.heading}</h3>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">
          {STEP_QUESTIONS.hint}
        </p>
      </div>

      {/* the three domains, side by side */}
      <div className="grid grid-cols-3 gap-2" role="tablist" aria-label="مجالات الأسئلة">
        {STARTER_CATEGORIES.map((cat) => (
          <button
            key={cat.key}
            type="button"
            role="tab"
            aria-selected={cat.key === activeKey}
            onClick={() => setActiveKey(cat.key)}
            className={cn(
              "rounded-xl border px-3 py-2 text-sm font-semibold transition-colors",
              cat.key === activeKey
                ? "border-primary/50 bg-primary/10 text-primary"
                : "border-border bg-muted/30 text-muted-foreground hover:border-primary/30 hover:text-foreground",
            )}
          >
            {cat.label}
          </button>
        ))}
      </div>

      <div className="space-y-2">
        {active.questions.map((question) => (
          <button
            key={question}
            type="button"
            onClick={() => onPickQuestion(question)}
            className="w-full rounded-xl border border-border bg-background px-4 py-3 text-start text-sm transition-colors hover:border-primary/40 hover:bg-primary/5"
          >
            {question}
          </button>
        ))}
      </div>
    </div>
  );
}
