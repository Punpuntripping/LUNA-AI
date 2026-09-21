"use client";

import { Info } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Standard AI legal disclaimer shown beneath every agent output (agent_search
 * "بحث قانوني" + agent_writing "تحليل قانوني") AND beneath every blog article —
 * the public wing, the 99 legacy share snapshots, and the مدوناتي owner view.
 * Single source of truth — it used to be baked into the aggregator's
 * ``content_md``; now it renders here so it stays out of copied / shared text
 * and is consistent across every surface.
 *
 * ⚠ FOUR CONSUMERS, ONE SIZE. `AgentSearchViewer`, `NoteEditor`,
 * `BlogArticleView` and `PublicAnswerView` all render this unstyled, so a
 * change here moves the notice in-app and on the public pages together. That is
 * deliberate: it is the same legal statement, and one of the two places being
 * quietly smaller than the other is how a required notice becomes decorative.
 */
export const AGENT_OUTPUT_DISCLAIMER_AR =
  "هذه المعلومات مُولَّدة بالذكاء الاصطناعي لأغراض قانونية عامة ولا تُعدّ استشارة قانونية رسمية. " +
  "يُرجى التحقق من المصادر ومراجعة محامٍ مرخّص للحصول على رأي مُلزم.";

export function AgentOutputDisclaimer({ className }: { className?: string }) {
  return (
    <div
      dir="rtl"
      // `text-sm` (14px), not `text-xs`: at 12px a required legal notice reads
      // as decoration on a phone, and it now carries the public blog wing where
      // the reader is anonymous and has no other context for what generated the
      // article. The icon is sized up with it so the row stays balanced.
      className={cn(
        "mt-6 flex items-start gap-2 border-t pt-3 text-sm leading-relaxed text-muted-foreground",
        className,
      )}
    >
      <Info className="mt-0.5 h-4 w-4 shrink-0" />
      <p>{AGENT_OUTPUT_DISCLAIMER_AR}</p>
    </div>
  );
}
