import * as Clipboard from "expo-clipboard";
import * as Haptics from "expo-haptics";
import { useState } from "react";
import { Pressable, StyleSheet } from "react-native";

import { markdownToPlainText } from "@contracts";

import { showToast } from "@/stores/toast";
import { useTheme } from "@/theme/ThemeProvider";

import { ActionSheet, type SheetAction } from "./ActionSheet";
import { Icon } from "./Icon";

// Y1 — «نسخ» (the markdown as-is) and «النسخ لناجز» (every markdown marker
// stripped — Najiz's editor pastes plain text). Haptic + «تم النسخ».
// Rich-HTML «نسخ» (web) is deferred (plan Y3).

// Visual box stays 36dp; the slop lifts the touch target to 44 (HIG / Material
// minimum). Exactly 4 so it stays inside a 44dp action row — Android drops
// touches that fall outside the parent's bounds.
const HIT_SLOP = 4;

export type CopyMode = "markdown" | "najiz";

const MSG_COPIED = "تم النسخ";
const MSG_COPY_FAILED = "تعذّر النسخ";

export async function copyText(text: string, mode: CopyMode): Promise<void> {
  const value = mode === "najiz" ? markdownToPlainText(text) : text;
  try {
    await Clipboard.setStringAsync(value);
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => undefined);
    showToast(MSG_COPIED);
  } catch {
    showToast(MSG_COPY_FAILED);
  }
}

/** The two copy rows, for hosts that build their own sheet (bubble long-press).
 *  `getText` is read at tap time — a streaming bubble copies its live content. */
export function copyActions(getText: () => string): SheetAction[] {
  return [
    { key: "copy", label: "نسخ", icon: "copy", onPress: () => void copyText(getText(), "markdown") },
    { key: "copy-najiz", label: "النسخ لناجز", icon: "copy", onPress: () => void copyText(getText(), "najiz") },
  ];
}

export interface CopyMenuProps {
  /** Markdown to copy (read at tap time). */
  getText: () => string;
  testID?: string;
}

/** «نسخ» icon button → sheet with «نسخ» / «النسخ لناجز». */
export function CopyMenu({ getText, testID }: CopyMenuProps) {
  const { colours } = useTheme();
  const [open, setOpen] = useState(false);
  return (
    <>
      <Pressable
        testID={testID}
        accessibilityRole="button"
        accessibilityLabel="نسخ"
        hitSlop={HIT_SLOP}
        onPress={() => setOpen(true)}
        style={({ pressed }) => [styles.button, { opacity: pressed ? 0.6 : 1 }]}
      >
        <Icon name="copy" size={18} colour={colours.textMuted} />
      </Pressable>
      <ActionSheet
        visible={open}
        onClose={() => setOpen(false)}
        actions={copyActions(getText)}
        testID={testID ? `${testID}-sheet` : undefined}
      />
    </>
  );
}

const styles = StyleSheet.create({
  button: { width: 36, height: 36, alignItems: "center", justifyContent: "center" },
});
