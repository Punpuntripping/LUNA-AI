import { useRef } from "react";
import { Pressable, StyleSheet, TextInput, View } from "react-native";

import { toLatinDigits } from "@contracts";

import { fontFamilyFor } from "@/theme/fonts";
import { useTheme } from "@/theme/ThemeProvider";

import { AppText } from "./AppText";

export const CODE_LENGTH = 6;

export interface CodeInputProps {
  value: string;
  onChange: (code: string) => void;
  /** Fired once the last digit lands (typing, paste or OS autofill). */
  onComplete?: (code: string) => void;
  error?: boolean;
  editable?: boolean;
  testID?: string;
}

/** Digits only, Latin, at most CODE_LENGTH. Accepts «١٢٣ ٤٥٦», "123 456", "123-456". */
export function normaliseCode(raw: string): string {
  return toLatinDigits(raw).replace(/\D/g, "").slice(0, CODE_LENGTH);
}

/**
 * Six boxes driven by ONE invisible TextInput, so a paste or the OS one-time
 * code autofill (iOS Mail/Messages, Android) fills every box at once.
 * The boxes read left-to-right like the code in the email, even in RTL.
 */
export function CodeInput({ value, onChange, onComplete, error = false, editable = true, testID }: CodeInputProps) {
  const { colours, radius } = useTheme();
  const inputRef = useRef<TextInput>(null);

  const handle = (raw: string) => {
    const code = normaliseCode(raw);
    onChange(code);
    if (code.length === CODE_LENGTH && code !== value) onComplete?.(code);
  };

  return (
    <Pressable onPress={() => inputRef.current?.focus()} accessibilityLabel="رمز التحقق" style={styles.wrap}>
      <View style={styles.row} pointerEvents="none">
        {Array.from({ length: CODE_LENGTH }, (_, i) => {
          const active = editable && i === Math.min(value.length, CODE_LENGTH - 1);
          return (
            <View
              key={i}
              style={[
                styles.box,
                {
                  borderRadius: radius.default,
                  backgroundColor: colours.surface1,
                  borderColor: error ? colours.destructive : active ? colours.ring : colours.border,
                },
              ]}
            >
              <AppText variant="xl" weight={600} align="center">
                {value[i] ?? ""}
              </AppText>
            </View>
          );
        })}
      </View>
      <TextInput
        ref={inputRef}
        testID={testID}
        value={value}
        onChangeText={handle}
        editable={editable}
        autoFocus
        keyboardType="number-pad"
        inputMode="numeric"
        textContentType="oneTimeCode"
        autoComplete="one-time-code"
        maxLength={CODE_LENGTH + 4}
        caretHidden
        style={[styles.hidden, { fontFamily: fontFamilyFor[400] }]}
      />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  wrap: { position: "relative" },
  row: { flexDirection: "row", direction: "ltr", justifyContent: "center", gap: 8 },
  box: { width: 46, height: 56, borderWidth: 1.5, alignItems: "center", justifyContent: "center" },
  // Covers the boxes so a long-press offers «لصق»; text itself is invisible.
  hidden: { position: "absolute", top: 0, bottom: 0, start: 0, end: 0, opacity: 0.02, color: "transparent" },
});
