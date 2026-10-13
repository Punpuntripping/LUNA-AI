import { StyleSheet, View } from "react-native";

import { useTheme } from "@/theme/ThemeProvider";

import { AppText } from "./AppText";

export function ErrorBanner({ message, tone = "error" }: { message: string | null; tone?: "error" | "info" }) {
  const { colours, radius } = useTheme();
  if (!message) return null;
  const bg = tone === "error" ? colours.errorBg : colours.infoBg;
  const fg = tone === "error" ? colours.errorFg : colours.infoFg;
  return (
    <View
      accessibilityRole="alert"
      accessibilityLiveRegion="polite"
      style={[styles.box, { backgroundColor: bg, borderRadius: radius.default }]}
    >
      <AppText variant="sm" style={{ color: fg }}>
        {message}
      </AppText>
    </View>
  );
}

const styles = StyleSheet.create({
  box: { paddingHorizontal: 12, paddingVertical: 10 },
});
