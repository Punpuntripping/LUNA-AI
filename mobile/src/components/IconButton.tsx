import { Pressable, StyleSheet, type ColorValue } from "react-native";

import { useTheme } from "@/theme/ThemeProvider";

import { Icon, type IconName } from "./Icon";

export interface IconButtonProps {
  icon: IconName;
  /** Arabic label for screen readers (required — the button has no text). */
  label: string;
  onPress: () => void;
  colour?: ColorValue;
  size?: number;
  disabled?: boolean;
  testID?: string;
}

/** 44×44 touch target around one glyph. */
export function IconButton({ icon, label, onPress, colour, size = 22, disabled = false, testID }: IconButtonProps) {
  const { colours } = useTheme();
  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ disabled }}
      disabled={disabled}
      hitSlop={4}
      onPress={onPress}
      style={({ pressed }) => [styles.button, { opacity: disabled ? 0.4 : pressed ? 0.6 : 1 }]}
    >
      <Icon name={icon} size={size} colour={colour ?? colours.textPrimary} />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  button: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
});
