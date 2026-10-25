import { useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  useWindowDimensions,
  View,
  type ColorValue,
  type LayoutChangeEvent,
} from "react-native";
import { KeyboardStickyView } from "react-native-keyboard-controller";

import type { ExportFormat, WorkspaceFeedback } from "@contracts";

import { errorMessage } from "@/api/errors";
import { ActionSheet, type SheetAction } from "@/components/ActionSheet";
import {
  DELETE_CONFIRM,
  VISIBILITY_HINT,
  visibilityActionLabel,
} from "@/components/workspace/workspace-model";
import { AppText } from "@/components/AppText";
import { copyActions } from "@/components/CopyMenu";
import { Icon, type IconName } from "@/components/Icon";
import { modalCoordinator } from "@/lib/modal-coordinator";
import { showToast } from "@/stores/toast";
import { useTheme } from "@/theme/ThemeProvider";

import {
  compressionLadder,
  compressionsAt,
  EXPORT_ERROR,
  hexWithAlpha,
  nextFitLevel,
  nextFeedback,
  type ViewMode,
} from "./action-bar";
import { exportAndDeliver } from "./export-file";

/** The dock's own height above the safe-area inset — the web's `h-[3rem]`. */
export const ACTION_BAR_HEIGHT = 48;

/** Height the docked bar takes over the viewer, EXCLUDING the safe-area inset
 *  (hosts add `bottomInset` themselves) — hosts pad their scroll content by
 *  this so the end of the document, «المراجع» and the disclaimer clear it. */
export const ACTION_BAR_SPACE = ACTION_BAR_HEIGHT;

/** Web `h-9` / `w-9`: button height and icon-only width. */
const CONTROL = 36;
/** Half of the web's `gap-1`: each control's transparent side padding, so the
 *  hit area spans the gap and the full bar height (48dp) without overlapping. */
const HALF_GAP = 2;
/** Give up hiding the bar while it measures after this long (never stuck invisible). */
const FIT_TIMEOUT_MS = 400;

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
  /** نسخ's visible label (the attachment viewer: «نسخ النص المستخرج»). */
  copyLabel?: string;
  /** With at least one handler: the trailing «المزيد» menu (workspace screen,
   *  plan 05 §W3) — إخفاء/إظهار and حذف (confirmed). Omit to render none. */
  manage?: ManageActions;
}

export interface ManageActions {
  /** Current `is_visible` (names the toggle: إخفاء عن ريحان / إظهار لريحان). */
  isVisible: boolean;
  onToggleVisibility?: () => void;
  /** Runs after the confirm step. */
  onDelete?: () => void;
  /** A request in flight: the «المزيد» button shows a spinner. */
  pending?: boolean;
}

type Menu = "copy" | "export" | "manage" | "confirmDelete" | null;

interface Fit {
  key: string;
  level: number;
  /** Measured and fitting (or out of steps) — the bar is shown. */
  settled: boolean;
}

