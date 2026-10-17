import { memo, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";

import type { ArtifactInfo, ArtifactInfoMap } from "@/chat/use-workspace-lookup";
import { ActionSheet } from "@/components/ActionSheet";
import { AppText } from "@/components/AppText";
import { Icon } from "@/components/Icon";
import { useTheme } from "@/theme/ThemeProvider";

// «افتح ال…» under an answer — reopens the turn's agent output in the
// ArtifactSheet (port of the web ArtifactChip, MessageBubble.tsx). One output
// → the button names it; several → «افتح المصادر (n)» opens a picker. Only
// kinds the sheet can show (agent_search / agent_writing); notes and
// attachments wait for the workspace (2b).

// The picker's Modal must finish dismissing before the ArtifactSheet Modal
// presents (iOS refuses a second modal mid-dismiss).
const PICKER_DISMISS_MS = 350;

const VIEWABLE = new Set(["agent_search", "agent_writing"]);

// Lookups, not morphology (web DEFINITE_SUBTYPE / DEFINITE_KIND): «نتيجة
// البحث» is already definite by إضافة and takes no «ال».
const DEFINITE_SUBTYPE: Record<string, string> = {
  report: "التقرير",
  contract: "العقد",
  memo: "المذكرة",
  summary: "الملخص",
  legal_opinion: "الرأي القانوني",
  legal_synthesis: "التحليل القانوني",
};
const DEFINITE_KIND: Record<string, string> = {
  agent_search: "نتيجة البحث",
  agent_writing: "المسودة",
};

function definiteName(entry: ArtifactInfo): string {
  return (entry.subtype ? DEFINITE_SUBTYPE[entry.subtype] : undefined) ?? DEFINITE_KIND[entry.kind] ?? "المصدر";
}

/** The message's artifact ids the sheet can show, in message order. */
export function viewableArtifacts(ids: readonly string[] | null | undefined, info: ArtifactInfoMap): string[] {
  return (ids ?? []).filter((id) => VIEWABLE.has(info.get(id)?.kind ?? ""));
}

export const OpenArtifactButton = memo(function OpenArtifactButton({
  ids,
  info,
  onOpen,
}: {
  ids: string[];
  info: ArtifactInfoMap;
  onOpen: (itemId: string) => void;
}) {
  const { colours, radius } = useTheme();
  const [picker, setPicker] = useState(false);
  if (ids.length === 0) return null;

  const single = ids.length === 1;
  const label = single ? `افتح ${definiteName(info.get(ids[0]) as ArtifactInfo)}` : `افتح المصادر (${ids.length})`;

  return (
    <View style={styles.wrap}>
      <Pressable
        testID="open-artifact"
        accessibilityRole="button"
        accessibilityLabel={label}
        onPress={() => (single ? onOpen(ids[0]) : setPicker(true))}
        style={({ pressed }) => [
          styles.button,
          {
            backgroundColor: colours.primary,
            borderRadius: radius.default,
            opacity: pressed ? 0.85 : 1,
          },
        ]}
      >
        <Icon name="open" size={16} colour={colours.primaryFg} />
        <AppText variant="sm" weight={600} style={{ color: colours.primaryFg }}>
          {label}
        </AppText>
      </Pressable>
      {single ? null : (
        <ActionSheet
          visible={picker}
          onClose={() => setPicker(false)}
          title="افتح المصادر"
          actions={ids.map((id) => {
            const entry = info.get(id) as ArtifactInfo;
            return {
              key: id,
              label: entry.title || definiteName(entry),
              icon: "deepSearch",
              onPress: () => setTimeout(() => onOpen(id), PICKER_DISMISS_MS),
            };
          })}
          testID="open-artifact-picker"
        />
      )}
    </View>
  );
});

const styles = StyleSheet.create({
  wrap: { flexDirection: "row", marginTop: 12 },
  button: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    minHeight: 40,
    paddingHorizontal: 14,
  },
});
