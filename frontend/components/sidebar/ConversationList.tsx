"use client";

import { useMemo, useState, useCallback } from "react";
import { MessageSquareOff, Plus, SearchX } from "lucide-react";
import { useRouter } from "next/navigation";
import { useConversations, useCreateConversation } from "@/hooks/use-conversations";
import { useDebounce } from "@/hooks/use-debounce";
import { getDateGroupAr } from "@/lib/utils";
import { ConversationItem } from "@/components/sidebar/ConversationItem";
import { ConversationSearch } from "@/components/sidebar/ConversationSearch";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { useSidebarStore } from "@/stores/sidebar-store";
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

// Filter conversations by title match (case-insensitive)
function filterConversations(
  conversations: ConversationSummary[],
  query: string
): ConversationSummary[] {
  const trimmed = query.trim().toLowerCase();
  if (!trimmed) return conversations;

  return conversations.filter((conv) => {
    const title = (conv.title_ar || "محادثة جديدة").toLowerCase();
    return title.includes(trimmed);
  });
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
  const router = useRouter();
  const { data, isLoading, isError } = useConversations(null);
  const createConversation = useCreateConversation();

  // Search state — lives locally, resets naturally when component unmounts (tab switch)
  const [searchInput, setSearchInput] = useState("");
  const debouncedQuery = useDebounce(searchInput, 300);

  const handleSearchChange = useCallback((value: string) => {
    setSearchInput(value);
  }, []);

  const handleNewConversation = () => {
    createConversation.mutate(
      { case_id: null },
      {
        onSuccess: (resp) => {
          useSidebarStore
            .getState()
            .setSelectedConversation(resp.conversation.conversation_id);
          router.push(`/chat/${resp.conversation.conversation_id}`);
        },
      }
    );
  };

  // Filter conversations based on debounced search query, then group by date
  const filtered = useMemo(() => {
    if (!data?.conversations) return [];
    return filterConversations(data.conversations, debouncedQuery);
  }, [data?.conversations, debouncedQuery]);

  const grouped = useMemo(() => {
    return groupByDate(filtered);
  }, [filtered]);

  const hasConversations = data?.conversations && data.conversations.length > 0;
  const isSearchActive = debouncedQuery.trim().length > 0;
  const hasNoResults = isSearchActive && filtered.length === 0;

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

  if (!hasConversations) {
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
    <div className="flex flex-col flex-1 min-h-0">
      {/* New conversation button */}
      <div className="p-2 shrink-0">
        <Button
          variant="outline"
          className="w-full gap-1.5 text-xs"
          onClick={handleNewConversation}
          disabled={createConversation.isPending}
        >
          <Plus className="h-3.5 w-3.5" />
          محادثة جديدة
        </Button>
      </div>

      {/* Search input */}
      <div className="px-2 pb-1 shrink-0">
        <ConversationSearch
          value={searchInput}
          onChange={handleSearchChange}
        />
      </div>

      {/* Conversation list or no-results state */}
      <ScrollArea className="flex-1 min-h-0">
        {hasNoResults ? (
          <div className="flex flex-col items-center justify-center py-10 px-4 text-center gap-2">
            <SearchX className="h-8 w-8 text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">
              لا توجد نتائج لـ &laquo;{debouncedQuery.trim()}&raquo;
            </p>
          </div>
        ) : (
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
                      searchQuery={isSearchActive ? debouncedQuery.trim() : undefined}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </ScrollArea>
    </div>
  );
}
