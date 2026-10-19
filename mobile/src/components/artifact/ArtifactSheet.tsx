import { useEffect, useState } from "react";
import { Modal, Platform, Pressable, StyleSheet, useWindowDimensions, View } from "react-native";
import { Gesture, GestureDetector, GestureHandlerRootView } from "react-native-gesture-handler";
import Animated, { useAnimatedStyle, useSharedValue, withSpring, withTiming } from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { scheduleOnRN } from "react-native-worklets";

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

/** Drag distance (dp) on the header that dismisses the sheet on Android. */
const DISMISS_DRAG = 90;
/** …or a downward fling at least this fast (dp/s), whatever the distance. */
const DISMISS_VELOCITY = 900;

/**
 * An agent output (`agent_search` / `agent_writing`) over the chat (plan 05,
 * owner decisions 2026-10-15). The chat stays mounted underneath, so dismissing
 * returns to it at the same scroll.
 *
 * iOS: a native page sheet — swipe-down dismisses (`allowSwipeDismissal`).
 * Android: a full-height slide-up modal; the hardware back button, «إغلاق», or
 * dragging the header (grabber + title row) down dismiss it. The drag is a
 * react-native-gesture-handler Pan (native recogniser — the old JS
 * PanResponder lost the touch to the header's children / the modal window on
 * Android). Gestures inside a RN Modal need their own GestureHandlerRootView
 * on Android (RNGH docs, "Modals").
 *
 * Opening a source (`[n]` / «عرض المصدر») CLOSES the sheet and pushes the
 * reader route: a RN Modal draws above the whole navigator, so a pushed screen
 * would otherwise open hidden behind it.
 */
export function ArtifactSheet({ itemId, onClose }: ArtifactSheetProps) {
  const { colours, scheme } = useTheme();
  const insets = useSafeAreaInsets();
  const { height } = useWindowDimensions();
  const open = itemId !== null;

  // Keep the last id while the close animation runs, so the body does not
  // flash empty on its way down (state adjusted during render, not a ref).
  const [lastId, setLastId] = useState<string | null>(itemId);
  if (itemId !== null && itemId !== lastId) setLastId(itemId);
  const activeId = itemId ?? lastId;

  const ios = Platform.OS === "ios";

  // Android drag-to-dismiss: the sheet follows the finger, then either slides
  // away (and closes) or springs back.
  const dragY = useSharedValue(0);
  useEffect(() => {
    if (open) dragY.set(0);
  }, [open, dragY]);

  const drag = Gesture.Pan()
    .enabled(!ios)
    .activeOffsetY(10)
    .failOffsetX([-24, 24])
    .onUpdate((e) => {
      dragY.set(Math.max(0, e.translationY));
    })
    .onEnd((e) => {
      if (e.translationY > DISMISS_DRAG || e.velocityY > DISMISS_VELOCITY) {
        dragY.set(withTiming(height, { duration: 180 }, (finished) => {
          if (finished) scheduleOnRN(onClose);
        }));
      } else {
        dragY.set(withSpring(0, { damping: 20, stiffness: 220 }));
      }
    });

  const sheetStyle = useAnimatedStyle(() => ({ transform: [{ translateY: dragY.get() }] }));

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
      <GestureHandlerRootView style={styles.fill}>
        <Animated.View
          style={[
            styles.root,
            {
              backgroundColor: colours.canvas,
              paddingTop: ios ? 0 : insets.top,
            },
            sheetStyle,
          ]}
        >
          <GestureDetector gesture={drag}>
            <View style={[styles.header, { borderBottomColor: colours.border }]} testID="artifact-drag-handle">
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
          </GestureDetector>
          {activeId ? (
            <ArtifactBody
              itemId={activeId}
              bottomInset={insets.bottom}
              onLeave={onClose}
            />
          ) : null}
        </Animated.View>
      </GestureHandlerRootView>
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
  fill: { flex: 1 },
  root: { flex: 1, direction: "rtl" },
  // Taller grab zone than the visible pill so the drag is easy to start.
  header: { borderBottomWidth: StyleSheet.hairlineWidth, paddingBottom: 8 },
  grabber: { alignSelf: "center", width: 40, height: 5, borderRadius: 3, marginTop: 10, marginBottom: 10 },
  headerRow: { flexDirection: "row", alignItems: "center", paddingHorizontal: 16, gap: 12 },
  title: { flex: 1 },
  close: { minHeight: 44, justifyContent: "center", paddingHorizontal: 4 },
});
