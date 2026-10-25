import { forwardRef, useCallback, useState, type ReactNode } from "react";
import { Pressable, StyleSheet, TextInput, View } from "react-native";

import { attachmentGate, useConversationAttachments } from "@/attachments/hooks";
import { MAX_ATTACHMENTS, MSG_TOO_MANY, validatePicks, type PickedFile } from "@/attachments/limits";
import { pickDocuments, PickerDenied, pickPhotos, takePhoto } from "@/attachments/pickers";
import { attachmentUploads } from "@/attachments/uploader";
import type { SheetAction } from "@/components/ActionSheet";
import { AppText } from "@/components/AppText";
import { Button } from "@/components/Button";
import { Icon } from "@/components/Icon";
import { LRI, PDI } from "@/components/markdown/links";
import { formatNumber } from "@/lib/format";
import { fontFamilyFor } from "@/theme/fonts";
import { useTheme } from "@/theme/ThemeProvider";
import { typeVariants } from "@/theme/typography";

import { AttachmentChips } from "./composer/AttachmentChips";
import { AttachSheet, type AttachSource } from "./composer/AttachSheet";
import { MAX_MESSAGE_CHARS } from "./UserBubble";

// M7 — the composer. Multiline; Enter inserts a newline (touch rule, web
// ChatInput.tsx:405-416) — the button is the only way to send. Send ↔ Stop
// while THIS conversation answers (Stop = detach; the run still finishes and
// the answer arrives — plan X5). 10,000 chars. Locked at the plan's parallel
// cap. The AI footer (owner decision D1) sits under it, always.
//
// Text alignment is physical on purpose (see TextField.tsx): TextInput's
// textAlign is absolute on both platforms.
//
// F4 — «+» opens the attach sheet (ملف / الصور / الكاميرا + the screen's extra
// rows); chips sit above the input. Send is blocked while any chip uploads and
// possible with completed chips alone (the engine then sends «مرفق» as the
// text). In c/new a pick first asks the screen to create the conversation
// (`onRequireConversation`, web create-on-attach) and uploads there. The
// screen's قالب / مدونة pills ride in `extraChips`; `sendableWithoutText` /
// `blockSend` fold them into the same send gate.

export const AI_FOOTER = "ريحان مساعد بالذكاء الاصطناعي وقد يخطئ — تحقق من المصادر";
const COUNTER_FROM = 9_000;  // «9,500 / 10,000» kept LTR inside the RTL line

export const DEMO_COMPOSER_HINT = "هذه محادثة تجريبية للاطّلاع";
export const DEMO_COMPOSER_CTA = "ابدأ محادثة جديدة";

// Web ChatInput.tsx:870 (no create-on-attach handler).
const MSG_NEEDS_CONVERSATION = "ابدأ محادثة أولاً قبل إضافة المرفقات";
const MSG_PICKER_FAILED = "تعذّر فتح الملفات — حاول مجدداً";

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
  /** Attachments: the conversation the chips belong to (`null` = c/new).
   *  Omitted ⇒ no «+» at all. */
  conversationId?: string | null;
  /** c/new: create the conversation (and switch to it); resolves its id, or
   *  null when creation failed (the screen shows why). */
  onRequireConversation?: () => Promise<string | null>;
  /** More rows for the «+» sheet (قالب، مدونة، مستوى التفصيل…). */
  attachActions?: SheetAction[];
  /** More pills above the input, before the attachment chips (قالب / مدونة). */
  extraChips?: ReactNode;
  /** Something outside the text makes the message sendable (a template, a
   *  ready blog) — like completed attachment chips. */
  sendableWithoutText?: boolean;
  /** Something outside the text is still loading (a blog import) — no send. */
  blockSend?: boolean;
  /** Bumped when the screen adds a قالب or مدونة — clears the attachment
   *  error line like the web's `setValidationError(null)` on those picks. */
  errorResetKey?: number;
}

