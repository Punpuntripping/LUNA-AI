import { useMemo, type ReactNode } from "react";
import { RefreshControl, ScrollView, StyleSheet, View } from "react-native";

import { AppText } from "@/components/AppText";
import { ACTION_BAR_SPACE } from "@/components/artifact/WorkspaceItemActionBar";
import { Markdown } from "@/components/markdown/Markdown";
import { chunkMarkdown } from "@/components/source/chunk-markdown";
import { useTheme } from "@/theme/ThemeProvider";

import { WiPreviewFade } from "./WiPreviewWall";

export interface MarkdownScrollProps {
  content: string;
  bottomInset: number;
  /** Shown when `content` is blank. */
  emptyText?: string;
  /** Under the body, in the same scroll column. */
  footer?: ReactNode;
  /** A preview-locked body: the canvas fades in over its last block (the
   *  host puts the WiPreviewWall in `footer`). */
  fadeEnd?: boolean;
  refreshing?: boolean;
  onRefresh?: () => void;
  testID?: string;
}

/** A stored markdown body (note preview, OCR text, convo summary, references
 *  stub) rendered like the web's ArtifactPreview, padded so its end clears the
 *  docked action bar. Long bodies are split in blocks (as the reader does). */
export function MarkdownScroll({
  content,
  bottomInset,
  emptyText,
  footer,
  fadeEnd = false,
  refreshing = false,
  onRefresh,
  testID,
}: MarkdownScrollProps) {
  const { colours } = useTheme();
  const blocks = useMemo(() => chunkMarkdown(content), [content]);
  const blank = content.trim().length === 0;
  return (
    <ScrollView
      testID={testID}
      contentContainerStyle={{ paddingHorizontal: 16, paddingTop: 12, paddingBottom: 24 + ACTION_BAR_SPACE + bottomInset }}
      refreshControl={
        onRefresh ? <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colours.primary} colors={[colours.primary]} /> : undefined
      }
    >
      {blank ? (
        emptyText ? (
          <AppText variant="sm" tone="muted">
            {emptyText}
          </AppText>
        ) : null
      ) : (
        blocks.map((text, i) => (
          <View key={i} style={styles.block}>
            <Markdown content={text} variant="reader" />
            {fadeEnd && i === blocks.length - 1 ? <WiPreviewFade /> : null}
          </View>
        ))
      )}
      {footer}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  block: { paddingBottom: 12 },
});
