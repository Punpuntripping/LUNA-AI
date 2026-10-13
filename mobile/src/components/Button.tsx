import { ActivityIndicator, Pressable, StyleSheet, View } from "react-native";

import { useTheme } from "@/theme/ThemeProvider";

import { AppText } from "./AppText";

export interface ButtonProps {
  label: string;
  onPress: () => void;
  variant?: "primary" | "secondary" | "ghost";
  loading?: boolean;
  disabled?: boolean;
  testID?: string;
}

export function Button({ label, onPress, variant = "primary", loading = false, disabled = false, testID }: ButtonProps) {
  const { colours, radius } = useTheme();
  const inactive = disabled || loading;
  const bg = variant === "primary" ? colours.primary : variant === "secondary" ? colours.surface2 : "transparent";
  const fg = variant === "primary" ? colours.primaryFg : variant === "secondary" ? colours.textPrimary : colours.primary;

  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ disabled: inactive, busy: loading }}
      disabled={inactive}
      onPress={onPress}
      style={({ pressed }) => [
        styles.base,
        {
          backgroundColor: bg,
          borderRadius: radius.default,
          borderColor: variant === "secondary" ? colours.border : "transparent",
          opacity: inactive ? 0.5 : pressed ? 0.85 : 1,
        },
      ]}
    >
      {/* While loading the label stays laid out but invisible (keeps the
          button's size) and the spinner sits alone on top of it. */}
      {/* stretch + centre: Android re-uses a stale measured width for a
          shrink-wrapped custom-font label after navigating back (QA F1,
          «تغيير كلمة المرور» clipped to «تغيير كلمة»). Full width avoids it. */}
      <AppText
        variant="base"
        weight={600}
        style={{ color: fg, opacity: loading ? 0 : 1, alignSelf: "stretch", textAlign: "center" }}
      >
        {label}
      </AppText>
      {loading ? (
        <View style={styles.spinner} pointerEvents="none">
          <ActivityIndicator size="small" color={fg} />
        </View>
      ) : null}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  base: {
    minHeight: 48,
    paddingHorizontal: 16,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center",
  },
  spinner: { position: "absolute", top: 0, bottom: 0, start: 0, end: 0, alignItems: "center", justifyContent: "center" },
});
