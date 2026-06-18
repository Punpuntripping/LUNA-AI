"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ChevronDown, Loader2, MessageSquareOff, Plus, SearchX } from "lucide-react";
import { cn } from "@/lib/utils";
import { useSidebarStore } from "@/stores/sidebar-store";
import { useDebounce } from "@/hooks/use-debounce";
import {
  useConversationsIndex,
  useSearchConversations,
} from "@/hooks/use-conversations";
import { ConversationItem } from "@/components/sidebar/ConversationItem";
import { ConversationSearch } from "@/components/sidebar/ConversationSearch";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { ConversationSummary } from "@/types";

type ChatsFilter = "all" | "starred";

const FILTER_LABELS: Record<ChatsFilter, string> = {
  all: "الكل",
  starred: "المميّزة",
};

function ChatsSkeleton() {
  return (
    <div className="space-y-1.5">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="h-11 rounded-md bg-muted/40 animate-pulse" />
      ))}
    </div>
  );
}

export function ChatsPage() {
  const router = useRouter();
  const { setActiveTab, setSelectedConversation } = useSidebarStore();

  const [rawQuery, setRawQuery] = useState("");
  const [filter, setFilter] = useState<ChatsFilter>("all");

  // Debounce the search input so we don't fire a query on every keystroke.
  const query = useDebounce(rawQuery, 250).trim();
  const isSearching = query.length > 0;
  const starred = filter === "starred";

  // When a query is present → search (titles + message content). Otherwise the
  // plain offset-paginated index (optionally restricted to starred).
  const indexQuery = useConversationsIndex({ starred });
  const searchQuery = useSearchConversations(query, { starred });

  const active = isSearching ? searchQuery : indexQuery;
  const {
    data,
    isLoading,
    isError,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = active;

  const conversations: ConversationSummary[] = useMemo(
    () => data?.pages.flatMap((page) => page.conversations) ?? [],
    [data],
  );

  // Infinite scroll: load the next page when the bottom sentinel scrolls in.
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const node = sentinelRef.current;
    if (!node) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting && hasNextPage && !isFetchingNextPage) {
          void fetchNextPage();
        }
      },
      { rootMargin: "200px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage, conversations.length]);

  const handleNewConversation = () => {
    // Mirror the sidebar lazy-create flow: no row is persisted here. The empty
    // composer (/chat) creates the conversation only on the first send.
    setActiveTab("conversations");
    setSelectedConversation(null);
    router.push("/chat");
  };

  return (
    <div className="flex h-full flex-col overflow-hidden" dir="rtl">
      <div className="mx-auto flex h-full w-full max-w-xl flex-col px-4 py-8 sm:px-6">
        {/* Header */}
        <div className="mb-5 flex items-center justify-between gap-3">
          <h1 className="text-2xl font-semibold text-foreground">المحادثات</h1>

          <div className="flex items-center gap-2">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm" className="gap-1.5">
                  <span className="text-muted-foreground">تصفية:</span>
                  <span>{FILTER_LABELS[filter]}</span>
                  <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-36">
                <DropdownMenuItem
                  onClick={() => setFilter("all")}
                  className={cn(filter === "all" && "font-medium text-primary")}
                >
                  {FILTER_LABELS.all}
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={() => setFilter("starred")}
                  className={cn(
                    filter === "starred" && "font-medium text-primary",
                  )}
                >
                  {FILTER_LABELS.starred}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>

            <Button size="sm" onClick={handleNewConversation} className="gap-1.5">
              <Plus className="h-4 w-4" />
              محادثة جديدة
            </Button>
          </div>
        </div>

        {/* Search box */}
        <div className="mb-4 shrink-0">
          <ConversationSearch value={rawQuery} onChange={setRawQuery} />
        </div>

        {/* Results */}
        <div className="flex-1 min-h-0 overflow-y-auto">
          {isLoading ? (
            <ChatsSkeleton />
          ) : isError ? (
            <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
              <p className="text-sm text-destructive">
                حدث خطأ في تحميل المحادثات
              </p>
            </div>
          ) : conversations.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
              {isSearching ? (
                <>
                  <SearchX className="h-10 w-10 text-muted-foreground/40" />
                  <p className="text-sm font-medium text-muted-foreground">
                    لا توجد نتائج
                  </p>
                  <p className="text-xs text-muted-foreground/70">
                    جرّب كلمات بحث أخرى
                  </p>
                </>
              ) : (
                <>
                  <MessageSquareOff className="h-10 w-10 text-muted-foreground/40" />
                  <p className="text-sm font-medium text-muted-foreground">
                    {starred ? "لا توجد محادثات مميّزة" : "لا توجد محادثات بعد"}
                  </p>
                  <p className="text-xs text-muted-foreground/70">
                    {starred
                      ? "ميّز محادثة بنجمة لتظهر هنا"
                      : "ابدأ محادثة جديدة للتحدث مع ريحان"}
                  </p>
                </>
              )}
            </div>
          ) : (
            <div className="space-y-0.5">
              {conversations.map((conv) => (
                <ConversationItem
                  key={conv.conversation_id}
                  conversation={conv}
                  searchQuery={isSearching ? query : ""}
                  alwaysShowActions
                />
              ))}

              {/* Infinite-scroll sentinel + spinner */}
              <div ref={sentinelRef} className="h-px" />
              {isFetchingNextPage && (
                <div className="flex items-center justify-center py-4">
                  <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
