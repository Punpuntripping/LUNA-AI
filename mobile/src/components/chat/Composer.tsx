import { forwardRef } from "react";
import { Pressable, StyleSheet, TextInput, View } from "react-native";

import { AppText } from "@/components/AppText";
import { Button } from "@/components/Button";
import { Icon } from "@/components/Icon";
import { LRI, PDI } from "@/components/markdown/links";
import { formatNumber } from "@/lib/format";
import { fontFamilyFor } from "@/theme/fonts";
import { useTheme } from "@/theme/ThemeProvider";
import { typeVariants } from "@/theme/typography";

import { MAX_MESSAGE_CHARS } from "./UserBubble";

// M7 — the composer. Multiline; Enter inserts a newline (touch rule, web
// ChatInput.tsx:405-416) — the button is the only way to send. Send ↔ Stop
// while THIS conversation answers (Stop = detach; the run still finishes and
// the answer arrives — plan X5). 10,000 chars. Locked at the plan's parallel
// cap. The AI footer (owner decision D1) sits under it, always.
//
// Text alignment is physical on purpose (see TextField.tsx): TextInput's
// textAlign is absolute on both platforms.

export const AI_FOOTER = "ريحان مساعد بالذكاء الاصطناعي وقد يخطئ — تحقق من المصادر";
const COUNTER_FROM = 9_000;  // «9,500 / 10,000» kept LTR inside the RTL line

export const DEMO_COMPOSER_HINT = "هذه محادثة تجريبية للاطّلاع";
export const DEMO_COMPOSER_CTA = "ابدأ محادثة جديدة";

export interface ComposerProps {
  value: string;
  onChangeText: (text: string) => void;
  onSend: () => void;
  onStop: () => void;
  /** This conversation has a send in flight → Stop instead of send. */
  running: boolean;
  /** Another conversation fills the plan's parallel cap. */
  atParallelCap: boolean;
  parallelCap: number | null;
  /** Briefly true while `c/new` creates the conversation. */
  busy?: boolean;
  /** Shared demo conversation: read-only hint instead of the input. */
  demo?: boolean;
  onStartNewChat?: () => void;
}

export const Composer = forwardRef<TextInput, ComposerProps>(function Composer(
  { value, onChangeText, onSend, onStop, running, atParallelCap, parallelCap, busy = false, demo = false, onStartNewChat },
  ref,
) {
  const { colours, radius } = useTheme();

  if (demo) {
    return (
      <View style={[styles.wrap, { borderTopColor: colours.border, backgroundColor: colours.canvas }]}>
        <View style={[styles.demo, { backgroundColor: colours.surface2, borderRadius: radius["2xl"] }]} testID="composer-demo">
          <AppText variant="sm" tone="secondary" align="center">
            {DEMO_COMPOSER_HINT}
          </AppText>
          {onStartNewChat ? <Button label={DEMO_COMPOSER_CTA} onPress={onStartNewChat} testID="composer-demo-cta" /> : null}
        </View>
        <Footer />
      </View>
    );
  }

  const trimmed = value.trim();
  const canSend = !running && !busy && !atParallelCap && trimmed.length > 0 && value.length <= MAX_MESSAGE_CHARS;
  const showCounter = value.length >= COUNTER_FROM;

  return (
    <View style={[styles.wrap, { borderTopColor: colours.border, backgroundColor: colours.canvas }]}>
      {atParallelCap ? (
        <AppText variant="xs" tone="muted" style={styles.note} testID="composer-parallel-note">
          {parallelCap === 1
            ? "محادثة أخرى قيد الإجابة — انتظر اكتمالها ثم أرسل."
            : "وصلت إلى الحدّ الأقصى للمحادثات المتزامنة — انتظر اكتمال إحداها."}
        </AppText>
      ) : null}

      <View style={[styles.box, { borderColor: colours.border, backgroundColor: colours.surface1, borderRadius: radius["2xl"] }]}>
        <TextInput
          ref={ref}
          testID="composer-input"
          value={value}
          onChangeText={onChangeText}
          multiline
          maxLength={MAX_MESSAGE_CHARS}
          placeholder="اكتب سؤالك القانوني…"
          placeholderTextColor={colours.textSubtle}
          accessibilityLabel="اكتب رسالتك"
          editable={!busy}
          textAlignVertical="top"
          style={[
            styles.input,
            typeVariants.base,
            { fontFamily: fontFamilyFor[400], color: colours.textPrimary },
          ]}
        />
        <View style={styles.side}>
          {running ? (
            <Pressable
              testID="composer-stop"
              accessibilityRole="button"
              accessibilityLabel="إيقاف"
              onPress={onStop}
              style={({ pressed }) => [styles.action, { backgroundColor: colours.textPrimary, opacity: pressed ? 0.7 : 1 }]}
            >
              <Icon name="stop" size={18} colour={colours.canvas} />
            </Pressable>
          ) : (
            <Pressable
              testID="composer-send"
              accessibilityRole="button"
              accessibilityLabel="إرسال"
              accessibilityState={{ disabled: !canSend }}
              disabled={!canSend}
              onPress={onSend}
              style={({ pressed }) => [
                styles.action,
                {
                  backgroundColor: canSend ? colours.primary : colours.surface3,
                  opacity: pressed ? 0.7 : 1,
                },
              ]}
            >
              <Icon name="send" size={18} colour={canSend ? colours.primaryFg : colours.textSubtle} />
            </Pressable>
          )}
        </View>
      </View>

      {showCounter ? (
        <AppText
          variant="xs"
          tone={value.length >= MAX_MESSAGE_CHARS ? "destructive" : "muted"}
          style={styles.counter}
          testID="composer-counter"
        >
          {`${LRI}${formatNumber(value.length)} / ${formatNumber(MAX_MESSAGE_CHARS)}${PDI}`}
        </AppText>
      ) : null}

      <Footer />
    </View>
  );
});

function Footer() {
  return (
    <AppText variant="xs" tone="subtle" align="center" style={styles.footer} testID="composer-ai-footer">
      {AI_FOOTER}
    </AppText>
  );
}

const styles = StyleSheet.create({
  wrap: { paddingHorizontal: 12, paddingTop: 8, borderTopWidth: StyleSheet.hairlineWidth },
  note: { marginBottom: 6, paddingHorizontal: 4 },
  box: { flexDirection: "row", alignItems: "flex-end", borderWidth: 1, paddingStart: 14, paddingEnd: 6, paddingVertical: 6 },
  input: {
    flex: 1,
    minHeight: 40,
    maxHeight: 160,
    paddingTop: 8,
    paddingBottom: 8,
    textAlign: "right",
    writingDirection: "rtl",
  },
  side: { paddingBottom: 2, paddingStart: 6 },
  action: { width: 38, height: 38, borderRadius: 19, alignItems: "center", justifyContent: "center" },
  counter: { marginTop: 4, paddingHorizontal: 4 },
  footer: { paddingTop: 6, paddingBottom: 6 },
  demo: { padding: 14, gap: 10 },
});
