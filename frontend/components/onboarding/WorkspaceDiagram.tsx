"use client";

import {
  Compass,
  FileText,
  MessageSquareText,
  PanelLeft,
  PenLine,
  Pin,
  Search,
} from "lucide-react";

/**
 * Step-2 diagram: a miniature of the real app layout — chat pane (right in
 * RTL) + workspace pane (left) holding workspace-item cards — with the three
 * agents underneath, all connected to the workspace. Pure CSS, theme tokens.
 */
export function WorkspaceDiagram() {
  return (
    <div dir="rtl" className="flex flex-col items-center">
      <div className="grid w-full grid-cols-2 gap-3 rounded-xl border border-border bg-muted/30 p-3">
        {/* chat mini-pane (right side in RTL, like the real app) */}
        <div className="space-y-2 rounded-lg border border-border bg-background p-3">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
            <MessageSquareText className="h-3.5 w-3.5" />
            المحادثة
          </div>
          <div className="h-2.5 w-4/5 rounded-full bg-muted" />
          <div className="h-2.5 w-3/5 rounded-full bg-primary/15" />
          <div className="h-2.5 w-2/3 rounded-full bg-muted" />
        </div>

        {/* workspace mini-pane with WI cards */}
        <div className="space-y-2 rounded-lg border border-primary/40 bg-background p-3">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-primary">
            <PanelLeft className="h-3.5 w-3.5" />
            مساحة العمل
          </div>
          <div className="flex items-center gap-1.5 rounded-md border border-border bg-muted/40 px-2 py-1.5 text-[11px]">
            <FileText className="h-3 w-3 shrink-0 text-primary" />
            مستند مرفوع
          </div>
          <div className="flex items-center gap-1.5 rounded-md border border-border bg-muted/40 px-2 py-1.5 text-[11px]">
            <Search className="h-3 w-3 shrink-0 text-primary" />
            نتيجة بحث
          </div>
          <div className="flex items-center gap-1.5 rounded-md border border-border bg-muted/40 px-2 py-1.5 text-[11px]">
            <Pin className="h-3 w-3 shrink-0 text-primary" />
            معلومة محفوظة
          </div>
        </div>
      </div>

      {/* all three agents read from / write to the workspace */}
      <div className="h-3 w-0 border-e border-dashed border-primary/50" />
      <div className="flex items-center gap-2">
        {[
          { label: "الموجّه", Icon: Compass },
          { label: "الكاتب", Icon: PenLine },
          { label: "الباحث", Icon: Search },
        ].map(({ label, Icon }) => (
          <div
            key={label}
            className="flex items-center gap-1.5 rounded-full border border-border bg-background px-3 py-1 text-xs font-medium"
          >
            <Icon className="h-3 w-3 text-primary" />
            {label}
          </div>
        ))}
      </div>
      <p className="mt-2 text-center text-[11px] text-muted-foreground">
        جميع الوكلاء يقرؤون من مساحة العمل ويضيفون إليها
      </p>
    </div>
  );
}
