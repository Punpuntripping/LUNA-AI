"use client";

import { useEduStore } from "@/stores/edu-store";
import { findLesson } from "@/components/edu/edu-syllabus";
import { EduLessonCard } from "@/components/edu/EduLessonCard";

/**
 * Renders whichever lesson the engine has made active — at most one, ever.
 *
 * Self-gating and renders `null` the overwhelming majority of the time, so it
 * mounts unconditionally in `ChatLayoutClient` beside `<OnboardingDialog/>` and
 * `<TourOverlay/>`. All scheduling lives in `edu-store`; this component holds no
 * logic of its own beyond "is there an active lesson".
 */
export function EduLessonHost() {
  const activeLesson = useEduStore((s) => s.activeLesson);
  const dismiss = useEduStore((s) => s.dismiss);

  if (!activeLesson) return null;
  const lesson = findLesson(activeLesson);
  // A lesson id persisted before the entry was removed from the syllabus.
  if (!lesson) return null;

  return (
    <EduLessonCard
      lesson={lesson}
      onDismiss={(reason) => dismiss(lesson.id, reason)}
    />
  );
}
