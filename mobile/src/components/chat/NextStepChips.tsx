import { memo } from "react";
import { Pressable, StyleSheet, View } from "react-native";

import type { NextStep, NextStepKind } from "@contracts";

import { AppText } from "@/components/AppText";
import { Icon, type IconName } from "@/components/Icon";
import { useTheme } from "@/theme/ThemeProvider";

// Next-step chips under the LATEST settled answer (web NextStepChips.tsx).
// Tap = PASTE the chip's full prompt into the composer, focused and editable.
// It NEVER sends (an `answer` prompt may end with «…» for the user to finish;
// up to two of them can share a turn, so keys are per item, not per kind).

// Keyed by string so this compiles whether or not the shared contracts include
// the newer `answer` kind yet; unknown kinds fall back to "open" at the call site.
const KIND_ICON: Record<NextStepKind | "answer", IconName> = {
  narrow_search: "search",
  draft: "draft",
  apply: "apply",
  open: "open",
  answer: "answer",
};

const PASTE_HINT = "تُضاف إلى مربع الكتابة لتعدّلها قبل الإرسال";

export const NextStepChips = memo(function NextStepChips({
  steps,
  onPaste,
}: {
  steps: NextStep[];
  onPaste: (prompt: string) => void;
}) {
  const { colours, radius } = useTheme();
  if (steps.length === 0) return null;
  return (
    <View style={styles.wrap} testID="next-step-chips">
      <View style={styles.label}>
        <Icon name="sparkles" size={14} colour={colours.textMuted} />
        <AppText variant="xs" tone="muted">
          اقتراحات للمتابعة
        </AppText>
      </View>
      <View style={styles.chips}>
        {steps.map((step, i) => (
          <Pressable
            key={`${step.kind}-${i}`}
            testID={`next-step-${step.kind}-${i}`}
            accessibilityRole="button"
            accessibilityLabel={`${step.label} — ${PASTE_HINT}`}
            onPress={() => onPaste(step.prompt)}
            style={({ pressed }) => [
              styles.chip,
              {
                borderColor: colours.border,
                borderRadius: radius.full,
                backgroundColor: pressed ? colours.surface2 : "transparent",
              },
            ]}
          >
            <Icon name={KIND_ICON[step.kind] ?? "open"} size={15} colour={colours.textMuted} />
            <AppText variant="sm" tone="secondary" numberOfLines={1} style={styles.chipText}>
              {step.label}
            </AppText>
            <Icon name="nextStep" size={13} colour={colours.textSubtle} />
          </Pressable>
        ))}
      </View>
    </View>
  );
});

const styles = StyleSheet.create({
  wrap: { gap: 6, marginTop: 12 },
  label: { flexDirection: "row", alignItems: "center", gap: 4 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  chip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    minHeight: 36,
    paddingHorizontal: 12,
    borderWidth: 1,
    maxWidth: "100%",
  },
  chipText: { flexShrink: 1 },
});
