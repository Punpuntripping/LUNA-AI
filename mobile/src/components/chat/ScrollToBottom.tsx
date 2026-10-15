import { Pressable, StyleSheet } from "react-native";

import { Icon } from "@/components/Icon";
import { useTheme } from "@/theme/ThemeProvider";

/** «↓» pill over the list when the user scrolled up from the newest message. */
export function ScrollToBottom({ visible, onPress }: { visible: boolean; onPress: () => void }) {
  const { colours, shadows } = useTheme();
  if (!visible) return null;
  return (
    <Pressable
      testID="chat-scroll-bottom"
      accessibilityRole="button"
      accessibilityLabel="الانتقال إلى آخر الرسائل"
      onPress={onPress}
      style={({ pressed }) => [
        styles.pill,
        {
          backgroundColor: colours.surface1,
          borderColor: colours.border,
          boxShadow: shadows.shadowMd,
          opacity: pressed ? 0.8 : 1,
        },
      ]}
    >
      <Icon name="arrowDown" size={18} colour={colours.textPrimary} />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  pill: {
    position: "absolute",
    bottom: 12,
    alignSelf: "center",
    width: 40,
    height: 40,
    borderRadius: 20,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center",
  },
});
