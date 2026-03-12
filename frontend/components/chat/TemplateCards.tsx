"use client";

import { useCallback } from "react";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/stores/chat-store";
import type { AgentFamily } from "@/types";

// ==========================================
// TemplateCards
// ==========================================
// Horizontal scrollable row of clickable template cards.
// Shown in the chat empty state to give users quick-start prompts.
// Clicking a card populates ChatInput and sets the agent_family in the store.

interface TemplateCardsProps {
  /** Called with the template prompt text when a card is clicked */
  onSelect: (prompt: string) => void;
  /** Additional className for the container */
  className?: string;
}

// ==========================================
// Agent family badge styling
// ==========================================

const FAMILY_COLORS: Record<AgentFamily, string> = {
  deep_search: "bg-blue-500/10 text-blue-500",
  simple_search: "bg-green-500/10 text-green-500",
  end_services: "bg-purple-500/10 text-purple-500",
  extraction: "bg-orange-500/10 text-orange-500",
  memory: "bg-yellow-500/10 text-yellow-600",
};

const FAMILY_LABELS: Record<AgentFamily, string> = {
  deep_search: "بحث معمق",
  simple_search: "بحث بسيط",
  end_services: "خدمات نهائية",
  extraction: "استخراج",
  memory: "ذاكرة",
};

// ==========================================
// Built-in templates (fallback when API has no data)
// ==========================================

interface BuiltInTemplate {
  title: string;
  description: string;
  agent_family: AgentFamily;
  prompt: string;
}

const BUILT_IN_TEMPLATES: BuiltInTemplate[] = [
  {
    title: "عقد إيجار تجاري",
    description: "إنشاء مسودة عقد إيجار",
    agent_family: "end_services",
    prompt: "أريد إنشاء عقد إيجار تجاري",
  },
  {
    title: "بحث في نظام العمل",
    description: "بحث معمق في نظام العمل السعودي",
    agent_family: "deep_search",
    prompt: "بحث معمق في نظام العمل السعودي",
  },
  {
    title: "تحليل عقد",
    description: "استخراج وتحليل بنود العقد",
    agent_family: "extraction",
    prompt: "أريد تحليل واستخراج معلومات من العقد المرفق",
  },
  {
    title: "حقوق العامل",
    description: "استشارة سريعة حول حقوق العمال",
    agent_family: "simple_search",
    prompt: "ما هي حقوق العامل في نظام العمل السعودي؟",
  },
];

// ==========================================
// Component
// ==========================================

export function TemplateCards({ onSelect, className }: TemplateCardsProps) {
  const setSelectedAgentFamily = useChatStore((s) => s.setSelectedAgentFamily);

  const handleClick = useCallback(
    (template: BuiltInTemplate) => {
      setSelectedAgentFamily(template.agent_family);
      onSelect(template.prompt);
    },
    [setSelectedAgentFamily, onSelect]
  );

  return (
    <div
      dir="rtl"
      lang="ar"
      className={cn("flex gap-3 overflow-x-auto pb-2 px-1", className)}
    >
      {BUILT_IN_TEMPLATES.map((tmpl) => (
        <button
          key={tmpl.title}
          type="button"
          onClick={() => handleClick(tmpl)}
          className={cn(
            "flex flex-col items-start gap-1.5 rounded-xl border p-4 min-w-[200px] max-w-[240px]",
            "text-right transition-all duration-200",
            "hover:bg-accent/50 hover:border-accent hover:shadow-sm",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1",
            "shrink-0"
          )}
        >
          <span className="font-medium text-sm leading-relaxed">
            {tmpl.title}
          </span>
          <span className="text-xs text-muted-foreground line-clamp-2 leading-relaxed">
            {tmpl.description}
          </span>
          <span
            className={cn(
              "text-[10px] px-2 py-0.5 rounded-full mt-1 font-medium",
              FAMILY_COLORS[tmpl.agent_family]
            )}
          >
            {FAMILY_LABELS[tmpl.agent_family]}
          </span>
        </button>
      ))}
    </div>
  );
}
