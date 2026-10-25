import type { ReactNode } from "react";
import { ActivityIndicator, Pressable, StyleSheet, TextInput, View, type TextInputProps } from "react-native";

import { AppText } from "@/components/AppText";
import { Icon, type IconName } from "@/components/Icon";
import { fontFamilyFor } from "@/theme/fonts";
import { useTheme } from "@/theme/ThemeProvider";
import { MAX_FONT_SCALE, typeVariants } from "@/theme/typography";

// Small building blocks shared by the composer-extras sheets (same look as
// PublishSheet: heading row, list rows with a leading glyph, muted states).

export function SheetHeading({ icon, title }: { icon: IconName; title: string }) {
  const { colours } = useTheme();
  return (
    <View style={styles.heading}>
      <Icon name={icon} size={16} colour={colours.textPrimary} />
      {/* flex: 1 — never shrink-wrapped (large font scales clip the last
          glyph of a self-measured Naskh label on Android, QA F3). */}
      <AppText variant="base" weight={600} accessibilityRole="header" style={styles.fill}>
        {title}
      </AppText>
    </View>
  );
}

/** Non-interactive loading / empty / error line (web MenuStatus). */
export function ListStatus({ text, loading = false }: { text: string; loading?: boolean }) {
  const { colours } = useTheme();
  return (
    <View style={styles.status} accessibilityLiveRegion="polite">
      {loading ? <ActivityIndicator size="small" color={colours.textMuted} /> : null}
      <AppText variant="xs" tone="muted" style={styles.fill}>
        {text}
      </AppText>
    </View>
  );
}

export function ListRow({
  icon,
  label,
  trailing,
  disabled = false,
  onPress,
  testID,
}: {
  icon?: IconName;
  label: string;
  trailing?: ReactNode;
  disabled?: boolean;
  onPress: () => void;
  testID?: string;
}) {
  const { colours } = useTheme();
  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ disabled }}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [styles.row, pressed && { backgroundColor: colours.surface2 }, disabled && styles.disabled]}
    >
      {icon ? <Icon name={icon} size={18} colour={colours.textMuted} /> : null}
      <AppText variant="base" numberOfLines={1} style={styles.rowLabel}>
        {label}
      </AppText>
      {trailing}
    </Pressable>
  );
}

/** One-line input styled like the sheets' fields. `ltr` for URLs. Text
 *  alignment is physical on purpose (see TextField.tsx). */
export function SheetInput({ ltr = false, style, ...rest }: TextInputProps & { ltr?: boolean }) {
  const { colours, radius } = useTheme();
  return (
    <TextInput
      placeholderTextColor={colours.textSubtle}
      maxFontSizeMultiplier={MAX_FONT_SCALE}
      style={[
        styles.input,
        typeVariants.sm,
        {
          fontFamily: fontFamilyFor[400],
          color: colours.textPrimary,
          borderColor: colours.border,
          backgroundColor: colours.canvas,
          borderRadius: radius.default,
          textAlign: ltr ? "left" : "right",
          writingDirection: ltr ? "ltr" : "rtl",
        },
        style,
      ]}
      {...rest}
    />
  );
}

export function ErrorText({ text }: { text: string }) {
  return (
    <AppText variant="xs" tone="destructive" accessibilityRole="alert">
      {text}
    </AppText>
  );
}

export const sheetStyles = StyleSheet.create({
  body: { paddingHorizontal: 12, paddingBottom: 4, gap: 10 },
  list: { maxHeight: 360 },
  buttons: { gap: 8, paddingTop: 4 },
});

const styles = StyleSheet.create({
  heading: { flexDirection: "row", alignItems: "center", gap: 8 },
  status: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 4, paddingVertical: 10 },
  row: { flexDirection: "row", alignItems: "center", gap: 10, minHeight: 48, paddingHorizontal: 8, borderRadius: 10 },
  rowLabel: { flex: 1 },
  fill: { flex: 1 },
  disabled: { opacity: 0.5 },
  input: { borderWidth: 1, paddingHorizontal: 12, paddingVertical: 10, minHeight: 44 },
});
