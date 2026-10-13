import { StyleSheet, Text, type TextProps } from "react-native";

import { fontFamilyFor, type FontWeight } from "@/theme/fonts";
import { useTheme } from "@/theme/ThemeProvider";
import { MAX_FONT_SCALE, typeVariants, type TypeVariant } from "@/theme/typography";

export type TextTone = "primary" | "secondary" | "muted" | "subtle" | "destructive" | "onPrimary" | "link";

export interface AppTextProps extends TextProps {
  variant?: TypeVariant;
  weight?: FontWeight;
  tone?: TextTone;
  align?: "auto" | "center";
}

/** The only Text the app renders: Noto Naskh Arabic, theme colours, RTL base
 *  direction. Alignment stays `auto` — the paragraph's direction decides, so
 *  Arabic sits at the start (right) without ever writing `right`. */
export function AppText({
  variant = "base",
  weight = 400,
  tone = "primary",
  align = "auto",
  style,
  maxFontSizeMultiplier = MAX_FONT_SCALE,
  ...rest
}: AppTextProps) {
  const { colours } = useTheme();
  const colour = {
    primary: colours.textPrimary,
    secondary: colours.textSecondary,
    muted: colours.textMuted,
    subtle: colours.textSubtle,
    destructive: colours.destructive,
    onPrimary: colours.primaryFg,
    link: colours.primary,
  }[tone];

  return (
    <Text
      maxFontSizeMultiplier={maxFontSizeMultiplier}
      style={[
        styles.base,
        typeVariants[variant],
        { fontFamily: fontFamilyFor[weight], color: colour, textAlign: align },
        style,
      ]}
      {...rest}
    />
  );
}

const styles = StyleSheet.create({
  base: { writingDirection: "rtl" },
});
