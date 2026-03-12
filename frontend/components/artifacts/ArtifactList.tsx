"use client";

import { useState } from "react";
import { ChevronDown, ChevronLeft, FileText } from "lucide-react";
import { cn } from "@/lib/utils";
import { ArtifactCard } from "./ArtifactCard";
import type { Artifact, ArtifactType } from "@/types";

const TYPE_LABELS: Record<ArtifactType, string> = {
  report: "تقارير",
  contract: "عقود",
  memo: "مذكرات",
  summary: "ملخصات",
  memory_file: "ذاكرة",
  legal_opinion: "آراء قانونية",
};

/** Order in which type groups appear */
const TYPE_ORDER: ArtifactType[] = [
  "report",
  "legal_opinion",
  "contract",
  "memo",
  "summary",
  "memory_file",
];

interface ArtifactListProps {
  artifacts: Artifact[] | undefined;
  isLoading: boolean;
  onArtifactClick?: (artifactId: string) => void;
}

/**
 * Groups artifacts by artifact_type, rendering each group as a collapsible section.
 */
export function ArtifactList({ artifacts, isLoading, onArtifactClick }: ArtifactListProps) {
  const [collapsedGroups, setCollapsedGroups] = useState<Set<ArtifactType>>(new Set());

  function toggleGroup(type: ArtifactType) {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(type)) {
        next.delete(type);
      } else {
        next.add(type);
      }
      return next;
    });
  }

  // Loading skeleton
  if (isLoading) {
    return (
      <div className="space-y-3 p-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="animate-pulse space-y-2">
            <div className="h-4 w-24 rounded bg-muted" />
            <div className="h-16 w-full rounded-lg bg-muted" />
          </div>
        ))}
      </div>
    );
  }

  // Empty state
  if (!artifacts || artifacts.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 p-8 text-center">
        <FileText className="h-10 w-10 text-muted-foreground/50" />
        <p className="text-sm text-muted-foreground">لا توجد مستندات بعد</p>
      </div>
    );
  }

  // Group by type
  const grouped = new Map<ArtifactType, Artifact[]>();
  for (const artifact of artifacts) {
    const existing = grouped.get(artifact.artifact_type);
    if (existing) {
      existing.push(artifact);
    } else {
      grouped.set(artifact.artifact_type, [artifact]);
    }
  }

  // Sort groups by defined order
  const sortedTypes = TYPE_ORDER.filter((t) => grouped.has(t));

  return (
    <div className="space-y-1 p-2">
      {sortedTypes.map((type) => {
        const items = grouped.get(type)!;
        const isCollapsed = collapsedGroups.has(type);
        const label = TYPE_LABELS[type] ?? type;

        return (
          <div key={type}>
            {/* Group header */}
            <button
              onClick={() => toggleGroup(type)}
              className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-start text-xs font-semibold text-muted-foreground hover:bg-accent/30 transition-colors"
            >
              {isCollapsed ? (
                <ChevronLeft className="h-3.5 w-3.5 shrink-0" />
              ) : (
                <ChevronDown className="h-3.5 w-3.5 shrink-0" />
              )}
              <span>{label}</span>
              <span className="ms-auto text-[10px] font-normal tabular-nums">
                {items.length}
              </span>
            </button>

            {/* Group items */}
            {!isCollapsed && (
              <div className={cn("space-y-1.5 pb-2", sortedTypes.length > 1 && "ps-2")}>
                {items.map((artifact) => (
                  <ArtifactCard
                    key={artifact.artifact_id}
                    artifact={artifact}
                    onClick={onArtifactClick}
                  />
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
