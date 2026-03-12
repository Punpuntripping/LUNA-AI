"use client";

import { useState } from "react";
import {
  FileText,
  Image,
  Download,
  Trash2,
  Loader2,
  File,
} from "lucide-react";
import { cn, getRelativeTimeAr } from "@/lib/utils";
import { useDownloadUrl } from "@/hooks/use-documents";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import type { Document } from "@/types";

interface DocumentCardProps {
  document: Document;
  onDelete?: (id: string) => void;
}

const STATUS_CONFIG: Record<
  Document["extraction_status"],
  { label: string; className: string }
> = {
  pending: {
    label: "قيد الانتظار",
    className: "bg-yellow-100 text-yellow-700",
  },
  processing: {
    label: "قيد المعالجة",
    className: "bg-blue-100 text-blue-700",
  },
  completed: {
    label: "مكتمل",
    className: "bg-green-100 text-green-700",
  },
  failed: {
    label: "فشل",
    className: "bg-red-100 text-red-700",
  },
};

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} بايت`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} ك.ب`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} م.ب`;
}

function getFileIcon(mimeType: string) {
  if (mimeType === "application/pdf") {
    return FileText;
  }
  if (mimeType.startsWith("image/")) {
    return Image;
  }
  return File;
}

export function DocumentCard({ document, onDelete }: DocumentCardProps) {
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [downloadRequested, setDownloadRequested] = useState(false);

  const { data: downloadData, isLoading: isDownloading } = useDownloadUrl(
    downloadRequested ? document.document_id : undefined
  );

  const statusConfig = STATUS_CONFIG[document.extraction_status];
  const IconComponent = getFileIcon(document.mime_type);

  const handleDownload = () => {
    if (downloadData?.url) {
      window.open(downloadData.url, "_blank", "noopener,noreferrer");
    } else {
      setDownloadRequested(true);
    }
  };

  // When download URL arrives, open it
  if (downloadRequested && downloadData?.url) {
    window.open(downloadData.url, "_blank", "noopener,noreferrer");
    setDownloadRequested(false);
  }

  const handleConfirmDelete = () => {
    onDelete?.(document.document_id);
    setShowDeleteDialog(false);
  };

  return (
    <>
      <div className="rounded-lg border border-border bg-card p-4 space-y-3 transition-shadow hover:shadow-sm">
        {/* Header: icon + name */}
        <div className="flex items-start gap-3">
          <div className="shrink-0 rounded-md bg-muted p-2">
            <IconComponent className="h-5 w-5 text-muted-foreground" />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-foreground truncate">
              {document.document_name}
            </p>
            <p className="text-xs text-muted-foreground mt-0.5">
              {formatFileSize(document.file_size_bytes)}
            </p>
          </div>
        </div>

        {/* Status + date row */}
        <div className="flex items-center justify-between">
          <span
            className={cn(
              "inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium",
              statusConfig.className
            )}
          >
            {statusConfig.label}
          </span>
          <span className="text-xs text-muted-foreground">
            {getRelativeTimeAr(document.created_at)}
          </span>
        </div>

        {/* Actions row */}
        <div className="flex items-center gap-2 pt-1 border-t border-border/50">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                className="h-8 flex-1 text-xs"
                onClick={handleDownload}
                disabled={isDownloading}
              >
                {isDownloading ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin me-1.5" />
                ) : (
                  <Download className="h-3.5 w-3.5 me-1.5" />
                )}
                تحميل
              </Button>
            </TooltipTrigger>
            <TooltipContent>تحميل المستند</TooltipContent>
          </Tooltip>

          {onDelete && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 text-xs text-destructive hover:text-destructive hover:bg-destructive/10"
                  onClick={() => setShowDeleteDialog(true)}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>حذف المستند</TooltipContent>
            </Tooltip>
          )}
        </div>
      </div>

      {/* Delete confirmation */}
      <AlertDialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>حذف المستند</AlertDialogTitle>
            <AlertDialogDescription>
              هل أنت متأكد من حذف &quot;{document.document_name}&quot;؟ لا يمكن
              التراجع عن هذا الإجراء.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>إلغاء</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleConfirmDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              حذف
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
