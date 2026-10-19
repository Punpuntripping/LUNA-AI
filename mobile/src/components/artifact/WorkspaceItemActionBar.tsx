import { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  useWindowDimensions,
  View,
  type ColorValue,
} from "react-native";
import { KeyboardStickyView } from "react-native-keyboard-controller";

import type { ExportFormat, WorkspaceFeedback } from "@contracts";

import { errorMessage } from "@/api/errors";
import { ActionSheet, type SheetAction } from "@/components/ActionSheet";
import { AppText } from "@/components/AppText";
import { copyActions } from "@/components/CopyMenu";
import { Icon, type IconName } from "@/components/Icon";
import { modalCoordinator } from "@/lib/modal-coordinator";
import { showToast } from "@/stores/toast";
import { useTheme } from "@/theme/ThemeProvider";

import { EXPORT_ERROR, nextFeedback, type ViewMode } from "./action-bar";
import { exportAndDeliver } from "./export-file";

/** Height the floating bar takes over the viewer (pill + its gap) — hosts pad
 *  their scroll content by this so the end of the document clears it. */
export const ACTION_BAR_SPACE = 68;

export interface WorkspaceItemActionBarProps {
  /** Markdown نسخ copies (body + «المراجع») — also what PDF / Word export. */
  copyText: string;
  /** Renders «PDF» (PDF / Word); the document title / file name. */
  exportTitle?: string;
  /** With `onModeChange`: the معاينة/تحرير toggle (agent_writing only). */
  mode?: ViewMode;
  onModeChange?: (mode: ViewMode) => void;
  /** The agent holds the lock: تحرير cannot be entered; معاينة stays. */
  editDisabled?: boolean;
  onShare?: () => void;
  onSaveBlog?: () => void;
  /** Set on the shared demo conversation: مشاركة / حفظ كمدونة render
   *  disabled and a tap explains why (the web's hover hint). */
  publishDisabledHint?: string;
  /** With `onFeedback`: the 👍/👎 pair. Tapping the active thumb clears it. */
  feedback?: WorkspaceFeedback;
  onFeedback?: (next: WorkspaceFeedback) => void;
  feedbackPending?: boolean;
  /** Safe-area bottom inset of the host. */
  bottomInset: number;
}

type Menu = "copy" | "export" | null;

/**
 * The agent-output action bar — port of the web's WorkspaceItemActionBar
 * (frontend/components/workspace/WorkspaceItemActionBar.tsx), one standard:
 *
 * - agent_writing: معاينة/تحرير · مشاركة · حفظ كمدونة · نسخ · PDF · 👍 · 👎
 * - agent_search:  مشاركة · حفظ كمدونة · نسخ · PDF · 👍 · 👎
 *
 * A floating pill anchored bottom-left (the end side in RTL) over the viewer,
 * buttons in RTL order. Wider than the screen → it scrolls sideways (the web's
 * phone dock does the same). Not draggable. Every affordance is opt-in by prop,
 * exactly like the web.
 */
