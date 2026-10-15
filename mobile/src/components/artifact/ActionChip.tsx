import { ActivityIndicator, Pressable, StyleSheet } from "react-native";

import { AppText } from "@/components/AppText";
import { useTheme } from "@/theme/ThemeProvider";

export interface ActionChipProps {
  label: string;
  onPress: () => void;
  /** `filled` = the one emphasised action (keeps the reader inside ريحان). */
  variant?: "filled" | "outline" | "ghost";
  disabled?: boolean;
  loading?: boolean;
  testID?: string;
}

/** Compact action for cards and action rows (44pt touch height). Labels
 *  wrap rather than truncate — Arabic exit labels run long. */
export function ActionChip({ label, onPress, variant = "outline", disabled = false, loading = false, testID }: ActionChipProps) {
  const { colours, radius } = useTheme();
  const inactive = disabled || loading;
  const bg = variant === "filled" ? colours.primary : "transparent";
  const fg = variant === "filled" ? colours.primaryFg : colours.primary;
  const border = variant === "outline" ? colours.borderStrong : "transparent";

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
        { backgroundColor: bg, borderColor: border, borderRadius: radius.default, opacity: inactive ? 0.5 : pressed ? 0.75 : 1 },
      ]}
    >
      {loading ? <ActivityIndicator size="small" color={fg} /> : null}
      <AppText variant="sm" weight={600} style={{ color: fg }}>
        {label}
      </AppText>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  base: {
    minHeight: 44,
    paddingHorizontal: 12,
    borderWidth: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
  },
});
