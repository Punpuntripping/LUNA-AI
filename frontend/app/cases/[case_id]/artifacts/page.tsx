"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import { FileText } from "lucide-react";
import { useCaseArtifacts } from "@/hooks/use-artifacts";
import { ArtifactList } from "@/components/artifacts/ArtifactList";
import { ArtifactViewer } from "@/components/artifacts/ArtifactViewer";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

// Next.js App Router requires default export for page files
// eslint-disable-next-line import/no-default-export
export default function CaseArtifactsPage() {
  const params = useParams();
  const caseId = params.case_id as string;
  const { data, isLoading } = useCaseArtifacts(caseId);

  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);

  function handleArtifactClick(artifactId: string) {
    setSelectedArtifactId(artifactId);
  }

  function handleCloseDialog() {
    setSelectedArtifactId(null);
  }

  return (
    <div className="flex flex-col h-full">
      {/* Page header */}
      <div className="flex items-center gap-3 border-b px-6 py-4">
        <FileText className="h-5 w-5 text-muted-foreground" />
        <h1 className="text-lg font-semibold text-foreground">
          مستندات القضية
        </h1>
        {data?.artifacts && data.artifacts.length > 0 && (
          <span className="rounded-full bg-muted px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
            {data.artifacts.length}
          </span>
        )}
      </div>

      {/* Artifacts list */}
      <div className="flex-1 overflow-auto">
        <div className="mx-auto max-w-3xl py-4">
          <ArtifactList
            artifacts={data?.artifacts}
            isLoading={isLoading}
            onArtifactClick={handleArtifactClick}
          />
        </div>
      </div>

      {/* Artifact viewer dialog */}
      <Dialog open={!!selectedArtifactId} onOpenChange={handleCloseDialog}>
        <DialogContent className="sm:max-w-[700px] h-[80vh] flex flex-col p-0 gap-0">
          <DialogHeader className="px-6 pt-6 pb-0">
            <DialogTitle>عرض المستند</DialogTitle>
          </DialogHeader>
          <div className="flex-1 min-h-0">
            {selectedArtifactId && (
              <ArtifactViewer artifactId={selectedArtifactId} />
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
