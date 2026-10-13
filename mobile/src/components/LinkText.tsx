import { Pressable, StyleSheet } from "react-native";

import { AppText, type AppTextProps } from "./AppText";

export interface LinkTextProps extends Omit<AppTextProps, "onPress" | "tone"> {
  label: string;
  onPress: () => void;
  disabled?: boolean;
}

/** A tappable text link (primary colour). The pressable itself is at least
 *  44pt tall — a real box, not just hitSlop, so it measures as 44 too. */
export function LinkText({ label, onPress, disabled = false, variant = "sm", weight = 600, ...rest }: LinkTextProps) {
  return (
    <Pressable
      accessibilityRole="link"
      accessibilityState={{ disabled }}
      onPress={onPress}
      disabled={disabled}
      hitSlop={8}
      style={styles.target}
    >
      <AppText variant={variant} weight={weight} tone={disabled ? "subtle" : "link"} {...rest}>
        {label}
      </AppText>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  target: { minHeight: 44, justifyContent: "center" },
});
