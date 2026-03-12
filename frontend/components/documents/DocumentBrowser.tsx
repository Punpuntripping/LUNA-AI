"use client";

import { FileText } from "lucide-react";
import { useDocuments, useDeleteDocument } from "@/hooks/use-documents";
import { DocumentCard } from "@/components/documents/DocumentCard";
import { UploadDropzone } from "@/components/documents/UploadDropzone";

interface DocumentBrowserProps {
  caseId: string;
}

function DocumentSkeleton() {
  return (
    <div className="rounded-lg border border-border bg-card p-4 space-y-3 animate-pulse">
      <div className="flex items-start gap-3">
        <div className="shrink-0 rounded-md bg-muted p-2 h-9 w-9" />
        <div className="flex-1 space-y-2">
          <div className="h-4 w-3/4 rounded bg-muted" />
          <div className="h-3 w-1/3 rounded bg-muted" />
        </div>
      </div>
      <div className="flex items-center justify-between">
        <div className="h-4 w-16 rounded-full bg-muted" />
        <div className="h-3 w-12 rounded bg-muted" />
      </div>
      <div className="border-t border-border/50 pt-2">
        <div className="h-8 w-full rounded bg-muted" />
      </div>
    </div>
  );
}

export function DocumentBrowser({ caseId }: DocumentBrowserProps) {
  const { data, isLoading } = useDocuments(caseId);
  const deleteDocument = useDeleteDocument();

  const handleDelete = (documentId: string) => {
    deleteDocument.mutate(documentId);
  };

  const documents = data?.documents ?? [];
  const isEmpty = !isLoading && documents.length === 0;

  return (
    <div className="space-y-6">
      {/* Upload area */}
      <UploadDropzone caseId={caseId} />

      {/* Loading state */}
      {isLoading && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <DocumentSkeleton key={i} />
          ))}
        </div>
      )}

      {/* Empty state */}
      {isEmpty && (
        <div className="flex flex-col items-center justify-center py-12 text-center">
          <div className="rounded-full bg-muted p-4 mb-4">
            <FileText className="h-8 w-8 text-muted-foreground" />
          </div>
          <p className="text-sm font-medium text-foreground mb-1">
            لا توجد مستندات
          </p>
          <p className="text-xs text-muted-foreground">
            قم برفع مستندات القضية باستخدام منطقة التحميل أعلاه
          </p>
        </div>
      )}

      {/* Document grid */}
      {!isLoading && documents.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {documents.map((doc) => (
            <DocumentCard
              key={doc.document_id}
              document={doc}
              onDelete={handleDelete}
            />
          ))}
        </div>
      )}
    </div>
  );
}
