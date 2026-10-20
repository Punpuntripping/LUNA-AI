import type { ReactNode } from "react";
import { StyleSheet, TextInput } from "react-native";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";

import { fontFamilyFor } from "@/theme/fonts";
import { useTheme } from "@/theme/ThemeProvider";
import { typeVariants } from "@/theme/typography";

import { ACTION_BAR_SPACE } from "./WorkspaceItemActionBar";

export interface DraftEditorProps {
  value: string;
  onChangeText: (next: string) => void;
  /** The agent holds the lock: the body is read-only (autosave waits). */
  readOnly: boolean;
  bottomInset: number;
  /** المراجع + the AI disclaimer, under the body in the SAME scroll column. */
  footer?: ReactNode;
  /** Defaults to the draft's; notes pass «اكتب ملاحظاتك هنا...». */
  placeholder?: string;
  /** Screen-reader label (default «محتوى المسودة»). */
  label?: string;
}

/**
 * «تحرير» of an agent_writing draft: the raw markdown in one multiline RTL
 * input that grows with its content inside a keyboard-aware column (the web's
 * auto-growing textarea), so the caret is scrolled clear of the keyboard and
 * of the action bar riding on top of it.
 */
export function DraftEditor({
  value,
  onChangeText,
  readOnly,
  bottomInset,
  footer,
  placeholder = "محتوى المسودة...",
  label = "محتوى المسودة",
}: DraftEditorProps) {
  const { colours } = useTheme();
  return (
    <KeyboardAwareScrollView
      bottomOffset={ACTION_BAR_SPACE + 12}
      keyboardShouldPersistTaps="handled"
      contentContainerStyle={{ paddingHorizontal: 16, paddingTop: 12, paddingBottom: 24 + ACTION_BAR_SPACE + bottomInset }}
      testID="wi-draft-editor"
    >
      <TextInput
        testID="wi-draft-input"
        value={value}
        onChangeText={onChangeText}
        editable={!readOnly}
        multiline
        scrollEnabled={false}
        textAlignVertical="top"
        placeholder={placeholder}
        placeholderTextColor={colours.textSubtle}
        accessibilityLabel={label}
        autoCorrect={false}
        style={[
          styles.input,
          typeVariants.base,
          { fontFamily: fontFamilyFor[400], color: colours.textPrimary },
        ]}
      />
      {footer}
    </KeyboardAwareScrollView>
  );
}

const styles = StyleSheet.create({
  input: { minHeight: 240, paddingVertical: 4, textAlign: "right", writingDirection: "rtl" },
});