export function WorkspaceItemActionBar({
  copyText,
  exportTitle,
  mode,
  onModeChange,
  editDisabled = false,
  onShare,
  onSaveBlog,
  publishDisabledHint,
  feedback = null,
  onFeedback,
  feedbackPending = false,
  bottomInset,
}: WorkspaceItemActionBarProps) {
  const { colours, radius, shadows } = useTheme();
  const { width } = useWindowDimensions();
  const [menu, setMenu] = useState<Menu>(null);
  const [exporting, setExporting] = useState(false);
  const mounted = useRef(true);
  useEffect(
    () => () => {
      mounted.current = false;
    },
    [],
  );

  const canCopy = copyText.trim().length > 0;
  const publishDisabled = !!publishDisabledHint;

  const runExport = async (format: ExportFormat) => {
    if (exporting || exportTitle === undefined) return;
    setExporting(true);
    try {
      // The menu sheet must be fully gone before iOS presents the share sheet.
      const settled = () => new Promise<void>((resolve) => modalCoordinator.whenSettled(resolve));
      const outcome = await exportAndDeliver(format, exportTitle, copyText, settled);
      if (outcome === "saved") showToast("تم حفظ الملف");
    } catch (err) {
      showToast(errorMessage(err, EXPORT_ERROR));
    } finally {
      if (mounted.current) setExporting(false);
    }
  };

  const exportActions: SheetAction[] = [
    { key: "pdf", label: "PDF", hint: "ملف جاهز للطباعة والإرسال", icon: "filePdf", onPress: () => void runExport("pdf") },
    { key: "docx", label: "Word", hint: "مستند قابل للتعديل", icon: "fileWord", onPress: () => void runExport("docx") },
  ];

  const publishPress = (action: (() => void) | undefined) => () => {
    if (publishDisabledHint) showToast(publishDisabledHint);
    else action?.();
  };

  return (
    <>
      {/* The dock rides on top of the keyboard while a draft is edited (same
          offsets as the chat composer), so معاينة stays one tap away. It has
          the bar's real height: Android drops touches outside a parent. */}
      <KeyboardStickyView
        offset={{ closed: 0, opened: bottomInset }}
        pointerEvents="box-none"
        style={[styles.dock, { height: bottomInset + ACTION_BAR_SPACE }]}
      >
        <View
          style={[
            styles.pill,
            {
              bottom: bottomInset + 8,
              maxWidth: width - 24,
              backgroundColor: colours.surface1,
              borderColor: colours.border,
              borderRadius: radius.xl,
              boxShadow: shadows.shadowMd,
            },
          ]}
          testID="wi-action-bar"
        >
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.row} bounces={false}>
            {mode !== undefined && onModeChange ? (
              <View
                accessibilityRole="tablist"
                accessibilityLabel="وضع العرض"
                style={[styles.toggle, { borderColor: colours.border, backgroundColor: colours.surface2, borderRadius: radius.default }]}
              >
                <ToggleTab
                  label="تحرير"
                  icon="edit"
                  selected={mode === "edit"}
                  disabled={editDisabled}
                  onPress={() => onModeChange("edit")}
                  testID="wi-mode-edit"
                />
                <ToggleTab
                  label="معاينة"
                  icon="preview"
                  selected={mode === "preview"}
                  onPress={() => onModeChange("preview")}
                  testID="wi-mode-preview"
                />
              </View>
            ) : null}

            {onShare ? (
              <BarButton
                label="مشاركة"
                a11yLabel="مشاركة عبر رابط"
                icon="share"
                looksDisabled={publishDisabled}
                hint={publishDisabledHint}
                onPress={publishPress(onShare)}
                testID="wi-share"
              />
            ) : null}

            {onSaveBlog ? (
              <BarButton
                label="حفظ كمدونة"
                icon="bookmarkAdd"
                looksDisabled={publishDisabled}
                hint={publishDisabledHint}
                onPress={publishPress(onSaveBlog)}
                testID="wi-save-blog"
              />
            ) : null}

            <BarButton label="نسخ" icon="copy" disabled={!canCopy} onPress={() => setMenu("copy")} testID="wi-copy" />

            {exportTitle !== undefined ? (
              <BarButton
                label="PDF"
                a11yLabel={exporting ? "جارٍ تجهيز الملف" : "تنزيل كملف PDF أو Word"}
                icon="download"
                busy={exporting}
                disabled={!canCopy || exporting}
                onPress={() => setMenu("export")}
                testID="wi-export"
              />
            ) : null}

            {onFeedback ? (
              <>
                <ThumbButton
                  label="إعجاب"
                  icon={feedback === "up" ? "thumbUpFill" : "thumbUp"}
                  active={feedback === "up"}
                  activeColour={colours.primary}
                  disabled={feedbackPending}
                  onPress={() => onFeedback(nextFeedback(feedback, "up"))}
                  testID="wi-like"
                />
                <ThumbButton
                  label="عدم إعجاب"
                  icon={feedback === "down" ? "thumbDownFill" : "thumbDown"}
                  active={feedback === "down"}
                  activeColour={colours.destructive}
                  disabled={feedbackPending}
                  onPress={() => onFeedback(nextFeedback(feedback, "down"))}
                  testID="wi-dislike"
                />
              </>
            ) : null}
          </ScrollView>
        </View>
      </KeyboardStickyView>

      <ActionSheet
        visible={menu !== null}
        onClose={() => setMenu(null)}
        actions={menu === "export" ? exportActions : copyActions(() => copyText)}
        testID={menu === "export" ? "wi-export-sheet" : "wi-copy-sheet"}
      />
    </>
  );
}