export const Composer = forwardRef<TextInput, ComposerProps>(function Composer(
  {
    value,
    onChangeText,
    onSend,
    onStop,
    running,
    atParallelCap,
    parallelCap,
    busy = false,
    demo = false,
    onStartNewChat,
    conversationId,
    onRequireConversation,
    attachActions,
    extraChips,
    sendableWithoutText = false,
    blockSend = false,
    errorResetKey = 0,
  },
  ref,
) {
  const { colours, radius } = useTheme();

  // ---- attachments (F4) ----
  const attachEnabled = conversationId !== undefined;
  const chips = useConversationAttachments(conversationId ?? null);
  const gate = attachmentGate(chips);
  const [sheetOpen, setSheetOpen] = useState(false);
  // The web's one `validationError` line (ChatInput.tsx): cleared when files
  // are picked again, on a قالب / مدونة pick (`errorResetKey`) and on send —
  // never left standing until the screen is left (QA F6).
  const [attachError, setAttachError] = useState<string | null>(null);
  const [seenResetKey, setSeenResetKey] = useState(errorResetKey);
  if (seenResetKey !== errorResetKey) {
    setSeenResetKey(errorResetKey);
    setAttachError(null);
  }
  const canAttach = attachEnabled && (!!conversationId || !!onRequireConversation);

  const addPicked = useCallback(
    async (picked: PickedFile[]) => {
      if (picked.length === 0) return;
      // The web's up-front check: one bad file refuses the whole selection,
      // and c/new never creates a conversation for an invalid one.
      const invalid = validatePicks(chips.length, picked);
      if (invalid) {
        setAttachError(invalid);
        return;
      }
      setAttachError(null);
      let id = conversationId ?? null;
      if (!id) {
        if (!onRequireConversation) {
          setAttachError(MSG_NEEDS_CONVERSATION);
          return;
        }
        id = await onRequireConversation();
        if (!id) return;
      }
      attachmentUploads.add(id, picked);
    },
    [chips.length, conversationId, onRequireConversation],
  );

  const onPick = useCallback(
    async (source: AttachSource) => {
      setAttachError(null);
      const remaining = MAX_ATTACHMENTS - chips.length;
      if (remaining <= 0) {
        setAttachError(MSG_TOO_MANY);
        return;
      }
      try {
        const picked =
          source === "files" ? await pickDocuments() : source === "photos" ? await pickPhotos(remaining) : await takePhoto();
        await addPicked(picked);
      } catch (err) {
        setAttachError(err instanceof PickerDenied ? err.message : MSG_PICKER_FAILED);
      }
    },
    [chips.length, addPicked],
  );

  const onRemoveChip = useCallback(
    (id: string) => {
      if (conversationId) attachmentUploads.remove(conversationId, id);
    },
    [conversationId],
  );
  const onRetryChip = useCallback(
    (id: string) => {
      if (conversationId) attachmentUploads.retry(conversationId, id);
    },
    [conversationId],
  );

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
  const canSend =
    !running &&
    !busy &&
    !atParallelCap &&
    !gate.uploading &&
    !blockSend &&
    (trimmed.length > 0 || gate.sendable > 0 || sendableWithoutText) &&
    value.length <= MAX_MESSAGE_CHARS;
  const showCounter = value.length >= COUNTER_FROM;
  const addDisabled = running || busy;

  return (
    <View style={[styles.wrap, { borderTopColor: colours.border, backgroundColor: colours.canvas }]}>
      {atParallelCap ? (
        <AppText variant="xs" tone="muted" style={styles.note} testID="composer-parallel-note">
          {parallelCap === 1
            ? "محادثة أخرى قيد الإجابة — انتظر اكتمالها ثم أرسل."
            : "وصلت إلى الحدّ الأقصى للمحادثات المتزامنة — انتظر اكتمال إحداها."}
        </AppText>
      ) : null}

      {extraChips}
      {attachEnabled ? <AttachmentChips chips={chips} onRemove={onRemoveChip} onRetry={onRetryChip} /> : null}
      {attachError ? (
        <AppText variant="xs" tone="destructive" style={styles.note} testID="composer-attach-error">
          {attachError}
        </AppText>
      ) : null}

      <View style={[styles.box, { borderColor: colours.border, backgroundColor: colours.surface1, borderRadius: radius["2xl"] }]}>
        {attachEnabled ? (
          <Pressable
            testID="composer-add"
            accessibilityRole="button"
            accessibilityLabel="إضافة إلى الرسالة"
            accessibilityState={{ disabled: addDisabled }}
            disabled={addDisabled}
            hitSlop={6}
            onPress={() => setSheetOpen(true)}
            style={({ pressed }) => [styles.add, { opacity: addDisabled ? 0.4 : pressed ? 0.6 : 1 }]}
          >
            <Icon name="plus" size={22} colour={colours.textMuted} />
          </Pressable>
        ) : null}
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
              accessibilityLabel={gate.uploading || blockSend ? "جارٍ رفع المرفقات" : "إرسال"}
              accessibilityState={{ disabled: !canSend }}
              disabled={!canSend}
              onPress={() => {
                // Web: a send clears the line (a too-long text replaces it
                // with its own error, shown by the screen).
                setAttachError(null);
                onSend();
              }}
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

      {attachEnabled ? (
        <AttachSheet
          visible={sheetOpen}
          onClose={() => setSheetOpen(false)}
          onPick={(source) => void onPick(source)}
          canAttach={canAttach}
          actions={attachActions}
        />
      ) : null}
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
  add: { width: 36, height: 38, marginStart: -10, alignItems: "center", justifyContent: "center" },
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
