"use client";

import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";
import { useChatStore } from "@/stores/chat-store";

// ─────────────────────────────────────────────────────────────────────────────
// EDITABLE CONFIG
// The assistant's name + the action phrases it rotates through while working.
// To add an action: just add a string to the relevant array below.
// To add a brand-new agent family: add a key to AGENT_PHRASES matching the
// `agent_family` value (see agent_family_enum in Supabase).
// ─────────────────────────────────────────────────────────────────────────────

/** The assistant's display name, shown before every action. */
const RAYHAN = "ريحان";

/** How long each phrase stays before crossfading to the next (ms). */
const ROTATION_MS = 7000;

/** Used before any agent family is known (classifier still deciding). */
const DEFAULT_PHRASES = ["يفكّر", "يتفكّر", "يحلّل"];

/** Used while a specific agent family is running. Keys match `agent_family`. */
const AGENT_PHRASES: Record<string, string[]> = {
  deep_search: ["يبحث بعمق", "يحلّل المصادر", "يراجع الأنظمة"],
  simple_search: ["يبحث", "يطّلع على الأنظمة"],
  extraction: ["يستخرج المعلومات", "يقرأ المستند"],
  memory: ["يستحضر السياق", "يتذكّر"],
  writing: ["يصيغ الإجابة", "يكتب"],
  end_services: ["يُجهّز الرد"],
};

// ─────────────────────────────────────────────────────────────────────────────

/** Resolve the phrase set for the currently-running agent family (stable ref). */
function getPhrases(family: string | null): string[] {
  if (family && AGENT_PHRASES[family]) return AGENT_PHRASES[family];
  return DEFAULT_PHRASES;
}

interface TypingIndicatorProps {
  className?: string;
}

export function TypingIndicator({ className }: TypingIndicatorProps) {
  const runningAgentFamily = useChatStore((s) => s.runningAgentFamily);
  const phrases = getPhrases(runningAgentFamily);

  const [index, setIndex] = useState(0);

  // Rotate the phrase on an interval; reset to the first phrase whenever the
  // active phrase set changes (e.g. the classifier just picked an agent).
  useEffect(() => {
    setIndex(0);
    if (phrases.length < 2) return;
    const id = setInterval(() => {
      setIndex((i) => (i + 1) % phrases.length);
    }, ROTATION_MS);
    return () => clearInterval(id);
  }, [phrases]);

  return (
    <div
      dir="rtl"
      lang="ar"
      role="status"
      aria-label={`${RAYHAN} يعمل على ردّك`}
      className={cn(
        "flex items-center gap-2 px-4 py-3 rounded-xl bg-card border max-w-fit",
        className
      )}
    >
      {/* Crossfading action label: all phrases are stacked in one grid cell so
          the box sizes to the widest phrase and outgoing/incoming phrases
          fade simultaneously. */}
      <div className="grid text-xs" aria-hidden="true">
        {phrases.map((phrase, i) => (
          <span
            key={phrase}
            className={cn(
              "[grid-area:1/1] whitespace-nowrap transition-opacity duration-500",
              i === index ? "opacity-100" : "opacity-0"
            )}
          >
            <span className="font-medium text-foreground/90">{RAYHAN}</span>{" "}
            <span className="text-muted-foreground">{phrase}</span>
          </span>
        ))}
      </div>

      <div className="flex items-center gap-1" aria-hidden="true">
        <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-bounce-dot [animation-delay:0ms]" />
        <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-bounce-dot [animation-delay:150ms]" />
        <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-bounce-dot [animation-delay:300ms]" />
      </div>
    </div>
  );
}