/**
 * The agent-output action bar — port of the web's WorkspaceItemActionBar
 * (frontend/components/workspace/WorkspaceItemActionBar.tsx), one standard:
 *
 * - agent_writing: معاينة/تحرير · مشاركة · حفظ كمدونة · نسخ · PDF · 👍 · 👎
 * - agent_search:  مشاركة · حفظ كمدونة · نسخ · PDF · 👍 · 👎
 * - note: معاينة/تحرير · نسخ; attachment / convo_context / references: نسخ
 *
 * App addition (no web button yet, plan 05 §W3): an icon-only «المزيد» at the
 * end with إخفاء/إظهار and حذف (confirmed) — workspace screen only, never on
 * the demo conversation.
 *
 * Rendered as the web's PHONE layout (`max-md:` "docked bar"): a full-width bar
 * fixed to the bottom, 48dp + safe-area inset, top border, opaque canvas,
 * `gap-1 px-2`, 36dp controls, `text-xs` labels, icon-only thumbs. Buttons in
 * RTL order. Where the web scrolls the row sideways, the app drops labels one
 * control at a time until the row fits (see `compressionLadder`) — the label
 * stays as the accessibilityLabel and a long-press toast. Every affordance is
 * opt-in by prop, exactly like the web.
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
  copyLabel = "نسخ",
  manage,
}: WorkspaceItemActionBarProps) {
  const { colours, radius } = useTheme();
  const { width, fontScale } = useWindowDimensions();
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
  const showToggle = mode !== undefined && onModeChange !== undefined;

  // --- fit: drop labels until the row is no wider than the screen ----------
  const ladder = useMemo(
    () =>
      compressionLadder({
        toggle: showToggle,
        share: onShare !== undefined,
        saveBlog: onSaveBlog !== undefined,
        export: exportTitle !== undefined,
      }),
    [showToggle, onShare, onSaveBlog, exportTitle],
  );
  const fitKey = `${width}|${fontScale}|${ladder.join(",")}`;
  const [fitState, setFit] = useState<Fit>({ key: fitKey, level: 0, settled: false });
  // Screen / font scale / controls changed → measure again from full labels.
  const fit = fitState.key === fitKey ? fitState : { key: fitKey, level: 0, settled: false };
  if (fitState.key !== fitKey) setFit(fit);
  const compact = compressionsAt(ladder, fit.level);

  const viewportWidth = useRef(0);
  const contentWidth = useRef(0);
  const evaluate = () => {
    const v = viewportWidth.current;
    const c = contentWidth.current;
    if (v <= 0 || c <= 0) return;
    setFit((prev) => {
      if (prev.key !== fitKey) return prev;
      const level = nextFitLevel(prev.level, ladder.length, c, v);
      if (level !== prev.level) return { ...prev, level };
      return prev.settled ? prev : { ...prev, settled: true };
    });
  };
  useEffect(() => {
    const timer = setTimeout(() => {
      setFit((prev) => (prev.key === fitKey && !prev.settled ? { ...prev, settled: true } : prev));
    }, FIT_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [fitKey]);

  // A ref, not the state: two taps inside one render must not start two renders.
  const exportBusy = useRef(false);
  const runExport = async (format: ExportFormat) => {
    if (exportBusy.current || exportTitle === undefined) return;
    exportBusy.current = true;
    setExporting(true);
    try {
      // The menu sheet must be fully gone before iOS presents the share sheet.
      const settled = () => new Promise<void>((resolve) => modalCoordinator.whenSettled(resolve));
      const outcome = await exportAndDeliver(format, exportTitle, copyText, settled);
      if (outcome === "saved") showToast("تم حفظ الملف");
    } catch (err) {
      showToast(errorMessage(err, EXPORT_ERROR));
    } finally {
      exportBusy.current = false;
      if (mounted.current) setExporting(false);
    }
  };

  const exportActions: SheetAction[] = [
    { key: "pdf", label: "PDF", hint: "ملف جاهز للطباعة والإرسال", icon: "filePdf", onPress: () => void runExport("pdf") },
    { key: "docx", label: "Word", hint: "مستند قابل للتعديل", icon: "fileWord", onPress: () => void runExport("docx") },
  ];

  const showManage = manage !== undefined && (manage.onToggleVisibility !== undefined || manage.onDelete !== undefined);
  const manageActions: SheetAction[] = [];
  if (manage?.onToggleVisibility) {
    const toggle = manage.onToggleVisibility;
    manageActions.push({
      key: "visibility",
      label: visibilityActionLabel(manage.isVisible),
      hint: manage.isVisible ? VISIBILITY_HINT.hide : VISIBILITY_HINT.show,
      icon: manage.isVisible ? "hide" : "preview",
      onPress: () => {
        setMenu(null);
        toggle();
      },
    });
  }
  if (manage?.onDelete) {
    manageActions.push({ key: "delete", label: "حذف", icon: "trash", destructive: true, onPress: () => setMenu("confirmDelete") });
  }
  const confirmActions: SheetAction[] = [
    {
      key: "confirm",
      label: DELETE_CONFIRM.confirm,
      icon: "trash",
      destructive: true,
      onPress: () => {
        setMenu(null);
        manage?.onDelete?.();
      },
    },
  ];

  const sheetActions =
    menu === "export"
      ? exportActions
      : menu === "manage"
        ? manageActions
        : menu === "confirmDelete"
          ? confirmActions
          : copyActions(() => copyText);

  const publishPress = (action: (() => void) | undefined) => () => {
    if (publishDisabledHint) showToast(publishDisabledHint);
    else action?.();
  };

  return (
    <>
      {/* The dock rides on top of the keyboard while a draft is edited (same
          offsets as the chat composer), so معاينة stays one tap away; its
          safe-area padding then tucks behind the keyboard. */}
      <KeyboardStickyView
        offset={{ closed: 0, opened: bottomInset }}
        style={[
          styles.dock,
          {
            height: ACTION_BAR_HEIGHT + bottomInset,
            paddingBottom: bottomInset,
            // Web: bg-background/95 + backdrop-blur. Without a blur module the
            // 95% let the document read through the bar (QA F4) — the same
            // background token, opaque, in light and dark.
            backgroundColor: colours.canvas,
            borderTopColor: colours.border,
          },
        ]}
      >
        <ScrollView
          horizontal
          // Last resort only (tiny screen + largest font): every label already dropped.
          showsHorizontalScrollIndicator={false}
          bounces={false}
          keyboardShouldPersistTaps="handled"
          onLayout={(e: LayoutChangeEvent) => {
            viewportWidth.current = e.nativeEvent.layout.width;
            evaluate();
          }}
          onContentSizeChange={(w) => {
            contentWidth.current = w;
            evaluate();
          }}
          style={[styles.scroller, { opacity: fit.settled ? 1 : 0 }]}
          contentContainerStyle={styles.row}
          testID="wi-action-bar"
        >
          {showToggle ? (
            <View style={styles.slot}>
              <View
                accessibilityRole="tablist"
                accessibilityLabel="وضع العرض"
                style={[
                  styles.toggle,
                  {
                    borderColor: colours.border,
                    backgroundColor: hexWithAlpha(colours.surface2, 0.3),
                    borderRadius: radius.default,
                  },
                ]}
              >
                <ToggleTab
                  label="تحرير"
                  icon="edit"
                  selected={mode === "edit"}
                  iconOnly={compact.has("toggleAll") || (compact.has("toggleUnselected") && mode !== "edit")}
                  disabled={editDisabled}
                  onPress={() => onModeChange("edit")}
                  testID="wi-mode-edit"
                />
                <ToggleTab
                  label="معاينة"
                  icon="preview"
                  selected={mode === "preview"}
                  iconOnly={compact.has("toggleAll") || (compact.has("toggleUnselected") && mode !== "preview")}
                  onPress={() => onModeChange("preview")}
                  testID="wi-mode-preview"
                />
              </View>
            </View>
          ) : null}

          {onShare ? (
            <BarButton
              label="مشاركة"
              a11yLabel="مشاركة عبر رابط"
              icon="share"
              iconOnly={compact.has("share")}
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
              iconOnly={compact.has("saveBlog")}
              looksDisabled={publishDisabled}
              hint={publishDisabledHint}
              onPress={publishPress(onSaveBlog)}
              testID="wi-save-blog"
            />
          ) : null}

          <BarButton
            label={copyLabel}
            icon="copy"
            iconOnly={compact.has("copy")}
            disabled={!canCopy}
            onPress={() => setMenu("copy")}
            testID="wi-copy"
          />

          {exportTitle !== undefined ? (
            <BarButton
              label="PDF"
              a11yLabel={exporting ? "جارٍ تجهيز الملف" : "تنزيل كملف PDF أو Word"}
              icon="download"
              iconOnly={compact.has("export")}
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

          {showManage ? (
            <BarButton
              label="المزيد"
              a11yLabel="خيارات العنصر"
              icon="more"
              iconOnly
              busy={manage?.pending === true}
              disabled={manage?.pending === true}
              onPress={() => setMenu("manage")}
              testID="wi-more"
            />
          ) : null}
        </ScrollView>
      </KeyboardStickyView>

      <ActionSheet
        visible={menu !== null}
        onClose={() => setMenu(null)}
        actions={sheetActions}
        title={menu === "confirmDelete" ? DELETE_CONFIRM.title : undefined}
        message={menu === "confirmDelete" ? DELETE_CONFIRM.message : undefined}
        // The manage rows decide what comes next (the confirm step).
        keepOpenOnPress={menu === "manage" || menu === "confirmDelete"}
        testID={menu === "export" ? "wi-export-sheet" : menu === "manage" ? "wi-manage-sheet" : menu === "confirmDelete" ? "wi-delete-confirm" : "wi-copy-sheet"}
      />
    </>
  );
}