interface BarButtonProps {
  label: string;
  /** Screen-reader label when it differs from the visible text. */
  a11yLabel?: string;
  icon: IconName;
  onPress: () => void;
  disabled?: boolean;
  /** Dimmed and announced disabled, but still tappable (shows `hint`). */
  looksDisabled?: boolean;
  hint?: string;
  busy?: boolean;
  testID?: string;
}

/** Ghost icon + label (the web's `variant="ghost" size="sm"` bar button). */
function BarButton({ label, a11yLabel, icon, onPress, disabled = false, looksDisabled = false, hint, busy = false, testID }: BarButtonProps) {
  const { colours } = useTheme();
  const dim = disabled || looksDisabled;
  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      accessibilityLabel={a11yLabel ?? label}
      accessibilityHint={hint}
      accessibilityState={{ disabled: dim, busy }}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [styles.button, { opacity: dim ? 0.45 : pressed ? 0.6 : 1 }]}
    >
      {busy ? <ActivityIndicator size="small" color={colours.textMuted} /> : <Icon name={icon} size={16} colour={colours.textMuted} />}
      <AppText variant="sm" style={{ color: colours.textMuted }} numberOfLines={1}>
        {label}
      </AppText>
    </Pressable>
  );
}

interface ToggleTabProps {
  label: string;
  icon: IconName;
  selected: boolean;
  disabled?: boolean;
  onPress: () => void;
  testID?: string;
}

function ToggleTab({ label, icon, selected, disabled = false, onPress, testID }: ToggleTabProps) {
  const { colours, radius } = useTheme();
  const fg = selected ? colours.primaryFg : colours.textMuted;
  return (
    <Pressable
      testID={testID}
      accessibilityRole="tab"
      accessibilityLabel={label}
      accessibilityState={{ selected, disabled }}
      disabled={disabled}
      hitSlop={{ top: 2, bottom: 2 }}
      onPress={onPress}
      style={({ pressed }) => [
        styles.tab,
        {
          backgroundColor: selected ? colours.primary : "transparent",
          borderRadius: radius.sm,
          opacity: disabled ? 0.45 : pressed ? 0.75 : 1,
        },
      ]}
    >
      <Icon name={icon} size={14} colour={fg} />
      <AppText variant="sm" weight={selected ? 600 : 400} style={{ color: fg }} numberOfLines={1}>
        {label}
      </AppText>
    </Pressable>
  );
}

interface ThumbButtonProps {
  label: string;
  icon: IconName;
  active: boolean;
  activeColour: ColorValue;
  disabled: boolean;
  onPress: () => void;
  testID?: string;
}

function ThumbButton({ label, icon, active, activeColour, disabled, onPress, testID }: ThumbButtonProps) {
  const { colours } = useTheme();
  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      accessibilityLabel={label}
      // `selected` is RN's pressed/checked state for a toggle button.
      accessibilityState={{ selected: active, disabled }}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [styles.thumb, { opacity: disabled ? 0.45 : pressed ? 0.6 : 1 }]}
    >
      <Icon name={icon} size={Platform.OS === "ios" ? 17 : 18} colour={active ? activeColour : colours.textMuted} />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  dock: { position: "absolute", start: 0, end: 0, bottom: 0 },
  pill: {
    position: "absolute",
    end: 12,
    borderWidth: StyleSheet.hairlineWidth,
    overflow: "hidden",
    direction: "rtl",
  },
  row: { flexDirection: "row", alignItems: "center", gap: 2, paddingHorizontal: 4, paddingVertical: 4 },
  toggle: { flexDirection: "row", alignItems: "center", gap: 2, padding: 2, borderWidth: StyleSheet.hairlineWidth },
  tab: { flexDirection: "row", alignItems: "center", gap: 4, minHeight: 40, paddingHorizontal: 10 },
  button: { flexDirection: "row", alignItems: "center", gap: 6, minHeight: 44, paddingHorizontal: 10 },
  thumb: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
});
