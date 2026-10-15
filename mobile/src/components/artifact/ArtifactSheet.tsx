import { useMemo, useState } from "react";
import { Modal, PanResponder, Platform, Pressable, StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { AppText } from "@/components/AppText";
import { useTheme } from "@/theme/ThemeProvider";

import { ArtifactBody } from "./ArtifactBody";
import { useWorkspaceItem } from "./use-artifact";

export interface ArtifactSheetProps {
  /** Part of the chat's contract; reserved for 2b («من WI-n» → sibling item). */
  conversationId: string;
  /** `null` = closed. */
  itemId: string | null;
  onClose: () => void;
}

/** Drag distance (dp) on the grabber that dismisses the sheet on Android. */
const DISMISS_DRAG = 80;

/**
 * An agent output (`agent_search` / `agent_writing`) over the chat (plan 05,
 * owner decisions 2026-10-15). The chat stays mounted underneath, so dismissing
 * returns to it at the same scroll.
 *
 * iOS: a native page sheet — swipe-down dismisses (`allowSwipeDismissal`).
 * Android: a full-height slide-up modal; the hardware back button, a downward
 * drag on the grabber, or «إغلاق» dismiss it.
 *
 * Opening a source (`[n]` / «عرض المصدر») CLOSES the sheet and pushes the
 * reader route: a RN Modal draws above the whole navigator, so a pushed screen
 * would otherwise open hidden behind it.
 */
export function ArtifactSheet({ itemId, onClose }: ArtifactSheetProps) {
  const { colours, scheme } = useTheme();
  const insets = useSafeAreaInsets();
  const open = itemId !== null;

  // Keep the last id while the close animation runs, so the body does not
  // flash empty on its way down (state adjusted during render, not a ref).
  const [lastId, setLastId] = useState<string | null>(itemId);
  if (itemId !== null && itemId !== lastId) setLastId(itemId);
  const activeId = itemId ?? lastId;

  const grabber = useMemo(
    () =>
      PanResponder.create({
        onMoveShouldSetPanResponder: (_e, g) => g.dy > 8 && Math.abs(g.dy) > Math.abs(g.dx),
        onPanResponderRelease: (_e, g) => {
          if (g.dy > DISMISS_DRAG) onClose();
        },
      }),
    [onClose],
  );

  const ios = Platform.OS === "ios";

  return (
    <Modal
      visible={open}
      animationType="slide"
      presentationStyle={ios ? "pageSheet" : undefined}
      allowSwipeDismissal
      statusBarTranslucent
      navigationBarTranslucent
      onRequestClose={onClose}
    >
      <View
        style={[
          styles.root,
          {
            backgroundColor: colours.canvas,
            paddingTop: ios ? 0 : insets.top,
          },
        ]}
      >
        <View {...(ios ? {} : grabber.panHandlers)} style={[styles.header, { borderBottomColor: colours.border }]}>
          <View style={[styles.grabber, { backgroundColor: scheme === "dark" ? colours.borderStrong : colours.border }]} />
          <View style={styles.headerRow}>
            <SheetTitle itemId={activeId} />
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="إغلاق"
              onPress={onClose}
              hitSlop={8}
              style={({ pressed }) => [styles.close, { opacity: pressed ? 0.6 : 1 }]}
              testID="artifact-close"
            >
              <AppText variant="base" weight={600} tone="link">
                إغلاق
              </AppText>
            </Pressable>
          </View>
        </View>
        {activeId ? (
          <ArtifactBody
            itemId={activeId}
            bottomInset={insets.bottom}
            onLeave={onClose}
          />
        ) : null}
      </View>
    </Modal>
  );
}

function SheetTitle({ itemId }: { itemId: string | null }) {
  const { data } = useWorkspaceItem(itemId);
  return (
    <AppText variant="lg" weight={600} numberOfLines={1} style={styles.title} accessibilityRole="header">
      {data?.title || "المخرج"}
    </AppText>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, direction: "rtl" },
  header: { borderBottomWidth: StyleSheet.hairlineWidth, paddingBottom: 8 },
  grabber: { alignSelf: "center", width: 40, height: 5, borderRadius: 3, marginTop: 8, marginBottom: 6 },
  headerRow: { flexDirection: "row", alignItems: "center", paddingHorizontal: 16, gap: 12 },
  title: { flex: 1 },
  close: { minHeight: 44, justifyContent: "center", paddingHorizontal: 4 },
});
