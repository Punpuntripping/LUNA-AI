import { FlashList } from "@shopify/flash-list";
import { router } from "expo-router";
import { useCallback, useMemo, useState } from "react";
import { ActivityIndicator, Pressable, RefreshControl, StyleSheet, TextInput, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { AGENT_OUTPUT_DISCLAIMER_AR, type ConversationSummary } from "@contracts";

import { errorMessage } from "@/api/errors";
import { useConversationsIndex, useSearchConversations } from "@/chat/use-conversations";
import { AnnouncementsBell } from "@/components/announcements/AnnouncementsBell";
import { AppText } from "@/components/AppText";
import { Button } from "@/components/Button";
import { ConversationMenu } from "@/components/chat/ConversationMenu";
import { ConversationRow } from "@/components/chat/ConversationRow";
import { ErrorBanner } from "@/components/ErrorBanner";
import { Icon } from "@/components/Icon";
import { IconButton } from "@/components/IconButton";
import { StackHeader } from "@/components/StackHeader";
import { UpdateBanner } from "@/components/UpdateBanner";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { fontFamilyFor } from "@/theme/fonts";
import { useTheme } from "@/theme/ThemeProvider";
import { typeVariants } from "@/theme/typography";

// L1–L5 — home = the conversation list. Search (300 ms debounce → server
// search over titles + message text, snippets on message matches),
// «الكل / المميّزة», infinite scroll, pull-to-refresh, long-press menu
// (تمييز · إعادة تسمية · حذف; demo → «إخفاء»). Offline: the cached list stays
// with a banner.

const SEARCH_DEBOUNCE_MS = 300;

type Filter = "all" | "starred";

const keyExtractor = (c: ConversationSummary) => c.conversation_id;

export default function ConversationListScreen() {
  const { colours, radius } = useTheme();
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const q = useDebouncedValue(query, SEARCH_DEBOUNCE_MS).trim();
  const starred = filter === "starred";

  const index = useConversationsIndex({ starred });
  const search = useSearchConversations(q, { starred });
  const active = q ? search : index;

  const rows = useMemo(() => active.data?.pages.flatMap((p) => p.conversations) ?? [], [active.data]);
  const [menuFor, setMenuFor] = useState<ConversationSummary | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const open = useCallback((c: ConversationSummary) => {
    router.push({ pathname: "/c/[id]", params: { id: c.conversation_id } });
  }, []);
  const renderItem = useCallback(
    ({ item }: { item: ConversationSummary }) => <ConversationRow conversation={item} onPress={open} onLongPress={setMenuFor} />,
    [open],
  );

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await active.refetch();
    } finally {
      setRefreshing(false);
    }
  }, [active]);

  const onEndReached = useCallback(() => {
    if (active.hasNextPage && !active.isFetchingNextPage) void active.fetchNextPage();
  }, [active]);

  const loading = active.isPending;
  const hardError = active.isError && !active.data ? errorMessage(active.error) : null;
  const staleError = active.isError && !!active.data;

  return (
    <SafeAreaView style={[styles.root, { backgroundColor: colours.canvas }]} edges={["bottom"]}>
      <StackHeader
        title="المحادثات"
        actions={
          <>
            <AnnouncementsBell testID="home-messages" />
            <IconButton icon="settings" label="الإعدادات" onPress={() => router.push("/settings")} testID="home-settings" />
            <IconButton icon="newChat" label="محادثة جديدة" onPress={() => router.push("/c/new")} testID="home-new-chat" />
          </>
        }
      />
      <View style={styles.top}>
        <UpdateBanner />
        <View style={[styles.search, { backgroundColor: colours.surface2, borderRadius: radius.xl }]}>
          <Icon name="search" size={18} colour={colours.textMuted} />
          <TextInput
            testID="home-search"
            value={query}
            onChangeText={setQuery}
            placeholder="ابحث في المحادثات"
            placeholderTextColor={colours.textSubtle}
            returnKeyType="search"
            autoCorrect={false}
            accessibilityLabel="ابحث في المحادثات"
            style={[styles.searchInput, typeVariants.base, { fontFamily: fontFamilyFor[400], color: colours.textPrimary }]}
          />
          {query ? (
            <Pressable accessibilityRole="button" accessibilityLabel="مسح البحث" hitSlop={10} onPress={() => setQuery("")}>
              <Icon name="close" size={16} colour={colours.textMuted} />
            </Pressable>
          ) : null}
        </View>
        <View style={[styles.tabs, { backgroundColor: colours.surface2, borderRadius: radius.xl }]} accessibilityRole="tablist">
          {(
            [
              { key: "all", label: "الكل" },
              { key: "starred", label: "المميّزة" },
            ] as const
          ).map((t) => {
            const selected = filter === t.key;
            return (
              <Pressable
                key={t.key}
                testID={`home-filter-${t.key}`}
                accessibilityRole="tab"
                accessibilityState={{ selected }}
                onPress={() => setFilter(t.key)}
                style={[styles.tab, { borderRadius: radius.default, backgroundColor: selected ? colours.surface1 : "transparent" }]}
              >
                <AppText variant="sm" weight={selected ? 600 : 400} tone={selected ? "primary" : "muted"} align="center">
                  {t.label}
                </AppText>
              </Pressable>
            );
          })}
        </View>
        {staleError ? <ErrorBanner tone="info" message="تعذّر التحديث — تُعرض آخر نسخة محفوظة" /> : null}
      </View>

      {loading ? (
        <View style={styles.centre}>
          <ActivityIndicator color={colours.primary} />
        </View>
      ) : hardError ? (
        <View style={styles.centre}>
          <ErrorBanner message={hardError} />
          <Button label="إعادة المحاولة" variant="secondary" onPress={() => void active.refetch()} />
        </View>
      ) : (
        <FlashList
          data={rows}
          renderItem={renderItem}
          keyExtractor={keyExtractor}
          onEndReached={onEndReached}
          onEndReachedThreshold={0.5}
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode="on-drag"
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={() => void onRefresh()} tintColor={colours.primary} colors={[colours.primary]} />
          }
          ListEmptyComponent={<EmptyList searching={!!q} starred={starred} />}
          ListFooterComponent={active.isFetchingNextPage ? <ActivityIndicator style={styles.more} color={colours.textMuted} /> : null}
          contentContainerStyle={styles.listContent}
        />
      )}

      <ConversationMenu conversation={menuFor} onClose={() => setMenuFor(null)} testID="home-conversation-menu" />
    </SafeAreaView>
  );
}

