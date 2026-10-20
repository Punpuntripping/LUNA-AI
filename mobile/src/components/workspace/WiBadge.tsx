import { StyleSheet, View } from "react-native";

import { AppText } from "@/components/AppText";
import { useTheme } from "@/theme/ThemeProvider";

import { wiAlias } from "./workspace-model";

/**
 * «WI-3» — the alias the agents say in chat («… (WI-1)»), web WiBadge: a
 * muted outlined pill, laid out LTR so the hyphen and number never reorder
 * inside the RTL row. Nothing when the item has no alias.
 */
export function WiBadge({ seq }: { seq: number | null | undefined }) {
  const { colours } = useTheme();
  const alias = wiAlias(seq);
  if (!alias) return null;
  return (
    <View
      style={[styles.pill, { borderColor: colours.border, backgroundColor: colours.surface2 }]}
      accessibilityLabel={`رمز العنصر في هذه المحادثة: ${alias}`}
    >
      <AppText variant="xs" weight={500} tone="muted" style={styles.text}>
        {alias}
      </AppText>
    </View>
  );
}

const styles = StyleSheet.create({
  pill: { direction: "ltr", borderWidth: StyleSheet.hairlineWidth, borderRadius: 999, paddingHorizontal: 6, paddingVertical: 1 },
  text: { writingDirection: "ltr", fontVariant: ["tabular-nums"] },
});
