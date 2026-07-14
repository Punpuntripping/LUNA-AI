"use client";

import {
  BookOpenText,
  Compass,
  MessageSquareText,
  PenLine,
  Scale,
  Search,
  ShieldCheck,
} from "lucide-react";
import { cn } from "@/lib/utils";

function VLine({ className }: { className?: string }) {
  return <div className={cn("h-4 w-px bg-border", className)} />;
}

/**
 * Step-1 diagram: سؤالك → الموجّه → {الكاتب، الباحث}. Agents are shown by
 * name only (no per-agent role text — deliberate); the searcher alone is
 * expanded into its two modes. Pure CSS/flex so it inherits RTL + theme
 * tokens — same convention as the landing hero (no SVG assets).
 */
export function AgentsDiagram() {
  return (
    <div dir="rtl" className="flex flex-col items-center">
      <div className="flex items-center gap-2 rounded-full border border-border bg-muted/50 px-4 py-1.5 text-sm text-muted-foreground">
        <MessageSquareText className="h-4 w-4" />
        سؤالك
      </div>
      <VLine />

      <div className="flex items-center gap-2 rounded-xl border border-border bg-background px-5 py-2.5 text-sm font-semibold shadow-sm">
        <Compass className="h-4 w-4 text-primary" />
        الموجّه
      </div>

      {/* T-branch: router → the two agents below */}
      <VLine className="h-3" />
      <div className="h-px w-1/2 bg-border" />
      <div className="flex w-1/2 justify-between">
        <VLine className="h-3" />
        <VLine className="h-3" />
      </div>

      <div className="grid w-full grid-cols-2 gap-4">
        <div className="flex flex-col items-center">
          <div className="flex items-center gap-2 rounded-xl border border-border bg-background px-5 py-2.5 text-sm font-semibold shadow-sm">
            <PenLine className="h-4 w-4 text-primary" />
            الكاتب
          </div>
        </div>

        {/* the searcher — the star of this step, expanded into its 2 modes */}
        <div className="flex flex-col items-center">
          <div className="flex items-center gap-2 rounded-xl border-2 border-primary/50 bg-primary/5 px-5 py-2.5 text-sm font-semibold text-primary shadow-sm">
            <Search className="h-4 w-4" />
            الباحث
          </div>
          <VLine className="h-3" />
          <div className="flex w-full flex-col gap-2">
            <div className="rounded-lg border border-border bg-background p-2.5">
              <div className="flex items-center gap-2 text-xs font-semibold">
                <BookOpenText className="h-3.5 w-3.5 shrink-0 text-primary" />
                بحث الأنظمة واللوائح
              </div>
              <div className="mt-1.5 flex items-center gap-1 text-[11px] text-muted-foreground">
                <ShieldCheck className="h-3 w-3 shrink-0" />
                يشمل فحص الالتزام
              </div>
            </div>
            <div className="rounded-lg border border-border bg-background p-2.5">
              <div className="flex items-center gap-2 text-xs font-semibold">
                <Scale className="h-3.5 w-3.5 shrink-0 text-primary" />
                بحث الأحكام القضائية
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
