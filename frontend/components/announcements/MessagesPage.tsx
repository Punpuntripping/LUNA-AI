"use client";

import { useEffect, useState } from "react";
import { Bell } from "lucide-react";
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
import { cn } from "@/lib/utils";

function MessagesSkeleton() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 3 }).map((_, i) => (
        <div key={i} className="h-32 rounded-xl bg-muted/40 animate-pulse" />
      ))}
    </div>
  );
}

/**
 * /messages — every «رسائل ريحان» item the user can see, newest first. Unread
 * ones are marked read once the list is on screen; their highlight is kept for
 * this visit (snapshotted before the optimistic flip) so the reader can still
 * tell what is new.
 */
export function MessagesPage() {
  const { data, isLoading, isError, refetch, isFetching } = useAnnouncements();
  const markRead = useMarkAnnouncementsRead();
  const [freshIds, setFreshIds] = useState<ReadonlySet<string> | null>(null);

  useEffect(() => {
    if (!data || freshIds !== null) return;
    const unread = data.items.filter((i) => !i.read).map((i) => i.announcement_id);
    setFreshIds(new Set(unread));
    if (unread.length > 0) markRead.mutate(unread);
  }, [data, freshIds, markRead]);

  return (
    <div className="flex h-full flex-col overflow-hidden" dir="rtl">
      <div className="mx-auto flex h-full w-full max-w-xl flex-col px-4 py-8 sm:px-6">
        <h1 className="text-2xl font-semibold text-foreground">رسائل ريحان</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          آخر ما طوّرناه في ريحان، وما يهمّك معرفته عن اشتراكك.
        </p>

        <div className="mt-6 flex-1 overflow-y-auto pb-6">
          {isLoading ? (
            <MessagesSkeleton />
          ) : isError ? (
            <div className="rounded-xl border border-border bg-card p-6 text-center">
              <p className="text-sm text-muted-foreground">
                تعذّر تحميل الرسائل، حاول مجدداً.
              </p>
              <Button
                variant="outline"
                size="sm"
                className="mt-4"
                onClick={() => void refetch()}
                disabled={isFetching}
              >
                إعادة المحاولة
              </Button>
            </div>
          ) : !data || data.items.length === 0 ? (
            <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed border-border p-10 text-center">
              <Bell className="h-6 w-6 text-muted-foreground" aria-hidden />
              <p className="text-sm text-muted-foreground">لا توجد رسائل بعد</p>
            </div>
          ) : (
            <ul className="space-y-3">
              {data.items.map((item) => {
                const fresh = freshIds?.has(item.announcement_id) ?? !item.read;
                return (
                  <li
                    key={item.announcement_id}
                    className={cn(
                      "rounded-xl border bg-card p-5",
                      fresh ? "border-primary/40 bg-primary/5" : "border-border",
                    )}
                    data-testid="announcement-item"
                  >
                    <div className="flex items-start gap-2">
                      {fresh && (
                        <span
                          className="mt-2 h-2 w-2 shrink-0 rounded-full bg-primary"
                          aria-label="جديدة"
                        />
                      )}
                      <div className="min-w-0 flex-1">
                        <h2 className="text-base font-bold text-foreground">
                          {item.title}
                        </h2>
                        <p className="mt-0.5 text-xs text-muted-foreground">
                          {formatAnnouncementDate(item.published_at)}
                        </p>
                      </div>
                    </div>
                    <div className="mt-3">
                      <AnnouncementMarkdown item={item} />
                    </div>
                    <AnnouncementLink
                      item={item}
                      variant="outline"
                      size="sm"
                      className="mt-4"
                    />
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
