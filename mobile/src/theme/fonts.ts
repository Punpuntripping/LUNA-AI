import {
  NotoNaskhArabic_400Regular,
  NotoNaskhArabic_500Medium,
  NotoNaskhArabic_600SemiBold,
  NotoNaskhArabic_700Bold,
} from "@expo-google-fonts/noto-naskh-arabic";

// Same family as the web (frontend/app/layout.tsx: Noto Naskh Arabic 400–700).
// Loaded at runtime with useFonts() while the splash screen is held; at the
// first dev build this moves to the expo-font config plugin (embedded fonts).

export const fontMap = {
  NotoNaskhArabic_400Regular,
  NotoNaskhArabic_500Medium,
  NotoNaskhArabic_600SemiBold,
  NotoNaskhArabic_700Bold,
};

export type FontWeight = 400 | 500 | 600 | 700;

/** Android ignores `fontWeight` on custom fonts — each weight is its own family. */
export const fontFamilyFor: Record<FontWeight, keyof typeof fontMap> = {
  400: "NotoNaskhArabic_400Regular",
  500: "NotoNaskhArabic_500Medium",
  600: "NotoNaskhArabic_600SemiBold",
  700: "NotoNaskhArabic_700Bold",
};
