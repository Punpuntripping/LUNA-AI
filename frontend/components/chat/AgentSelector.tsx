"use client";

import { useCallback } from "react";
import {
  Sparkles,
  Search,
  FileSearch,
  Brain,
  FileEdit,
  Check,
  ChevronDown,
} from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuLabel,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/stores/chat-store";
import type { AgentFamily } from "@/types";
import type { LucideIcon } from "lucide-react";

// ==========================================
// Agent definition with metadata
// ==========================================

interface AgentOption {
  /** null = auto/router, otherwise an AgentFamily string */
  value: AgentFamily | null;
  /** Arabic display name */
  label: string;
  /** Arabic description shown in the dropdown */
  description: string;
  /** Lucide icon component */
  icon: LucideIcon;
}

const AGENT_OPTIONS: AgentOption[] = [
  {
    value: null,
    label: "تلقائي",
    description: "يختار الوكيل المناسب تلقائياً",
    icon: Sparkles,
  },
  {
    value: "deep_search",
    label: "بحث معمّق",
    description: "بحث شامل في الأنظمة والتشريعات",
    icon: Search,
  },
  {
    value: "extraction",
    label: "استخراج",
    description: "استخراج المعلومات من المستندات",
    icon: FileSearch,
  },
  {
    value: "memory",
    label: "ذاكرة",
    description: "استرجاع من ذاكرة القضية",
    icon: Brain,
  },
  {
    value: "end_services",
    label: "خدمات",
    description: "صياغة المستندات والعقود",
    icon: FileEdit,
  },
];

// ==========================================
// Helper: find option by value
// ==========================================

function findOption(value: AgentFamily | null): AgentOption {
  return AGENT_OPTIONS.find((o) => o.value === value) ?? AGENT_OPTIONS[0];
}

// ==========================================
// AgentSelector Component
// ==========================================

interface AgentSelectorProps {
  className?: string;
}

export function AgentSelector({ className }: AgentSelectorProps) {
  const selectedAgent = useChatStore((s) => s.selectedAgent);
  const setSelectedAgent = useChatStore((s) => s.setSelectedAgent);
  const isStreaming = useChatStore((s) => s.isStreaming);

  const current = findOption(selectedAgent);
  const CurrentIcon = current.icon;

  const handleSelect = useCallback(
    (value: AgentFamily | null) => {
      setSelectedAgent(value);
    },
    [setSelectedAgent]
  );

  return (
    <div dir="rtl" lang="ar" className={cn("flex items-center", className)}>
      <DropdownMenu dir="rtl">
        <DropdownMenuTrigger
          disabled={isStreaming}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm",
            "border bg-background transition-colors",
            "hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            "disabled:cursor-not-allowed disabled:opacity-50",
            // Highlight when a specific agent (non-auto) is selected
            selectedAgent !== null &&
              "border-primary/30 bg-primary/5 text-primary"
          )}
        >
          <CurrentIcon className="h-4 w-4 shrink-0" />
          <span className="font-medium">{current.label}</span>
          <ChevronDown className="h-3 w-3 shrink-0 opacity-60" />
        </DropdownMenuTrigger>

        <DropdownMenuContent
          side="bottom"
          align="start"
          sideOffset={6}
          className="w-72 rounded-xl p-1"
        >
          <DropdownMenuLabel className="px-3 py-1.5 text-xs text-muted-foreground font-normal">
            اختر الوكيل
          </DropdownMenuLabel>
          <DropdownMenuSeparator />

          {AGENT_OPTIONS.map((option) => {
            const Icon = option.icon;
            const isActive = selectedAgent === option.value;

            return (
              <DropdownMenuItem
                key={option.value ?? "auto"}
                onClick={() => handleSelect(option.value)}
                className={cn(
                  "flex items-start gap-3 px-3 py-2.5 rounded-lg cursor-pointer",
                  isActive && "bg-accent"
                )}
              >
                {/* Icon */}
                <div
                  className={cn(
                    "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md",
                    isActive
                      ? "bg-primary text-primary-foreground"
                      : "bg-muted text-muted-foreground"
                  )}
                >
                  <Icon className="h-4 w-4" />
                </div>

                {/* Text */}
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium leading-tight">
                    {option.label}
                  </p>
                  <p className="text-xs text-muted-foreground mt-0.5 leading-snug">
                    {option.description}
                  </p>
                </div>

                {/* Checkmark (on the left side in RTL = end) */}
                {isActive && (
                  <Check className="h-4 w-4 shrink-0 text-primary mt-0.5" />
                )}
              </DropdownMenuItem>
            );
          })}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

// ==========================================
// Exports for use by other components
// ==========================================

export { AGENT_OPTIONS, findOption };
export type { AgentOption };
