import {
  BookOpen,
  ClipboardList,
  FilePenLine,
  FileText,
  Gavel,
  Handshake,
  Mail,
  MessageCircle,
  Microscope,
  PenLine,
  Receipt,
  ScrollText,
  Stamp,
  StickyNote,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  coerceFileDocType,
  fileTypeFamily,
  type FileDocType,
  type FileTypeFamily,
} from "@/lib/contracts";

/**
 * The «الملفات» identity: one icon per document type, one tint per FAMILY.
 *
 * Five families, not fourteen colours (the workspace badges were retoned away
 * from a rainbow — see `KIND_COLORS` in WorkspaceCard). Only existing theme
 * tokens, so light, dark and «الوضع الهادئ» all follow the palette:
 *
 * | family          | types                                         | tokens                 |
 * | --------------- | --------------------------------------------- | ---------------------- |
 * | court           | حكم · محضر جلسة · صحيفة دعوى · مذكرة · تقرير خبرة | primary (sage)         |
 * | records         | عقد · سجل رسمي · إقرار                         | info (slate blue)      |
 * | money           | مستند مالي                                    | warning (amber)        |
 * | correspondence  | مراسلة · خطاب                                  | muted / text-muted     |
 * | general         | ملاحظاتي · مرجع · أخرى · بلا نوع                | plain paper (outline)  |
 */
export const FILE_TYPE_ICONS: Record<FileDocType, LucideIcon> = {
  judgment: Gavel,
  hearing_minutes: ClipboardList,
  claim: FilePenLine,
  memo: ScrollText,
  contract: Handshake,
  chat: MessageCircle,
  letter: Mail,
  financial: Receipt,
  official_record: Stamp,
  expert_report: Microscope,
  statement: PenLine,
  user_notes: StickyNote,
  reference: BookOpen,
  other: FileText,
};

interface FamilyStyle {
  /** The icon tile. */
  tile: string;
  /** The slim spine on the card's start edge. */
  spine: string;
  /** The type label beside the tile. */
  label: string;
}

export const FILE_FAMILY_STYLES: Record<FileTypeFamily, FamilyStyle> = {
  court: {
    tile: "bg-primary/10 text-primary",
    spine: "bg-primary/70",
    label: "text-primary",
  },
  records: {
    tile: "bg-info text-info-fg",
    spine: "bg-info-fg/50",
    label: "text-info-fg",
  },
  money: {
    tile: "bg-warning text-warning-fg",
    spine: "bg-warning-fg/50",
    label: "text-warning-fg",
  },
  correspondence: {
    tile: "bg-muted text-text-muted",
    spine: "bg-text-muted/40",
    label: "text-text-muted",
  },
  general: {
    tile: "border border-border bg-card text-muted-foreground",
    spine: "bg-border-strong",
    label: "text-muted-foreground",
  },
};

export function fileTypeIcon(docType: unknown): LucideIcon {
  return FILE_TYPE_ICONS[coerceFileDocType(docType) ?? "other"];
}

export function fileFamilyStyle(docType: unknown): FamilyStyle {
  return FILE_FAMILY_STYLES[fileTypeFamily(docType)];
}

const TILE_SIZES = {
  md: { box: "h-9 w-9 rounded-lg", icon: "h-[18px] w-[18px]" },
  lg: { box: "h-12 w-12 rounded-xl", icon: "h-6 w-6" },
} as const;

/** The type's icon on its family tint — the same tile on the card and the file page. */
export function FileTypeTile({
  docType,
  size = "md",
  className,
}: {
  docType: unknown;
  size?: keyof typeof TILE_SIZES;
  className?: string;
}) {
  const Icon = fileTypeIcon(docType);
  const dims = TILE_SIZES[size];
  return (
    <span
      aria-hidden="true"
      className={cn(
        "flex shrink-0 items-center justify-center",
        dims.box,
        fileFamilyStyle(docType).tile,
        className,
      )}
    >
      <Icon className={dims.icon} />
    </span>
  );
}
