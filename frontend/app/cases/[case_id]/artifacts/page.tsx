"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import { FileText } from "lucide-react";
import { useCaseWorkspace } from "@/hooks/use-workspace";
import { WorkspaceList } from "@/components/workspace/WorkspaceList";
import { WorkspaceItemViewer } from "@/components/workspace/WorkspaceItemViewer";
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
  const { data, isLoading } = useCaseWorkspace(caseId);

  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);

  function handleItemClick(itemId: string) {
    setSelectedItemId(itemId);
  }

  function handleCloseDialog() {
    setSelectedItemId(null);
  }

  return (
    <div className="flex flex-col h-full">
      {/* Page header */}
      <div className="flex items-center gap-3 border-b px-6 py-4">
        <FileText className="h-5 w-5 text-muted-foreground" />
        <h1 className="text-lg font-semibold text-foreground">
          مستندات القضية
        </h1>
        {data?.items && data.items.length > 0 && (
          <span className="rounded-full bg-muted px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
            {data.items.length}
          </span>
        )}
      </div>

      {/* Workspace list */}
      <div className="flex-1 overflow-auto">
        <div className="mx-auto max-w-3xl py-4">
          <WorkspaceList
            items={data?.items}
            isLoading={isLoading}
            onItemClick={handleItemClick}
          />
        </div>
      </div>

      {/* Item viewer dialog */}
      <Dialog open={!!selectedItemId} onOpenChange={handleCloseDialog}>
        <DialogContent className="sm:max-w-[700px] h-[80vh] flex flex-col p-0 gap-0">
          <DialogHeader className="px-6 pt-6 pb-0">
            <DialogTitle>عرض المستند</DialogTitle>
          </DialogHeader>
          <div className="flex-1 min-h-0">
            {selectedItemId && (
              <WorkspaceItemViewer itemId={selectedItemId} />
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
