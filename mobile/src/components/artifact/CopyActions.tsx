import * as Clipboard from "expo-clipboard";
import * as Haptics from "expo-haptics";
import { useEffect, useRef, useState } from "react";
import { StyleSheet, View } from "react-native";

import { markdownToPlainText } from "@contracts";

import { ActionChip } from "./ActionChip";

type CopyMode = "md" | "plain";

/**
 * «نسخ» (markdown as-is) and «النسخ لناجز» (plain text — Najiz fields take no
 * formatting). `text` is the full payload, «المراجع» already appended by the
 * caller (`appendReferencesForCopy`).
 */
export function CopyActions({ text, disabled = false }: { text: string; disabled?: boolean }) {
  const [copied, setCopied] = useState<CopyMode | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const copy = async (mode: CopyMode) => {
    try {
      await Clipboard.setStringAsync(mode === "md" ? text : markdownToPlainText(text));
    } catch {
      return; // the text stays selectable; nothing to report
    }
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => undefined);
    setCopied(mode);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(null), 1500);
  };

  const off = disabled || text.trim().length === 0;
  return (
    <View style={styles.row}>
      <ActionChip label={copied === "md" ? "تم النسخ" : "نسخ"} onPress={() => void copy("md")} disabled={off} testID="artifact-copy" />
      <ActionChip
        label={copied === "plain" ? "تم النسخ" : "النسخ لناجز"}
        onPress={() => void copy("plain")}
        disabled={off}
        testID="artifact-copy-najiz"
      />
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
});
