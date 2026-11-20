"use client";

import { useDeferredValue, useMemo, useState } from "react";
import { FolderOpen, Upload } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { SearchBar } from "@/components/search/SearchBar";
import { SearchEmptyState } from "@/components/search/SearchEmptyState";
import { SEARCH_PRIVATE_COPY } from "@/lib/search/copy";
import { useHideLibraryFile, useLibraryFiles } from "@/hooks/use-files";
import {
  arrangeLibraryFiles,
  fileTypeChips,
  filterFilesByType,
  formatFileCount,
  searchLibraryFiles,
  type FileTypeFilter,
  type LibraryFile,
} from "@/lib/contracts";
import { FileDocCard } from "./FileDocCard";
import { useFilesUi } from "./files-ui";
import { FILES_COPY } from "./copy";

const GRID = "grid gap-4 sm:grid-cols-2 lg:grid-cols-3";

/**
 * `/files` — «الملفات», the user's file library (plan §3.4; a sidebar row of
 * its own since the owner's 2026-11-19 correction, no longer a tab of /chats).
 *
 * Framed like its siblings «قوالبي» / «مدوناتي» (`MyTemplatesGrid`,
 * `MyBlogsGrid`): `max-w-5xl` page, an icon tile + title + one-line subtitle
 * header with the action at the end, the search under it, a 1 / 2 / 3-column
 * card grid. What it adds: the type chips, and the §3.4 grouping — with more
 * than 6 files in 2 or more types each type is a section with its own grid
 * (largest first, «أخرى» last, newest first inside); otherwise one grid, newest
 * first.
 *
 * One `GET /files` response; the search box and the chips filter it on the
 * client. A card opens `/files/{id}`; its menu removes the file from
 * «الملفات» only (§3.7).
 */
export function FilesPage() {
  const { fileHref, showUpcoming } = useFilesUi();
  const { data, isLoading, isError, refetch, isRefetching } = useLibraryFiles();
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query);
  const [typeFilter, setTypeFilter] = useState<FileTypeFilter>("all");
  const [pendingDelete, setPendingDelete] = useState<LibraryFile | null>(null);

  const files = useMemo(() => data?.files ?? [], [data]);
  const chips = useMemo(() => fileTypeChips(files), [files]);
  // A chip whose last file was just removed falls back to «الكل».
  const activeFilter: FileTypeFilter =
    typeFilter === "all" || chips.some((c) => c.type === typeFilter) ? typeFilter : "all";

  const visible = useMemo(
    () => searchLibraryFiles(filterFilesByType(files, activeFilter), deferredQuery),
    [files, activeFilter, deferredQuery],
  );
  const layout = useMemo(() => arrangeLibraryFiles(visible), [visible]);
  const isSearching = deferredQuery.trim().length > 0;

  const renderCard = (file: LibraryFile) => (
    <FileDocCard
      key={file.item_id}
      file={file}
      href={fileHref(file.item_id)}
      onRequestDelete={() => setPendingDelete(file)}
      showUpcoming={showUpcoming}
    />
  );

  return (
    // Native scroll, NOT Radix ScrollArea (which the sibling grids use): its
    // viewport wraps content in a `display: table` box, and the horizontally
    // scrolling chip row would widen the whole page past a phone screen.
    <div className="min-h-0 flex-1 overflow-y-auto" dir="rtl">
      <div className="mx-auto w-full max-w-5xl px-4 py-8">
        {/* `max-md:ps-12` clears the sidebar's fixed menu button (start-3),
            which otherwise sits on the icon tile on a phone. */}
        <header className="mb-6 flex items-center gap-3 max-md:ps-12">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <FolderOpen className="h-5 w-5" />
          </span>
          <div className="min-w-0">
            <h1 className="text-xl font-bold text-foreground">{FILES_COPY.pageTitle}</h1>
            {isLoading ? (
              <div className="mt-1.5 h-3.5 w-32 animate-pulse rounded bg-muted" aria-hidden="true" />
            ) : (
              <p className="text-sm text-muted-foreground tabular-nums">
                {files.length > 0
                  ? `${formatFileCount(Math.max(data?.total ?? 0, files.length))} ${FILES_COPY.pageSubtitleSuffix}`
                  : FILES_COPY.pageSubtitleEmpty}
              </p>
            )}
          </div>
          {showUpcoming && <UpcomingUploadButton />}
        </header>

        {files.length > 0 && (
          <div className="mb-4">
            <SearchBar
              value={query}
              onChange={setQuery}
              placeholder={SEARCH_PRIVATE_COPY.files.placeholder}
              ariaLabel={SEARCH_PRIVATE_COPY.files.ariaLabel}
              minLength={1}
              className="sm:w-full"
            />
          </div>
        )}

        {/* Type chips — «الكل» + only the types this user has */}
        {chips.length > 1 && (
          <div
            role="group"
            aria-label={FILES_COPY.typeChipsLabel}
            className="-mx-1 mb-6 flex gap-1.5 overflow-x-auto px-1 pb-1"
          >
            <TypeChip
              label={FILES_COPY.allChip}
              count={files.length}
              active={activeFilter === "all"}
              onClick={() => setTypeFilter("all")}
            />
            {chips.map((chip) => (
              <TypeChip
                key={chip.type}
                label={chip.label}
                count={chip.count}
                active={activeFilter === chip.type}
                onClick={() => setTypeFilter(chip.type)}
              />
            ))}
          </div>
        )}

        {isLoading ? (
          <div className={GRID} aria-hidden="true">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="h-48 animate-pulse rounded-xl bg-muted/40" />
            ))}
          </div>
        ) : isError ? (
          <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
            <p className="text-sm text-muted-foreground">{FILES_COPY.loadError}</p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void refetch()}
              disabled={isRefetching}
            >
              {FILES_COPY.retry}
            </Button>
          </div>
        ) : files.length === 0 ? (
          <div className="flex flex-col items-center justify-center px-4 py-16 text-center">
            <div className="mb-5 flex h-16 w-16 items-center justify-center rounded-2xl bg-muted text-muted-foreground">
              <FolderOpen className="h-7 w-7" />
            </div>
            <h2 className="mb-2 text-lg font-bold text-foreground">{FILES_COPY.emptyTitle}</h2>
            <p className="max-w-md text-sm text-muted-foreground">{FILES_COPY.emptyHint}</p>
          </div>
        ) : visible.length === 0 ? (
          <SearchEmptyState />
        ) : layout.kind === "flat" ? (
          <div className={GRID}>{layout.files.map(renderCard)}</div>
        ) : (
          <div className="space-y-8">
            {layout.groups.map((group) => (
              <section key={group.type} aria-label={group.label}>
                <h2 className="mb-3 flex items-baseline gap-2 text-sm font-semibold text-foreground">
                  {group.label}
                  <span className="text-xs font-normal tabular-nums text-muted-foreground">
                    {group.files.length}
                  </span>
                </h2>
                <div className={GRID}>{group.files.map(renderCard)}</div>
              </section>
            ))}
          </div>
        )}
      </div>

      <DeleteFromLibraryDialog file={pendingDelete} onClose={() => setPendingDelete(null)} />
    </div>
  );
}

