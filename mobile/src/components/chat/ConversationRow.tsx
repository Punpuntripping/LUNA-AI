import { memo } from "react";
import { Pressable, StyleSheet, View } from "react-native";

import type { ConversationSummary } from "@contracts";

import { useIsConversationRunning } from "@/chat/stream-store";
import { AppText } from "@/components/AppText";
import { Icon } from "@/components/Icon";
import { formatRelativeTime } from "@/lib/format";
import { useTheme } from "@/theme/ThemeProvider";

import { conversationTitle } from "./ConversationMenu";

// L3 — one conversation in the list: title, message-match snippet (search),
// relative time (Latin digits), ★ when starred, «يعمل» while it answers here.

export interface ConversationRowProps {
  conversation: ConversationSummary;
  onPress: (c: ConversationSummary) => void;
  onLongPress: (c: ConversationSummary) => void;
}

export const ConversationRow = memo(function ConversationRow({ conversation, onPress, onLongPress }: ConversationRowProps) {
  const { colours, radius } = useTheme();
  const running = useIsConversationRunning(conversation.conversation_id);
  const snippet = conversation.match_type === "message" ? conversation.snippet?.trim() : null;
  const title = conversationTitle(conversation);

  return (
    <Pressable
      testID="conversation-row"
      accessibilityRole="button"
      accessibilityLabel={title}
      accessibilityHint="اضغط مطولاً للخيارات"
      onPress={() => onPress(conversation)}
      onLongPress={() => onLongPress(conversation)}
      delayLongPress={350}
      style={({ pressed }) => [styles.row, { borderRadius: radius.xl, backgroundColor: pressed ? colours.surface2 : "transparent" }]}
    >
      <View style={styles.main}>
        <View style={styles.titleLine}>
          {conversation.is_starred ? <Icon name="star" size={14} colour={colours.primary} /> : null}
          <AppText variant="base" weight={600} numberOfLines={1} style={styles.title}>
            {title}
          </AppText>
          {conversation.is_demo ? (
            <View style={[styles.badge, { backgroundColor: colours.pillBg, borderRadius: radius.full }]}>
              <AppText variant="xs" style={{ color: colours.pillFg }}>
                تجريبية
              </AppText>
            </View>
          ) : null}
        </View>
        {snippet ? (
          <AppText variant="sm" tone="muted" numberOfLines={2}>
            {snippet}
          </AppText>
        ) : null}
      </View>
      <View style={styles.meta}>
        {running ? (
          <View style={styles.running} accessibilityLabel="يعمل">
            <View style={[styles.dot, { backgroundColor: colours.primary }]} />
            <AppText variant="xs" tone="link">
              يعمل
            </AppText>
          </View>
        ) : (
          <AppText variant="xs" tone="subtle">
            {formatRelativeTime(conversation.updated_at)}
          </AppText>
        )}
      </View>
    </Pressable>
  );
});

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "flex-start", gap: 12, paddingHorizontal: 12, paddingVertical: 12, marginHorizontal: 8 },
  main: { flex: 1, gap: 2 },
  titleLine: { flexDirection: "row", alignItems: "center", gap: 6 },
  title: { flexShrink: 1 },
  badge: { paddingHorizontal: 8 },
  meta: { paddingTop: 4 },
  running: { flexDirection: "row", alignItems: "center", gap: 4 },
  dot: { width: 7, height: 7, borderRadius: 4 },
});
