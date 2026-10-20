import { templateChipLabel } from "@contracts";
import { ActivityIndicator, Pressable, StyleSheet, View } from "react-native";

import { AppText } from "@/components/AppText";
import { Icon, type IconName } from "@/components/Icon";
import { useTheme } from "@/theme/ThemeProvider";

import { removeBlogChip } from "./blog-import";
import { useComposerExtrasStore, usePendingBlogs, usePendingTemplate } from "./pending-store";
import { blogChipLabel, type PendingBlogChip } from "./send-parts";

/**
 * The pills above the composer input — web BlogChips + TemplateChip: one per
 * blog (spinner while importing, title when ready, the Arabic error on a bad
 * link) and the single قالب. «×» removes; a blog item this chip created is
 * deleted with it. Renders nothing when there are no chips.
 */
export function ComposerExtrasChips({ draftKey }: { draftKey: string }) {
  const template = usePendingTemplate(draftKey);
  const blogs = usePendingBlogs(draftKey);
  const setTemplate = useComposerExtrasStore((s) => s.setTemplate);
  if (!template && blogs.length === 0) return null;

  return (
    <View style={styles.wrap} testID="composer-extras-chips">
      {blogs.map((b) => (
        <BlogPill key={b.id} chip={b} onRemove={() => removeBlogChip(draftKey, b)} />
      ))}
      {template ? (
        <Pill
          icon="template"
          label={templateChipLabel(template.title)}
          removeLabel="إزالة القالب"
          onRemove={() => setTemplate(draftKey, null)}
          testID="composer-template-chip"
        />
      ) : null}
    </View>
  );
}

function BlogPill({ chip, onRemove }: { chip: PendingBlogChip; onRemove: () => void }) {
  return (
    <Pill
      icon="blog"
      loading={chip.status === "loading"}
      failed={chip.status === "failed"}
      label={blogChipLabel(chip)}
      removeLabel="إزالة المدونة"
      onRemove={onRemove}
      testID={`composer-blog-chip-${chip.status}`}
    />
  );
}

function Pill({
  icon,
  label,
  removeLabel,
  onRemove,
  loading = false,
  failed = false,
  testID,
}: {
  icon: IconName;
  label: string;
  removeLabel: string;
  onRemove: () => void;
  loading?: boolean;
  failed?: boolean;
  testID?: string;
}) {
  const { colours, radius } = useTheme();
  const fg = failed ? colours.destructive : colours.textPrimary;
  return (
    <View
      testID={testID}
      style={[
        styles.pill,
        {
          borderRadius: radius.full,
          borderColor: failed ? colours.destructive : colours.border,
          backgroundColor: failed ? colours.errorBg : colours.surface2,
        },
      ]}
    >
      {loading ? (
        <ActivityIndicator size="small" color={colours.textMuted} />
      ) : (
        <Icon name={icon} size={14} colour={failed ? colours.destructive : colours.primary} />
      )}
      <AppText variant="xs" numberOfLines={1} style={[styles.label, { color: fg }]}>
        {label}
      </AppText>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={removeLabel}
        hitSlop={10}
        onPress={onRemove}
        style={({ pressed }) => [styles.remove, { opacity: pressed ? 0.6 : 1 }]}
      >
        <Icon name="close" size={12} colour={colours.textMuted} />
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { flexDirection: "row", flexWrap: "wrap", gap: 8, paddingBottom: 6 },
  pill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    maxWidth: 256,
    borderWidth: StyleSheet.hairlineWidth,
    paddingStart: 10,
    paddingEnd: 6,
    paddingVertical: 4,
  },
  label: { flexShrink: 1 },
  remove: { padding: 2 },
});
