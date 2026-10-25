import { memo, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";

import type { ArtifactInfoMap } from "@/chat/use-workspace-lookup";
import { ActionSheet } from "@/components/ActionSheet";
import { AppText } from "@/components/AppText";
import { Icon } from "@/components/Icon";
import { definiteArtifactName } from "@/components/workspace/workspace-model";
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
  const label = single ? `افتح ${definiteArtifactName(info.get(ids[0]))}` : `افتح المصادر (${ids.length})`;

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
            const entry = info.get(id);
            return {
              key: id,
              label: entry?.title || definiteArtifactName(entry),
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
