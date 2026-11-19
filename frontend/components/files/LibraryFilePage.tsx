"use client";

import Link from "next/link";
import { ArrowRight, FileWarning } from "lucide-react";
import { ApiClientError } from "@/lib/api";
import { useLibraryFile } from "@/hooks/use-files";
import { FileViewer } from "./FileViewer";
import { useFilesUi } from "./files-ui";
import { FILES_COPY } from "./copy";

/**
 * `/files/[itemId]` — one file of «الملفات» as a page (plan §3.4): the same
 * `FileViewer` the pane uses (here with the type tile in its header), under a
 * back link to `/files`. Rendered inside the sidebar shell (`app/files/layout`).
 */
export function LibraryFilePage({ itemId }: { itemId: string }) {
  const { libraryHref } = useFilesUi();
  const { data, isLoading, error } = useLibraryFile(itemId);
  const notFound = error instanceof ApiClientError && error.status === 404;

  return (
    <div className="flex h-full flex-col overflow-hidden" dir="rtl">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col px-4 py-6 sm:px-6 sm:py-8">
          {/* `max-md:ps-12` clears the sidebar's fixed menu button (start-3). */}
          <div className="mb-4 max-md:ps-12">
            <Link
              href={libraryHref}
              className="inline-flex items-center gap-1.5 rounded-md px-1.5 py-1 text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {/* RTL: ArrowRight points "back" (toward the start). */}
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
              {FILES_COPY.backToLibrary}
            </Link>
          </div>

          {isLoading ? (
            <ViewerSkeleton />
          ) : error || !data ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 py-16 text-center">
              <FileWarning className="h-9 w-9 text-muted-foreground/60" aria-hidden="true" />
              <p className="text-sm text-muted-foreground">
                {notFound ? FILES_COPY.fileNotFound : FILES_COPY.fileLoadError}
              </p>
            </div>
          ) : (
            <FileViewer file={data} variant="page" />
          )}
        </div>
      </div>
    </div>
  );
}

function ViewerSkeleton() {
  return (
    <div className="animate-pulse space-y-3" aria-hidden="true">
      <div className="flex items-center gap-3">
        <div className="h-12 w-12 rounded-xl bg-muted" />
        <div className="h-7 flex-1 rounded bg-muted" />
      </div>
      <div className="h-4 w-40 rounded bg-muted/70" />
      <div className="mt-4 h-9 w-full rounded bg-muted/60" />
      <div className="mt-4 h-32 w-full rounded-lg bg-muted/50" />
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="h-10 w-full rounded bg-muted/40" />
      ))}
    </div>
  );
}
