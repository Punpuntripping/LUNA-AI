import { StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { useToast } from "@/stores/toast";
import { useTheme } from "@/theme/ThemeProvider";

import { AppText } from "./AppText";

/** Mount once (signed-in layout). Floats above the composer; never blocks touches. */
export function ToastHost() {
  const message = useToast((s) => s.message);
  const { colours, radius } = useTheme();
  const insets = useSafeAreaInsets();
  if (!message) return null;
  return (
    <View pointerEvents="none" style={[styles.wrap, { bottom: insets.bottom + 120 }]}>
      <View
        accessibilityRole="alert"
        accessibilityLiveRegion="polite"
        style={[styles.toast, { backgroundColor: colours.textPrimary, borderRadius: radius.full }]}
      >
        <AppText variant="sm" weight={600} style={{ color: colours.canvas }}>
          {message}
        </AppText>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { position: "absolute", start: 0, end: 0, alignItems: "center", direction: "rtl" },
  toast: { paddingHorizontal: 16, paddingVertical: 8 },
});
