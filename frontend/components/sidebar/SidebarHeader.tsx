"use client";

import { useMemo } from "react";
import { PanelRightClose, PanelRightOpen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useSidebarStore } from "@/stores/sidebar-store";
import { useAuthStore } from "@/stores/auth-store";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

function getInitial(name: string | null | undefined, email: string | null | undefined): string {
  const source = (name?.trim() || email?.trim() || "").trim();
  if (!source) return "ر";
  const first = Array.from(source)[0];
  return first.toUpperCase();
}

export function SidebarHeader() {
  const { isOpen, toggle } = useSidebarStore();
  const user = useAuthStore((s) => s.user);

  const initial = useMemo(
    () => getInitial(user?.full_name_ar, user?.email),
    [user?.full_name_ar, user?.email]
  );

  return (
    <div className="flex items-center justify-between gap-2 p-3 border-b border-sidebar-border">
      {isOpen && (
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground text-sm font-semibold">
            {initial}
          </div>
          <div className="flex flex-col min-w-0 leading-tight">
            <span className="text-sm font-semibold text-sidebar-foreground truncate">
              ريحان
            </span>
            <span className="text-xs text-muted-foreground truncate">
              القانونية
            </span>
          </div>
        </div>
      )}

      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8 shrink-0 text-sidebar-foreground"
            onClick={toggle}
          >
            {isOpen ? (
              <PanelRightClose className="h-4 w-4" />
            ) : (
              <PanelRightOpen className="h-4 w-4" />
            )}
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom">
          <p>{isOpen ? "طي الشريط الجانبي" : "فتح الشريط الجانبي"}</p>
        </TooltipContent>
      </Tooltip>
    </div>
  );
}