function EmptyList({ searching, starred }: { searching: boolean; starred: boolean }) {
  const { colours, radius } = useTheme();
  if (searching) {
    return (
      <AppText variant="base" tone="muted" align="center" style={styles.emptyLine} testID="home-empty-search">
        لا توجد نتائج مطابقة
      </AppText>
    );
  }
  if (starred) {
    return (
      <AppText variant="base" tone="muted" align="center" style={styles.emptyLine} testID="home-empty-starred">
        لا توجد محادثات مميّزة بعد — اضغط مطولاً على محادثة لتمييزها
      </AppText>
    );
  }
  return (
    <View style={styles.empty} testID="home-empty">
      <AppText variant="h3" weight={700} align="center">
        ابدأ أول محادثة مع ريحان
      </AppText>
      <View style={[styles.disclaimer, { backgroundColor: colours.surface2, borderRadius: radius.xl }]}>
        <AppText variant="sm" tone="secondary">
          {AGENT_OUTPUT_DISCLAIMER_AR}
        </AppText>
      </View>
      <Button label="محادثة جديدة" onPress={() => router.push("/c/new")} testID="home-empty-new-chat" />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, direction: "rtl" },
  top: { paddingHorizontal: 16, paddingTop: 8, paddingBottom: 4, gap: 10 },
  search: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 12, minHeight: 44 },
  searchInput: { flex: 1, paddingVertical: 8, textAlign: "right", writingDirection: "rtl" },
  tabs: { flexDirection: "row", padding: 3, gap: 3 },
  tab: { flex: 1, paddingVertical: 6 },
  centre: { flex: 1, justifyContent: "center", padding: 24, gap: 12 },
  listContent: { paddingVertical: 8 },
  more: { paddingVertical: 16 },
  emptyLine: { paddingHorizontal: 24, paddingTop: 48 },
  empty: { paddingHorizontal: 24, paddingTop: 48, gap: 16 },
  disclaimer: { padding: 14 },
});
