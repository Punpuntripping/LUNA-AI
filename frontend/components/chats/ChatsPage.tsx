"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ChevronDown, Loader2, MessageSquareOff, Plus } from "lucide-react";
import { cn } from "@/lib/utils";
import { useSidebarStore } from "@/stores/sidebar-store";
import { useSearchQuery } from "@/hooks/use-search";
import {
  useConversationsIndex,
  useSearchConversations,
} from "@/hooks/use-conversations";
import { isDemoConversation } from "@/hooks/use-demo-conversation";
import { usePreferencesStore } from "@/stores/preferences-store";
import { ConversationItem } from "@/components/sidebar/ConversationItem";
import { ConversationSearch } from "@/components/sidebar/ConversationSearch";
import { SearchEmptyState } from "@/components/search/SearchEmptyState";
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

  const [filter, setFilter] = useState<ChatsFilter>("all");

  // Raw value + 250 ms debounce, from the ONE hook every live search box in the
  // app now shares (bm25 plan §9). The debounce is the same 250 ms this page
  // shipped with — `SEARCH_DEBOUNCE_MS` was lifted FROM here.
  //
  // `minLength: 1` keeps this page's ORIGINAL floor. The hook defaults to 3 for
  // the BM25 library surfaces, where `search_service.normalize_query` 400s below
  // it — but `/chats` is not one: it hits `GET /api/v1/conversations?q=`
  // (trigram over titles + message content), which has no minimum. Taking the
  // default here would have deleted 1–2 character search from a shipped feature.
  const { value, setValue, query, isSearching } = useSearchQuery({ minLength: 1 });
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
    isFetching,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = active;

  // D8 «إخفاء» — a per-user preference flag, so the filter is client-side.
  const demoHidden = usePreferencesStore((s) => s.demoConversationHidden);

  const conversations: ConversationSummary[] = useMemo(() => {
    const rows = data?.pages.flatMap((page) => page.conversations) ?? [];
    // §4.1: the demo is furniture, not the user's content — it is excluded
    // from search and from «المميّزة» (it cannot be starred at all). The
    // backend already omits it from both, so this is a second lock on the
    // same door, and the one that also honours «إخفاء».
    const dropDemo = demoHidden || isSearching || starred;
    return dropDemo ? rows.filter((row) => !isDemoConversation(row)) : rows;
  }, [data, demoHidden, isSearching, starred]);

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
          <ConversationSearch
            value={value}
            onChange={setValue}
            // An infinite-scroll page fetch is not "the search is working" —
            // it has its own spinner at the sentinel, and turning the magnifier
            // into a spinner for it would be a second answer to a question
            // nobody asked.
            isPending={isSearching && isFetching && !isFetchingNextPage}
          />
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
          ) : conversations.length === 0 && isSearching ? (
            // The zero-result copy this page shipped with was, word for word,
            // what `SearchEmptyState` renders — so it delegates rather than
            // keeping a second copy that could drift.
            <SearchEmptyState />
          ) : conversations.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
              <MessageSquareOff className="h-10 w-10 text-muted-foreground/40" />
              <p className="text-sm font-medium text-muted-foreground">
                {starred ? "لا توجد محادثات مميّزة" : "لا توجد محادثات بعد"}
              </p>
              <p className="text-xs text-muted-foreground/70">
                {starred
                  ? "ميّز محادثة بنجمة لتظهر هنا"
                  : "ابدأ محادثة جديدة للتحدث مع ريحان"}
              </p>
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
