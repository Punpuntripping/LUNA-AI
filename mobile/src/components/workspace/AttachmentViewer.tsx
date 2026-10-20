import { Image } from "expo-image";
import { useState } from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";

import type { WorkspaceItem } from "@contracts";

import { errorMessage } from "@/api/errors";
import { AppText } from "@/components/AppText";
import { ActionChip } from "@/components/artifact/ActionChip";
import { ACTION_BAR_SPACE, WorkspaceItemActionBar, type ManageActions } from "@/components/artifact/WorkspaceItemActionBar";
import { openInBrowser } from "@/components/source/exits";
import { useTheme } from "@/theme/ThemeProvider";

import { MarkdownScroll } from "./MarkdownScroll";
import { useWorkspaceFileUrl } from "./use-workspace";
import { attachmentMeta, attachmentView, ATTACHMENT_TEXTS, originalFileMode } from "./workspace-model";

export interface AttachmentViewerProps {
  item: WorkspaceItem;
  bottomInset: number;
  manage?: ManageActions;
}

/**
 * An `attachment` — web AttachmentRenderer: the OCR extraction first (exactly
 * what the agents read), «الملف الأصلي» switches to the original (signed URL
 * from `GET /workspace/{id}/file`) and «النص المستخرج» back. No text yet → the
 * original with the OCR status line. Purged original → the text alone, or
 * only the reason when nothing was extracted.
 */
export function AttachmentViewer({ item, bottomInset, manage }: AttachmentViewerProps) {
  const { colours } = useTheme();
  const [showOriginal, setShowOriginal] = useState(false);
  const view = attachmentView(item, showOriginal);
  const ocrText = (item.content_md ?? "").trim();

  let body;
  if (view.kind === "ocr") {
    body = (
      <>
        <View style={[styles.toolbar, { borderBottomColor: colours.border }]}>
          {view.purged ? (
            <AppText variant="xs" tone="muted">
              {ATTACHMENT_TEXTS.purged}
            </AppText>
          ) : (
            <ActionChip label={ATTACHMENT_TEXTS.original} variant="ghost" onPress={() => setShowOriginal(true)} testID="wi-attachment-original" />
          )}
        </View>
        <MarkdownScroll content={ocrText} bottomInset={bottomInset} testID="wi-attachment-ocr" />
      </>
    );
  } else if (view.kind === "purged-empty") {
    body = (
      <View style={[styles.centre, { paddingBottom: ACTION_BAR_SPACE + bottomInset }]}>
        <AppText variant="sm" tone="muted" align="center">
          {ATTACHMENT_TEXTS.purged}
        </AppText>
        <AppText variant="xs" tone="muted" align="center">
          {view.detail}
        </AppText>
      </View>
    );
  } else {
    body = (
      <>
        {view.canBack ? (
          <View style={[styles.toolbar, { borderBottomColor: colours.border, backgroundColor: colours.surface2 }]}>
            <ActionChip label={ATTACHMENT_TEXTS.extracted} variant="ghost" onPress={() => setShowOriginal(false)} testID="wi-attachment-extracted" />
          </View>
        ) : view.banner ? (
          <View style={[styles.banner, { borderBottomColor: colours.border, backgroundColor: colours.surface2 }]} accessibilityRole="text">
            <AppText variant="xs" tone="muted">
              {view.banner}
            </AppText>
          </View>
        ) : null}
        <OriginalFile item={item} bottomInset={bottomInset} />
      </>
    );
  }

  return (
    <View style={styles.root}>
      {body}
      <WorkspaceItemActionBar
        copyText={view.kind === "ocr" ? ocrText : ""}
        copyLabel={ATTACHMENT_TEXTS.copyExtracted}
        bottomInset={bottomInset}
        manage={manage}
      />
    </View>
  );
}

/** The original: an image inline (web `<img>`); a PDF / anything else opens in
 *  the in-app browser from the signed URL (web `<iframe>` / download link). */
function OriginalFile({ item, bottomInset }: { item: WorkspaceItem; bottomInset: number }) {
  const { colours, radius } = useTheme();
  const file = useWorkspaceFileUrl(item.item_id, true);
  const meta = attachmentMeta(item);
  const filename = meta.filename ?? item.title;
  const pad = { paddingBottom: ACTION_BAR_SPACE + bottomInset + 12 };

  if (file.isPending) {
    return (
      <View style={[styles.centre, pad]}>
        <ActivityIndicator color={colours.primary} />
        <AppText variant="sm" tone="muted">
          {ATTACHMENT_TEXTS.loading}
        </AppText>
      </View>
    );
  }
  if (file.isError || !file.data?.url) {
    return (
      <View style={[styles.centre, pad]}>
        <AppText variant="sm" tone="destructive" align="center">
          {ATTACHMENT_TEXTS.loadFailed}
        </AppText>
        {file.isError ? (
          <AppText variant="xs" tone="muted" align="center">
            {errorMessage(file.error, "")}
          </AppText>
        ) : null}
        <ActionChip label="إعادة المحاولة" variant="ghost" onPress={() => void file.refetch()} />
      </View>
    );
  }

  const url = file.data.url;
  if (originalFileMode(meta.mimeType) === "image") {
    return (
      <View style={[styles.imageWrap, pad, { backgroundColor: colours.surface2 }]}>
        <Image
          source={{ uri: url }}
          style={[styles.image, { borderRadius: radius.md }]}
          contentFit="contain"
          accessibilityLabel={filename}
          testID="wi-attachment-image"
        />
      </View>
    );
  }

  return (
    <View style={[styles.centre, pad]}>
      <AppText variant="base" weight={600} align="center" numberOfLines={3}>
        {filename}
      </AppText>
      <ActionChip label={ATTACHMENT_TEXTS.openFile} variant="filled" onPress={() => openInBrowser(url)} testID="wi-attachment-open" />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  toolbar: { flexDirection: "row", alignItems: "center", paddingHorizontal: 12, paddingVertical: 4, minHeight: 44, borderBottomWidth: StyleSheet.hairlineWidth },
  banner: { paddingHorizontal: 16, paddingVertical: 8, borderBottomWidth: StyleSheet.hairlineWidth },
  centre: { flex: 1, alignItems: "center", justifyContent: "center", gap: 12, padding: 24 },
  imageWrap: { flex: 1, padding: 12 },
  image: { flex: 1, width: "100%" },
});
