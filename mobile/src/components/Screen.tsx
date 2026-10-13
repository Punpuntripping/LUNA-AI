import type { ReactNode } from "react";
import { KeyboardAvoidingView, ScrollView, StyleSheet, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { useTheme } from "@/theme/ThemeProvider";

export interface ScreenProps {
  children: ReactNode;
  /** Scrollable form screens (default) vs a fixed, centred layout. */
  scroll?: boolean;
  centred?: boolean;
}

/** Page shell: safe area, canvas background, keyboard avoidance, and an
 *  explicit RTL layout direction (a no-op once I18nManager is RTL; it keeps
 *  logical start/end correct in Expo Go, where SDK 57 leaves RTL off).
 *
 *  Keyboard: `padding` on BOTH platforms. Android on SDK 57 is always
 *  edge-to-edge, so the window no longer resizes for the keyboard and the
 *  docs' `undefined` left forms covered (QA 2026-10-13). The ScrollView then
 *  shrinks to the space above the keyboard and the whole form — submit
 *  button included — can be scrolled to; taps on buttons/links land on the
 *  first touch while the keyboard is up (`handled`). Built-in only:
 *  react-native-keyboard-controller needs a development build (no Expo Go). */
export function Screen({ children, scroll = true, centred = false }: ScreenProps) {
  const { colours } = useTheme();
  const body = <View style={[styles.body, centred && styles.centred]}>{children}</View>;
  return (
    <SafeAreaView style={[styles.root, { backgroundColor: colours.canvas }]} edges={["top", "bottom"]}>
      <KeyboardAvoidingView style={styles.root} behavior="padding">
        {scroll ? (
          <ScrollView
            contentContainerStyle={styles.scroll}
            keyboardShouldPersistTaps="handled"
            showsVerticalScrollIndicator={false}
          >
            {body}
          </ScrollView>
        ) : (
          body
        )}
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, direction: "rtl" },
  scroll: { flexGrow: 1 },
  body: { flex: 1, paddingHorizontal: 24, paddingVertical: 24, gap: 16 },
  centred: { justifyContent: "center" },
});
