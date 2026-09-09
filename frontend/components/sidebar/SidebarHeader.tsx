"use client";

import { useMemo } from "react";
import { PanelRightClose, PanelRightOpen } from "lucide-react";
import { BrandLockup } from "@/components/brand/BrandLockup";
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
          {/* The lockup replaces the «ريحان» / «القانونية» text pair outright.
              «القانونية» was a subtitle to a WORDMARK MADE OF TEXT; under an
              image wordmark that already carries its own leaf it just crowds a
              narrow rail, and the site header names the brand with the lockup
              alone. `min-w-0` keeps it shrinking with the rail rather than
              pushing the collapse button off the edge. */}
          <BrandLockup className="h-7 min-w-0" />
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
