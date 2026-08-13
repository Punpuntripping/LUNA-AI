"use client";

import { MessageSquareOff, ChevronLeft } from "lucide-react";
import { useRouter } from "next/navigation";
import { useConversations } from "@/hooks/use-conversations";
import { isDemoConversation } from "@/hooks/use-demo-conversation";
import { usePreferencesStore } from "@/stores/preferences-store";
import { ConversationItem } from "@/components/sidebar/ConversationItem";

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
  // D8: «إخفاء» is a per-user preference, never a delete — the demo row is one
  // shared conversation, so hiding it can only ever be a client-side filter.
  // Defaults to false and only flips on a successful hydrate.
  const demoHidden = usePreferencesStore((s) => s.demoConversationHidden);

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

  const visible = (data?.conversations ?? []).filter(
    (conv) => !(demoHidden && isDemoConversation(conv)),
  );
  // Server already orders starred-first then most-recent. The demo pins ABOVE
  // all of it (§4.1) — a stable partition, so the server's ordering survives
  // inside each group and a starred conversation still leads the real list.
  const allConversations = [
    ...visible.filter(isDemoConversation),
    ...visible.filter((conv) => !isDemoConversation(conv)),
  ];
  // Cap the sidebar AFTER pinning, or the demo could fall off the end of a
  // busy account's list.
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
      {/* Native scroll, NOT Radix ScrollArea: its viewport wraps content in a
          `display:table; min-width:100%` div, so nowrap titles expand it past
          the sidebar and clip instead of truncating with an ellipsis. */}
      <div className="flex-1 min-h-0 overflow-y-auto scrollbar-thin">
        <div className="px-2 pb-2 space-y-0.5">
          {conversations.map((conv) => (
            <ConversationItem key={conv.conversation_id} conversation={conv} />
          ))}

          {/* Always present — the only entry point to the full /chats page. */}
          <button
            type="button"
            onClick={() => router.push("/chats")}
            className="group flex w-full items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-accent/50 hover:text-foreground"
          >
            <span>عرض جميع المحادثات</span>
            <ChevronLeft className="h-3.5 w-3.5 shrink-0 transition-transform group-hover:-translate-x-0.5" />
          </button>
        </div>
      </div>
    </div>
  );
}

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-4 pt-3 pb-2 shrink-0">
      <p className="text-xs font-medium uppercase tracking-[0.2em] text-muted-foreground/60">
        {children}
      </p>
    </div>
  );
}