interface BarButtonProps {
  label: string;
  /** Screen-reader label when it differs from the visible text. */
  a11yLabel?: string;
  icon: IconName;
  /** Label dropped to fit the screen: a square 36dp icon button. */
  iconOnly?: boolean;
  onPress: () => void;
  disabled?: boolean;
  /** Dimmed and announced disabled, but still tappable (shows `hint`). */
  looksDisabled?: boolean;
  hint?: string;
  busy?: boolean;
  testID?: string;
}

/** Ghost icon + label — the web's `variant="ghost" size="sm"` bar button at
 *  `max-md:h-9 gap-1.5 px-2 text-xs`. */
function BarButton({
  label,
  a11yLabel,
  icon,
  iconOnly = false,
  onPress,
  disabled = false,
  looksDisabled = false,
  hint,
  busy = false,
  testID,
}: BarButtonProps) {
  const { colours, radius } = useTheme();
  // Busy is not "disabled-looking": the render behind PDF / Word takes ~10 s
  // and a dimmed muted spinner read as nothing happening (QA F10). Taps stay
  // blocked through `disabled`.
  const dim = (disabled && !busy) || looksDisabled;
  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      accessibilityLabel={a11yLabel ?? label}
      accessibilityHint={hint}
      accessibilityState={{ disabled: dim, busy }}
      disabled={disabled}
      onPress={onPress}
      // The web's hover tooltip, for a control whose label was dropped.
      onLongPress={iconOnly ? () => showToast(label) : undefined}
      style={[styles.slot, { opacity: dim ? 0.45 : 1 }]}
    >
      {({ pressed }) => (
        <View
          style={[
            iconOnly ? styles.iconFace : styles.buttonFace,
            { borderRadius: radius.default, backgroundColor: pressed ? colours.surface2 : "transparent" },
          ]}
        >
          {busy ? (
            <ActivityIndicator size="small" color={colours.primary} />
          ) : (
            <Icon name={icon} size={iconOnly ? 18 : 15} colour={colours.textMuted} />
          )}
          {iconOnly ? null : (
            <AppText variant="xs" style={{ color: colours.textMuted }} numberOfLines={1}>
              {label}
            </AppText>
          )}
        </View>
      )}
    </Pressable>
  );
}

