// Internal helper shared by ReferencesMesh + ReadAfter (NOT exported from the
// public blocks index). Maps a `ReferenceKind` to its lucide icon + a short
// Arabic label used for the kind chip / aria text.

import {
  Scale,
  FileText,
  Gavel,
  Megaphone,
  Landmark,
  BookOpen,
  ClipboardList,
  ExternalLink,
  type LucideIcon,
} from "lucide-react";
import type { ReferenceKind } from "@/types/library";

const KIND_ICON: Record<ReferenceKind, LucideIcon> = {
  regulation: Scale,
  article: FileText,
  judgment: Gavel,
  circular: Megaphone,
  service: Landmark,
  blog: BookOpen,
  form: ClipboardList,
  external: ExternalLink,
};

export const REFERENCE_KIND_LABEL: Record<ReferenceKind, string> = {
  regulation: "نظام",
  article: "مادة",
  judgment: "حكم",
  circular: "تعميم",
  service: "خدمة",
  blog: "مقال",
  form: "نموذج",
  external: "مصدر خارجي",
};

interface ReferenceKindIconProps {
  kind: ReferenceKind;
  className?: string;
}

/** The kind's icon. `aria-hidden` — the label text carries meaning. */
export function ReferenceKindIcon({ kind, className }: ReferenceKindIconProps) {
  const Icon = KIND_ICON[kind];
  return <Icon aria-hidden="true" className={className} />;
}
