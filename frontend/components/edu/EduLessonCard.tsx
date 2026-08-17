"use client";

import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { UsageBarSlot } from "@/components/edu/slots/UsageBarSlot";
import type { EduLesson } from "@/components/edu/edu-syllabus";
import type { EduDismissReason } from "@/stores/edu-store";

interface EduLessonCardProps {
  lesson: EduLesson;
  onDismiss: (reason: EduDismissReason) => void;
}

/**
 * One lesson of «سلسلة تعلّم ريحان».
 *
 * NOT a Dialog, on purpose. It never traps focus, never blocks the composer,
 * and never dims the page — the user can ignore it completely and keep typing.
 * «اتعرف على ريحان» and «جولة المخرجات» stay the only modal surfaces in the app;
 * a series that interrupts every four messages would have to be modal-free to be
 * tolerable at all.
 *
 * POSITION — bottom-END, which in this RTL app is the LEFT edge. The design note
 * says "bottom-start", but start is where the sidebar lives (`w-64`, always
 * mounted on desktop), so a start-anchored card would sit underneath it. End is
 * the free corner in both directions.
 *
 * Z-INDEX — z-40, deliberately under the sidebar (z-50), the mobile workspace
 * overlay (z-60) and every portalled Radix layer (z-70,
 * [[project_radix_layer_z70]]). This is the lowest-priority chrome in the app:
 * anything the user deliberately opened outranks a lesson they did not ask for.
 * (The engine also refuses to show it while any of those are open.)
 */
export function EduLessonCard({ lesson, onDismiss }: EduLessonCardProps) {
  const Icon = lesson.icon;

  // `animate-fab-in` is the app's existing entry motion (globals.css) — fade +
  // rise + slight scale, and already silenced under `prefers-reduced-motion`.
  // Reused rather than introducing a second animation vocabulary for one card.
  return (
    <div
      dir="rtl"
      lang="ar"
      role="status"
      aria-live="polite"
      data-testid="edu-lesson-card"
      data-lesson={lesson.id}
      className="
        animate-fab-in fixed bottom-24 end-4 z-40
        w-[min(20rem,calc(100vw-2rem))]
        rounded-lg border border-border bg-card p-4 shadow-lg
      "
      style={{ marginBottom: "env(safe-area-inset-bottom)" }}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
            <Icon className="h-4 w-4" />
          </span>
          <h3 className="truncate text-sm font-semibold text-foreground">
            {lesson.title}
          </h3>
        </div>
        <button
          type="button"
          onClick={() => onDismiss("close")}
          aria-label="إغلاق"
          data-testid="edu-lesson-close"
          className="-me-1 -mt-1 shrink-0 rounded-md p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="mt-2 flex flex-col gap-1.5">
        {lesson.body.map((line) => (
          <p key={line} className="text-xs leading-relaxed text-muted-foreground">
            {line}
          </p>
        ))}
      </div>

      {lesson.slot === "usage_bar" && (
        <div className="mt-3">
          <UsageBarSlot />
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1">
          {lesson.action && (
            <Button
              size="sm"
              className="h-7 px-2 text-xs"
              data-testid="edu-lesson-action"
              onClick={() => {
                lesson.action?.run();
                onDismiss("action");
              }}
            >
              {lesson.action.label}
            </Button>
          )}
          {lesson.learnMore && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs text-muted-foreground"
              data-testid="edu-lesson-learn-more"
              onClick={() => {
                // New tab — a lesson must never cost the user the conversation
                // they are in the middle of.
                window.open(lesson.learnMore?.href, "_blank");
                onDismiss("learn_more");
              }}
            >
              {lesson.learnMore.label}
            </Button>
          )}
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 px-2 text-xs"
          data-testid="edu-lesson-got-it"
          onClick={() => onDismiss("got_it")}
        >
          فهمت
        </Button>
      </div>
    </div>
  );
}
