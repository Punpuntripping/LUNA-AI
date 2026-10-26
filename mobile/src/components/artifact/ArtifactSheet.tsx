import { useIsFocused } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import { Modal, Platform, Pressable, StatusBar, StyleSheet, useWindowDimensions, View } from "react-native";
import { Gesture, GestureDetector, GestureHandlerRootView } from "react-native-gesture-handler";
import Animated, { useAnimatedStyle, useSharedValue, withSpring, withTiming } from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { scheduleOnRN } from "react-native-worklets";

import { AppText } from "@/components/AppText";
import { ToastHost } from "@/components/ToastHost";
import { modalCoordinator, useSheetPresence } from "@/lib/modal-coordinator";
import { useTheme } from "@/theme/ThemeProvider";

import { ArtifactBody, type ScrollAnchor } from "./ArtifactBody";
import { useWorkspaceItem } from "./use-artifact";

export interface ArtifactSheetProps {
  /** Part of the chat's contract; reserved for 2b («من WI-n» → sibling item). */
  conversationId: string;
  /** `null` = closed. */
  itemId: string | null;
  /** The shared demo conversation (action bar: no 👍/👎, publish disabled). */
  isDemo: boolean;
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
 * Presenting (QA N1, blank white sheet): the Modal is shown only once
 *   - the host screen is focused, and
 *   - no other Modal is open or still animating out (`modalCoordinator`).
 * Its content (and the drag offset) is remounted on every presentation, so a
 * previous drag-to-dismiss can never leave the new sheet translated off-screen.
 *
 * Opening a source (`[n]` / «عرض المصدر») does NOT close the sheet (QA N5): it
 * pushes the reader route, the host screen loses focus and the Modal hides
 * (a RN Modal draws above the whole navigator, so the reader would otherwise
 * open hidden behind it). Back → focus returns → the sheet shows again at the
 * row the user left from.
 */
export function ArtifactSheet({ itemId, isDemo, onClose }: ArtifactSheetProps) {
  const open = itemId !== null;
  const focused = useIsFocused();
  const wanted = open && focused;
  // Not `open` in the coordinator (the export flow inside waits on
  // whenSettled) — a separate signal so «رسائل ريحان» never pops over it.
  useSheetPresence(wanted);

  // Keep the last id while the close animation runs, so the body does not
  // flash empty on its way down (state adjusted during render, not a ref).
  const [lastId, setLastId] = useState<string | null>(itemId);
  if (itemId !== null && itemId !== lastId) setLastId(itemId);
  const activeId = itemId ?? lastId;

  // `wanted` → wait for the other modals to settle → visible. Hiding is
  // immediate. Each presentation gets a new `seq` (remounts the content).
  const [presented, setPresented] = useState<{ visible: boolean; seq: number }>({ visible: false, seq: 0 });
  useEffect(() => {
    if (!wanted) return undefined;
    const cancel = modalCoordinator.whenSettled(() => setPresented((p) => ({ visible: true, seq: p.seq + 1 })));
    return () => {
      cancel();
      setPresented((p) => (p.visible ? { ...p, visible: false } : p));
    };
  }, [wanted]);
  const visible = wanted && presented.visible;

  // Where the user was when they left for the reader; used by the next
  // presentation of the same item, dropped when the sheet really closes.
  const [anchor, setAnchor] = useState<{ itemId: string; anchor: ScrollAnchor } | null>(null);
  if (!open && anchor !== null) setAnchor(null);
  const onLeave = useCallback((id: string, a: ScrollAnchor | null) => {
    setAnchor(a ? { itemId: id, anchor: a } : null);
  }, []);
  const restore = activeId && anchor?.itemId === activeId ? anchor.anchor : null;

  // QA F6: on Android the sheet is its own Dialog window, which copies the
  // activity's status-bar appearance once at creation and could come up with
  // dark icons over the dark canvas. Once shown (the window is registered with
  // RN's StatusBar module as an extra window by then), push the theme's style
  // again — StatusBar.setBarStyle applies it to the activity AND the dialog.
  const { scheme } = useTheme();
  const [shown, setShown] = useState(false);
  if (!visible && shown) setShown(false);
  useEffect(() => {
    if (Platform.OS !== "android" || !shown) return;
    StatusBar.setBarStyle(scheme === "dark" ? "light-content" : "dark-content", false);
  }, [shown, scheme]);

  return (
    <Modal
      visible={visible}
      animationType="slide"
      presentationStyle={Platform.OS === "ios" ? "pageSheet" : undefined}
      allowSwipeDismissal
      statusBarTranslucent
      navigationBarTranslucent
      onRequestClose={onClose}
      onShow={() => setShown(true)}
    >
      <SheetContent
        key={presented.seq}
        activeId={activeId}
        isDemo={isDemo}
        restore={restore}
        onLeave={onLeave}
        onClose={onClose}
      />
    </Modal>
  );
}

interface SheetContentProps {
  activeId: string | null;
  isDemo: boolean;
  restore: ScrollAnchor | null;
  onLeave: (itemId: string, anchor: ScrollAnchor | null) => void;
  onClose: () => void;
}

/** The sheet's window content. Mounted fresh per presentation: the drag
 *  offset starts at 0 every time (it used to live in the parent and keep the
 *  dismissed `height` across opens — N1). */
function SheetContent({ activeId, isDemo, restore, onLeave, onClose }: SheetContentProps) {
  const { colours, scheme } = useTheme();
  const insets = useSafeAreaInsets();
  const { height } = useWindowDimensions();
  const ios = Platform.OS === "ios";

  // Android drag-to-dismiss: the sheet follows the finger, then either slides
  // away (and closes) or springs back.
  const dragY = useSharedValue(0);

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

  // Never trust a non-finite / negative offset — the sheet must not be able
  // to end up mounted but off-screen.
  const sheetStyle = useAnimatedStyle(() => {
    const y = dragY.get();
    return { transform: [{ translateY: Number.isFinite(y) && y > 0 ? y : 0 }] };
  });

  return (
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
            isDemo={isDemo}
            restore={restore}
            onLeave={(a) => onLeave(activeId, a)}
          />
        ) : null}
        {/* The app's ToastHost sits under this Modal; «تم النسخ» etc. from the
            action bar must show on top of the sheet. */}
        <ToastHost />
      </Animated.View>
    </GestureHandlerRootView>
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
