import { DETAIL_LEVEL_OPTIONS } from "@contracts";
import { Pressable, StyleSheet, View } from "react-native";

import { AppText } from "@/components/AppText";
import { useTheme } from "@/theme/ThemeProvider";

import { useDetailLevel } from "./use-detail-level";

/**
 * مختصر / متوسط / مفصّل — the web DetailLevelToggle (segmented radiogroup +
 * the caption «اختر مستوى التفصيل للإجابة»). Disabled while a change saves.
 */
export function DetailLevelControl({ hideCaption = false }: { hideCaption?: boolean }) {
  const { colours, radius } = useTheme();
  const { level, saving, setLevel } = useDetailLevel();

  return (
    <View style={styles.wrap}>
      <View
        accessibilityRole="radiogroup"
        accessibilityLabel="مستوى التفصيل"
        style={[styles.segment, { backgroundColor: colours.surface2, borderRadius: radius.default, opacity: saving ? 0.8 : 1 }]}
      >
        {DETAIL_LEVEL_OPTIONS.map((opt) => {
          const selected = opt.value === level;
          return (
            <Pressable
              key={opt.value}
              accessibilityRole="radio"
              accessibilityLabel={opt.label}
              accessibilityState={{ selected, disabled: saving }}
              disabled={saving}
              onPress={() => setLevel(opt.value)}
              style={[styles.item, { borderRadius: radius.md, backgroundColor: selected ? colours.surface1 : "transparent" }]}
              testID={`detail-level-${opt.value}`}
            >
              <AppText variant="sm" weight={selected ? 600 : 400} tone={selected ? "primary" : "muted"} align="center">
                {opt.label}
              </AppText>
            </Pressable>
          );
        })}
      </View>
      {hideCaption ? null : (
        <AppText variant="xs" tone="muted">
          اختر مستوى التفصيل للإجابة
        </AppText>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: 8 },
  segment: { flexDirection: "row", padding: 4, gap: 4 },
  item: { flex: 1, minHeight: 44, paddingVertical: 8, justifyContent: "center" },
});
