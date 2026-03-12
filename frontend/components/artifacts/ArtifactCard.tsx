"use client";

import { FileText } from "lucide-react";
import { cn, getRelativeTimeAr } from "@/lib/utils";
import { useChatStore } from "@/stores/chat-store";
import type { Artifact, ArtifactType } from "@/types";

const TYPE_COLORS: Record<ArtifactType, string> = {
  report: "bg-blue-500/10 text-blue-500",
  contract: "bg-purple-500/10 text-purple-500",
  memo: "bg-indigo-500/10 text-indigo-500",
  summary: "bg-orange-500/10 text-orange-500",
  memory_file: "bg-yellow-500/10 text-yellow-600",
  legal_opinion: "bg-emerald-500/10 text-emerald-500",
};

const TYPE_LABELS: Record<ArtifactType, string> = {
  report: "تقرير",
  contract: "عقد",
  memo: "مذكرة",
  summary: "ملخص",
  memory_file: "ذاكرة",
  legal_opinion: "رأي قانوني",
};

interface ArtifactCardProps {
  artifact: Artifact;
  onClick?: (artifactId: string) => void;
}

export function ArtifactCard({ artifact, onClick }: ArtifactCardProps) {
  const openArtifactPanel = useChatStore((s) => s.openArtifactPanel);

  const typeColor = TYPE_COLORS[artifact.artifact_type] ?? "bg-muted text-muted-foreground";
  const typeLabel = TYPE_LABELS[artifact.artifact_type] ?? artifact.artifact_type;

  function handleClick() {
    if (onClick) {
      onClick(artifact.artifact_id);
    } else {
      openArtifactPanel(artifact.artifact_id);
    }
  }

  return (
    <button
      onClick={handleClick}
      className="w-full rounded-lg border border-border/50 bg-card p-3 text-start transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <div className="flex items-start gap-3">
        {/* Icon */}
        <div className="mt-0.5 shrink-0">
          <FileText className="h-4 w-4 text-muted-foreground" />
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0 space-y-1.5">
          {/* Title */}
          <p className="text-sm font-medium truncate text-foreground">
            {artifact.title}
          </p>

          {/* Badge + time row */}
          <div className="flex items-center gap-2">
            <span
              className={cn(
                "inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium",
                typeColor
              )}
            >
              {typeLabel}
            </span>

            <span className="text-[11px] text-muted-foreground">
              {getRelativeTimeAr(artifact.created_at)}
            </span>
          </div>
        </div>
      </div>
    </button>
  );
}
