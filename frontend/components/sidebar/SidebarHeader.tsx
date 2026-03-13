"use client";

import { PanelRightClose, PanelRightOpen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useSidebarStore } from "@/stores/sidebar-store";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

export function SidebarHeader() {
  const { isOpen, toggle } = useSidebarStore();

  return (
    <div className="flex items-center justify-between p-3 border-b border-sidebar-border">
      {/* Logo / Title */}
      {isOpen && (
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-foreground text-xs font-bold">
            لونا
          </div>
          <span className="text-sm font-semibold text-sidebar-foreground">
            لونا القانونية
          </span>
        </div>
      )}

      <div className="flex items-center gap-1">
        {/* Collapse toggle */}
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-sidebar-foreground"
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
    </div>
  );
}
