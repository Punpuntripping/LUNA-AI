import { Image } from "expo-image";
import { Pressable, ScrollView, StyleSheet, View } from "react-native";

import { formatBytes } from "@/attachments/limits";
import type { PendingAttachment } from "@/attachments/upload-manager";
import { AppText } from "@/components/AppText";
import { Icon } from "@/components/Icon";
import { LRI, PDI } from "@/components/markdown/links";
import { useTheme } from "@/theme/ThemeProvider";

// The strip of attachment chips above the composer input (web FilePreview +
// AttachmentUploadCard): thumbnail / PDF glyph, name, progress or «PDF · 2.1 MB»
// or the Arabic error, ✕ (cancel / remove) and — phone addition — «إعادة
// المحاولة» on a failed chip.

export interface AttachmentChipsProps {
  chips: readonly PendingAttachment[];
  onRemove: (id: string) => void;
  onRetry: (id: string) => void;
}

export function AttachmentChips({ chips, onRemove, onRetry }: AttachmentChipsProps) {
  if (chips.length === 0) return null;
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      keyboardShouldPersistTaps="handled"
      contentContainerStyle={styles.strip}
      testID="composer-attachments"
    >
      {chips.map((chip) => (
        <AttachmentChip key={chip.id} chip={chip} onRemove={onRemove} onRetry={onRetry} />
      ))}
    </ScrollView>
  );
}

function extensionOf(name: string): string {
  const ext = name.split(".").pop()?.toUpperCase();
  return ext && ext !== name.toUpperCase() ? ext : "FILE";
}

function AttachmentChip({
  chip,
  onRemove,
  onRetry,
}: {
  chip: PendingAttachment;
  onRemove: (id: string) => void;
  onRetry: (id: string) => void;
}) {
  const { colours, radius } = useTheme();
  const uploading = chip.status === "preparing" || chip.status === "uploading" || chip.status === "finalizing";
  const failed = chip.status === "failed";
  const completed = chip.status === "completed";
  const pct = Math.round(chip.progress * 100);
  const isPdf = chip.mimeType === "application/pdf" || /\.pdf$/i.test(chip.name);

  const status = failed
    ? chip.error ?? "فشل الرفع"
    : uploading
      ? chip.status === "preparing"
        ? "جارٍ التجهيز…"
        : `${LRI}${pct}%${chip.size ? ` · ${formatBytes(chip.size)}` : ""}${PDI}`
      : `${LRI}${extensionOf(chip.name)}${chip.size ? ` · ${formatBytes(chip.size)}` : ""}${PDI}`;

  return (
    <View
      testID={`attachment-chip-${chip.status}`}
      accessible={false}
      style={[
        styles.chip,
        {
          borderRadius: radius.xl,
          backgroundColor: colours.surface2,
          borderColor: failed ? colours.destructive : completed ? colours.successFg : colours.border,
        },
      ]}
    >
      <View style={[styles.thumb, { borderRadius: radius.default, backgroundColor: colours.surface3 }]}>
        {chip.previewUri ? (
          <Image source={{ uri: chip.previewUri }} style={[styles.thumbImage, { opacity: uploading ? 0.6 : 1 }]} contentFit="cover" />
        ) : (
          <Icon name={isPdf ? "filePdf" : "image"} size={22} colour={failed ? colours.destructive : colours.textMuted} />
        )}
      </View>

      <View style={styles.info}>
        <AppText variant="xs" weight={600} numberOfLines={1}>
          {chip.name}
        </AppText>
        {uploading && chip.status !== "preparing" ? (
          <View style={[styles.track, { backgroundColor: colours.surface3 }]}>
            <View style={[styles.fill, { width: `${pct}%`, backgroundColor: colours.primary }]} />
          </View>
        ) : null}
        <AppText variant="xs" tone={failed ? "destructive" : "muted"} numberOfLines={failed ? 2 : 1}>
          {status}
        </AppText>
        {failed ? (
          <Pressable
            testID="attachment-retry"
            accessibilityRole="button"
            accessibilityLabel="إعادة المحاولة"
            hitSlop={8}
            onPress={() => onRetry(chip.id)}
            style={styles.retry}
          >
            <Icon name="regenerate" size={14} colour={colours.primary} />
            <AppText variant="xs" tone="link" weight={600}>
              إعادة المحاولة
            </AppText>
          </Pressable>
        ) : null}
      </View>

      <Pressable
        testID="attachment-remove"
        accessibilityRole="button"
        accessibilityLabel={uploading ? "إلغاء الرفع" : "حذف الملف"}
        hitSlop={10}
        onPress={() => onRemove(chip.id)}
        style={[styles.remove, { backgroundColor: colours.textPrimary }]}
      >
        <Icon name="close" size={11} colour={colours.canvas} />
      </Pressable>
      {completed ? (
        <View style={[styles.badge, { backgroundColor: colours.successFg }]}>
          <Icon name="check" size={10} colour={colours.canvas} />
        </View>
      ) : failed ? (
        <View style={[styles.badge, { backgroundColor: colours.destructive }]}>
          <Icon name="error" size={10} colour={colours.canvas} />
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  strip: { gap: 8, paddingVertical: 6, paddingHorizontal: 2 },
  chip: {
    width: 190,
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    padding: 6,
    paddingEnd: 20,
    borderWidth: 1,
  },
  thumb: { width: 44, height: 44, alignItems: "center", justifyContent: "center", overflow: "hidden" },
  thumbImage: { width: 44, height: 44 },
  info: { flex: 1, gap: 2 },
  track: { height: 3, borderRadius: 2, overflow: "hidden" },
  fill: { height: 3 },
  retry: { flexDirection: "row", alignItems: "center", gap: 4, paddingTop: 2 },
  // Web card: ✕ at the top-start corner (over the thumbnail), status badge top-end.
  remove: {
    position: "absolute",
    top: 2,
    start: 2,
    width: 18,
    height: 18,
    borderRadius: 9,
    alignItems: "center",
    justifyContent: "center",
  },
  badge: {
    position: "absolute",
    top: 4,
    end: 4,
    width: 16,
    height: 16,
    borderRadius: 8,
    alignItems: "center",
    justifyContent: "center",
  },
});
