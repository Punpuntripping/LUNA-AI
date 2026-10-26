import type { Announcement } from "@contracts";
import { useIsFocused } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, FlatList, RefreshControl, StyleSheet, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { errorMessage } from "@/api/errors";
import { unreadIds } from "@/announcements/logic";
import { useAnnouncements, useMarkAnnouncementsRead } from "@/announcements/use-announcements";
import { AnnouncementCard } from "@/components/announcements/AnnouncementContent";
import { AppText } from "@/components/AppText";
import { Button } from "@/components/Button";
import { ErrorBanner } from "@/components/ErrorBanner";
import { useTheme } from "@/theme/ThemeProvider";

// «رسائل ريحان» (plan D4, web /messages): every message the server shows this
// account, newest first. Unread ones are marked read once they are on screen
// (the bell drops at once); they keep their dot for the rest of this visit.
// The popup never shows on this screen (AnnouncementPopupHost).

const LOAD_ERROR = "تعذّر تحميل الرسائل. حاول مرة أخرى.";
const NO_IDS: readonly string[] = [];

const keyExtractor = (a: Announcement) => a.announcement_id;

export default function MessagesScreen() {
  const { colours } = useTheme();
  const query = useAnnouncements();
  const markRead = useMarkAnnouncementsRead();
  const focused = useIsFocused();
  const [refreshing, setRefreshing] = useState(false);

  const data = query.data;
  const unread = data ? unreadIds(data.items) : NO_IDS;

  // Ids that reached this screen unread — they keep the dot after the cache
  // flips them read (state adjusted during render, not in an effect).
  const [fresh, setFresh] = useState<ReadonlySet<string>>(() => new Set());
  const unseen = unread.filter((id) => !fresh.has(id));
  if (unseen.length > 0) setFresh(new Set([...fresh, ...unseen]));

  const unreadKey = unread.join(",");
  useEffect(() => {
    if (!focused || !unreadKey) return;
    markRead(unreadKey.split(","));
  }, [focused, unreadKey, markRead]);

  const renderItem = useCallback(
    ({ item }: { item: Announcement }) => (
      <AnnouncementCard announcement={item} unread={!item.read || fresh.has(item.announcement_id)} />
    ),
    [fresh],
  );

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await query.refetch();
    } finally {
      setRefreshing(false);
    }
  }, [query]);

  const hardError = query.isError && !data ? errorMessage(query.error, LOAD_ERROR) : null;
  const staleError = query.isError && !!data;

  return (
    <SafeAreaView style={[styles.root, { backgroundColor: colours.canvas }]} edges={["bottom"]}>
      {query.isPending ? (
        <View style={styles.centre}>
          <ActivityIndicator color={colours.primary} />
        </View>
      ) : hardError ? (
        <View style={styles.centre}>
          <ErrorBanner message={hardError} />
          <Button label="إعادة المحاولة" variant="secondary" onPress={() => void query.refetch()} testID="messages-retry" />
        </View>
      ) : (
        <FlatList
          data={data?.items ?? []}
          renderItem={renderItem}
          keyExtractor={keyExtractor}
          extraData={fresh}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={() => void onRefresh()} tintColor={colours.primary} colors={[colours.primary]} />
          }
          ItemSeparatorComponent={Separator}
          ListHeaderComponent={
            staleError ? (
              <View style={styles.banner}>
                <ErrorBanner tone="info" message="تعذّر التحديث — تُعرض آخر نسخة محفوظة" />
              </View>
            ) : null
          }
          ListEmptyComponent={
            <AppText variant="base" tone="muted" align="center" style={styles.empty} testID="messages-empty">
              لا توجد رسائل بعد
            </AppText>
          }
          contentContainerStyle={styles.list}
          testID="messages-list"
        />
      )}
    </SafeAreaView>
  );
}

function Separator() {
  return <View style={styles.separator} />;
}

const styles = StyleSheet.create({
  root: { flex: 1, direction: "rtl" },
  centre: { flex: 1, justifyContent: "center", padding: 24, gap: 12 },
  list: { padding: 16 },
  banner: { paddingBottom: 12 },
  separator: { height: 12 },
  empty: { paddingHorizontal: 24, paddingTop: 48 },
});
