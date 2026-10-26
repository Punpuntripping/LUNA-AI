"use client";

import Link from "next/link";
import { Bell } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useAnnouncements } from "@/hooks/use-announcements";
import { useIsMobile } from "@/hooks/use-media-query";
import { useSidebarStore } from "@/stores/sidebar-store";
import { cn } from "@/lib/utils";

/**
 * «رسائل ريحان» bell — sits beside the settings gear in the sidebar footer.
 * The badge counts messages that slipped past their popup (theme primary,
 * never a warning colour). Opens /messages — closing the phone drawer first,
 * like every other sidebar row, so it does not keep covering the page.
 */
export function AnnouncementsBell() {
  const { data } = useAnnouncements();
  const isMobile = useIsMobile();
  const setSidebarOpen = useSidebarStore((s) => s.setOpen);
  const unread = data?.unread_count ?? 0;
  const label = unread > 0 ? `رسائل ريحان — ${unread} غير مقروءة` : "رسائل ريحان";

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Link
          href="/messages"
          onClick={() => isMobile && setSidebarOpen(false)}
          aria-label={label}
          data-testid="sidebar-announcements-bell"
          className={cn(
            buttonVariants({ variant: "ghost", size: "icon" }),
            "relative h-7 w-7 max-md:h-9 max-md:w-9 shrink-0 text-muted-foreground",
          )}
        >
          <Bell className="h-3.5 w-3.5" />
          {unread > 0 && (
            <span
              className="absolute -top-0.5 -end-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-bold leading-none text-primary-foreground"
              aria-hidden
            >
              {unread > 9 ? "9+" : unread}
            </span>
          )}
        </Link>
      </TooltipTrigger>
      <TooltipContent side="top">
        <p>رسائل ريحان</p>
      </TooltipContent>
    </Tooltip>
  );
}
