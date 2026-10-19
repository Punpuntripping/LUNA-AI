import { useState, type ReactNode } from "react";
import { KeyboardAvoidingView, Modal, Pressable, StyleSheet, TextInput, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { fontFamilyFor } from "@/theme/fonts";
import { useTheme } from "@/theme/ThemeProvider";
import { typeVariants } from "@/theme/typography";
import { useModalPresence } from "@/lib/modal-coordinator";

import { AppText } from "./AppText";
import { Button } from "./Button";
import { Icon, type IconName } from "./Icon";

// Bottom sheets for long-press menus, confirms and the rename prompt. Own
// component (not a native menu) so the rows are Noto Naskh, RTL and themed on
// both platforms — native menus render the system font and lay out per OS.

export interface SheetAction {
  key: string;
  label: string;
  icon?: IconName;
  /** Muted second line under the label (the web menus' item hints). */
  hint?: string;
  destructive?: boolean;
  onPress: () => void;
}

export interface SheetFrameProps {
  visible: boolean;
  onClose: () => void;
  children: ReactNode;
  testID?: string;
}

/** The modal + panel. ONE Modal per flow: switching content inside it avoids
 *  iOS refusing to present a second modal while the first is dismissing. */
export function Sheet({ visible, onClose, children, testID }: SheetFrameProps) {
  const { colours, radius } = useTheme();
  const insets = useSafeAreaInsets();
  // A sheet that presents itself (ArtifactSheet auto-open) waits until this
  // one is gone and its dismiss animation has finished.
  useModalPresence(visible);
  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose} statusBarTranslucent>
      {/* A prompt's keyboard pushes the panel up (edge-to-edge: no window resize). */}
      <KeyboardAvoidingView style={styles.root} behavior="padding">
        <Pressable style={styles.backdrop} onPress={onClose} accessibilityRole="button" accessibilityLabel="إغلاق" />
        <View
          testID={testID}
          style={[
            styles.sheet,
            {
              backgroundColor: colours.surface1,
              borderTopStartRadius: radius["2xl"],
              borderTopEndRadius: radius["2xl"],
              paddingBottom: insets.bottom + 12,
            },
          ]}
        >
          <View style={[styles.grabber, { backgroundColor: colours.borderStrong }]} />
          {children}
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

export interface SheetActionsProps {
  onClose: () => void;
  title?: string;
  /** Optional line under the title (e.g. a confirm's consequence). */
  message?: string;
  actions: SheetAction[];
  /** Multi-step flows: the action itself decides what comes next. */
  keepOpenOnPress?: boolean;
  testID?: string;
}

/** Title + rows + «إلغاء». Tapping a row closes the sheet first, then runs it. */
export function SheetActions({ onClose, title, message, actions, keepOpenOnPress = false, testID }: SheetActionsProps) {
  const { colours } = useTheme();
  return (
    <>
      {title ? (
        <AppText variant="sm" weight={600} tone="muted" numberOfLines={2} style={styles.title}>
          {title}
        </AppText>
      ) : null}
      {message ? (
        <AppText variant="sm" tone="secondary" style={styles.message}>
          {message}
        </AppText>
      ) : null}
      {actions.map((a) => {
        const fg = a.destructive ? colours.destructive : colours.textPrimary;
        return (
          <Pressable
            key={a.key}
            testID={testID ? `${testID}-${a.key}` : undefined}
            accessibilityRole="button"
            accessibilityLabel={a.label}
            accessibilityHint={a.hint}
            onPress={() => {
              if (!keepOpenOnPress) onClose();
              a.onPress();
            }}
            style={({ pressed }) => [styles.row, pressed && { backgroundColor: colours.surface2 }]}
          >
            {a.icon ? <Icon name={a.icon} size={20} colour={fg} /> : null}
            {a.hint ? (
              <View style={styles.labels}>
                <AppText variant="base" style={{ color: fg }}>
                  {a.label}
                </AppText>
                <AppText variant="sm" tone="muted">
                  {a.hint}
                </AppText>
              </View>
            ) : (
              <AppText variant="base" style={{ color: fg }}>
                {a.label}
              </AppText>
            )}
          </Pressable>
        );
      })}
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="إلغاء"
        onPress={onClose}
        style={({ pressed }) => [styles.row, styles.cancel, pressed && { backgroundColor: colours.surface2 }]}
      >
        <AppText variant="base" weight={600} tone="muted">
          إلغاء
        </AppText>
      </Pressable>
    </>
  );
}

export interface ActionSheetProps extends SheetActionsProps {
  visible: boolean;
}

export function ActionSheet({ visible, onClose, testID, ...rest }: ActionSheetProps) {
  return (
    <Sheet visible={visible} onClose={onClose} testID={testID}>
      <SheetActions onClose={onClose} testID={testID} {...rest} />
    </Sheet>
  );
}

export interface SheetPromptProps {
  onClose: () => void;
  title: string;
  initialValue: string;
  confirmLabel: string;
  maxLength?: number;
  onSubmit: (value: string) => void;
  testID?: string;
}

/** One-field prompt (rename). Submits the trimmed value; empty is refused.
 *  Mount it only while shown — it seeds from `initialValue` on mount. */
export function SheetPrompt({ onClose, title, initialValue, confirmLabel, maxLength = 500, onSubmit, testID }: SheetPromptProps) {
  const { colours, radius } = useTheme();
  const [value, setValue] = useState(initialValue);
  const trimmed = value.trim();
  const submit = () => {
    if (!trimmed) return;
    onClose();
    onSubmit(trimmed);
  };

  return (
    <>
      <AppText variant="base" weight={600} style={styles.title}>
        {title}
      </AppText>
      <TextInput
        testID={testID ? `${testID}-input` : undefined}
        value={value}
        onChangeText={setValue}
        autoFocus
        maxLength={maxLength}
        selectTextOnFocus
        returnKeyType="done"
        onSubmitEditing={submit}
        placeholderTextColor={colours.textSubtle}
        style={[
          styles.input,
          typeVariants.base,
          {
            fontFamily: fontFamilyFor[400],
            color: colours.textPrimary,
            borderColor: colours.border,
            backgroundColor: colours.canvas,
            borderRadius: radius.default,
          },
        ]}
      />
      <View style={styles.buttons}>
        <Button label={confirmLabel} disabled={!trimmed} onPress={submit} testID={testID ? `${testID}-confirm` : undefined} />
        <Button label="إلغاء" variant="ghost" onPress={onClose} />
      </View>
    </>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, justifyContent: "flex-end", direction: "rtl" },
  backdrop: { ...StyleSheet.absoluteFill, backgroundColor: "rgba(0,0,0,0.35)" },
  sheet: { paddingTop: 8, paddingHorizontal: 8 },
  grabber: { alignSelf: "center", width: 40, height: 4, borderRadius: 2, marginBottom: 8 },
  title: { paddingHorizontal: 12, paddingVertical: 8 },
  message: { paddingHorizontal: 12, paddingBottom: 8 },
  row: { flexDirection: "row", alignItems: "center", gap: 12, minHeight: 52, paddingHorizontal: 12, borderRadius: 10 },
  labels: { flex: 1, paddingVertical: 6 },
  cancel: { justifyContent: "center" },
  input: { marginHorizontal: 12, borderWidth: 1, paddingHorizontal: 12, paddingVertical: 10, textAlign: "right", writingDirection: "rtl" },
  buttons: { paddingHorizontal: 12, paddingTop: 12, gap: 8 },
});
