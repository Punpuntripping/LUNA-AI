import { StyleSheet, View } from "react-native";

import { isWiPreviewLocked, wiPreviewVisibleContent, type WorkspaceItem } from "@contracts";

import { AppText } from "@/components/AppText";
import { WorkspaceItemActionBar, type ManageActions } from "@/components/artifact/WorkspaceItemActionBar";
import { useTheme } from "@/theme/ThemeProvider";

import { MarkdownScroll } from "./MarkdownScroll";
import { WiPreviewWall } from "./WiPreviewWall";
import { CONVO_CONTEXT_EMPTY, REFERENCES_STUB_BANNER } from "./workspace-model";

export interface TextItemViewerProps {
  item: WorkspaceItem;
  bottomInset: number;
  manage?: ManageActions;
}

/**
 * View-only kinds — web ConvoContextViewer («لم يُولَّد ملخّص للمحادثة بعد»
 * when empty) and ReferencesRenderer (the «قيد التطوير — قائمة المراجع» stub
 * banner). Markdown body + a نسخ-only bar. A preview-locked body (never sent
 * for these kinds today, honoured anyway) shows its visible part + the wall.
 */
export function TextItemViewer({ item, bottomInset, manage }: TextItemViewerProps) {
  const { colours } = useTheme();
  const content = wiPreviewVisibleContent(item);
  const previewLocked = isWiPreviewLocked(item);
  const isReferences = item.kind === "references";

  if (!isReferences && !content.trim()) {
    return (
      <View style={styles.root}>
        <View style={styles.centre}>
          <AppText variant="sm" tone="muted" align="center">
            {CONVO_CONTEXT_EMPTY}
          </AppText>
        </View>
        {manage ? <WorkspaceItemActionBar copyText="" bottomInset={bottomInset} manage={manage} /> : null}
      </View>
    );
  }

  return (
    <View style={styles.root}>
      {isReferences ? (
        <View style={[styles.banner, { backgroundColor: colours.surface2, borderBottomColor: colours.border }]}>
          <AppText variant="xs" tone="muted">
            {REFERENCES_STUB_BANNER}
          </AppText>
        </View>
      ) : null}
      <MarkdownScroll
        content={content}
        bottomInset={bottomInset}
        fadeEnd={previewLocked}
        footer={previewLocked ? <WiPreviewWall /> : undefined}
      />
      <WorkspaceItemActionBar copyText={content} bottomInset={bottomInset} manage={manage} />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  centre: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24 },
  banner: { paddingHorizontal: 16, paddingVertical: 8, borderBottomWidth: StyleSheet.hairlineWidth },
});
