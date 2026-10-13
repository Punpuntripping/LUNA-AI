import { Linking, Pressable, StyleSheet, View } from "react-native";

import { useUpdateGate } from "@/stores/update-gate";
import { useTheme } from "@/theme/ThemeProvider";

import { AppText } from "./AppText";
import { LinkText } from "./LinkText";

/** Dismissible «تحديث متوفر» banner — installed version < latest_version. */
export function UpdateBanner() {
  const { colours, radius } = useTheme();
  const { available, bannerDismissed, storeUrl, dismissBanner } = useUpdateGate();
  if (!available || bannerDismissed) return null;

  return (
    <View style={[styles.box, { backgroundColor: colours.infoBg, borderRadius: radius.default }]}>
      <AppText variant="sm" style={[styles.text, { color: colours.infoFg }]}>
        يتوفر إصدار أحدث من ريحان
      </AppText>
      {storeUrl ? <LinkText label="تحديث" onPress={() => Linking.openURL(storeUrl).catch(() => undefined)} /> : null}
      <Pressable accessibilityRole="button" accessibilityLabel="إغلاق" hitSlop={12} onPress={dismissBanner}>
        <AppText variant="sm" weight={700} style={{ color: colours.infoFg }}>
          ✕
        </AppText>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  box: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 12, paddingVertical: 10 },
  text: { flex: 1 },
});