function TypeChip({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  // Same chip as «مكتبتي»'s tabs (MyLibraryTabs) so the collections match.
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 rounded-full border px-3 py-1 text-sm font-medium transition-colors max-md:py-1.5",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active
          ? "border-primary/60 bg-primary/5 text-foreground"
          : "border-transparent text-muted-foreground hover:bg-accent/50 hover:text-foreground",
      )}
    >
      <span>{label}</span>
      <span
        className={cn(
          "text-xs font-medium tabular-nums",
          active ? "text-primary" : "text-muted-foreground/80",
        )}
      >
        {count}
      </span>
    </button>
  );
}

function UpcomingUploadButton() {
  // A disabled <button> swallows the pointer events a tooltip needs, so the
  // trigger is a focusable wrapper and the button inside is inert.
  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            tabIndex={0}
            title={FILES_COPY.soon}
            className="ms-auto shrink-0 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled
              aria-disabled="true"
              className="pointer-events-none gap-1.5"
            >
              <Upload className="h-4 w-4" />
              {FILES_COPY.upload}
            </Button>
          </span>
        </TooltipTrigger>
        <TooltipContent side="bottom">
          <p className="text-xs">{FILES_COPY.soon}</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

/** §3.7 — leaves «الملفات», stays inside the conversations that used it. */
function DeleteFromLibraryDialog({
  file,
  onClose,
}: {
  file: LibraryFile | null;
  onClose: () => void;
}) {
  const hide = useHideLibraryFile();
  const [failed, setFailed] = useState(false);

  const close = () => {
    setFailed(false);
    hide.reset();
    onClose();
  };

  const confirm = () => {
    if (!file) return;
    setFailed(false);
    hide.mutate(file.item_id, {
      onSuccess: close,
      onError: () => setFailed(true),
    });
  };

  return (
    <AlertDialog open={file !== null} onOpenChange={(open) => !open && !hide.isPending && close()}>
      <AlertDialogContent dir="rtl">
        <AlertDialogHeader>
          <AlertDialogTitle>{FILES_COPY.deleteTitle}</AlertDialogTitle>
          <AlertDialogDescription className="leading-7">
            {file && <span className="mb-1 block font-medium text-foreground">«{file.title}»</span>}
            {FILES_COPY.deleteBody}
          </AlertDialogDescription>
        </AlertDialogHeader>
        {failed && (
          <p role="alert" className="text-sm text-destructive">
            {FILES_COPY.deleteError}
          </p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={hide.isPending}>{FILES_COPY.cancel}</AlertDialogCancel>
          <Button
            type="button"
            onClick={confirm}
            disabled={hide.isPending}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            {FILES_COPY.deleteConfirm}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
