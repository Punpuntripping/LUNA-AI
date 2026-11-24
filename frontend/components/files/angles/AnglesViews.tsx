"use client";

import Link from "next/link";
import { ChevronLeft, Compass, MessagesSquare } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  ANGLE_KIND_LABELS,
  ANGLE_KINDS,
  formatAngleCount,
  formatFileCount,
  formatFileDate,
  groupAngleMapsByConversation,
  type AngleKind,
  type AngleMap,
  type AngleMapSummary,
} from "@/lib/contracts";
import { ANGLE_KIND_STYLE } from "./angle-style";
import { AngleMapView } from "./AngleMapView";
import { ANGLES_COPY } from "./copy";

/**
 * «الأوجه» at the front of «الملفات» (owner, 2026-11-24 — .claude/plans/angle_map_ui.md
 * rev 3): the switch «الأوجه» | «الملفات», the chat pane's stack of a
 * conversation's maps (newest first), and the /files list of maps grouped by
 * conversation. Presentational — the containers (`PaneFilesTab`, `FilesPage`,
 * `AngleMapPage`) fetch; the dev preview feeds fixtures.
 */

export type FilesView = "angles" | "files";

/** «الخيوط» | «الملفات» — «الخيوط» first, «الملفات» open by default. */
export function FilesViewSwitch({
  value,
  onChange,
  className,
}: {
  value: FilesView;
  onChange: (view: FilesView) => void;
  className?: string;
}) {
  const options: { view: FilesView; label: string }[] = [
    { view: "angles", label: ANGLES_COPY.viewAngles },
    { view: "files", label: ANGLES_COPY.viewFiles },
  ];
  return (
    <div
      role="group"
      aria-label={ANGLES_COPY.viewSwitchLabel}
      className={cn("inline-flex rounded-lg border border-border bg-muted/40 p-0.5", className)}
    >
      {options.map((o) => (
        <button
          key={o.view}
          type="button"
          aria-pressed={value === o.view}
          onClick={() => onChange(o.view)}
          className={cn(
            "min-w-20 rounded-md px-3 py-1 text-xs font-medium transition-colors max-md:py-1.5",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            value === o.view
              ? "bg-background text-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** «رسالة أمس · ملفان · 8 أوجه» — a map's one-line heading. */
export function angleMapHeadingText(map: Pick<AngleMap, "created_at" | "documents">, angleCount: number): string {
  return [ANGLES_COPY.message(formatFileDate(map.created_at)), formatFileCount(map.documents.length), formatAngleCount(angleCount)]
    .filter(Boolean)
    .join(" · ");
}

function KindCounts({ kinds }: { kinds: Record<AngleKind, number> }) {
  return (
    <span className="flex flex-wrap gap-x-2.5 gap-y-0.5">
      {ANGLE_KINDS.map((k) =>
        kinds[k] > 0 ? (
          <span key={k} className={cn("inline-flex items-center gap-1", ANGLE_KIND_STYLE[k].text)}>
            <span className={cn("h-1.5 w-1.5 rounded-full", ANGLE_KIND_STYLE[k].line)} aria-hidden="true" />
            {ANGLE_KIND_LABELS[k]} <b className="tabular-nums">{kinds[k]}</b>
          </span>
        ) : null,
      )}
    </span>
  );
}

/**
 * The chat pane's «الأوجه»: the conversation's maps stacked, newest first, each
 * under its message's heading. `maps` in list order; `null` = still loading.
 */
export function AnglesStack({ maps, error }: { maps: (AngleMap | null)[]; error?: boolean }) {
  return (
    <div className="space-y-6 px-3 pb-8 pt-3">
      <p className="text-xs leading-6 text-muted-foreground">{ANGLES_COPY.intro}</p>
      {error && <p className="text-sm text-muted-foreground">{ANGLES_COPY.loadError}</p>}
      {maps.map((map, i) =>
        map ? (
          <section key={map.map_id} aria-label={angleMapHeadingText(map, map.angles.length)}>
            <AngleMapView
              map={map}
              heading={
                <h3
                  className={cn(
                    "flex items-center gap-2 text-sm font-semibold text-foreground",
                    i > 0 && "border-t border-border pt-5",
                  )}
                >
                  <Compass className="h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
                  {angleMapHeadingText(map, map.angles.length)}
                </h3>
              }
            />
          </section>
        ) : (
          <div key={`loading-${i}`} className="space-y-2" aria-hidden="true">
            <div className="h-5 w-48 animate-pulse rounded bg-muted" />
            <div className="h-40 w-full animate-pulse rounded-lg bg-muted/60" />
          </div>
        ),
      )}
    </div>
  );
}

/**
 * /files «الأوجه»: every map, grouped by conversation (groups in the order of
 * their newest map), one row per map — its message's date, its files, its angle
 * counts — opening the map's page.
 */
export function LibraryAnglesList({
  maps,
  mapHref,
}: {
  maps: AngleMapSummary[];
  mapHref: (mapId: string) => string;
}) {
  if (maps.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center px-4 py-16 text-center">
        <div className="mb-5 flex h-16 w-16 items-center justify-center rounded-2xl bg-muted text-muted-foreground">
          <Compass className="h-7 w-7" />
        </div>
        <h2 className="mb-2 text-lg font-bold text-foreground">{ANGLES_COPY.libraryEmptyTitle}</h2>
        <p className="max-w-md text-sm text-muted-foreground">{ANGLES_COPY.libraryEmptyHint}</p>
      </div>
    );
  }
  const groups = groupAngleMapsByConversation(maps);
  return (
    <div className="space-y-7">
      <p className="text-xs leading-6 text-muted-foreground">{ANGLES_COPY.intro}</p>
      {groups.map((g) => (
        <section key={g.conversation_id ?? "none"} aria-label={g.conversation_title ?? ANGLES_COPY.noConversation}>
          <h2 className="mb-2.5 flex items-center gap-2 text-sm font-semibold text-foreground">
            <MessagesSquare className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
            <span className="min-w-0 truncate">{g.conversation_title || ANGLES_COPY.noConversation}</span>
          </h2>
          <ul className="space-y-2">
            {g.maps.map((m) => (
              <li key={m.map_id}>
                <Link
                  href={mapHref(m.map_id)}
                  className="group flex items-center gap-3 rounded-xl border border-border bg-card px-4 py-3 transition-colors hover:border-primary/40 hover:bg-accent/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-medium text-foreground">
                      {angleMapHeadingText(m, m.angle_count)}
                    </span>
                    <span className="mt-0.5 block truncate text-xs leading-5 text-muted-foreground">
                      {m.documents.map((d) => d.title).filter(Boolean).join(" · ")}
                    </span>
                    <span className="mt-1 block text-xs leading-5">
                      <KindCounts kinds={m.kinds} />
                    </span>
                  </span>
                  <ChevronLeft
                    className="h-4 w-4 shrink-0 text-muted-foreground transition-transform group-hover:-translate-x-0.5"
                    aria-hidden="true"
                  />
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

/** The /files map page's body: title (its conversation), heading, the map. */
export function AngleMapPageBody({
  map,
  conversationHref,
}: {
  map: AngleMap;
  conversationHref?: string | null;
}) {
  return (
    <AngleMapView
      map={map}
      heading={
        <header className="space-y-1">
          <h1 className="text-lg font-bold leading-8 text-foreground">
            {map.conversation_title || ANGLES_COPY.noConversation}
          </h1>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
            <span>{angleMapHeadingText(map, map.angles.length)}</span>
            {conversationHref && (
              <Link
                href={conversationHref}
                className="rounded text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {ANGLES_COPY.openConversation}
              </Link>
            )}
          </div>
          <p className="pt-1 text-xs leading-6 text-muted-foreground">{ANGLES_COPY.intro}</p>
        </header>
      }
    />
  );
}
