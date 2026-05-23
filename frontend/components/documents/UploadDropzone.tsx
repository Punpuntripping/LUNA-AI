"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useDropzone, type FileRejection } from "react-dropzone";
import { Upload, AlertCircle, X, RotateCw, Check } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  runResumableUpload,
  type ImperativeUploadHandle,
  type ResumableUploadStatus,
} from "@/hooks/use-resumable-upload";

interface UploadDropzoneProps {
  caseId: string;
}

const ACCEPTED_TYPES: Record<string, string[]> = {
  "application/pdf": [".pdf"],
  "image/png": [".png"],
  "image/jpeg": [".jpg", ".jpeg"],
};

const MAX_FILE_SIZE = 50 * 1024 * 1024; // 50MB
const MAX_FILES = 5;

interface UploadSession {
  id: string;
  file: File;
  status: ResumableUploadStatus;
  progress: number;
  sentBytes: number;
  totalBytes: number;
  error: string | null;
}

function getArabicError(code: string): string {
  switch (code) {
    case "file-too-large":
      return "حجم الملف يتجاوز 50 ميجابايت";
    case "file-invalid-type":
      return "نوع الملف غير مدعوم. الأنواع المقبولة: PDF، PNG، JPG";
    case "too-many-files":
      return `الحد الأقصى ${MAX_FILES} ملفات في كل مرة`;
    default:
      return "حدث خطأ أثناء تحميل الملف";
  }
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatEta(sent: number, total: number, startedAt: number): string {
  if (sent <= 0 || total <= 0) return "";
  const elapsedMs = Date.now() - startedAt;
  if (elapsedMs < 250) return "";
  const bytesPerMs = sent / elapsedMs;
  if (bytesPerMs <= 0) return "";
  const remainingMs = (total - sent) / bytesPerMs;
  if (!isFinite(remainingMs) || remainingMs < 0) return "";
  const remainingSec = Math.ceil(remainingMs / 1000);
  if (remainingSec < 60) return `${remainingSec} ث متبقية`;
  const min = Math.ceil(remainingSec / 60);
  return `${min} د متبقية`;
}

interface SessionRowProps {
  session: UploadSession;
  startedAt: number;
  onCancel: () => void;
  onRetry: () => void;
  onDismiss: () => void;
}

function SessionRow({
  session,
  startedAt,
  onCancel,
  onRetry,
  onDismiss,
}: SessionRowProps) {
  const pct = Math.round((session.progress || 0) * 100);
  const isActive =
    session.status === "initializing" ||
    session.status === "uploading" ||
    session.status === "finalizing";
  const isCompleted = session.status === "completed";
  const isFailed = session.status === "failed";
  const isCancelled = session.status === "cancelled";

  return (
    <div
      dir="rtl"
      className={cn(
        "flex flex-col gap-1.5 rounded-md border px-3 py-2 text-sm",
        isCompleted && "border-emerald-500/30 bg-emerald-500/5",
        isFailed && "border-destructive/30 bg-destructive/5",
        isCancelled && "border-muted bg-muted/30 opacity-70",
      )}
    >
      <div className="flex items-center gap-2">
        <span className="flex-1 truncate font-medium" title={session.file.name}>
          {session.file.name}
        </span>
        <span className="text-xs text-muted-foreground tabular-nums">
          {formatBytes(session.file.size)}
        </span>
        {isActive && (
          <button
            type="button"
            onClick={onCancel}
            className="text-muted-foreground hover:text-destructive transition-colors"
            aria-label="إلغاء الرفع"
          >
            <X className="h-4 w-4" />
          </button>
        )}
        {isCompleted && (
          <span
            className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500 text-white"
            aria-label="اكتمل"
          >
            <Check className="h-3 w-3" />
          </span>
        )}
        {isFailed && (
          <>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 gap-1 px-2 text-xs"
              onClick={onRetry}
            >
              <RotateCw className="h-3 w-3" />
              إعادة المحاولة
            </Button>
            <button
              type="button"
              onClick={onDismiss}
              className="text-muted-foreground hover:text-foreground transition-colors"
              aria-label="إغلاق"
            >
              <X className="h-4 w-4" />
            </button>
          </>
        )}
      </div>

      {isActive && (
        <>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
            <div
              className="h-full bg-primary transition-all duration-200"
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="flex items-center justify-between text-xs text-muted-foreground tabular-nums">
            <span>
              {pct}% · {formatBytes(session.sentBytes)} من{" "}
              {formatBytes(session.totalBytes || session.file.size)}
            </span>
            <span>{formatEta(session.sentBytes, session.totalBytes, startedAt)}</span>
          </div>
        </>
      )}

      {isFailed && session.error && (
        <p className="text-xs text-destructive">{session.error}</p>
      )}
      {isCancelled && (
        <p className="text-xs text-muted-foreground">تم إلغاء الرفع</p>
      )}
    </div>
  );
}

export function UploadDropzone({ caseId }: UploadDropzoneProps) {
  const qc = useQueryClient();
  const [sessions, setSessions] = useState<UploadSession[]>([]);
  const [rejectError, setRejectError] = useState<string | null>(null);
  const handlesRef = useRef<Map<string, ImperativeUploadHandle>>(new Map());
  // Track start time per session so we can compute ETA — independent of
  // React render to avoid jitter when state batches updates.
  const startedAtRef = useRef<Map<string, number>>(new Map());

  // Abort everything in flight on unmount.
  useEffect(() => {
    const handles = handlesRef.current;
    return () => {
      handles.forEach((h) => h.cancel());
      handles.clear();
    };
  }, []);

  const patchSession = useCallback(
    (id: string, partial: Partial<UploadSession>) => {
      setSessions((curr) =>
        curr.map((s) => (s.id === id ? { ...s, ...partial } : s)),
      );
    },
    [],
  );

  const startUpload = useCallback(
    (sessionId: string, file: File) => {
      startedAtRef.current.set(sessionId, Date.now());
      const handle = runResumableUpload(
        { kind: "document", caseId },
        file,
        qc,
        {
          onProgress: (st) => {
            patchSession(sessionId, {
              status: st.status,
              progress: st.progress,
              sentBytes: st.sentBytes,
              totalBytes: st.totalBytes,
              error: st.error,
            });
          },
          onCompleted: () => {
            handlesRef.current.delete(sessionId);
            patchSession(sessionId, { status: "completed", progress: 1 });
          },
          onFailed: (error) => {
            handlesRef.current.delete(sessionId);
            patchSession(sessionId, { status: "failed", error });
          },
          onCancelled: () => {
            handlesRef.current.delete(sessionId);
            patchSession(sessionId, { status: "cancelled" });
          },
        },
      );
      handlesRef.current.set(sessionId, handle);
    },
    [caseId, qc, patchSession],
  );

  const onDrop = useCallback(
    (acceptedFiles: File[], rejections: FileRejection[]) => {
      setRejectError(null);

      if (rejections.length > 0) {
        const firstError = rejections[0].errors[0];
        setRejectError(getArabicError(firstError.code));
        return;
      }
      if (acceptedFiles.length === 0) return;

      const newSessions: UploadSession[] = acceptedFiles.map((file) => ({
        id: `up-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        file,
        status: "initializing",
        progress: 0,
        sentBytes: 0,
        totalBytes: file.size,
        error: null,
      }));

      setSessions((curr) => [...curr, ...newSessions]);
      newSessions.forEach((s) => startUpload(s.id, s.file));
    },
    [startUpload],
  );

  const { getRootProps, getInputProps, isDragActive, isDragReject } =
    useDropzone({
      onDrop,
      accept: ACCEPTED_TYPES,
      maxSize: MAX_FILE_SIZE,
      maxFiles: MAX_FILES,
    });

  const handleCancel = useCallback((id: string) => {
    handlesRef.current.get(id)?.cancel();
  }, []);

  const handleRetry = useCallback(
    (id: string) => {
      const session = sessions.find((s) => s.id === id);
      if (!session) return;
      patchSession(id, {
        status: "initializing",
        progress: 0,
        sentBytes: 0,
        error: null,
      });
      startUpload(id, session.file);
    },
    [sessions, patchSession, startUpload],
  );

  const handleDismiss = useCallback((id: string) => {
    handlesRef.current.delete(id);
    startedAtRef.current.delete(id);
    setSessions((curr) => curr.filter((s) => s.id !== id));
  }, []);

  // Auto-clear completed sessions after a short grace period so the list
  // doesn't accumulate stale green rows — but only the completed ones,
  // failures stay until the user explicitly dismisses or retries.
  useEffect(() => {
    const completedIds = sessions
      .filter((s) => s.status === "completed")
      .map((s) => s.id);
    if (completedIds.length === 0) return;
    const t = window.setTimeout(() => {
      setSessions((curr) =>
        curr.filter((s) => !completedIds.includes(s.id)),
      );
      completedIds.forEach((id) => {
        handlesRef.current.delete(id);
        startedAtRef.current.delete(id);
      });
    }, 1500);
    return () => window.clearTimeout(t);
  }, [sessions]);

  return (
    <div dir="rtl" lang="ar" className="space-y-2">
      <div
        {...getRootProps()}
        className={cn(
          "relative flex flex-col items-center justify-center rounded-lg border-2 border-dashed p-6 transition-colors cursor-pointer",
          isDragActive && !isDragReject && "border-primary bg-primary/5",
          isDragReject && "border-destructive bg-destructive/5",
          !isDragActive &&
            "border-border hover:border-primary/50 hover:bg-accent/30",
        )}
      >
        <input {...getInputProps()} />
        <div className="flex flex-col items-center gap-2">
          <Upload
            className={cn(
              "h-8 w-8",
              isDragActive ? "text-primary" : "text-muted-foreground",
            )}
          />
          <p className="text-sm text-muted-foreground text-center">
            {isDragActive
              ? "أفلت الملفات هنا"
              : "اسحب الملفات هنا أو انقر للتحميل"}
          </p>
          <p className="text-xs text-muted-foreground/70">
            PDF، PNG، JPG — الحد الأقصى 50 ميجابايت — حتى {MAX_FILES} ملفات
          </p>
        </div>
      </div>

      {rejectError && (
        <div className="flex items-center gap-2 rounded-md bg-destructive/10 border border-destructive/20 p-2.5 text-sm text-destructive">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span>{rejectError}</span>
        </div>
      )}

      {sessions.length > 0 && (
        <div className="space-y-1.5">
          {sessions.map((session) => (
            <SessionRow
              key={session.id}
              session={session}
              startedAt={startedAtRef.current.get(session.id) ?? Date.now()}
              onCancel={() => handleCancel(session.id)}
              onRetry={() => handleRetry(session.id)}
              onDismiss={() => handleDismiss(session.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
