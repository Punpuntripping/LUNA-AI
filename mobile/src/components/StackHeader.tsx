import type { ReactNode } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { useTheme } from "@/theme/ThemeProvider";

import { AppText } from "./AppText";

export interface StackHeaderProps {
  title: string;
  /** Renders the «رجوع» button when the screen has somewhere to go back to. */
  onBack?: () => void;
  /** Buttons at the end of the row (left in RTL), e.g. the chat's «…» menu. */
  actions?: ReactNode;
}

/**
 * Header for every stack screen that shows one (replaces the native header,
 * which lays out LTR in Expo Go). The row sets `direction: "rtl"` itself, so
 * the back button sits at the start (right) and the title follows it whether
 * or not I18nManager is RTL — that style is a no-op in an RTL build, never a
 * second flip. The arrow is drawn with plain transforms (no start/end, no
 * glyph), so it always points right: «back» in an RTL app.
 */
export function StackHeader({ title, onBack, actions }: StackHeaderProps) {
  const { colours } = useTheme();
  const insets = useSafeAreaInsets();

  return (
    <View style={[styles.bar, { paddingTop: insets.top, backgroundColor: colours.surface1 }]}>
      <View style={styles.row}>
        {onBack ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="رجوع"
            onPress={onBack}
            style={({ pressed }) => [styles.back, { opacity: pressed ? 0.6 : 1 }]}
            testID="header-back"
          >
            <BackArrow colour={colours.textPrimary} />
          </Pressable>
        ) : (
          <View style={styles.spacer} />
        )}
        <AppText variant="lg" weight={600} numberOfLines={1} style={styles.title} accessibilityRole="header">
          {title}
        </AppText>
        {actions ? <View style={styles.actions}>{actions}</View> : null}
      </View>
    </View>
  );
}

/** «>» chevron from two bars: the upper bar slopes down, the lower one up,
 *  meeting on the right. Physical transforms only — layout direction can't
 *  mirror it. */
function BackArrow({ colour }: { colour: string }) {
  return (
    <View style={styles.arrow} pointerEvents="none">
      <View style={[styles.bar1, { backgroundColor: colour }]} />
      <View style={[styles.bar2, { backgroundColor: colour }]} />
    </View>
  );
}

const ARROW_BAR = 11;
const ARROW_OFFSET = ARROW_BAR / 2 / Math.SQRT2;

const styles = StyleSheet.create({
  bar: { direction: "rtl" },
  row: { flexDirection: "row", alignItems: "center", minHeight: 56, paddingHorizontal: 6, gap: 4 },
  back: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
  spacer: { width: 12 },
  title: { flex: 1 },
  actions: { flexDirection: "row", alignItems: "center", gap: 2 },
  arrow: { width: 16, height: 18, alignItems: "center", justifyContent: "center" },
  bar1: {
    position: "absolute",
    width: ARROW_BAR,
    height: 2,
    borderRadius: 1,
    transform: [{ translateY: -ARROW_OFFSET }, { rotate: "45deg" }],
  },
  bar2: {
    position: "absolute",
    width: ARROW_BAR,
    height: 2,
    borderRadius: 1,
    transform: [{ translateY: ARROW_OFFSET }, { rotate: "-45deg" }],
  },
});
