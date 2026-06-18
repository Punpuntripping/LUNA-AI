"use client";

import { MessageSquareOff, ChevronLeft } from "lucide-react";
import { useRouter } from "next/navigation";
import { useConversations } from "@/hooks/use-conversations";
import { ConversationItem } from "@/components/sidebar/ConversationItem";
import { ScrollArea } from "@/components/ui/scroll-area";

/** Cap the sidebar at the top recent conversations (starred float in first). */
const SIDEBAR_LIMIT = 15;

function ConversationSkeleton() {
  return (
    <div className="space-y-1.5 px-3 py-2">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="h-7 rounded-md bg-muted/40 animate-pulse" />
      ))}
    </div>
  );
}

export function ConversationList() {
  const router = useRouter();
  const { data, isLoading, isError } = useConversations(null);

  if (isLoading) {
    return (
      <div className="flex flex-col flex-1 min-h-0">
        <SectionHeader>المحادثات الأخيرة</SectionHeader>
        <ConversationSkeleton />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="flex flex-col flex-1 min-h-0">
        <SectionHeader>المحادثات الأخيرة</SectionHeader>
        <div className="flex flex-col items-center justify-center py-8 px-4 text-center">
          <p className="text-sm text-destructive">حدث خطأ في تحميل المحادثات</p>
        </div>
      </div>
    );
  }

  const allConversations = data?.conversations ?? [];
  // Server already orders starred-first then most-recent; just cap the sidebar.
  const conversations = allConversations.slice(0, SIDEBAR_LIMIT);

  if (allConversations.length === 0) {
    return (
      <div className="flex flex-col flex-1 min-h-0">
        <SectionHeader>المحادثات الأخيرة</SectionHeader>
        <div className="flex flex-col items-center justify-center py-12 px-4 text-center gap-3">
          <MessageSquareOff className="h-9 w-9 text-muted-foreground/40" />
          <div>
            <p className="text-sm font-medium text-muted-foreground">
              لا توجد محادثات بعد
            </p>
            <p className="text-xs text-muted-foreground/70 mt-1">
              ابدأ محادثة جديدة للتحدث مع ريحان
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <SectionHeader>المحادثات الأخيرة</SectionHeader>
      <ScrollArea className="flex-1 min-h-0">
        <div className="px-2 pb-2 space-y-0.5">
          {conversations.map((conv) => (
            <ConversationItem key={conv.conversation_id} conversation={conv} />
          ))}

          {/* Always present — the only entry point to the full /chats page. */}
          <button
            type="button"
            onClick={() => router.push("/chats")}
            className="group flex w-full items-center justify-between gap-2 rounded-md px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent/40 hover:text-foreground"
          >
            <span>عرض جميع المحادثات</span>
            <ChevronLeft className="h-3.5 w-3.5 shrink-0 transition-transform group-hover:-translate-x-0.5" />
          </button>
        </div>
      </ScrollArea>
    </div>
  );
}

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-4 pt-3 pb-2 shrink-0">
      <p className="text-[11px] font-medium uppercase tracking-[0.2em] text-muted-foreground/60">
        {children}
      </p>
    </div>
  );
}
