"use client";

import { useMemo } from "react";
import { PanelRightClose, PanelRightOpen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useSidebarStore } from "@/stores/sidebar-store";
import { useAuthStore } from "@/stores/auth-store";
import { userInitial } from "@/lib/user-name";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

interface SidebarHeaderProps {
  /**
   * Whether the sidebar body is on screen at full width. Defaults to the
   * store's `isOpen`, which is the desktop rail's collapse state; inside the
   * mobile drawer the host passes `true` — being open is what put the drawer
   * on screen in the first place.
   */
  expanded?: boolean;
}

export function SidebarHeader({ expanded }: SidebarHeaderProps = {}) {
  const { isOpen, toggle } = useSidebarStore();
  const user = useAuthStore((s) => s.user);
  const showBrand = expanded ?? isOpen;

  const initial = useMemo(
    () => userInitial(user),
    [user?.call_name, user?.full_name_ar, user?.email]
  );

  return (
    <div className="flex items-center justify-between gap-2 p-3 border-b border-sidebar-border">
      {showBrand && (
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground text-sm font-semibold">
            {initial}
          </div>
          <div className="flex flex-col min-w-0 leading-tight">
            <div className="flex items-center gap-1.5 min-w-0">
              <span className="text-sm font-semibold text-sidebar-foreground truncate">
                ريحان
              </span>
              <span className="shrink-0 rounded-full bg-primary/10 px-1.5 py-0.5 text-xs font-medium leading-none text-primary">
                إطلاق تجريبي
              </span>
            </div>
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
            className="h-8 w-8 max-md:h-10 max-md:w-10 shrink-0 text-sidebar-foreground"
            onClick={toggle}
            aria-label={showBrand ? "طي الشريط الجانبي" : "فتح الشريط الجانبي"}
          >
            {showBrand ? (
              <PanelRightClose className="h-4 w-4" />
            ) : (
              <PanelRightOpen className="h-4 w-4" />
            )}
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom">
          <p>{showBrand ? "طي الشريط الجانبي" : "فتح الشريط الجانبي"}</p>
        </TooltipContent>
      </Tooltip>
    </div>
  );
}
