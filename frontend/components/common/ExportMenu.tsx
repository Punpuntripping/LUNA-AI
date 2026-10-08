"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Download, FileText, FileType2, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ApiClientError, exportDocument, type ExportFormat } from "@/lib/api";
import { cn } from "@/lib/utils";

interface ExportMenuProps {
  /**
   * Markdown to export — the host's SAME ``copyText`` the «نسخ» menu copies
   * (body + «المراجع» block), so the file always equals what نسخ copies.
   */
  text: string;
  /** Document title; becomes the file name (sanitized) and the server-side H1. */
  title: string;
  /** Disables the trigger. Blank ``text`` disables it regardless. */
  disabled?: boolean;
  className?: string;
}

const FALLBACK_FILENAME = "مستند";
const FILENAME_MAX = 120;
const ERROR_FALLBACK = "تعذّر تنزيل الملف";
const ERROR_RESET_MS = 6000;
const TITLE_MAX = 200;

const EXTENSION: Record<ExportFormat, string> = {
  pdf: ".pdf",
  docx: ".docx",
};

/** Strip characters illegal in file names on Windows/macOS, trim to 120. */
export function sanitizeExportFilename(title: string): string {
  const cleaned = title
    // eslint-disable-next-line no-control-regex
    .replace(/[/\\:*?"<>|\u0000-\u001f]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, FILENAME_MAX)
    .trim();
  return cleaned || FALLBACK_FILENAME;
}

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoke on the next tick — Safari aborts the download if the URL dies
  // synchronously with the click.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * «PDF» export trigger with a two-item dropdown (plan: document_export_pdf_word.md):
 *
 * - «PDF»  — server-rendered PDF (A4, RTL, page numbers).
 * - «Word» — server-rendered .docx (RTL, editable).
 *
 * The trigger mirrors ``CopyMenu``'s ``bar`` variant exactly (ghost, icon +
 * label) so it sits in ``WorkspaceItemActionBar`` as a sibling of «نسخ». The
 * menu portals at z-[70] (``ui/dropdown-menu``), above the mobile workspace
 * overlay and the docked action bar.
 *
 * No toast system exists in the app, so a failure shows a short inline Arabic
 * message next to the trigger (server ``detail`` when present).
 */
export function ExportMenu({
  text,
  title,
  disabled = false,
  className,
}: ExportMenuProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const errorTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (errorTimer.current !== null) clearTimeout(errorTimer.current);
    };
  }, []);

  const canExport = !disabled && !busy && text.trim().length > 0;

  const showError = (message: string) => {
    setError(message);
    if (errorTimer.current !== null) clearTimeout(errorTimer.current);
    errorTimer.current = setTimeout(() => setError(null), ERROR_RESET_MS);
  };

  const runExport = async (format: ExportFormat) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const docTitle = title.trim().slice(0, TITLE_MAX) || FALLBACK_FILENAME;
      const blob = await exportDocument(format, docTitle, text);
      triggerDownload(blob, sanitizeExportFilename(title) + EXTENSION[format]);
    } catch (err) {
      if (!mounted.current) return;
      // ApiClientError carries the server's Arabic detail (or the Arabic
      // fallback); anything else (network failure) gets the generic message.
      const message =
        err instanceof ApiClientError && err.status !== 401
          ? err.message
          : ERROR_FALLBACK;
      showError(message);
    } finally {
      if (mounted.current) setBusy(false);
    }
  };

  return (
    <>
      <DropdownMenu dir="rtl">
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className={cn(
              "h-7 max-md:h-9 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground",
              className,
            )}
            disabled={!canExport}
            aria-label={busy ? "جارٍ تجهيز الملف" : "تنزيل كملف PDF أو Word"}
            aria-busy={busy}
          >
            {busy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Download className="h-3.5 w-3.5" />
            )}
            PDF
          </Button>
        </DropdownMenuTrigger>
        {/* `dir` comes from the Root above — Radix stamps it on the content. */}
        <DropdownMenuContent align="start" className="w-64">
          <ExportMenuItem
            title="PDF"
            hint="ملف جاهز للطباعة والإرسال"
            icon={
              <FileText className="h-4 w-4 text-red-600 dark:text-red-400" />
            }
            onSelect={() => void runExport("pdf")}
          />
          <ExportMenuItem
            title="Word"
            hint="مستند قابل للتعديل"
            icon={
              <FileType2 className="h-4 w-4 text-blue-600 dark:text-blue-400" />
            }
            onSelect={() => void runExport("docx")}
          />
        </DropdownMenuContent>
      </DropdownMenu>
      {error && (
        <span
          role="alert"
          className="shrink-0 whitespace-nowrap px-1 text-xs text-destructive"
        >
          {error}
        </span>
      )}
    </>
  );
}

function ExportMenuItem({
  title,
  hint,
  icon,
  onSelect,
}: {
  title: string;
  hint: string;
  icon: ReactNode;
  onSelect: () => void;
}) {
  return (
    <DropdownMenuItem
      onSelect={onSelect}
      // `max-md:min-h-9` — 36px touch target below `md` (3.4).
      className="items-start gap-2 text-start max-md:min-h-9"
    >
      <span className="mt-0.5 shrink-0" aria-hidden>
        {icon}
      </span>
      <span className="flex flex-col gap-0.5">
        <span className="text-sm">{title}</span>
        <span className="text-xs text-muted-foreground">{hint}</span>
      </span>
    </DropdownMenuItem>
  );
}
