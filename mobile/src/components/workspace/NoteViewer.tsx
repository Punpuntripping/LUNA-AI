import { ActivityIndicator, Pressable, StyleSheet, TextInput, View } from "react-native";

import { wiPreviewVisibleContent, type WorkspaceItem } from "@contracts";

import { AppText } from "@/components/AppText";
import { DraftEditor } from "@/components/artifact/DraftEditor";
import { isAgentLocked } from "@/components/artifact/use-artifact";
import { WorkspaceItemActionBar, type ManageActions } from "@/components/artifact/WorkspaceItemActionBar";
import { formatDateTime } from "@/lib/format";
import { fontFamilyFor } from "@/theme/fonts";
import { useTheme } from "@/theme/ThemeProvider";
import { typeVariants } from "@/theme/typography";

import { MarkdownScroll } from "./MarkdownScroll";
import { useNoteDraft } from "./use-note-draft";
import { WiPreviewWall } from "./WiPreviewWall";
import { NOTE_BODY_PLACEHOLDER, NOTE_TITLE_PLACEHOLDER, previewAccess } from "./workspace-model";

export interface NoteViewerProps {
  item: WorkspaceItem;
  bottomInset: number;
  manage?: ManageActions;
}

/**
 * A `note` — web NoteEditor (MarkdownDocEditor): editable title, معاينة/تحرير
 * (opens in تحرير when empty), 800 ms autosave with «جارٍ الحفظ» / «تم الحفظ
 * تلقائياً» / «التغييرات تُحفظ تلقائياً» + «آخر تحديث», the lock and 409
 * banners, and the bar's نسخ (live text). Mount keyed by `item_id`.
 *
 * Preview-locked (the server sent `preview` — never for the user's own notes
 * today, honoured anyway): the visible part, read-only, with the wall; no
 * معاينة/تحرير. The title stays editable (the server allows title edits).
 */
export function NoteViewer({ item, bottomInset, manage }: NoteViewerProps) {
  const { colours } = useTheme();
  const locked = isAgentLocked(item);
  const access = previewAccess(item);
  const draft = useNoteDraft(item, locked, !access.editBody);
  const updated = formatDateTime(item.updated_at);
  const mode = access.editBody ? draft.mode : "preview";
  const body = access.locked ? wiPreviewVisibleContent(item) : draft.content;

  return (
    <View style={styles.root}>
      {locked ? (
        <View style={[styles.banner, { backgroundColor: colours.warningBg }]} accessibilityRole="alert">
          <AppText variant="sm" weight={600} style={{ color: colours.warningFg }}>
            ريحان يحرر هذا الملف الآن…
          </AppText>
        </View>
      ) : draft.conflict ? (
        <View style={[styles.banner, styles.bannerRow, { backgroundColor: colours.errorBg }]} accessibilityRole="alert">
          <AppText variant="sm" style={[styles.flex, { color: colours.errorFg }]}>
            {draft.conflict}
          </AppText>
          <Pressable accessibilityRole="button" accessibilityLabel="إخفاء" onPress={draft.clearConflict} style={styles.bannerAction}>
            <AppText variant="sm" weight={600} style={{ color: colours.errorFg, textDecorationLine: "underline" }}>
              إخفاء
            </AppText>
          </Pressable>
        </View>
      ) : null}

      <View style={[styles.titleRow, { borderBottomColor: colours.border }]}>
        <TextInput
          testID="wi-note-title"
          value={draft.title}
          onChangeText={draft.setTitle}
          editable={!locked}
          placeholder={NOTE_TITLE_PLACEHOLDER}
          placeholderTextColor={colours.textSubtle}
          accessibilityLabel="عنوان الملاحظة"
          maxLength={500}
          returnKeyType="done"
          style={[styles.title, typeVariants.base, { fontFamily: fontFamilyFor[600], color: colours.textPrimary }]}
        />
      </View>

      <View style={[styles.status, { borderBottomColor: colours.border }]}>
        <View style={styles.statusStart}>
          {draft.saving ? <ActivityIndicator size="small" color={colours.textMuted} /> : null}
          <AppText variant="xs" tone="muted">
            {draft.saving ? "جارٍ الحفظ" : draft.savedAt ? "تم الحفظ تلقائياً" : "التغييرات تُحفظ تلقائياً"}
          </AppText>
        </View>
        {updated ? (
          <AppText variant="xs" tone="muted" style={styles.shrink}>
            آخر تحديث: {updated}
          </AppText>
        ) : null}
      </View>

      {mode === "edit" ? (
        <DraftEditor
          value={draft.content}
          onChangeText={draft.setContent}
          readOnly={locked}
          bottomInset={bottomInset}
          placeholder={NOTE_BODY_PLACEHOLDER}
          label="نص الملاحظة"
        />
      ) : (
        <MarkdownScroll
          content={body}
          bottomInset={bottomInset}
          fadeEnd={access.locked}
          footer={access.locked ? <WiPreviewWall /> : undefined}
        />
      )}

      <WorkspaceItemActionBar
        copyText={body}
        mode={access.editBody ? draft.mode : undefined}
        onModeChange={access.editBody ? draft.setMode : undefined}
        editDisabled={locked}
        bottomInset={bottomInset}
        manage={manage}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  flex: { flex: 1 },
  banner: { paddingHorizontal: 16, paddingVertical: 8 },
  bannerRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  bannerAction: { minHeight: 44, justifyContent: "center", paddingHorizontal: 4 },
  titleRow: { paddingHorizontal: 16, paddingVertical: 6, borderBottomWidth: StyleSheet.hairlineWidth },
  title: { minHeight: 44, textAlign: "right", writingDirection: "rtl", paddingVertical: 6 },
  status: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    flexWrap: "wrap",
    gap: 8,
    paddingHorizontal: 16,
    paddingVertical: 6,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  statusStart: { flexDirection: "row", alignItems: "center", gap: 6, flexShrink: 1 },
  shrink: { flexShrink: 1 },
});
