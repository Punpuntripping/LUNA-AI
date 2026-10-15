import { memo } from "react";
import { StyleSheet, View } from "react-native";

import type { TemplateUsed } from "@contracts";

import { AppText } from "@/components/AppText";
import { Icon } from "@/components/Icon";
import { useTheme } from "@/theme/ThemeProvider";

// «القوالب المستخدمة في الكتابة» under every settled answer whose turn
// drafted something (web TemplatesUsedLine.tsx). `[]` = drafted without a
// template. Titles are plain text in 2a (no template screen in the app yet).

export const TemplatesUsedLine = memo(function TemplatesUsedLine({ templates }: { templates: TemplateUsed[] }) {
  const { colours, radius } = useTheme();
  return (
    <View style={styles.wrap} testID="templates-used">
      <View style={styles.label}>
        <Icon name="template" size={14} colour={colours.textMuted} />
        <AppText variant="xs" tone="muted">
          القوالب المستخدمة في الكتابة:
        </AppText>
      </View>
      {templates.length === 0 ? (
        <AppText variant="xs" tone="muted">
          لم يُستخدم قالب — بُني الهيكل تلقائياً
        </AppText>
      ) : (
        templates.map((t) => (
          <View key={t.template_id} style={styles.item}>
            <AppText variant="xs" tone="secondary" numberOfLines={1} style={styles.title}>
              {t.title}
            </AppText>
            <View style={[styles.badge, { backgroundColor: colours.pillBg, borderRadius: radius.full }]}>
              <AppText variant="xs" style={{ color: colours.pillFg }}>
                {t.scope === "system" ? "عام" : "خاص"}
              </AppText>
            </View>
          </View>
        ))
      )}
    </View>
  );
});

const styles = StyleSheet.create({
  wrap: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", columnGap: 8, rowGap: 4, marginTop: 10 },
  label: { flexDirection: "row", alignItems: "center", gap: 4 },
  item: { flexDirection: "row", alignItems: "center", gap: 6, maxWidth: "100%" },
  title: { flexShrink: 1 },
  badge: { paddingHorizontal: 8 },
});
