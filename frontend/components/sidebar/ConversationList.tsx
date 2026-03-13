"use client";

import { useMemo } from "react";
import { MessageSquareOff } from "lucide-react";
import { useConversations } from "@/hooks/use-conversations";
import { getDateGroupAr } from "@/lib/utils";
import { ConversationItem } from "@/components/sidebar/ConversationItem";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { ConversationSummary } from "@/types";

// Group conversations by Arabic date labels
function groupByDate(conversations: ConversationSummary[]): Map<string, ConversationSummary[]> {
  const groups = new Map<string, ConversationSummary[]>();
  const order = ["اليوم", "أمس", "هذا الأسبوع", "هذا الشهر", "أقدم"];

  // Initialize all groups to maintain order
  for (const label of order) {
    groups.set(label, []);
  }

  for (const conv of conversations) {
    const group = getDateGroupAr(conv.updated_at);
    const list = groups.get(group);
    if (list) {
      list.push(conv);
    } else {
      groups.set(group, [conv]);
    }
  }

  // Remove empty groups
  Array.from(groups.entries()).forEach(([key, value]) => {
    if (value.length === 0) {
      groups.delete(key);
    }
  });

  return groups;
}

function ConversationSkeleton() {
  return (
    <div className="space-y-2 p-2">
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="flex items-center gap-2 rounded-md px-2 py-2 animate-pulse">
          <div className="h-4 w-4 rounded bg-muted" />
          <div className="flex-1 space-y-1.5">
            <div className="h-3.5 w-3/4 rounded bg-muted" />
            <div className="h-2.5 w-1/3 rounded bg-muted" />
          </div>
        </div>
      ))}
    </div>
  );
}

export function ConversationList() {
  const { data, isLoading, isError } = useConversations(null);

  const grouped = useMemo(() => {
    if (!data?.conversations) return new Map<string, ConversationSummary[]>();
    return groupByDate(data.conversations);
  }, [data?.conversations]);

  if (isLoading) {
    return <ConversationSkeleton />;
  }

  if (isError) {
    return (
      <div className="flex flex-col items-center justify-center py-8 px-4 text-center">
        <p className="text-sm text-destructive">
          حدث خطأ في تحميل المحادثات
        </p>
      </div>
    );
  }

  if (!data?.conversations || data.conversations.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 px-4 text-center gap-3">
        <MessageSquareOff className="h-10 w-10 text-muted-foreground/50" />
        <div>
          <p className="text-sm font-medium text-muted-foreground">
            لا توجد محادثات بعد
          </p>
          <p className="text-xs text-muted-foreground/70 mt-1">
            ابدأ محادثة جديدة للتحدث مع لونا
          </p>
        </div>
      </div>
    );
  }

  return (
    <ScrollArea className="flex-1">
      <div className="p-2 space-y-3">
        {Array.from(grouped.entries()).map(([groupLabel, conversations]) => (
          <div key={groupLabel}>
            <p className="px-2 py-1 text-xs font-medium text-muted-foreground">
              {groupLabel}
            </p>
            <div className="space-y-2">
              {conversations.map((conv) => (
                <ConversationItem
                  key={conv.conversation_id}
                  conversation={conv}
                />
              ))}
            </div>
          </div>
        ))}
      </div>
    </ScrollArea>
  );
}
