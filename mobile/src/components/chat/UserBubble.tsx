import { memo, useCallback, useState } from "react";
import { Pressable, StyleSheet, TextInput, View } from "react-native";

import type { Message } from "@contracts";

import { useSession } from "@/auth/session-store";
import { useIsConversationRunning } from "@/chat/stream-store";
import { ActionSheet, type SheetAction } from "@/components/ActionSheet";
import { AppText } from "@/components/AppText";
import { Button } from "@/components/Button";
import { copyActions, CopyMenu } from "@/components/CopyMenu";
import { Icon } from "@/components/Icon";
import { IconButton } from "@/components/IconButton";
import { formatRelativeTime } from "@/lib/format";
import { fontFamilyFor } from "@/theme/fonts";
import { useTheme } from "@/theme/ThemeProvider";
import { typeVariants } from "@/theme/typography";

import { useChatRow } from "./ChatContext";
import { RetryButton } from "./FailedBubble";

// M3 — the user's question: compact tinted bubble on the start rail (right in
// RTL), same as the web. Long-press (or the row under it): نسخ / النسخ لناجز /
// تعديل. «حفظ وإرسال» is a NEW send with the edited text (web
// MessageBubble.tsx:239-258) — the original stays. A failed row offers
// «إعادة المحاولة» (a new POST; server dedup still guards).

export const MAX_MESSAGE_CHARS = 10_000;

export const UserBubble = memo(function UserBubble({ message }: { message: Message }) {
  const { colours, radius } = useTheme();
  const { conversationId, isDemo, editAndResend, retry } = useChatRow();
  const running = useIsConversationRunning(conversationId);
  const name = useSession((s) => s.user?.call_name?.trim() || s.user?.full_name_ar?.trim() || "أنت");
  const [menu, setMenu] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  const settled = !message.isOptimistic && !message.isFailed;
  // No edit while this conversation is answering (it would supersede the run).
  const canEdit = settled && !isDemo && !running;
  const isAnswer = message.metadata?.kind === "agent_answer";
  const getText = useCallback(() => message.content, [message.content]);

  const startEdit = () => {
    setDraft(message.content);
    setEditing(true);
  };
  const saveEdit = () => {
    const text = draft.trim();
    setEditing(false);
    if (text && text !== message.content.trim()) editAndResend(text);
  };

  const actions: SheetAction[] = [
    ...copyActions(getText),
    ...(canEdit ? [{ key: "edit", label: "تعديل", icon: "edit" as const, onPress: startEdit }] : []),
  ];

  return (
    <View style={[styles.wrap, message.isOptimistic && !message.isFailed && styles.pending]} testID="user-message">
      <View style={styles.caption}>
        <AppText variant="xs" tone="muted">
          {name}
        </AppText>
        {isAnswer ? (
          <View style={[styles.answerTag, { backgroundColor: colours.surface2, borderRadius: radius.sm }]}>
            <AppText variant="xs" tone="muted">
              (جواب)
            </AppText>
          </View>
        ) : null}
      </View>

      {editing ? (
        <View style={styles.edit}>
          <TextInput
            testID="user-message-edit-input"
            value={draft}
            onChangeText={setDraft}
            multiline
            autoFocus
            maxLength={MAX_MESSAGE_CHARS}
            style={[
              styles.editInput,
              typeVariants.sm,
              {
                fontFamily: fontFamilyFor[400],
                color: colours.textPrimary,
                borderColor: colours.border,
                backgroundColor: colours.surface1,
                borderRadius: radius.xl,
              },
            ]}
          />
          <View style={styles.editButtons}>
            <View style={styles.flex}>
              <Button label="حفظ وإرسال" onPress={saveEdit} disabled={!draft.trim()} testID="user-message-edit-save" />
            </View>
            <View style={styles.flex}>
              <Button label="إلغاء" variant="ghost" onPress={() => setEditing(false)} />
            </View>
          </View>
        </View>
      ) : (
        <Pressable
          onLongPress={() => setMenu(true)}
          delayLongPress={350}
          accessibilityHint="اضغط مطولاً لخيارات النسخ والتعديل"
          style={[styles.bubble, { backgroundColor: colours.surface2, borderRadius: radius["2xl"] }]}
        >
          <AppText variant="sm" style={styles.text}>
            {message.content}
          </AppText>
        </Pressable>
      )}

      {message.attachments.length > 0 ? (
        <View style={styles.attachments}>
          {message.attachments.map((a) => (
            <View
              key={a.id}
              style={[styles.attachment, { borderColor: colours.border, borderRadius: radius.default }]}
            >
              <Icon name="template" size={14} colour={colours.textMuted} />
              <AppText variant="xs" tone="secondary" numberOfLines={1} style={styles.flexShrink}>
                {a.filename}
              </AppText>
            </View>
          ))}
        </View>
      ) : null}

      {message.isFailed ? (
        <View style={styles.failed}>
          <Icon name="error" size={15} colour={colours.destructive} />
          <AppText variant="xs" tone="destructive">
            فشل إرسال الرسالة
          </AppText>
          {!running && !isDemo ? <RetryButton onPress={() => retry(message.message_id)} /> : null}
        </View>
      ) : settled && !editing ? (
        <View style={styles.actions}>
          <CopyMenu getText={getText} testID="user-message-copy" />
          {canEdit ? <IconButton icon="edit" label="تعديل" size={18} colour={colours.textMuted} onPress={startEdit} /> : null}
          <AppText variant="xs" tone="subtle">
            {formatRelativeTime(message.created_at)}
          </AppText>
        </View>
      ) : null}

      <ActionSheet visible={menu} onClose={() => setMenu(false)} actions={actions} testID="user-message-menu" />
    </View>
  );
});

const styles = StyleSheet.create({
  wrap: { alignItems: "flex-start", gap: 4, marginBottom: 12 },
  pending: { opacity: 0.7 },
  caption: { flexDirection: "row", alignItems: "center", gap: 6 },
  answerTag: { paddingHorizontal: 6 },
  bubble: { maxWidth: "88%", paddingHorizontal: 16, paddingVertical: 10 },
  text: { lineHeight: 26 },
  edit: { alignSelf: "stretch", gap: 8 },
  editInput: {
    minHeight: 80,
    maxHeight: 220,
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 10,
    textAlign: "right",
    writingDirection: "rtl",
    textAlignVertical: "top",
  },
  editButtons: { flexDirection: "row", gap: 8 },
  flex: { flex: 1 },
  flexShrink: { flexShrink: 1 },
  attachments: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  attachment: { flexDirection: "row", alignItems: "center", gap: 4, borderWidth: 1, paddingHorizontal: 8, paddingVertical: 4, maxWidth: 220 },
  failed: { flexDirection: "row", alignItems: "center", gap: 8, flexWrap: "wrap" },
  actions: { flexDirection: "row", alignItems: "center", gap: 2 },
});
