import { memo } from "react";
import { Pressable, StyleSheet, View } from "react-native";

import { AppText } from "@/components/AppText";
import { Icon } from "@/components/Icon";
import { formatRelativeTime } from "@/lib/format";
import { useTheme } from "@/theme/ThemeProvider";

// M5 — an assistant placeholder whose run is gone (`isDeadPlaceholder`: the
// server says nothing runs, or the recovery window passed). Replaces the
// perpetual «ريحان يفكّر»; retry = regenerate the preceding question (web
// FailedResponseBubble.tsx).

export const FailedBubble = memo(function FailedBubble({
  createdAt,
  onRetry,
}: {
  createdAt: string;
  onRetry?: () => void;
}) {
  const { colours, radius } = useTheme();
  return (
    <View
      testID="chat-failed-response"
      style={[styles.box, { borderColor: colours.destructive, backgroundColor: colours.surface1, borderRadius: radius["2xl"] }]}
    >
      <View style={styles.row}>
        <Icon name="error" size={18} colour={colours.destructive} />
        <AppText variant="sm" tone="destructive" style={styles.flex}>
          تعذّر إكمال الرد. قد يكون الاتصال قد انقطع أثناء المعالجة.
        </AppText>
      </View>
      {onRetry ? <RetryButton onPress={onRetry} /> : null}
      <AppText variant="xs" tone="muted">
        {formatRelativeTime(createdAt)}
      </AppText>
    </View>
  );
});

export function RetryButton({ onPress, testID = "chat-retry" }: { onPress: () => void; testID?: string }) {
  const { colours, radius } = useTheme();
  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      accessibilityLabel="إعادة المحاولة"
      onPress={onPress}
      style={({ pressed }) => [
        styles.retry,
        { borderColor: colours.destructive, borderRadius: radius.default, opacity: pressed ? 0.6 : 1 },
      ]}
    >
      <Icon name="regenerate" size={14} colour={colours.destructive} />
      <AppText variant="xs" weight={600} tone="destructive">
        إعادة المحاولة
      </AppText>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  box: { alignSelf: "flex-start", maxWidth: "92%", gap: 10, borderWidth: 2, paddingHorizontal: 14, paddingVertical: 12 },
  row: { flexDirection: "row", alignItems: "flex-start", gap: 8 },
  flex: { flexShrink: 1 },
  retry: {
    alignSelf: "flex-start",
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    borderWidth: 1,
    paddingHorizontal: 10,
    minHeight: 32,
  },
});
