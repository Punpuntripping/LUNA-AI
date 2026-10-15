import { memo, type ReactNode } from "react";
import { StyleSheet, View } from "react-native";

import { AppText } from "@/components/AppText";
import { Icon } from "@/components/Icon";
import { useTheme } from "@/theme/ThemeProvider";

// `agent_question` turn: the agent paused to ask. Framed «السؤال» callout with
// the agent's suggested replies as READ-ONLY chips — the user types the reply
// in the composer (web MessageBubble.tsx agent-question branch).

export const AgentQuestion = memo(function AgentQuestion({
  suggestions,
  children,
}: {
  suggestions: string[] | undefined;
  children: ReactNode;
}) {
  const { colours, radius } = useTheme();
  return (
    <View
      testID="agent-question"
      style={[
        styles.box,
        {
          borderColor: colours.accentLine,
          borderStartColor: colours.primary,
          backgroundColor: colours.accentSoft,
          borderRadius: radius["2xl"],
        },
      ]}
    >
      <View style={styles.head}>
        <Icon name="question" size={15} colour={colours.primary} />
        <AppText variant="xs" weight={700} tone="link">
          السؤال
        </AppText>
      </View>
      {children}
      {suggestions && suggestions.length > 0 ? (
        <View style={styles.chips}>
          {suggestions.map((s, i) => (
            <View key={`${i}-${s}`} style={[styles.chip, { backgroundColor: colours.surface2, borderRadius: radius.full }]}>
              <AppText variant="xs" tone="muted">
                {s}
              </AppText>
            </View>
          ))}
        </View>
      ) : null}
    </View>
  );
});

const styles = StyleSheet.create({
  box: { alignSelf: "stretch", borderWidth: 1, borderStartWidth: 4, paddingHorizontal: 14, paddingVertical: 12, gap: 6 },
  head: { flexDirection: "row", alignItems: "center", gap: 6 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 6 },
  chip: { paddingHorizontal: 10, paddingVertical: 4 },
});
