"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { cn } from "@/lib/utils";
import { usageApi } from "@/lib/api";
import { usageKeys } from "@/hooks/use-usage";
import { budgetFromUsage, planFiles } from "@/lib/ocr-budget";
import { AttachmentUploadCard } from "@/components/chat/AttachmentUploadCard";
import type { PendingFile, UsageReport } from "@/types";

interface FilePreviewProps {
  files: PendingFile[];
  /** Removes the file from the chat-store AND aborts its tus upload. */
  onRemove: (id: string) => void;
  className?: string;
}

/**
 * Horizontal strip of attachment cards shown above the chat input. Each
 * card renders its own progress / status / cancel UI — this component
 * only handles the layout, plus the OCR page-budget preview: when a PDF is
 * bigger than what its reading allowance covers, its card says how many of
 * its pages will be read (plan ocr_page_budget.md D8c). /usage is fetched only
 * once a PDF with a known page count is attached.
 */
export function FilePreview({ files, onRemove, className }: FilePreviewProps) {
  const hasCountedPdf = files.some((f) => (f.pageCount ?? 0) > 0);
  const { data: usage } = useQuery<UsageReport>({
    queryKey: usageKeys.current(),
    queryFn: usageApi.get,
    enabled: hasCountedPdf,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
  const previews = useMemo(() => {
    const budget = budgetFromUsage(usage);
    return budget ? planFiles(files, budget) : null;
  }, [files, usage]);

  if (files.length === 0) return null;

  return (
    <div
      dir="rtl"
      className={cn(
        "flex gap-3 overflow-x-auto py-2 px-1 scrollbar-thin",
        className,
      )}
    >
      {files.map((file) => (
        <AttachmentUploadCard
          key={file.id}
          file={file}
          onRemove={onRemove}
          ocrPreview={previews?.get(file.id)}
        />
      ))}
    </div>
  );
}
