"use client";

import { useEffect, useMemo, useState } from "react";
import { usePathname } from "next/navigation";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import {
  AnnouncementLink,
  AnnouncementMarkdown,
  formatAnnouncementDate,
} from "@/components/announcements/AnnouncementBody";
import {
  useAnnouncements,
  useMarkAnnouncementsRead,
} from "@/hooks/use-announcements";
import { unreadOldestFirst, type Announcement } from "@/lib/contracts";

/** How often a waiting message re-checks whether the screen is free. The first
 *  check is one interval in, so the self-gating first-run dialogs mounted
 *  beside this one (رمز التفعيل, المهنة, عرض الشهر الأول) claim the screen first. */
const RETRY_MS = 1500;

/** Any open Radix dialog/sheet — incl. the phone sidebar drawer and the mobile
 *  workspace overlay. A message never lands on top of something the user opened. */
function aDialogIsOpen(): boolean {
  return document.querySelector('[role="dialog"]') !== null;
}

/**
 * «رسائل ريحان» popup — every unread message pops up once, oldest first, one at
 * a time (.claude/plans/announcements.md D2). Closing it any way, or pressing its
 * link, marks it read; the next unread one follows. Not on /messages itself,
 * which lists them all. Anything that slips by stays counted on the sidebar bell.
 */
export function AnnouncementPopup() {
  const pathname = usePathname();
  const { data } = useAnnouncements();
  const markRead = useMarkAnnouncementsRead();
  const [current, setCurrent] = useState<Announcement | null>(null);

  const onMessagesPage = pathname?.startsWith("/messages") ?? false;
  const next = useMemo(
    () => (data ? (unreadOldestFirst(data.items)[0] ?? null) : null),
    [data],
  );

  useEffect(() => {
    if (current || !next || onMessagesPage) return;
    const timer = window.setInterval(() => {
      if (aDialogIsOpen()) return;
      window.clearInterval(timer);
      setCurrent(next);
    }, RETRY_MS);
    return () => window.clearInterval(timer);
  }, [current, next, onMessagesPage]);

  if (!current) return null;

  const close = () => {
    markRead.mutate([current.announcement_id]);
    setCurrent(null);
  };

  return (
    <Dialog open onOpenChange={(open) => !open && close()}>
      <DialogContent
        className="max-w-lg"
        presentation="mobileSheet"
        dir="rtl"
        lang="ar"
        data-testid="announcement-popup"
      >
        <DialogHeader>
          <DialogTitle>{current.title}</DialogTitle>
          <DialogDescription>
            {formatAnnouncementDate(current.published_at)}
          </DialogDescription>
        </DialogHeader>

        {/* No inner scroll box: DialogContent already scrolls as a whole, and a
            nested one drew a scrollbar on sub-pixel overflow even for short text. */}
        <AnnouncementMarkdown item={current} />

        <div className="flex flex-col gap-2 pt-1">
          <AnnouncementLink item={current} onNavigate={close} className="w-full" />
          <Button
            variant={current.link_url ? "ghost" : "default"}
            className="w-full"
            onClick={close}
          >
            حسناً
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
