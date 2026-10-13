import { forwardRef } from "react";
import { StyleSheet, TextInput, View, type TextInputProps } from "react-native";

import { fontFamilyFor } from "@/theme/fonts";
import { useTheme } from "@/theme/ThemeProvider";
import { MAX_FONT_SCALE, typeVariants } from "@/theme/typography";

import { AppText } from "./AppText";

export interface TextFieldProps extends TextInputProps {
  label: string;
  error?: string | null;
  /** LTR content (email, password) — typed left-to-right inside an RTL form.
   *  Only the VALUE is LTR: while empty, the Arabic placeholder stays right. */
  ltr?: boolean;
}

export const TextField = forwardRef<TextInput, TextFieldProps>(function TextField(
  { label, error, ltr = false, style, ...rest },
  ref,
) {
  const { colours, radius } = useTheme();
  // Physical alignment on purpose: TextInput's textAlign is absolute on both
  // platforms (Android gravity; iOS input attributes carry no layout
  // direction), so "right" is right in Expo Go and in an RTL build alike.
  const ltrValue = ltr && !!rest.value;
  // writingDirection is iOS-only; Android picks the placeholder's base direction
  // from its first strong character, so «8 أحرف…» (digit first) rendered as LTR.
  // A leading RLM forces an RTL paragraph on both platforms.
  const placeholder = rest.placeholder ? `‏${rest.placeholder}` : rest.placeholder;
  return (
    <View style={styles.wrap}>
      <AppText variant="sm" weight={500} tone="secondary">
        {label}
      </AppText>
      <TextInput
        ref={ref}
        placeholderTextColor={colours.textSubtle}
        maxFontSizeMultiplier={MAX_FONT_SCALE}
        style={[
          styles.input,
          typeVariants.base,
          {
            fontFamily: fontFamilyFor[400],
            color: colours.textPrimary,
            backgroundColor: colours.surface1,
            borderColor: error ? colours.destructive : colours.border,
            borderRadius: radius.default,
            textAlign: ltrValue ? "left" : "right",
            writingDirection: ltrValue ? "ltr" : "rtl",
          },
          style,
        ]}
        {...rest}
        placeholder={placeholder}
      />
      {error ? (
        <AppText variant="xs" tone="destructive">
          {error}
        </AppText>
      ) : null}
    </View>
  );
});

const styles = StyleSheet.create({
  wrap: { gap: 6 },
  input: { minHeight: 48, borderWidth: 1, paddingHorizontal: 12, paddingVertical: 8 },
});
