"use client";

import { useEffect, useRef, type ReactNode } from "react";
import Link from "next/link";
import {
  AlertCircle,
  Clock,
  FileX2,
  Loader2,
  MessageSquarePlus,
  MoreVertical,
  FolderOpen,
  Trash2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { WiBadge } from "@/components/workspace/WiBadge";
import { FileTypePill } from "./FileTypePill";
import { FILES_COPY } from "./copy";
import { ReattachAdvice } from "./ReattachAdvice";
import {
  FILE_STATUS_COPY,
  formatFileDate,
  formatPageCount,
  type FileCardModel,
} from "@/lib/contracts";

interface FileCardProps {
  file: FileCardModel;
  /** A link to the file page (instead of `onOpen`). */
  href?: string;
  /** Pane: the card is a button (opens the item in the pane). */
  onOpen?: () => void;
  /** The «WI-3» alias — pane only (it is per conversation). */
  showWiBadge?: boolean;
  /** Pane: ring + scroll into view while a chat chip points at this file. */
  highlighted?: boolean;
  /** DOM id of the card (the pane keeps `workspace-item-{id}`). */
  domId?: string;
  /** Shows the «⋮» menu with «فتح» and «حذف من الملفات». */
  onRequestDelete?: () => void;
  /** Phase 3 «أضف إلى محادثة», shown inactive («قريبًا»). Default hidden. */
  showUpcoming?: boolean;
}

const STATUS_ICON = {
  pending: Clock,
  processing: Loader2,
  empty: FileX2,
  failed: AlertCircle,
} as const;

/**
 * One file (plan §3.2): type pill · title · the first two lines of the summary
 * · a quiet line with the page count (when known) and the date. In the pane it
 * also carries the WI badge; with `onRequestDelete` a «⋮» menu.
 *
 * States (from `metadata.ocr_status`): a file not read yet says it will be read
 * when the message is sent; «جارٍ استخراج النص…» only while that read is
 * running; nothing to read / a failed read say so; a read file with no summary
 * yet says that instead of an empty line.
 *
 * Used by the pane («الملفات» tab). The `/files` page uses `FileDocCard`.
 */
export function FileCard({
  file,
  href,
  onOpen,
  showWiBadge = false,
  highlighted = false,
  domId,
  onRequestDelete,
  showUpcoming = false,
}: FileCardProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!highlighted) return;
    rootRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [highlighted]);

  const pages = formatPageCount(file.page_count);
  const date = formatFileDate(file.created_at);
  const hasMenu = Boolean(onRequestDelete);

  const body = (
    <>
      <div className="flex min-w-0 items-center gap-2">
        <FileTypePill docType={file.doc_type} />
        <p className="min-w-0 flex-1 truncate text-sm font-medium text-foreground" title={file.title}>
          {file.title}
        </p>
      </div>

      <CardSummary file={file} />
      {file.reattach_advised && <ReattachAdvice className="mt-2" />}

      <div className="mt-2 flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground tabular-nums">
        {showWiBadge && <WiBadge seq={file.wi_seq} />}
        {pages && <span className="whitespace-nowrap">{pages}</span>}
        {pages && date && <span aria-hidden="true">·</span>}
        {date && <span className="whitespace-nowrap">{date}</span>}
      </div>
    </>
  );

  const cardClass = cn(
    "block w-full rounded-lg border border-border/50 bg-card p-3 text-start",
    "transition-[box-shadow,background-color] duration-300",
    "hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
    hasMenu && "pe-11",
    highlighted && "ring-2 ring-primary ring-offset-2 ring-offset-background",
  );

  let card: ReactNode;
  if (href) {
    card = (
      <Link href={href} className={cardClass}>
        {body}
      </Link>
    );
  } else {
    card = (
      <button type="button" onClick={onOpen} className={cardClass}>
        {body}
      </button>
    );
  }

  return (
    <div ref={rootRef} id={domId} className="relative" dir="rtl">
      {card}
      {hasMenu && (
        <FileCardMenu
          href={href}
          onOpen={onOpen}
          onRequestDelete={onRequestDelete}
          showUpcoming={showUpcoming}
          className="absolute end-1.5 top-1.5"
        />
      )}
    </div>
  );
}

/**
 * The «⋮» menu of a file card: «فتح», the inactive Phase-3 «أضف إلى محادثة»
 * (only with `showUpcoming`), «حذف من الملفات». Shared by the pane card and the
 * `/files` document tile. A sibling of the card's link, never inside it.
 */
export function FileCardMenu({
  href,
  onOpen,
  onRequestDelete,
  showUpcoming = false,
  className,
}: {
  href?: string;
  onOpen?: () => void;
  onRequestDelete?: () => void;
  showUpcoming?: boolean;
  className?: string;
}) {
  return (
    <div className={className}>
      <DropdownMenu dir="rtl">
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-muted-foreground hover:text-foreground max-md:h-9 max-md:w-9"
            aria-label={FILES_COPY.cardMenu}
          >
            <MoreVertical className="h-3.5 w-3.5" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-48">
          {href ? (
            <DropdownMenuItem asChild>
              <Link href={href}>
                <FolderOpen className="me-2 h-3.5 w-3.5" />
                {FILES_COPY.open}
              </Link>
            </DropdownMenuItem>
          ) : (
            <DropdownMenuItem onSelect={() => onOpen?.()}>
              <FolderOpen className="me-2 h-3.5 w-3.5" />
              {FILES_COPY.open}
            </DropdownMenuItem>
          )}
          {showUpcoming && (
            <DropdownMenuItem
              aria-disabled="true"
              title={FILES_COPY.soon}
              onSelect={(e) => e.preventDefault()}
              className="cursor-default text-muted-foreground opacity-60 focus:bg-transparent focus:text-muted-foreground"
            >
              <MessageSquarePlus className="me-2 h-3.5 w-3.5" />
              {FILES_COPY.addToConversation}
              <SoonTag />
            </DropdownMenuItem>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onSelect={() => onRequestDelete?.()}
            className="text-destructive focus:text-destructive"
          >
            <Trash2 className="me-2 h-3.5 w-3.5" />
            {FILES_COPY.removeFromLibrary}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

function CardSummary({ file }: { file: FileCardModel }) {
  if (file.status !== "ready") {
    const Icon = STATUS_ICON[file.status];
    return (
      <p
        className={cn(
          "mt-1.5 flex items-center gap-1.5 text-sm",
          file.status === "failed" ? "text-destructive" : "text-muted-foreground",
        )}
      >
        <Icon
          className={cn("h-3.5 w-3.5 shrink-0", file.status === "processing" && "animate-spin")}
          aria-hidden="true"
        />
        {FILE_STATUS_COPY[file.status]}
      </p>
    );
  }
  if (!file.summary_preview.trim()) {
    return <p className="mt-1.5 text-sm text-muted-foreground/70">{FILES_COPY.noSummary}</p>;
  }
  return (
    <p className="mt-1.5 line-clamp-2 text-sm leading-6 text-muted-foreground">
      {file.summary_preview}
    </p>
  );
}

/** The small «قريبًا» marker on an inactive Phase-3 control. */
export function SoonTag({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "ms-auto rounded-full border border-border/70 px-1.5 py-0.5 text-xs leading-none text-muted-foreground",
        className,
      )}
    >
      {FILES_COPY.soon}
    </span>
  );
}
