import { memo } from "react";
import { Pressable, StyleSheet, View } from "react-native";

import type { WorkspaceItem, WorkspaceItemKind } from "@contracts";

import { AppText } from "@/components/AppText";
import { Icon, type IconName } from "@/components/Icon";
import { formatRelativeTime } from "@/lib/format";
import type { ColourName } from "@/theme/tokens.generated";
import { useTheme } from "@/theme/ThemeProvider";

import { WiBadge } from "./WiBadge";
import { cardLabel, HIDDEN_BADGE, isUserHidden } from "./workspace-model";

// Web WorkspaceCard `KIND_ICON` (lucide Paperclip / NotebookPen / BookOpen /
// FileText / MessageSquare) mapped onto the app's existing glyphs.
const KIND_ICON: Record<WorkspaceItemKind, IconName> = {
  attachment: "attachFile",
  note: "draft",
  agent_search: "open",
  agent_writing: "template",
  convo_context: "chat",
  references: "open",
};

// Web `KIND_COLORS`, on the same tokens (light + dark come from globals.css).
const KIND_COLOURS: Record<WorkspaceItemKind, { bg: ColourName; fg: ColourName }> = {
  attachment: { bg: "surface2", fg: "textMuted" },
  note: { bg: "warningBg", fg: "warningFg" },
  agent_search: { bg: "accentSoft", fg: "accent" },
  agent_writing: { bg: "infoBg", fg: "infoFg" },
  convo_context: { bg: "successBg", fg: "successFg" },
  references: { bg: "brandSoftBg", fg: "brandSoftFg" },
};

export interface WorkspaceCardProps {
  item: WorkspaceItem;
  onPress: (item: WorkspaceItem) => void;
}

/** One item: kind glyph · title · «WI-n» · kind/subtype pill · relative time
 *  (+ «مخفي» when the user hid it from ريحان). */
export const WorkspaceCard = memo(function WorkspaceCard({ item, onPress }: WorkspaceCardProps) {
  const { colours, radius } = useTheme();
  const tint = KIND_COLOURS[item.kind] ?? { bg: "surface2", fg: "textMuted" };
  const hidden = isUserHidden(item);
  const label = cardLabel(item);
  const when = formatRelativeTime(item.created_at);

  return (
    <Pressable
      testID={`wi-card-${item.item_id}`}
      accessibilityRole="button"
      accessibilityLabel={item.title || label}
      accessibilityHint={hidden ? HIDDEN_BADGE : undefined}
      onPress={() => onPress(item)}
      style={({ pressed }) => [
        styles.card,
        {
          borderColor: colours.border,
          backgroundColor: pressed ? colours.surface2 : colours.surface1,
          borderRadius: radius.default,
          opacity: hidden ? 0.6 : 1,
        },
      ]}
    >
      <Icon name={KIND_ICON[item.kind] ?? "template"} size={16} colour={colours.textMuted} style={styles.icon} />
      <View style={styles.body}>
        <AppText variant="sm" weight={500} numberOfLines={1}>
          {item.title || label}
        </AppText>
        <View style={styles.meta}>
          <WiBadge seq={item.wi_seq} />
          <View style={[styles.pill, { backgroundColor: colours[tint.bg] }]}>
            <AppText variant="xs" weight={500} style={{ color: colours[tint.fg] }} numberOfLines={1}>
              {label}
            </AppText>
          </View>
          {hidden ? (
            <View style={[styles.pill, { borderWidth: StyleSheet.hairlineWidth, borderColor: colours.borderStrong }]}>
              <AppText variant="xs" tone="muted">
                {HIDDEN_BADGE}
              </AppText>
            </View>
          ) : null}
          {when ? (
            <AppText variant="xs" tone="muted" numberOfLines={1} style={styles.time}>
              {when}
            </AppText>
          ) : null}
        </View>
      </View>
    </Pressable>
  );
});

const styles = StyleSheet.create({
  card: { flexDirection: "row", alignItems: "flex-start", gap: 12, padding: 12, borderWidth: StyleSheet.hairlineWidth },
  icon: { marginTop: 3 },
  body: { flex: 1, gap: 6 },
  meta: { flexDirection: "row", alignItems: "center", flexWrap: "wrap", gap: 8 },
  pill: { borderRadius: 999, paddingHorizontal: 8, paddingVertical: 1 },
  time: { flexShrink: 1 },
});