interface ToggleTabProps {
  label: string;
  icon: IconName;
  selected: boolean;
  iconOnly: boolean;
  disabled?: boolean;
  onPress: () => void;
  testID?: string;
}

/** One segment of معاينة/تحرير — web `max-md:h-9 gap-1 px-2 text-xs`, the
 *  selected one in `variant="default"` (primary). */
function ToggleTab({ label, icon, selected, iconOnly, disabled = false, onPress, testID }: ToggleTabProps) {
  const { colours, radius } = useTheme();
  const fg = selected ? colours.primaryFg : colours.textMuted;
  return (
    <Pressable
      testID={testID}
      accessibilityRole="tab"
      accessibilityLabel={label}
      accessibilityState={{ selected, disabled }}
      disabled={disabled}
      // 36dp segment + 6 + 6 = the bar's full 48dp.
      hitSlop={{ top: 6, bottom: 6 }}
      onPress={onPress}
      onLongPress={iconOnly ? () => showToast(label) : undefined}
      style={({ pressed }) => [
        iconOnly ? styles.tabIcon : styles.tab,
        {
          backgroundColor: selected ? colours.primary : pressed ? colours.surface2 : "transparent",
          borderRadius: radius.sm,
          opacity: disabled ? 0.45 : 1,
        },
      ]}
    >
      <Icon name={icon} size={iconOnly ? 16 : 13} colour={fg} />
      {iconOnly ? null : (
        <AppText variant="xs" weight={selected ? 600 : 400} style={{ color: fg }} numberOfLines={1}>
          {label}
        </AppText>
      )}
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

/** Icon-only `size="icon"` thumb — web `max-md:h-9 max-md:w-9`. */
function ThumbButton({ label, icon, active, activeColour, disabled, onPress, testID }: ThumbButtonProps) {
  const { colours, radius } = useTheme();
  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      accessibilityLabel={label}
      // `selected` is RN's pressed/checked state for a toggle button.
      accessibilityState={{ selected: active, disabled }}
      disabled={disabled}
      onPress={onPress}
      onLongPress={() => showToast(label)}
      style={[styles.slot, { opacity: disabled ? 0.45 : 1 }]}
    >
      {({ pressed }) => (
        <View
          style={[styles.iconFace, { borderRadius: radius.default, backgroundColor: pressed ? colours.surface2 : "transparent" }]}
        >
          <Icon name={icon} size={Platform.OS === "ios" ? 17 : 18} colour={active ? activeColour : colours.textMuted} />
        </View>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  dock: {
    position: "absolute",
    start: 0,
    end: 0,
    bottom: 0,
    borderTopWidth: StyleSheet.hairlineWidth,
    boxShadow: "0px -1px 3px rgba(0, 0, 0, 0.08)",
    direction: "rtl",
  },
  scroller: { height: ACTION_BAR_HEIGHT, flexGrow: 0 },
  // px-2 (8dp) = 6 here + each control's 2dp half-gap.
  // flexGrow: a row that fits still fills the bar and starts at the right (RTL).
  row: { flexGrow: 1, flexDirection: "row", alignItems: "center", paddingHorizontal: 8 - HALF_GAP },
  // Every control: full bar height, half the gap on each side (gap-1 = 4dp).
  slot: { height: ACTION_BAR_HEIGHT, paddingHorizontal: HALF_GAP, alignItems: "center", justifyContent: "center" },
  buttonFace: { flexDirection: "row", alignItems: "center", gap: 6, height: CONTROL, paddingHorizontal: 8 },
  iconFace: { width: CONTROL, height: CONTROL, alignItems: "center", justifyContent: "center" },
  // rounded-md border bg-muted/30 p-0.5 gap-0.5
  toggle: { flexDirection: "row", alignItems: "center", gap: 2, padding: 2, borderWidth: StyleSheet.hairlineWidth },
  tab: { flexDirection: "row", alignItems: "center", gap: 4, height: CONTROL, paddingHorizontal: 8 },
  tabIcon: { width: CONTROL, height: CONTROL, alignItems: "center", justifyContent: "center" },
});
