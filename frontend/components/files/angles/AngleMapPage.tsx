"use client";

import Link from "next/link";
import { ArrowRight, Compass } from "lucide-react";
import { useAngleMap, useFilesStatus } from "@/hooks/use-files";
import { useFilesUi } from "../files-ui";
import { AngleMapPageBody } from "./AnglesViews";
import { ANGLES_COPY } from "./copy";

/**
 * `/files/angles/[mapId]` — one angle map as a page, opened from «الأوجه» on
 * /files (angle_map_ui.md rev 3). Same shell as a file's page; back to /files,
 * which opens on «الأوجه».
 */
export function AngleMapPage({ mapId }: { mapId: string }) {
  const { libraryHref } = useFilesUi();
  const { angleMaps, settled } = useFilesStatus();
  const { data, isLoading, isError } = useAngleMap(mapId);
  const loading = !settled || (angleMaps && isLoading);

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
              {ANGLES_COPY.backToFiles}
            </Link>
          </div>

          {loading ? (
            <div className="animate-pulse space-y-3" aria-hidden="true">
              <div className="h-7 w-2/3 rounded bg-muted" />
              <div className="h-4 w-48 rounded bg-muted/70" />
              <div className="mt-4 h-48 w-full rounded-lg bg-muted/50" />
            </div>
          ) : !angleMaps || isError || !data ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 py-16 text-center">
              <Compass className="h-9 w-9 text-muted-foreground/60" aria-hidden="true" />
              <p className="text-sm text-muted-foreground">{ANGLES_COPY.mapGone}</p>
            </div>
          ) : (
            <AngleMapPageBody
              map={data}
              conversationHref={data.conversation_id ? `/chat/${encodeURIComponent(data.conversation_id)}` : null}
            />
          )}
        </div>
      </div>
    </div>
  );
}
