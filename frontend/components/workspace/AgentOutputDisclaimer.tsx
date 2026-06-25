"use client";

import { Info } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Standard AI legal disclaimer shown beneath every agent output (agent_search
 * "بحث قانوني" + agent_writing "تحليل قانوني"). Single source of truth — it used
 * to be baked into the aggregator's ``content_md``; now it renders here so it
 * stays out of copied / shared text and is consistent across both viewers.
 */
export const AGENT_OUTPUT_DISCLAIMER_AR =
  "هذه المعلومات مُولَّدة بالذكاء الاصطناعي لأغراض قانونية عامة ولا تُعدّ استشارة قانونية رسمية. " +
  "يُرجى التحقق من المصادر ومراجعة محامٍ مرخّص للحصول على رأي مُلزم.";

export function AgentOutputDisclaimer({ className }: { className?: string }) {
  return (
    <div
      dir="rtl"
      className={cn(
        "mt-6 flex items-start gap-2 border-t pt-3 text-[11px] leading-relaxed text-muted-foreground",
        className,
      )}
    >
      <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <p>{AGENT_OUTPUT_DISCLAIMER_AR}</p>
    </div>
  );
}
