import type { ReactNode } from "react";
import { Pressable, StyleSheet, View } from "react-native";

import { useTheme } from "@/theme/ThemeProvider";

import { AppText } from "./AppText";

export interface CheckboxProps {
  checked: boolean;
  onChange: (next: boolean) => void;
  /** Label content — may contain inline links (rendered by the caller). */
  children: ReactNode;
  accessibilityLabel: string;
  testID?: string;
}

export function Checkbox({ checked, onChange, children, accessibilityLabel, testID }: CheckboxProps) {
  const { colours, radius } = useTheme();
  return (
    <View style={styles.row}>
      <Pressable
        testID={testID}
        accessibilityRole="checkbox"
        accessibilityState={{ checked }}
        accessibilityLabel={accessibilityLabel}
        onPress={() => onChange(!checked)}
        style={styles.target}
      >
        <View
          style={[
            styles.box,
            {
              borderRadius: radius.sm,
              borderColor: checked ? colours.primary : colours.borderStrong,
              backgroundColor: checked ? colours.primary : colours.surface1,
            },
          ]}
        >
          {checked ? (
            <AppText variant="sm" weight={700} tone="onPrimary" style={styles.tick}>
              ✓
            </AppText>
          ) : null}
        </View>
      </Pressable>
      <View style={styles.label}>{children}</View>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: 2 },
  // 44pt tap target around the 22pt box.
  target: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
  box: { width: 22, height: 22, borderWidth: 1.5, alignItems: "center", justifyContent: "center" },
  tick: { lineHeight: 18 },
  label: { flex: 1 },
});
