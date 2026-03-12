"use client";

import { useCallback, useState } from "react";
import { useDropzone, type FileRejection } from "react-dropzone";
import { Upload, Loader2, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { useUploadDocument } from "@/hooks/use-documents";

interface UploadDropzoneProps {
  caseId: string;
}

const ACCEPTED_TYPES: Record<string, string[]> = {
  "application/pdf": [".pdf"],
  "image/png": [".png"],
  "image/jpeg": [".jpg", ".jpeg"],
};

const MAX_FILE_SIZE = 50 * 1024 * 1024; // 50MB

function getArabicError(code: string): string {
  switch (code) {
    case "file-too-large":
      return "حجم الملف يتجاوز 50 ميجابايت";
    case "file-invalid-type":
      return "نوع الملف غير مدعوم. الأنواع المقبولة: PDF، PNG، JPG";
    case "too-many-files":
      return "يمكنك رفع ملف واحد فقط في كل مرة";
    default:
      return "حدث خطأ أثناء تحميل الملف";
  }
}

export function UploadDropzone({ caseId }: UploadDropzoneProps) {
  const uploadDocument = useUploadDocument();
  const [error, setError] = useState<string | null>(null);

  const onDrop = useCallback(
    (acceptedFiles: File[], rejections: FileRejection[]) => {
      setError(null);

      if (rejections.length > 0) {
        const firstError = rejections[0].errors[0];
        setError(getArabicError(firstError.code));
        return;
      }

      if (acceptedFiles.length === 0) return;

      const file = acceptedFiles[0];
      uploadDocument.mutate(
        { caseId, file },
        {
          onError: () => {
            setError("فشل رفع الملف. يرجى المحاولة مرة أخرى.");
          },
          onSuccess: () => {
            setError(null);
          },
        }
      );
    },
    [caseId, uploadDocument]
  );

  const { getRootProps, getInputProps, isDragActive, isDragReject } =
    useDropzone({
      onDrop,
      accept: ACCEPTED_TYPES,
      maxSize: MAX_FILE_SIZE,
      maxFiles: 1,
      disabled: uploadDocument.isPending,
    });

  const isUploading = uploadDocument.isPending;

  return (
    <div className="space-y-2">
      <div
        {...getRootProps()}
        className={cn(
          "relative flex flex-col items-center justify-center rounded-lg border-2 border-dashed p-6 transition-colors cursor-pointer",
          isDragActive && !isDragReject && "border-primary bg-primary/5",
          isDragReject && "border-destructive bg-destructive/5",
          !isDragActive &&
            !isUploading &&
            "border-border hover:border-primary/50 hover:bg-accent/30",
          isUploading && "border-muted cursor-not-allowed opacity-60"
        )}
      >
        <input {...getInputProps()} />

        {isUploading ? (
          <div className="flex flex-col items-center gap-2">
            <Loader2 className="h-8 w-8 animate-spin text-primary" />
            <p className="text-sm text-muted-foreground">جارٍ رفع الملف...</p>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2">
            <Upload
              className={cn(
                "h-8 w-8",
                isDragActive ? "text-primary" : "text-muted-foreground"
              )}
            />
            <p className="text-sm text-muted-foreground text-center">
              {isDragActive
                ? "أفلت الملف هنا"
                : "اسحب الملفات هنا أو انقر للتحميل"}
            </p>
            <p className="text-xs text-muted-foreground/70">
              PDF، PNG، JPG — الحد الأقصى 50 ميجابايت
            </p>
          </div>
        )}
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-md bg-destructive/10 border border-destructive/20 p-2.5 text-sm text-destructive">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}
    </div>
  );
}
